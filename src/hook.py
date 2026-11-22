"""
hook.py - Claude CodeのStopHookから呼ばれるエントリーポイント。
transcriptを読み込んでメモリDBに保存し、HANDOVER.mdを生成する。

stdinからJSON形式でフック情報を受け取り、transcript_pathを取得して処理する。
stdoutには何も出力しない（Claude Codeがstdoutを解釈するため）。
例外が発生してもstderrにログを出して正常終了する（Claude Codeの動作を止めないため）。
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Windowsのcp932問題を回避するためstdout/stderrをUTF-8に再設定する
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# srcパッケージとして実行される場合とスクリプト直接実行の両方に対応
_src_dir = Path(__file__).parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from chunker import load_chunks
from storage import init_db, save_chunks_text_only

# ベクトル化はこのフックではやらない（毎ターン約1.2GBのモデル読み込みになるため）。
# 担当＝kizamiの自動/sl巡回（idle_sl_watcher）と手動の/sr（2026-11-23 整理）。

# kizamiの自動/slは、元の会話の写し（--fork-session）の中で/slを撃つ。
# 写しの中でもこのフックが動くと、元の会話の複製が別の会話IDで保存されて検索に二重に出る。
# 巡回は写しを撃つ間だけこの環境変数を立てるので、立っていたら何もしない（2026-11-23）。
_AUTO_SL_FORK_ENV = "KIZAMI_AUTO_SL_FORK"


def _log(message: str) -> None:
    """stderrにフォーマット済みログを出力する（stdoutは使わない）"""
    print(f"[sui-memory] {message}", file=sys.stderr)


def _generate_handover(cwd: str, chunks: list[dict]) -> None:
    """
    HANDOVER.mdをcwdに生成する。
    CLAUDE.mdが存在するディレクトリのみ対象（プロジェクトディレクトリ限定）。

    Args:
        cwd: 作業ディレクトリのパス
        chunks: セッションのQ&Aチャンクリスト
    """
    cwd_path = Path(cwd)

    # CLAUDE.mdが存在しないディレクトリ（ホームディレクトリ等）はスキップ
    if not (cwd_path / "CLAUDE.md").exists():
        _log(f"CLAUDE.mdが見つからないためHANDOVER.md生成をスキップ: {cwd}")
        return

    # 現在日時を取得（JST表記）
    now = datetime.now()
    date_str = now.strftime("%Y/%m/%d")
    time_str = now.strftime("%H:%M")

    # セッション情報を先頭チャンクから取得
    session_id = chunks[0].get("session_id", "不明") if chunks else "不明"
    # timestampはISO8601形式なので人間が読みやすい形式に変換
    raw_ts = chunks[-1].get("timestamp", "") if chunks else ""
    try:
        ts_dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        timestamp_str = ts_dt.strftime("%Y/%m/%d %H:%M")
    except (ValueError, AttributeError):
        timestamp_str = raw_ts or "不明"

    # 直近のチャンクのuser_textを最大10件、箇条書きにする
    recent_chunks = chunks[-10:] if len(chunks) > 10 else chunks
    user_lines = "\n".join(
        f"- {chunk['user'][:80].replace(chr(10), ' ')}"
        for chunk in recent_chunks
        if chunk.get("user")
    )
    if not user_lines:
        user_lines = "- （記録なし）"

    # HANDOVER.mdの内容を組み立てる
    content = f"""# セッション引き継ぎ（{date_str} {time_str}）

## 最初にやること
1. CLAUDE.mdを読め
2. このファイルを読め
3. 指示を待て

## 前回セッション情報
- セッションID: {session_id}
- 終了時刻: {timestamp_str}

## 今回のセッションで話した内容
{user_lines}

## 未解決・積み残し
※ CLAUDE.mdのTODOセクションを確認せよ

## 開発体制
- Claude.ai: 設計・仕様を固める。ターミナルへの指示書を作る
- Claude Code（ターミナル）: コードを読んで書いてデプロイする
- 運営者: 判断・テスト・確認を行う
"""

    # HANDOVER.mdをcwdに書き出す
    handover_path = cwd_path / "HANDOVER.md"
    handover_path.write_text(content, encoding="utf-8")
    _log(f"HANDOVER.mdを生成しました: {handover_path}")


def main() -> None:
    """
    StopHookのエントリーポイント。
    stdinからJSONを読み込み、transcript_pathのtranscriptをメモリDBに保存する。
    CLAUDE.mdが存在するプロジェクトにはHANDOVER.mdも生成する。
    """
    # 自動/slの写しの中なら何もしない（元の会話は元のセッションのフックが保存している）
    if os.environ.get(_AUTO_SL_FORK_ENV) == "1":
        return

    try:
        # stdinからフック情報を読み込む（バイト読みでサロゲート問題を回避）
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        hook_data = json.loads(raw)
        transcript_path = hook_data.get("transcript_path", "")

        if not transcript_path:
            _log("transcript_pathが見つかりません。スキップします。")
            return

        # transcriptファイルの存在確認
        path = Path(transcript_path)
        if not path.exists():
            _log(f"警告: transcriptファイルが存在しません: {transcript_path}")
            return

        # transcriptをQ&Aチャンクに分割
        chunks = load_chunks(str(path))

        if not chunks:
            _log("チャンクが0件のため保存をスキップします。")
            return

        # DBを初期化（既存テーブルがあればスキップ）
        init_db()

        # テキストのみDBに保存（embedding=NULL、ベクトル化は/srで遅延実行）
        inserted = save_chunks_text_only(chunks)

        # セッションIDは先頭チャンクから取得（短縮表示用）
        session_short = chunks[0].get("session_id", "")[:8]
        _log(f"{inserted}件のメモリを保存しました（セッション: {session_short}）")

        # cwdを取得してHANDOVER.mdを生成する
        # chunksのprojectフィールドを優先し、なければhook_dataのcwdを使う
        cwd = chunks[0].get("project", "") or hook_data.get("cwd", "")
        if cwd:
            _generate_handover(cwd, chunks)
        else:
            _log("cwdが取得できないためHANDOVER.md生成をスキップします。")

    except json.JSONDecodeError as e:
        # stdinのJSONパースに失敗した場合
        _log(f"エラー: JSONのパースに失敗しました: {e}")

    except Exception as e:
        # その他の予期しないエラー（Claude Codeの動作は止めない）
        _log(f"エラー: {e}")


if __name__ == "__main__":
    main()
