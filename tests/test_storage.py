"""
test_storage.py - storage.pyのユニットテスト
"""

import sys
import tempfile
import time
from pathlib import Path

# srcディレクトリをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from storage import init_db, save_chunks, get_all, fts_search


def make_db() -> Path:
    """テスト用の一時DBファイルパスを返す"""
    tmp = tempfile.mktemp(suffix=".db")
    return Path(tmp)


def make_chunk(user: str, assistant: str, session_id: str = "s1", project: str = "/tmp") -> dict:
    """テスト用チャンクを生成するヘルパー"""
    return {
        "user": user,
        "assistant": assistant,
        "timestamp": "2026-03-23T10:00:00.000Z",
        "session_id": session_id,
        "project": project,
    }


# --- init_db のテスト ---

def test_init_db_creates_tables():
    """init_dbでmemoriesとmemories_ftsテーブルが作成される"""
    import sqlite3
    db = make_db()
    init_db(db)

    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    conn.close()

    assert "memories" in tables
    assert "memories_fts" in tables
    print("  OK: テーブル作成確認")


def test_init_db_idempotent():
    """init_dbを2回呼んでもエラーにならない"""
    db = make_db()
    init_db(db)
    init_db(db)  # 2回目もエラーにならない
    print("  OK: init_db冪等性")


# --- save_chunks のテスト ---

def test_save_chunks_basic():
    """チャンクが正しく保存される"""
    db = make_db()
    init_db(db)

    chunks = [make_chunk("質問A", "回答A", session_id="session-001")]
    inserted = save_chunks(chunks, db)

    assert inserted == 1
    rows = get_all(db_path=db)
    assert len(rows) == 1
    assert rows[0]["user_text"] == "質問A"
    assert rows[0]["assistant_text"] == "回答A"
    assert rows[0]["session_id"] == "session-001"
    print("  OK: 基本保存")


def test_save_chunks_no_duplicate_session():
    """同一session_idのチャンクは重複保存されない"""
    db = make_db()
    init_db(db)

    chunk = make_chunk("質問", "回答", session_id="dup-session")
    inserted1 = save_chunks([chunk], db)
    inserted2 = save_chunks([chunk], db)  # 2回目は保存されない

    assert inserted1 == 1
    assert inserted2 == 0
    rows = get_all(db_path=db)
    assert len(rows) == 1
    print("  OK: 重複セッション防止")


def test_save_chunks_different_sessions():
    """異なるsession_idなら複数保存される"""
    db = make_db()
    init_db(db)

    chunks = [
        make_chunk("質問1", "回答1", session_id="s1"),
        make_chunk("質問2", "回答2", session_id="s2"),
        make_chunk("質問3", "回答3", session_id="s3"),
    ]
    inserted = save_chunks(chunks, db)

    assert inserted == 3
    rows = get_all(db_path=db)
    assert len(rows) == 3
    print("  OK: 複数セッション保存")


def test_save_chunks_empty():
    """空リストを渡してもエラーにならない"""
    db = make_db()
    init_db(db)

    inserted = save_chunks([], db)
    assert inserted == 0
    print("  OK: 空リスト")


# --- get_all のテスト ---

def test_get_all_limit():
    """limitパラメータで件数が制限される"""
    db = make_db()
    init_db(db)

    chunks = [make_chunk(f"Q{i}", f"A{i}", session_id=f"s{i}") for i in range(10)]
    save_chunks(chunks, db)

    rows = get_all(limit=3, db_path=db)
    assert len(rows) == 3
    print("  OK: get_all limit")


def test_get_all_order():
    """get_allは新しい順（created_at DESC）で返る"""
    db = make_db()
    init_db(db)

    # 時間差をつけて保存
    save_chunks([make_chunk("古い", "回答", session_id="old")], db)
    time.sleep(0.01)
    save_chunks([make_chunk("新しい", "回答", session_id="new")], db)

    rows = get_all(db_path=db)
    assert rows[0]["user_text"] == "新しい"
    assert rows[1]["user_text"] == "古い"
    print("  OK: get_all 新しい順")


# --- fts_search のテスト ---

def test_fts_search_hit():
    """FTS5でキーワード検索がヒットする"""
    db = make_db()
    init_db(db)

    chunks = [
        make_chunk("Pythonの使い方", "Pythonは汎用言語です", session_id="s1"),
        make_chunk("JavaScriptとは", "JSはWeb言語です", session_id="s2"),
    ]
    save_chunks(chunks, db)

    results = fts_search("Python", db_path=db)
    assert len(results) == 1
    assert results[0]["user_text"] == "Pythonの使い方"
    print("  OK: FTS検索ヒット")


def test_fts_search_no_hit():
    """マッチしない場合は空リストを返す"""
    db = make_db()
    init_db(db)

    save_chunks([make_chunk("Pythonの話", "回答", session_id="s1")], db)

    results = fts_search("Rust", db_path=db)
    assert results == []
    print("  OK: FTS検索ノーヒット")


def test_fts_search_has_score():
    """検索結果にscoreフィールドが含まれる"""
    db = make_db()
    init_db(db)

    save_chunks([make_chunk("SQLiteの検索機能", "FTS5が使えます", session_id="s1")], db)

    results = fts_search("SQLite", db_path=db)
    assert len(results) == 1
    assert "score" in results[0]
    print("  OK: FTS スコアフィールド確認")


def test_fts_search_assistant_text():
    """assistant_textのキーワードでも検索できる"""
    db = make_db()
    init_db(db)

    save_chunks([make_chunk("何かについて", "ベクトル検索が便利です", session_id="s1")], db)

    results = fts_search("ベクトル", db_path=db)
    assert len(results) == 1
    assert results[0]["assistant_text"] == "ベクトル検索が便利です"
    print("  OK: FTS assistant_text検索")


if __name__ == "__main__":
    print("=== storage テスト開始 ===")
    test_init_db_creates_tables()
    test_init_db_idempotent()
    test_save_chunks_basic()
    test_save_chunks_no_duplicate_session()
    test_save_chunks_different_sessions()
    test_save_chunks_empty()
    test_get_all_limit()
    test_get_all_order()
    test_fts_search_hit()
    test_fts_search_no_hit()
    test_fts_search_has_score()
    test_fts_search_assistant_text()
    print("=== 全テスト通過 ===")
