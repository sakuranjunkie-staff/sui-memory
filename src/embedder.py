"""
embedder.py - テキストをRuri v3-310mでベクトル化するモジュール

Ruri v3はクエリとドキュメントでプレフィックスを使い分ける:
  - ドキュメント（保存時）: "文章: " + テキスト
  - クエリ（検索時）  : "クエリ: " + テキスト
"""

from sentence_transformers import SentenceTransformer

# 使用モデル名
MODEL_NAME = "cl-nagoya/ruri-v3-310m"

# シングルトンインスタンス（初回ロード後に使い回す）
_embedder: SentenceTransformer | None = None


def get_embedder() -> SentenceTransformer:
    """
    SentenceTransformerモデルをシングルトンで返す。
    初回呼び出し時のみモデルをダウンロード・初期化する。
    """
    global _embedder
    if _embedder is None:
        # 初回のみダウンロード・ロード（以降はキャッシュから読む）
        _embedder = SentenceTransformer(MODEL_NAME)
    return _embedder


def embed(texts: list[str]) -> list[list[float]]:
    """
    テキストリストをドキュメント用ベクトルに変換して返す。
    保存時（ドキュメント側）に使用する。
    "文章: " プレフィックスを付与してエンコードする。

    Args:
        texts: ベクトル化するテキストのリスト

    Returns:
        各テキストのベクトル（float のリスト）のリスト
    """
    model = get_embedder()
    # ドキュメント用プレフィックスを付与してエンコード
    # normalize_embeddings=True でL2正規化済みベクトルを返す（コサイン類似度計算に必要）
    prefixed = ["文章: " + t for t in texts]
    vectors = model.encode(prefixed, convert_to_numpy=True, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


def embed_one(text: str) -> list[float]:
    """
    1件のテキストをドキュメント用ベクトルに変換して返す。
    embed() の単一テキスト版。

    Args:
        text: ベクトル化するテキスト

    Returns:
        ベクトル（float のリスト）
    """
    return embed([text])[0]


def embed_query(text: str) -> list[float]:
    """
    クエリ用ベクトルを返す。
    検索時（クエリ側）に使用する。
    "クエリ: " プレフィックスを付与してエンコードする。

    Args:
        text: 検索クエリテキスト

    Returns:
        クエリのベクトル（float のリスト）
    """
    model = get_embedder()
    # クエリ用プレフィックスを付与してエンコード（正規化済み）
    vector = model.encode("クエリ: " + text, convert_to_numpy=True, normalize_embeddings=True)
    return vector.tolist()
