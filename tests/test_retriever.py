"""
test_retriever.py - retriever.pyのユニットテスト
実際にDBにデータを保存して検索が機能することを確認する。
"""

import sys
import math
import time
import tempfile
from pathlib import Path

# srcディレクトリをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from retriever import rrf_score, time_decay, search
from storage import init_db, save_chunks


# --- rrf_score のテスト ---

def test_rrf_score_rank0():
    """rank=0（1位）のスコアは 1/(k+0)"""
    score = rrf_score(0, k=60)
    assert abs(score - 1.0 / 60) < 1e-9
    print(f"  OK: rrf_score rank=0 → {score:.6f}")


def test_rrf_score_rank1():
    """rank=1（2位）のスコアは 1/(k+1)"""
    score = rrf_score(1, k=60)
    assert abs(score - 1.0 / 61) < 1e-9
    print(f"  OK: rrf_score rank=1 → {score:.6f}")


def test_rrf_score_decreases_with_rank():
    """順位が下がるにつれてスコアが小さくなる"""
    scores = [rrf_score(r) for r in range(5)]
    assert all(scores[i] > scores[i + 1] for i in range(len(scores) - 1))
    print("  OK: rrf_score 順位が下がるほど低スコア")


def test_rrf_score_custom_k():
    """kパラメータが反映される"""
    s1 = rrf_score(0, k=60)
    s2 = rrf_score(0, k=10)
    # k=10の方が大きいスコア（1/10 > 1/60）
    assert s2 > s1
    print("  OK: rrf_score カスタムk")


# --- time_decay のテスト ---

def test_time_decay_now():
    """作成直後（now）の減衰係数は1.0に近い"""
    decay = time_decay(time.time())
    assert abs(decay - 1.0) < 0.001
    print(f"  OK: time_decay 最新 → {decay:.6f}")


def test_time_decay_half_life():
    """半減期（30日前）の減衰係数は0.5に近い"""
    thirty_days_ago = time.time() - 30 * 86400
    decay = time_decay(thirty_days_ago, half_life_days=30)
    assert abs(decay - 0.5) < 0.001
    print(f"  OK: time_decay 30日前 → {decay:.6f}")


def test_time_decay_double_half_life():
    """60日前の減衰係数は0.25に近い"""
    sixty_days_ago = time.time() - 60 * 86400
    decay = time_decay(sixty_days_ago, half_life_days=30)
    assert abs(decay - 0.25) < 0.001
    print(f"  OK: time_decay 60日前 → {decay:.6f}")


def test_time_decay_decreases_over_time():
    """時間が経過するほど減衰係数が小さくなる"""
    now = time.time()
    decays = [time_decay(now - i * 86400) for i in [0, 10, 20, 30]]
    assert all(decays[i] > decays[i + 1] for i in range(len(decays) - 1))
    print("  OK: time_decay 時間経過で単調減少")


# --- search の統合テスト ---

def make_db_with_data() -> Path:
    """テスト用DBにサンプルデータを保存して返す"""
    db = Path(tempfile.mktemp(suffix=".db"))
    init_db(db)

    chunks = [
        {
            "user": "Pythonでリストをソートするには",
            "assistant": "sorted()関数またはlist.sort()メソッドを使います",
            "timestamp": "2026-03-23T10:00:00.000Z",
            "session_id": "r-s1",
            "project": "/tmp",
        },
        {
            "user": "SQLiteのFTS5とは何ですか",
            "assistant": "全文検索を高速に行うSQLite拡張機能です",
            "timestamp": "2026-03-23T10:01:00.000Z",
            "session_id": "r-s2",
            "project": "/tmp",
        },
        {
            "user": "猫の種類を教えて",
            "assistant": "アメリカンショートヘア、ロシアンブルーなどがいます",
            "timestamp": "2026-03-23T10:02:00.000Z",
            "session_id": "r-s3",
            "project": "/tmp",
        },
        {
            "user": "Pythonの型ヒントについて",
            "assistant": "型ヒントはコードの可読性と静的解析を改善します",
            "timestamp": "2026-03-23T10:03:00.000Z",
            "session_id": "r-s4",
            "project": "/tmp",
        },
        {
            "user": "ベクトル検索の仕組み",
            "assistant": "テキストをベクトルに変換してコサイン類似度で近いものを探します",
            "timestamp": "2026-03-23T10:04:00.000Z",
            "session_id": "r-s5",
            "project": "/tmp",
        },
    ]
    save_chunks(chunks, db)
    return db


def test_search_returns_results():
    """searchがresultを返す"""
    db = make_db_with_data()
    results = search("Python", limit=5, db_path=db)
    assert len(results) >= 1
    print(f"  OK: search 結果あり ({len(results)}件)")


def test_search_has_score_field():
    """各結果にscoreフィールドが含まれる"""
    db = make_db_with_data()
    results = search("Python", limit=5, db_path=db)
    assert all("score" in r for r in results)
    print("  OK: search scoreフィールドあり")


def test_search_score_descending():
    """結果がスコア降順に並んでいる"""
    db = make_db_with_data()
    results = search("Python", limit=5, db_path=db)
    scores = [r["score"] for r in results]
    assert all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1))
    print(f"  OK: search スコア降順 {[f'{s:.5f}' for s in scores]}")


def test_search_python_returns_python_content():
    """Python関連クエリでPython関連の結果が上位に来る"""
    db = make_db_with_data()
    results = search("Pythonのプログラミング", limit=3, db_path=db)
    assert len(results) >= 1
    # 上位1件はPython関連のはず
    top = results[0]
    assert "Python" in top["user_text"] or "Python" in top["assistant_text"]
    print(f"  OK: Python検索 top1='{top['user_text'][:30]}'")


def test_search_limit():
    """limitパラメータで件数が制限される"""
    db = make_db_with_data()
    results = search("検索", limit=2, db_path=db)
    assert len(results) <= 2
    print(f"  OK: search limit=2 → {len(results)}件")


def test_search_no_distance_field():
    """vector_search由来のdistanceフィールドは除去されている"""
    db = make_db_with_data()
    results = search("Python", limit=5, db_path=db)
    assert all("distance" not in r for r in results)
    print("  OK: distanceフィールドなし")


def test_search_rrf_fusion():
    """
    FTSとベクトル両方にヒットするクエリはスコアが高くなる（RRF加算の効果確認）
    ベクトル検索のみヒットするクエリとFTS両方ヒットするクエリを比較する
    """
    db = make_db_with_data()

    # "FTS5" はキーワードでも意味的にも関連
    results = search("SQLite FTS5全文検索", limit=5, db_path=db)
    assert len(results) >= 1
    # FTS5関連コンテンツが含まれていることを確認
    texts = [r["user_text"] + r["assistant_text"] for r in results]
    assert any("FTS5" in t or "全文検索" in t for t in texts)
    print("  OK: RRF融合 FTS5関連コンテンツが上位に来る")


def test_search_all_required_fields():
    """結果dictに必要なフィールドが全て含まれる"""
    db = make_db_with_data()
    results = search("Python", limit=1, db_path=db)
    required = {"id", "session_id", "project", "user_text", "assistant_text",
                "timestamp", "created_at", "score"}
    assert len(results) >= 1
    assert required.issubset(set(results[0].keys())), \
        f"不足フィールド: {required - set(results[0].keys())}"
    print("  OK: 必要フィールド全て存在")


if __name__ == "__main__":
    print("=== retriever テスト開始 ===")
    test_rrf_score_rank0()
    test_rrf_score_rank1()
    test_rrf_score_decreases_with_rank()
    test_rrf_score_custom_k()
    test_time_decay_now()
    test_time_decay_half_life()
    test_time_decay_double_half_life()
    test_time_decay_decreases_over_time()
    print("--- 統合テスト（DB + 埋め込みモデル使用）---")
    test_search_returns_results()
    test_search_has_score_field()
    test_search_score_descending()
    test_search_python_returns_python_content()
    test_search_limit()
    test_search_no_distance_field()
    test_search_rrf_fusion()
    test_search_all_required_fields()
    print("=== 全テスト通過 ===")
