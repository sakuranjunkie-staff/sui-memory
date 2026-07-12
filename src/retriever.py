"""
retriever.py - FTS5キーワード検索とベクトル検索をRRFで統合し、
               時間減衰を適用して最終スコアを返すモジュール
"""

import time
from pathlib import Path

from storage import DB_PATH, fts_search, vector_search
from embedder import embed_query


def rrf_score(rank: int, k: int = 60) -> float:
    """
    RRF（Reciprocal Rank Fusion）スコアを計算する。
    公式: 1 / (k + rank)
    rankは0始まりを想定（1位=0、2位=1、...）。

    Args:
        rank: 検索結果内での順位（0始まり）
        k: 平滑化定数（デフォルト60）

    Returns:
        RRFスコア（高いほど上位）
    """
    return 1.0 / (k + rank)


def time_decay(created_at: float, half_life_days: int = 30) -> float:
    """
    時間減衰係数を計算する。
    公式: 0.5 ** ((now - created_at) / (half_life_days * 86400))
    - 最新なら 1.0
    - 半減期（デフォルト30日）前なら 0.5
    - 2倍の半減期（60日）前なら 0.25

    Args:
        created_at: レコードのUnix timestamp
        half_life_days: 半減期（日数、デフォルト30日）

    Returns:
        減衰係数（0.0〜1.0）
    """
    now = time.time()
    elapsed = now - created_at
    half_life_seconds = half_life_days * 86400
    return 0.5 ** (elapsed / half_life_seconds)


def search(
    query: str,
    limit: int = 5,
    db_path: Path = DB_PATH,
    exclude_project: str | None = None,
    half_life_days: int = 180,
) -> list[dict]:
    """
    FTS5キーワード検索とベクトル検索をRRFで統合し、
    時間減衰を適用した最終スコアで上位limit件を返す。

    処理手順:
    1. fts_search でキーワード検索（最大20件）
    2. embed_query でクエリをベクトル化
    3. vector_search でベクトル検索（最大20件）
    4. 両結果をRRFでスコア統合（同一idはスコアを加算）
    5. 各結果に時間減衰係数を掛ける
    6. 最終スコア降順でlimit件返す

    Args:
        query: 検索クエリ文字列
        limit: 返す件数（デフォルト5）
        db_path: DBファイルのパス
        half_life_days: 時間減衰の半減期（日数）。明示検索は「古い会話を思い出す」
                        道具なのでデフォルト180日と緩く、古い完全一致が直近の
                        ノイズに沈まないようにする（30日だと90日前の完全一致が
                        ×0.125に沈み、道具の目的と逆向きに働いていた）

    Returns:
        scoreフィールドを含むdictのリスト（スコア降順）
    """
    # --- Step 1: FTS5キーワード検索 ---
    fts_results = fts_search(query, limit=20, db_path=db_path, exclude_project=exclude_project)

    # --- Step 2 & 3: ベクトル検索 ---
    query_vec = embed_query(query)
    vec_results = vector_search(query_vec, limit=20, db_path=db_path, exclude_project=exclude_project)

    # --- Step 4: RRFスコア統合 ---
    # id → {レコード本体, rrf合計スコア} のマップ
    merged: dict[int, dict] = {}

    # FTS5結果のRRFスコアを加算
    for rank, row in enumerate(fts_results):
        row_id = row["id"]
        if row_id not in merged:
            merged[row_id] = dict(row)
            merged[row_id]["_rrf"] = 0.0
        merged[row_id]["_rrf"] += rrf_score(rank)

    # ベクトル検索結果のRRFスコアを加算（同一idの場合は累積）
    for rank, row in enumerate(vec_results):
        row_id = row["id"]
        if row_id not in merged:
            merged[row_id] = dict(row)
            merged[row_id]["_rrf"] = 0.0
        merged[row_id]["_rrf"] += rrf_score(rank)

    # --- Step 5: 時間減衰を掛けて最終スコアを計算 ---
    for row_id, record in merged.items():
        decay = time_decay(record["created_at"], half_life_days=half_life_days)
        record["score"] = record["_rrf"] * decay

    # 内部計算用フィールドを削除
    for record in merged.values():
        record.pop("_rrf", None)
        # vector_searchのdistanceフィールドも除去（scoreに統一）
        record.pop("distance", None)

    # --- Step 6: 最終スコア降順でlimit件返す ---
    sorted_results = sorted(merged.values(), key=lambda r: r["score"], reverse=True)
    return sorted_results[:limit]


def search_by_timerange(
    query: str,
    days: int,
    limit: int = 5,
    db_path: Path = DB_PATH,
    exclude_project: str | None = None,
    until_days: float = 0,
    half_life_days: int = 180,
) -> list[dict]:
    """
    指定した日数以内のメモリのみを対象にしてハイブリッド検索する。
    until_days を指定すると「〇日前から△日前まで」の窓検索になる
    （例: days=133, until_days=103 → 133日前〜103日前＝3月ごろの記憶だけ）。

    処理手順:
    1. fts_search・vector_search で候補を広めに取得（各20件）
    2. created_at が cutoff 以降のものだけに絞り込む（時間フィルター）
    3. 残った候補に対してRRFスコア統合・時間減衰を適用する
    4. 最終スコア降順でlimit件返す

    Args:
        query: 検索クエリ文字列
        days: 直近何日以内を対象にするか（例: 7 → 直近7日以内）＝窓の古い側
        limit: 返す件数（デフォルト5）
        db_path: DBファイルのパス
        until_days: 窓の新しい側（何日前まで）。0なら現在まで（従来動作）
        half_life_days: 時間減衰の半減期（日数）。search()と同じ理由でデフォルト180日

    Returns:
        scoreフィールドを含むdictのリスト（スコア降順）
    """
    # 時間フィルターのカットオフ（Unix timestamp）
    now = time.time()
    cutoff = now - days * 86400
    # 窓の新しい側（0なら制限なし＝現在まで）
    until_ts = (now - until_days * 86400) if until_days > 0 else None

    # --- Step 1 & 2: 時間フィルタをSQL側で適用してFTS5・ベクトル検索 ---
    # 以前は全期間のtop-20を取ってからPython側でカットオフを適用していたが、
    # それだと期間外の強マッチが候補枠を食い潰し、期間内の関連記憶が候補にすら
    # 入らない（候補の飢餓）。期間を広げても同じ20件を絞り直すだけで候補が増えず、
    # NORESULTや薄い結果の常態化を招いていた。必ずsinceでSQL側から絞る。
    fts_results = fts_search(query, limit=20, db_path=db_path, exclude_project=exclude_project, since=cutoff, until=until_ts)
    query_vec = embed_query(query)
    vec_results = vector_search(query_vec, limit=20, db_path=db_path, exclude_project=exclude_project, since=cutoff, until=until_ts)

    # --- Step 3: RRFスコア統合 ---
    merged: dict[int, dict] = {}

    # FTS5結果のRRFスコアを加算
    for rank, row in enumerate(fts_results):
        row_id = row["id"]
        if row_id not in merged:
            merged[row_id] = dict(row)
            merged[row_id]["_rrf"] = 0.0
        merged[row_id]["_rrf"] += rrf_score(rank)

    # ベクトル検索結果のRRFスコアを加算（同一idは累積）
    for rank, row in enumerate(vec_results):
        row_id = row["id"]
        if row_id not in merged:
            merged[row_id] = dict(row)
            merged[row_id]["_rrf"] = 0.0
        merged[row_id]["_rrf"] += rrf_score(rank)

    # 時間減衰を掛けて最終スコアを計算
    for record in merged.values():
        decay = time_decay(record["created_at"], half_life_days=half_life_days)
        record["score"] = record["_rrf"] * decay

    # 内部計算用フィールドを除去
    for record in merged.values():
        record.pop("_rrf", None)
        record.pop("distance", None)

    # --- Step 4: 最終スコア降順でlimit件返す ---
    sorted_results = sorted(merged.values(), key=lambda r: r["score"], reverse=True)
    return sorted_results[:limit]


def search_recent(
    query: str,
    limit: int = 5,
    db_path: Path = DB_PATH,
    exclude_project: str | None = None,
) -> list[dict]:
    """
    直近7日以内のメモリを対象にしてハイブリッド検索するショートカット。

    search_by_timerange(query, days=7, limit=limit) の呼び出しと同等。

    Args:
        query: 検索クエリ文字列
        limit: 返す件数（デフォルト5）
        db_path: DBファイルのパス
        exclude_project: このプロジェクトパスのレコードを除外する（例: 'G:/KagamiAlice'）

    Returns:
        scoreフィールドを含むdictのリスト（スコア降順）
    """
    return search_by_timerange(query, days=7, limit=limit, db_path=db_path, exclude_project=exclude_project)
