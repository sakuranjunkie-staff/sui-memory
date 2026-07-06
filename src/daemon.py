"""
daemon.py - sui-memory 常駐 HTTP サーバ

目的:
- /sr 等の手動操作で「使い捨て python プロセス」を毎回立ち上げるコストを削減
- ruri モデル + sentence-transformers + torch を**起動時に1回だけ**ロード
- 以降は HTTP リクエストで数百 ms で応答する

エンドポイント:
- GET  /health           — 生存確認（モデルロード状態含む）
- POST /save_session     — 最新セッションの jsonl をチャンク化して保存 + ベクトル化
- POST /embed_pending    — embedding=NULL のチャンクを一括ベクトル化
- POST /save_and_embed   — 上記2つをまとめて実行（/sr 互換）

ポート: 7766（環境変数 SUI_MEMORY_DAEMON_PORT で上書き可）
バインド: 127.0.0.1（ローカル限定・外部公開しない）

起動方法:
    uv run --project C:/Users/bukol/Documents/sui-memory python -m sui_memory_daemon
    または:
    uv run --project C:/Users/bukol/Documents/sui-memory uvicorn src.daemon:app --host 127.0.0.1 --port 7766

クライアント側 (/sr 等):
- requests.get("http://127.0.0.1:7766/health") で 200 が返れば生存
- POST /save_and_embed で /sr 相当の処理（数百 ms で完了）
- 接続失敗時は従来の使い捨て python 起動に fallback

設計:
- ruri モデル + DB は module-level に保持（preload）
- 各エンドポイントは既存の chunker.load_chunks / storage.* を再利用
- 並行リクエストは sentence-transformers の内部スレッドセーフ性に依存（基本 OK）
"""

from __future__ import annotations

import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# パッケージ内部 import を有効にする
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# 起動時にモデルプリロード
import chunker
import storage
import embedder
import retriever


# ---------------------------------------------------------------------------
# モジュールレベル状態（プリロード）
# ---------------------------------------------------------------------------

_DAEMON_STARTED_AT: float = 0.0
_DAEMON_VERSION: str = "1.1.0"

# 環境変数 SUI_MEMORY_SHUTDOWN_AFTER=1 で起動された場合、
# /save_and_embed や /embed_pending の処理完了後に自動で exit する。
# これは /sr が「使い捨て daemon」としてオンデマンド起動するためのフラグ。
# 既存の daemon を別用途で立てている場合は環境変数なしで起動するので影響しない。
_SHUTDOWN_AFTER_REQUEST: bool = os.getenv("SUI_MEMORY_SHUTDOWN_AFTER", "0") == "1"

# 環境変数 SUI_MEMORY_IDLE_TIMEOUT=<秒> で起動された場合、モデルを1回だけロードして
# 常駐し、その秒数だけリクエストが無ければ自動 exit する（常駐 + アイドルタイムアウト）。
# これにより /sr を連続で叩くと2回目以降は温まった daemon が即応答し、
# 無操作が続けば 1.2GB のモデルを抱えたまま居座らずに解放される。
# SHUTDOWN_AFTER（1リクエストで即 exit）とは排他的な2つの運用モード。
_IDLE_TIMEOUT: float = float(os.getenv("SUI_MEMORY_IDLE_TIMEOUT", "0") or "0")

# 最終リクエスト時刻（アイドル判定用）。ミドルウェアが毎リクエスト更新する。
_LAST_REQUEST_AT: float = 0.0


def _start_idle_watchdog() -> None:
    """SUI_MEMORY_IDLE_TIMEOUT>0 のとき、無操作が続いたら daemon を自動終了する監視スレッド。"""
    if _IDLE_TIMEOUT <= 0:
        return

    def _run():
        # チェック間隔はタイムアウトの1/4程度（最大30秒・最小5秒）
        interval = max(5.0, min(30.0, _IDLE_TIMEOUT / 4))
        while True:
            time.sleep(interval)
            if time.time() - _LAST_REQUEST_AT > _IDLE_TIMEOUT:
                os._exit(0)

    threading.Thread(target=_run, daemon=True).start()


def _delayed_shutdown(delay_sec: float = 0.5) -> None:
    """応答が送り返されたあとに daemon プロセスを終了する。

    クライアントが応答を受け取り終わる前に exit するとレスポンスが届かないため、
    別スレッドで短い遅延後に os._exit(0) を呼ぶ。
    """
    def _run():
        time.sleep(delay_sec)
        os._exit(0)
    threading.Thread(target=_run, daemon=True).start()


def _preload() -> None:
    """起動時に DB 初期化 + ruri モデルをメモリにロードする。

    ここで時間のかかる import + モデルロードを終わらせれば、以降の HTTP リクエストは
    ベクトル化処理本体のみで数百 ms で応答する。
    """
    global _DAEMON_STARTED_AT, _LAST_REQUEST_AT
    storage.init_db()
    # SentenceTransformer のシングルトンロード（chunker / embedder / retriever 全てで共有）
    embedder.get_embedder()
    _DAEMON_STARTED_AT = time.time()
    _LAST_REQUEST_AT = time.time()
    _start_idle_watchdog()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _preload()
    yield


app = FastAPI(title="sui-memory daemon", version=_DAEMON_VERSION, lifespan=_lifespan)


@app.middleware("http")
async def _touch_idle(request, call_next):
    """毎リクエストでアイドルタイマーを更新する（IDLE_TIMEOUT モードの自動終了判定用）。"""
    global _LAST_REQUEST_AT
    _LAST_REQUEST_AT = time.time()
    return await call_next(request)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    ok: bool
    version: str
    started_at: float
    uptime_sec: float
    db_path: str
    model_loaded: bool


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        ok=True,
        version=_DAEMON_VERSION,
        started_at=_DAEMON_STARTED_AT,
        uptime_sec=time.time() - _DAEMON_STARTED_AT,
        db_path=str(storage.DB_PATH),
        model_loaded=embedder._embedder is not None,
    )


# ---------------------------------------------------------------------------
# Save current session (jsonl → chunk → DB)
# ---------------------------------------------------------------------------


class SaveSessionRequest(BaseModel):
    # 任意のセッションファイルパス（省略時は projects/ から最新を自動検出）
    transcript_path: Optional[str] = None


class SaveSessionResponse(BaseModel):
    inserted: int
    skipped: int
    transcript: str


def _find_latest_transcript() -> Optional[Path]:
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return None
    jsonl_files = [p for p in projects_dir.rglob("*.jsonl") if p.stat().st_size > 0]
    if not jsonl_files:
        return None
    return max(jsonl_files, key=lambda p: p.stat().st_mtime)


@app.post("/save_session", response_model=SaveSessionResponse)
def save_session(req: SaveSessionRequest) -> SaveSessionResponse:
    if req.transcript_path:
        path = Path(req.transcript_path)
        if not path.exists():
            raise HTTPException(404, f"transcript not found: {req.transcript_path}")
    else:
        path = _find_latest_transcript()
        if path is None:
            raise HTTPException(404, "no transcript found in ~/.claude/projects")

    chunks = chunker.load_chunks(str(path))
    if not chunks:
        return SaveSessionResponse(inserted=0, skipped=0, transcript=path.name)

    inserted = storage.save_chunks_text_only(chunks)
    skipped = len(chunks) - inserted
    return SaveSessionResponse(inserted=inserted, skipped=skipped, transcript=path.name)


# ---------------------------------------------------------------------------
# Embed pending (NULL embedding rows)
# ---------------------------------------------------------------------------


class EmbedPendingRequest(BaseModel):
    batch_size: int = 100
    # 任意の DB パス。指定しない場合はデフォルト DB（~/.sui-memory/memory.db）。
    # KagamiAlice など別 DB を使うクライアントは絶対パスを指定する。
    db_path: Optional[str] = None


class EmbedPendingResponse(BaseModel):
    embedded: int


# ---------------------------------------------------------------------------
# DB パスのホワイトリスト（ローカル攻撃者が任意 SQLite を開けないようガード）
# ---------------------------------------------------------------------------

_ALLOWED_DB_PATHS: set[Path] = {
    storage.DB_PATH.resolve(),  # sui-memory デフォルト
    Path(r"G:\KagamiAlice\.sui-memory\memory.db").resolve(),  # KagamiAlice 専用
}


def _validate_db_path(raw_path: str) -> Path:
    """db_path がホワイトリストに含まれるか確認して Path を返す。

    違反時は HTTPException(403) で拒絶する。
    """
    candidate = Path(raw_path).resolve()
    if candidate not in _ALLOWED_DB_PATHS:
        raise HTTPException(
            status_code=403,
            detail=f"db_path not in allowlist: {candidate}",
        )
    return candidate


@app.post("/embed_pending", response_model=EmbedPendingResponse)
def embed_pending_endpoint(req: EmbedPendingRequest) -> EmbedPendingResponse:
    """埋め込み未完了レコードをベクトル化する。

    NOTE: このエンドポイントは `_SHUTDOWN_AFTER_REQUEST` を発火させない。
    /embed_pending は KagamiAlice 等の常時動作するクライアントから繰り返し叩かれる
    用途で、Claude /sr のオンデマンド起動 daemon（SHUTDOWN_AFTER=1）と
    ニアミスすると daemon が落ちて以後 fallback 経路に張り付く事故になるため。
    daemon の自動 exit は /save_and_embed のみ反応する設計。
    """
    if req.db_path:
        db_path = _validate_db_path(req.db_path)
        embedded = storage.embed_pending(batch_size=req.batch_size, db_path=db_path)
    else:
        embedded = storage.embed_pending(batch_size=req.batch_size)
    return EmbedPendingResponse(embedded=embedded)


# ---------------------------------------------------------------------------
# Save & embed (/sr 相当・1 リクエストで完結)
# ---------------------------------------------------------------------------


class SaveAndEmbedRequest(BaseModel):
    transcript_path: Optional[str] = None
    batch_size: int = 100


class SaveAndEmbedResponse(BaseModel):
    inserted: int
    skipped: int
    embedded: int
    transcript: str


@app.post("/save_and_embed", response_model=SaveAndEmbedResponse)
def save_and_embed(req: SaveAndEmbedRequest) -> SaveAndEmbedResponse:
    """/sr スキルが呼ぶエンドポイント。最新セッション保存 + 未ベクトル化分の処理を 1 回で行う。

    SUI_MEMORY_SHUTDOWN_AFTER=1 で起動された daemon は、このレスポンス送信後に自動 exit する
    （/sr のオンデマンド起動用途）。
    """
    save_req = SaveSessionRequest(transcript_path=req.transcript_path)
    save_resp = save_session(save_req)
    embedded = storage.embed_pending(batch_size=req.batch_size)
    if _SHUTDOWN_AFTER_REQUEST:
        _delayed_shutdown()
    return SaveAndEmbedResponse(
        inserted=save_resp.inserted,
        skipped=save_resp.skipped,
        embedded=embedded,
        transcript=save_resp.transcript,
    )


# ---------------------------------------------------------------------------
# CLI: スタンドアロン起動
# ---------------------------------------------------------------------------


def _run() -> None:
    """`python -m daemon` 用の uvicorn 起動エントリーポイント。"""
    import uvicorn
    port = int(os.getenv("SUI_MEMORY_DAEMON_PORT", "7766"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    _run()
