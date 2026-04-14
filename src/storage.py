"""
storage.py - チャンクをSQLiteに保存・取得するモジュール
DBファイルは ~/.sui-memory/memory.db に配置する
"""

import sqlite3
import struct
import time
from pathlib import Path

import sqlite_vec


# DBファイルのデフォルトパス
DB_PATH = Path.home() / ".sui-memory" / "memory.db"


def _get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """
    SQLite接続を返す。
    DBディレクトリが存在しない場合は自動作成する。
    sqlite-vec拡張をロードしてベクトル検索を有効化する。
    WALモードを有効化し、並行アクセス時のロック競合に備えて5秒のbusyタイムアウトを設定する。
    （StopHookのasync実行と/srの同時実行で SQLITE_BUSY にならないようにするため）
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # timeout=5.0: ロック競合時に最大5秒リトライしてからエラー（デフォルトは0秒=即失敗）
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    # sqlite-vec拡張をロード（ベクトル検索に必要）
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    # WALモード: 並行read/writeに強く、書き込みが他のreaderをブロックしない
    conn.execute("PRAGMA journal_mode=WAL")
    # カラム名でアクセスできるようにする
    conn.row_factory = sqlite3.Row
    return conn


def _vec_to_blob(vec: list[float]) -> bytes:
    """float のリストをSQLite BLOB（バイナリ）に変換する"""
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> list[float]:
    """SQLite BLOB（バイナリ）を float のリストに変換する"""
    n = len(blob) // 4  # float32 = 4バイト
    return list(struct.unpack(f"{n}f", blob))


def init_db(db_path: Path = DB_PATH) -> None:
    """
    DBとテーブルを初期化する。
    既にテーブルが存在する場合はスキップする（CREATE TABLE IF NOT EXISTS）。
    embeddingカラム・project_nameカラムが未追加の場合はALTER TABLEでマイグレーションする。
    """
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()

        # メモリ本体テーブル（embeddingカラムはマイグレーションで追加）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id    TEXT    NOT NULL,
                project       TEXT,
                user_text     TEXT    NOT NULL,
                assistant_text TEXT   NOT NULL,
                timestamp     TEXT    NOT NULL,
                created_at    REAL    NOT NULL
            )
        """)

        # カラム追加マイグレーション（既存DBに不足カラムを追加）
        cur.execute("PRAGMA table_info(memories)")
        columns = {row["name"] for row in cur.fetchall()}
        if "embedding" not in columns:
            cur.execute("ALTER TABLE memories ADD COLUMN embedding BLOB")
        if "project_name" not in columns:
            # プロジェクトパスの末尾ディレクトリ名（例: "fx-shield"）
            cur.execute("ALTER TABLE memories ADD COLUMN project_name TEXT")

        # FTS5全文検索インデックス（trigramトークナイザーで部分一致対応）
        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                user_text,
                assistant_text,
                content=memories,
                content_rowid=id,
                tokenize='trigram'
            )
        """)

        # FTS5の自動同期トリガー（INSERT時）
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ai
            AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, user_text, assistant_text)
                VALUES (new.id, new.user_text, new.assistant_text);
            END
        """)

        # FTS5の自動同期トリガー（DELETE時）
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ad
            AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, user_text, assistant_text)
                VALUES ('delete', old.id, old.user_text, old.assistant_text);
            END
        """)

        # FTS5の自動同期トリガー（UPDATE時）
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_au
            AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, user_text, assistant_text)
                VALUES ('delete', old.id, old.user_text, old.assistant_text);
                INSERT INTO memories_fts(rowid, user_text, assistant_text)
                VALUES (new.id, new.user_text, new.assistant_text);
            END
        """)

        conn.commit()
    finally:
        conn.close()


def _extract_project_name(project: str) -> str:
    """
    プロジェクトパスの末尾ディレクトリ名を返す。
    例: "C:\\Users\\bukol\\Documents\\fx-shield" → "fx-shield"
    空パスや解析失敗時は空文字を返す。
    """
    if not project:
        return ""
    try:
        return Path(project).name
    except Exception:
        return ""


def _build_to_insert(cur, chunks: list[dict]) -> tuple[list[dict], list[str]]:
    """
    チャンクリストから新規保存対象を絞り込む共通処理。
    (session_id, timestamp) の組み合わせでDBと重複チェックする。
    戻り値: (to_insert, project_names)
    """
    to_insert = []
    for chunk in chunks:
        session_id = chunk.get("session_id", "")
        timestamp  = chunk.get("timestamp", "")
        cur.execute(
            "SELECT COUNT(*) FROM memories WHERE session_id = ? AND timestamp = ?",
            (session_id, timestamp)
        )
        if cur.fetchone()[0] == 0:
            to_insert.append(chunk)

    project_names = [
        _extract_project_name(chunk.get("project", ""))
        for chunk in to_insert
    ]
    return to_insert, project_names


def save_chunks_text_only(chunks: list[dict], db_path: Path = DB_PATH) -> int:
    """
    チャンクのテキストのみ保存する（embedding=NULL）。
    StopHookからの高速保存用。embedderを一切呼ばないのでミリ秒で完了する。
    ベクトル化は後から embed_pending() で行う。

    Args:
        chunks: chunker.load_chunks()が返すチャンクのリスト
        db_path: DBファイルのパス

    Returns:
        実際に挿入した件数
    """
    if not chunks:
        return 0

    conn = _get_conn(db_path)
    inserted = 0
    try:
        cur = conn.cursor()
        to_insert, project_names = _build_to_insert(cur, chunks)

        if not to_insert:
            return 0

        saved_at = time.time()

        for chunk, pn in zip(to_insert, project_names):
            cur.execute(
                """
                INSERT INTO memories
                    (session_id, project, project_name, user_text, assistant_text, timestamp, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.get("session_id", ""),
                    chunk.get("project", ""),
                    pn,
                    chunk.get("user", ""),
                    chunk.get("assistant", ""),
                    chunk.get("timestamp", ""),
                    saved_at,
                ),
            )
            inserted += 1

        conn.commit()
    finally:
        conn.close()

    return inserted


def embed_pending(batch_size: int = 50, db_path: Path = DB_PATH) -> int:
    """
    embedding=NULLのレコードをベクトル化してDBを更新する。
    /sr コマンドや明示的な同期タイミングで呼ぶ遅延ベクトル化。

    Args:
        batch_size: 一度に処理する最大件数（デフォルト50）
        db_path:    DBファイルのパス

    Returns:
        ベクトル化した件数（0件なら処理不要）
    """
    from embedder import embed

    conn = _get_conn(db_path)
    updated = 0
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, project_name, user_text, assistant_text
            FROM memories
            WHERE embedding IS NULL
            LIMIT ?
            """,
            (batch_size,)
        )
        rows = cur.fetchall()

        if not rows:
            return 0

        # project_nameプレフィックスを付加してベクトル化（save_chunksと同じ形式）
        texts = [
            f"[プロジェクト: {row['project_name'] or ''}]\n"
            f"{row['user_text']}\n{row['assistant_text']}"
            for row in rows
        ]
        vectors = embed(texts)

        for row, vec in zip(rows, vectors):
            cur.execute(
                "UPDATE memories SET embedding = ? WHERE id = ?",
                (_vec_to_blob(vec), row["id"])
            )
            updated += 1

        conn.commit()
    finally:
        conn.close()

    return updated


def save_chunks(chunks: list[dict], db_path: Path = DB_PATH) -> int:
    """
    チャンクリストをmemoriesとmemories_ftsに保存する。
    同一session_idのチャンクが既に存在する場合は重複保存しない。
    各チャンクのproject_name + user_text + assistant_textを結合してベクトル化し保存する。

    Args:
        chunks: chunker.load_chunks()が返すチャンクのリスト
        db_path: DBファイルのパス

    Returns:
        実際に挿入した件数
    """
    if not chunks:
        return 0

    # embedderは重いので保存対象チャンクが確定してからロードする
    from embedder import embed

    conn = _get_conn(db_path)
    inserted = 0
    try:
        cur = conn.cursor()
        to_insert, project_names = _build_to_insert(cur, chunks)

        if not to_insert:
            return 0

        # 保存対象チャンクをまとめてベクトル化（バッチ処理で効率化）
        # project_nameをプレフィックスとして付加することで、プロジェクト横断の
        # ベクトル検索精度を向上させる（例: "fx-shield の Prisma移行" で正しいプロジェクトがヒット）
        texts = [
            f"[プロジェクト: {pn}]\n" + chunk.get("user", "") + "\n" + chunk.get("assistant", "")
            for chunk, pn in zip(to_insert, project_names)
        ]
        vectors = embed(texts)

        # 同一セッション内の全チャンクに同じ保存時刻を使う（時間減衰計算を一貫させる）
        saved_at = time.time()

        for chunk, pn, vec in zip(to_insert, project_names, vectors):
            cur.execute(
                """
                INSERT INTO memories
                    (session_id, project, project_name, user_text, assistant_text, timestamp, created_at, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.get("session_id", ""),
                    chunk.get("project", ""),
                    pn,
                    chunk.get("user", ""),
                    chunk.get("assistant", ""),
                    chunk.get("timestamp", ""),
                    saved_at,           # 同一セッションは同一タイムスタンプ
                    _vec_to_blob(vec),  # float32 BLOBとして保存
                ),
            )
            inserted += 1

        conn.commit()
    finally:
        conn.close()

    return inserted


def get_all(limit: int = 1000, db_path: Path = DB_PATH) -> list[dict]:
    """
    memoriesテーブルの全件を取得する（新しい順）。
    embeddingカラムは除外する（/recallの表示用途では不要なBLOBを転送しないため）。

    Args:
        limit: 最大取得件数（デフォルト1000）
        db_path: DBファイルのパス

    Returns:
        テキストフィールドを含むdictのリスト（embeddingは含まない）
    """
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, session_id, project, project_name,
                   user_text, assistant_text, timestamp, created_at
            FROM memories ORDER BY created_at DESC LIMIT ?
            """,
            (limit,)
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def vector_search(
    query_vec: list[float],
    limit: int = 20,
    db_path: Path = DB_PATH,
    exclude_project: str | None = None,
) -> list[dict]:
    """
    sqlite-vecを使ってコサイン類似度でベクトル検索する。

    Args:
        query_vec: embed_query()で生成したクエリベクトル
        limit: 最大取得件数（デフォルト20）
        db_path: DBファイルのパス
        exclude_project: このプロジェクトパスのレコードを除外する（例: 'G:/KagamiAlice'）

    Returns:
        類似度の高い順に並んだdictのリスト。
        各dictにはmemoriesの全フィールドと distance（小さいほど類似）が含まれる。
    """
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        # embeddingがNULLのレコードは除外する。必要に応じてプロジェクト除外も適用する
        if exclude_project:
            cur.execute(
                """
                SELECT
                    m.id,
                    m.session_id,
                    m.project,
                    m.project_name,
                    m.user_text,
                    m.assistant_text,
                    m.timestamp,
                    m.created_at,
                    vec_distance_cosine(m.embedding, ?) AS distance
                FROM memories m
                WHERE m.embedding IS NOT NULL
                  AND (m.project IS NULL OR m.project != ?)
                ORDER BY distance ASC
                LIMIT ?
                """,
                (_vec_to_blob(query_vec), exclude_project, limit),
            )
        else:
            cur.execute(
                """
                SELECT
                    m.id,
                    m.session_id,
                    m.project,
                    m.project_name,
                    m.user_text,
                    m.assistant_text,
                    m.timestamp,
                    m.created_at,
                    vec_distance_cosine(m.embedding, ?) AS distance
                FROM memories m
                WHERE m.embedding IS NOT NULL
                ORDER BY distance ASC
                LIMIT ?
                """,
                (_vec_to_blob(query_vec), limit),
            )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def fts_search(
    query: str,
    limit: int = 20,
    db_path: Path = DB_PATH,
    exclude_project: str | None = None,
) -> list[dict]:
    """
    FTS5でキーワード検索する。

    Args:
        query: 検索クエリ文字列
        limit: 最大取得件数（デフォルト20）
        db_path: DBファイルのパス
        exclude_project: このプロジェクトパスのレコードを除外する（例: 'G:/KagamiAlice'）

    Returns:
        マッチしたrowid・スコア・全フィールドを含むdictのリスト
        （スコアは負値で、絶対値が大きいほど関連性が高い）
    """
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        # FTS5の特殊構文（ハイフン・コロン等）を安全にするため、各トークンを
        # ダブルクォートで囲んでフレーズ検索として渡す。
        # トークン内のダブルクォートは "" に変換（FTS5フレーズ内のエスケープ規則）。
        escaped_query = " ".join(f'"{t.replace(chr(34), chr(34)+chr(34))}"' for t in query.split() if t)
        if not escaped_query:
            return []
        if exclude_project:
            cur.execute(
                """
                SELECT
                    m.id,
                    m.session_id,
                    m.project,
                    m.project_name,
                    m.user_text,
                    m.assistant_text,
                    m.timestamp,
                    m.created_at,
                    rank AS score
                FROM memories_fts
                JOIN memories m ON memories_fts.rowid = m.id
                WHERE memories_fts MATCH ?
                  AND (m.project IS NULL OR m.project != ?)
                ORDER BY rank
                LIMIT ?
                """,
                (escaped_query, exclude_project, limit),
            )
        else:
            cur.execute(
                """
                SELECT
                    m.id,
                    m.session_id,
                    m.project,
                    m.project_name,
                    m.user_text,
                    m.assistant_text,
                    m.timestamp,
                    m.created_at,
                    rank AS score
                FROM memories_fts
                JOIN memories m ON memories_fts.rowid = m.id
                WHERE memories_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (escaped_query, limit),
            )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
