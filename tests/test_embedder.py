"""
test_embedder.py - embedder.pyのユニットテスト
モデルの初回ダウンロードが発生する場合があるため時間がかかることがある。
"""

import sys
import math
import tempfile
from pathlib import Path

# srcディレクトリをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from embedder import get_embedder, embed, embed_one, embed_query


# --- get_embedder のテスト ---

def test_get_embedder_singleton():
    """get_embedderは同一インスタンスを返す（シングルトン）"""
    m1 = get_embedder()
    m2 = get_embedder()
    assert m1 is m2
    print("  OK: シングルトン確認")


# --- embed のテスト ---

def test_embed_returns_list_of_vectors():
    """embedはベクトルのリストを返す"""
    result = embed(["テスト文章"])
    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], list)
    assert len(result[0]) > 0
    assert isinstance(result[0][0], float)
    print(f"  OK: embed 出力次元数={len(result[0])}")


def test_embed_multiple_texts():
    """複数テキストを渡すと同数のベクトルが返る"""
    texts = ["文章A", "文章B", "文章C"]
    result = embed(texts)
    assert len(result) == 3
    # すべて同じ次元数であることを確認
    dims = {len(v) for v in result}
    assert len(dims) == 1
    print("  OK: embed 複数テキスト")


def test_embed_vector_normalized():
    """ベクトルがほぼL2正規化されている（コサイン類似度前提）"""
    vec = embed_one("正規化テスト")
    norm = math.sqrt(sum(x * x for x in vec))
    # 正規化済みならノルムが1.0に近い（許容誤差0.01）
    assert abs(norm - 1.0) < 0.01, f"norm={norm:.4f}"
    print(f"  OK: ベクトル正規化確認 (norm={norm:.4f})")


# --- embed_one のテスト ---

def test_embed_one_returns_single_vector():
    """embed_oneは1件のベクトルを返す"""
    result = embed_one("単一テキスト")
    assert isinstance(result, list)
    assert isinstance(result[0], float)
    print("  OK: embed_one 単一ベクトル")


def test_embed_one_consistent_with_embed():
    """embed_one(t) と embed([t])[0] は同じベクトルを返す"""
    text = "一致確認テスト"
    v1 = embed_one(text)
    v2 = embed([text])[0]
    # 全要素が一致することを確認（浮動小数点誤差を許容）
    assert all(abs(a - b) < 1e-6 for a, b in zip(v1, v2))
    print("  OK: embed_one と embed の一致")


# --- embed_query のテスト ---

def test_embed_query_returns_vector():
    """embed_queryはベクトルを返す"""
    result = embed_query("検索クエリ")
    assert isinstance(result, list)
    assert isinstance(result[0], float)
    print("  OK: embed_query ベクトル返却")


def test_embed_query_differs_from_embed_one():
    """
    クエリベクトルとドキュメントベクトルは異なる
    （プレフィックスが違うため完全一致にはならない）
    """
    text = "Pythonの使い方"
    doc_vec = embed_one(text)
    qry_vec = embed_query(text)
    # 全要素が完全一致することはない
    assert doc_vec != qry_vec
    print("  OK: クエリベクトルとドキュメントベクトルが異なる")


def test_embed_query_similarity():
    """
    意味的に近いクエリとドキュメントは類似度が高い
    （コサイン類似度 > 0.7 を期待）
    """
    doc = embed_one("Pythonはプログラミング言語です")
    qry = embed_query("Pythonとは何ですか")

    # コサイン類似度を計算
    dot = sum(a * b for a, b in zip(doc, qry))
    norm_d = math.sqrt(sum(x * x for x in doc))
    norm_q = math.sqrt(sum(x * x for x in qry))
    cosine = dot / (norm_d * norm_q)

    assert cosine > 0.7, f"類似度が低い: {cosine:.4f}"
    print(f"  OK: クエリ/ドキュメント類似度={cosine:.4f}")


# --- storage との統合テスト ---

def test_vector_search_integration():
    """
    save_chunks → vector_search の一連の流れをテストする
    """
    import tempfile
    from storage import init_db, save_chunks, vector_search
    from embedder import embed_query

    db = Path(tempfile.mktemp(suffix=".db"))
    init_db(db)

    chunks = [
        {
            "user": "Pythonとは何ですか",
            "assistant": "Pythonは汎用プログラミング言語です",
            "timestamp": "2026-03-23T10:00:00.000Z",
            "session_id": "vs-s1",
            "project": "/tmp",
        },
        {
            "user": "猫の飼い方",
            "assistant": "猫には水と食事と愛情が必要です",
            "timestamp": "2026-03-23T10:01:00.000Z",
            "session_id": "vs-s2",
            "project": "/tmp",
        },
    ]
    save_chunks(chunks, db)

    # Pythonに関連するクエリで検索
    qvec = embed_query("Pythonについて教えて")
    results = vector_search(qvec, limit=2, db_path=db)

    assert len(results) >= 1
    # 最も近いのはPythonの話題のはず
    assert "Python" in results[0]["user_text"] or "Python" in results[0]["assistant_text"]
    print(f"  OK: ベクトル検索統合 top1='{results[0]['user_text'][:20]}...'")


if __name__ == "__main__":
    print("=== embedder テスト開始 ===")
    print("(初回はモデルダウンロードが発生します)")
    test_get_embedder_singleton()
    test_embed_returns_list_of_vectors()
    test_embed_multiple_texts()
    test_embed_vector_normalized()
    test_embed_one_returns_single_vector()
    test_embed_one_consistent_with_embed()
    test_embed_query_returns_vector()
    test_embed_query_differs_from_embed_one()
    test_embed_query_similarity()
    test_vector_search_integration()
    print("=== 全テスト通過 ===")
