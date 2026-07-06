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

# ドキュメント埋め込み対象テキストの最大文字数。
# transcriptに巨大な貼り付け（スキルダンプ等）が混入すると1チャンクが50万字級になり、
# モデル上限の8192トークンまで膨らんだ系列はCPUエンコード1件で20秒超かかる
# （実測: 8192トークン1件=23秒）。先頭 _MAX_EMBED_CHARS 字に切り詰めて埋め込む。
# 全文はDBとFTS5に保存済みなので、キーワード検索（FTS5）の正しさは損なわれない。
# ベクトル側もQ&Aペアは冒頭にユーザー発言が来るため、先頭2000字で主題は十分捉えられる。
_MAX_EMBED_CHARS = 2000

# ドキュメントエンコード時のバッチサイズ。
# sentence-transformersはバッチ内を最長系列にパディングするため、長短混在の
# 大バッチでは短文まで最長系列ぶんの計算コストを払う
# （実測: 長文1件を含む18件を一括エンコード=58秒 → batch_size=4 なら19秒）。
# encode内部で長さ順ソートされるので、小バッチなら同程度の長さ同士が組になり
# パディングの無駄が最長4件分に限定される。
_ENCODE_BATCH_SIZE = 4


def get_embedder() -> SentenceTransformer:
    """
    SentenceTransformerモデルをシングルトンで返す。
    初回呼び出し時のみモデルをダウンロード・初期化する。

    ローカルキャッシュ済みなら local_files_only=True でロードし、HF Hub への
    オンライン確認（毎回の "unauthenticated requests to the HF Hub" 往復）を回避する。
    回線ぶんの遅延と、回線不調時のハングを防ぐ。未キャッシュ（初回など）は
    オンライン取得にフォールバックする。
    """
    global _embedder
    if _embedder is None:
        try:
            # キャッシュ済みモデルをオフラインでロード（HF Hub 往復をスキップ）
            _embedder = SentenceTransformer(MODEL_NAME, local_files_only=True)
        except Exception:
            # 未キャッシュ時はオンラインでダウンロード・ロード
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
    # 巨大テキスト（貼り付けダンプ等）によるCPUエンコードのハングを防ぐため、
    # 先頭 _MAX_EMBED_CHARS 字に切り詰め、小バッチでパディング増幅を抑える
    prefixed = ["文章: " + t[:_MAX_EMBED_CHARS] for t in texts]
    vectors = model.encode(
        prefixed,
        convert_to_numpy=True,
        normalize_embeddings=True,
        batch_size=_ENCODE_BATCH_SIZE,
    )
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
