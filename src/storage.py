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
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    # sqlite-vec拡張をロード（ベクトル検索に必要）
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
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
    embeddingカラムが未追加の場合はALTER TABLEでマイグレーションする。
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

        # embeddingカラムが存在しない場合はマイグレーションで追加する
        cur.execute("PRAGMA table_info(memories)")
        columns = {row["name"] for row in cur.fetchall()}
        if "embedding" not in columns:
            cur.execute("ALTER TABLE memories ADD COLUMN embedding BLOB")

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


def save_chunks(chunks: list[dict], db_path: Path = DB_PATH) -> int:
    """
    チャンクリストをmemoriesとmemories_ftsに保存する。
    同一session_idのチャンクが既に存在する場合は重複保存しない。
    各チャンクのuser_text + assistant_textを結合してベクトル化し保存する。

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

        # 保存対象チャンクを先に絞り込む（重複チェック）
        to_insert = []
        for chunk in chunks:
            session_id = chunk.get("session_id", "")
            cur.execute(
                "SELECT COUNT(*) FROM memories WHERE session_id = ?",
                (session_id,)
            )
            if cur.fetchone()[0] == 0:
                to_insert.append(chunk)

        if not to_insert:
            return 0

        # 保存対象チャンクをまとめてベクトル化（バッチ処理で効率化）
        texts = [
            chunk.get("user", "") + "\n" + chunk.get("assistant", "")
            for chunk in to_insert
        ]
        vectors = embed(texts)

        for chunk, vec in zip(to_insert, vectors):
            cur.execute(
                """
                INSERT INTO memories
                    (session_id, project, user_text, assistant_text, timestamp, created_at, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.get("session_id", ""),
                    chunk.get("project", ""),
                    chunk.get("user", ""),
                    chunk.get("assistant", ""),
                    chunk.get("timestamp", ""),
                    time.time(),        # Unix timestamp（時間減衰計算用）
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

    Args:
        limit: 最大取得件数（デフォルト1000）
        db_path: DBファイルのパス

    Returns:
        全フィールドを含むdictのリスト
    """
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def vector_search(query_vec: list[float], limit: int = 20, db_path: Path = DB_PATH) -> list[dict]:
    """
    sqlite-vecを使ってコサイン類似度でベクトル検索する。

    Args:
        query_vec: embed_query()で生成したクエリベクトル
        limit: 最大取得件数（デフォルト20）
        db_path: DBファイルのパス

    Returns:
        類似度の高い順に並んだdictのリスト。
        各dictにはmemoriesの全フィールドと distance（小さいほど類似）が含まれる。
    """
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        # embeddingがNULLのレコードは除外する
        cur.execute(
            """
            SELECT
                m.id,
                m.session_id,
                m.project,
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


def fts_search(query: str, limit: int = 20, db_path: Path = DB_PATH) -> list[dict]:
    """
    FTS5でキーワード検索する。

    Args:
        query: 検索クエリ文字列
        limit: 最大取得件数（デフォルト20）
        db_path: DBファイルのパス

    Returns:
        マッチしたrowid・スコア・全フィールドを含むdictのリスト
        （スコアは負値で、絶対値が大きいほど関連性が高い）
    """
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                m.id,
                m.session_id,
                m.project,
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
            (query, limit),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
