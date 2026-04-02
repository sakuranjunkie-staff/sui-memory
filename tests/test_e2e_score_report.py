"""
test_e2e_score_report.py
エンドツーエンドのスコア検証テスト。
仮想的なQ&Aデータを投入し、RRF/時間減衰の計算結果を数値レポートとして出力する。
「仮想数値→保存→検索→スコアレポート出力→計算検証」の一連フローを確認する。

実行: uv run --project C:/Users/bukol/Documents/sui-memory python tests/test_e2e_score_report.py
"""
import sys, io, math, time, types, tempfile, sqlite3
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ---------------------------------------------------------------------------
# embedderモック: 各チャンクに意味的に異なるベクトルを割り当てる
# ---------------------------------------------------------------------------
DIM = 16  # テスト用次元数

def _unit(v: list[float]) -> list[float]:
    """L2正規化"""
    norm = math.sqrt(sum(x*x for x in v))
    return [x / norm for x in v] if norm > 0 else v

# テーマごとに直交に近いベクトルを定義（コサイン類似度で差が出るように）
THEME_VECS = {
    "python":    _unit([1.0, 0.8, 0.1, 0.0] + [0.0] * 12),
    "sqlite":    _unit([0.1, 0.0, 1.0, 0.9] + [0.0] * 12),
    "network":   _unit([0.0, 0.1, 0.0, 0.0] + [1.0, 0.8] + [0.0] * 10),
    "cat":       _unit([0.0] * 6 + [1.0, 0.9] + [0.0] * 8),
    "generic":   _unit([0.5] * 16),
}

# 各チャンクテキストとテーマの対応
CONTENT_THEME_MAP: dict[str, str] = {}

def mock_embed(texts: list[str]) -> list[list[float]]:
    result = []
    for t in texts:
        theme = "generic"
        for key in THEME_VECS:
            if key in t.lower():
                theme = key
                break
        CONTENT_THEME_MAP[t] = theme
        result.append(THEME_VECS[theme])
    return result

def mock_embed_query(text: str) -> list[float]:
    theme = "generic"
    for key in THEME_VECS:
        if key in text.lower():
            theme = key
            break
    return THEME_VECS[theme]

mod = types.ModuleType("embedder")
mod.embed = mock_embed
mod.embed_query = mock_embed_query
sys.modules["embedder"] = mod

from storage import init_db, save_chunks, get_all, fts_search, vector_search
from retriever import rrf_score, time_decay, search, search_by_timerange

PASS = 0
FAIL = 0

def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}" + (f" — {detail}" if detail else ""))
        FAIL += 1

def hr(char="─", n=60):
    print(char * n)

TMP_DB = Path(tempfile.mktemp(suffix=".db"))
init_db(TMP_DB)

# ---------------------------------------------------------------------------
# データ投入: テーマ別チャンク（fresh + aged）
# ---------------------------------------------------------------------------
now = time.time()

FRESH_CHUNKS = [
    # Pythonテーマ
    {"session_id": "s-py1", "project": "C:/proj/myapp",
     "user": "Pythonのリスト内包表記の書き方を教えて",
     "assistant": "Pythonではlist comprehensionを [x for x in iterable] の形で書きます",
     "timestamp": "2026-04-02T09:00:00"},
    {"session_id": "s-py2", "project": "C:/proj/myapp",
     "user": "Pythonで辞書をソートするには",
     "assistant": "sorted(d.items(), key=lambda x: x[1]) でPython辞書をソートできます",
     "timestamp": "2026-04-02T09:01:00"},
    # SQLiteテーマ
    {"session_id": "s-sq1", "project": "C:/proj/db-tool",
     "user": "SQLiteのWALモードとは何ですか",
     "assistant": "Write-Ahead Loggingです。SQLiteで並行read/writeを可能にするジャーナルモードです",
     "timestamp": "2026-04-02T09:02:00"},
    {"session_id": "s-sq2", "project": "C:/proj/db-tool",
     "user": "SQLiteのFTS5とtrigramトークナイザーについて",
     "assistant": "FTS5はSQLiteの全文検索モジュールです。trigramは3文字単位でインデックスを作ります",
     "timestamp": "2026-04-02T09:03:00"},
    # ネットワークテーマ（モックembedderの"network"キーワード検出に合わせてnetworkを含める）
    {"session_id": "s-nw1", "project": "C:/proj/net",
     "user": "HTTPとHTTPS のnetwork通信の違いを教えて",
     "assistant": "HTTPSはnetwork通信をSSL/TLSで暗号化します。HTTPは平文です",
     "timestamp": "2026-04-02T09:04:00"},
    # 無関係テーマ
    {"session_id": "s-ct1", "project": "C:/proj/misc",
     "user": "猫の種類を教えて",
     "assistant": "アメリカンショートヘア、ノルウェージャンフォレストキャット、猫好きには人気です",
     "timestamp": "2026-04-02T09:05:00"},
]

# 古いデータ（30日前）: 別のPythonチャンク
OLD_CHUNKS = [
    {"session_id": "s-old1", "project": "C:/proj/old",
     "user": "PythonとRubyの違いは何ですか（古い話題）",
     "assistant": "どちらもスクリプト言語ですが、PythonはAI/DataScience用途が多いです",
     "timestamp": "2026-03-02T10:00:00"},
]

# save_chunksで投入（即時ベクトル化）
n_fresh = save_chunks(FRESH_CHUNKS, TMP_DB)
# 古いデータを保存後、created_atを30日前に直接更新
n_old = save_chunks(OLD_CHUNKS, TMP_DB)

# created_atを30日前に書き換え（search_by_timerangeのフィルターテスト用）
conn_fix = sqlite3.connect(str(TMP_DB))
conn_fix.execute(
    "UPDATE memories SET created_at = ? WHERE session_id = 's-old1'",
    (now - 30 * 86400,)
)
conn_fix.commit()
conn_fix.close()

print(f"\n{'='*60}")
print(f"  データ投入完了: fresh={n_fresh}件, old={n_old}件")
print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Section 1: RRFスコアの数値検証
# ---------------------------------------------------------------------------
print("\n\n▶ Section 1: RRF スコア数値検証")
hr()

rrf_values = [(r, rrf_score(r, k=60)) for r in range(6)]
print("  rank | RRF score    | 1/(60+rank)")
print("  -----|--------------|-------------")
for rank, score in rrf_values:
    expected = 1.0 / (60 + rank)
    match = abs(score - expected) < 1e-12
    print(f"  {rank:4d} | {score:.10f} | {expected:.10f}  {'✓' if match else '✗'}")
    check(f"rrf_score(rank={rank}) == 1/(60+{rank})", match, f"差: {abs(score-expected):.2e}")

# RRF加算: 同じIDがFTSもベクトルも1位なら 1/60 + 1/60 = 1/30
rrf_both_top = rrf_score(0) + rrf_score(0)
expected_both = 2.0 / 60
check("RRF加算: 両1位 = 2/60", abs(rrf_both_top - expected_both) < 1e-12)
print(f"  両方1位の合算スコア: {rrf_both_top:.8f} (= {expected_both:.8f})")


# ---------------------------------------------------------------------------
# Section 2: 時間減衰の数値検証
# ---------------------------------------------------------------------------
print("\n\n▶ Section 2: 時間減衰 数値検証")
hr()

decay_cases = [
    ("今 (0日)", 0),
    ("1週間前 (7日)", 7),
    ("半減期 (30日)", 30),
    ("60日前", 60),
    ("90日前", 90),
    ("1年前 (365日)", 365),
]

print("  期間         | decay係数  | 理論値")
print("  -------------|------------|--------")
for label, days in decay_cases:
    ts = now - days * 86400
    decay = time_decay(ts, half_life_days=30)
    expected = 0.5 ** (days / 30)
    match = abs(decay - expected) < 0.001
    print(f"  {label:14s} | {decay:.8f} | {expected:.8f}  {'✓' if match else '✗'}")
    check(f"time_decay({days}日) ≈ 0.5^({days}/30)", match, f"差: {abs(decay-expected):.6f}")

# スコアへの影響量: RRF 1位 × 各減衰係数
print("\n  [参考] RRF 1位スコア × time_decay の組み合わせ:")
for label, days in decay_cases:
    ts = now - days * 86400
    decay = time_decay(ts, half_life_days=30)
    final = rrf_score(0) * decay
    print(f"    {label:14s}: score = {rrf_score(0):.6f} × {decay:.6f} = {final:.8f}")


# ---------------------------------------------------------------------------
# Section 3: FTS検索の動作確認（数値出力付き）
# ---------------------------------------------------------------------------
print("\n\n▶ Section 3: FTS検索 スコアレポート")
hr()

fts_queries = [
    ("Python", "Pythonテーマがヒットするか"),
    ("SQLite WAL", "SQLiteテーマが複数ヒットするか"),
    ("FTS5 trigram", "FTS5関連がヒットするか"),
    ("存在しないXYZWORD", "0件が正しく返るか"),
]

for query, note in fts_queries:
    results = fts_search(query, limit=5, db_path=TMP_DB)
    print(f"\n  クエリ: '{query}' ({note})")
    if not results:
        print("    → 0件")
    else:
        for i, r in enumerate(results):
            pn = r.get("project_name", "")
            u = r["user_text"][:50].replace("\n", " ")
            score = r.get("score", r.get("rank", "N/A"))
            print(f"    {i+1}. [{pn}] {u}")

check("FTS: Pythonでヒット", len(fts_search("Python", limit=5, db_path=TMP_DB)) >= 1)
check("FTS: 'SQLite WAL'でヒット", len(fts_search("SQLite WAL", limit=5, db_path=TMP_DB)) >= 1)
check("FTS: 存在しないクエリで0件", len(fts_search("XYZWORD12345", limit=5, db_path=TMP_DB)) == 0)


# ---------------------------------------------------------------------------
# Section 4: ベクトル検索の動作確認
# ---------------------------------------------------------------------------
print("\n\n▶ Section 4: ベクトル検索 distanceレポート")
hr()

vec_queries = [
    ("python", THEME_VECS["python"]),
    ("sqlite", THEME_VECS["sqlite"]),
    ("network", THEME_VECS["network"]),
]

for name, qvec in vec_queries:
    results = vector_search(qvec, limit=3, db_path=TMP_DB)
    print(f"\n  クエリベクトル: {name}テーマ")
    for i, r in enumerate(results):
        u = r["user_text"][:50].replace("\n", " ")
        d = r["distance"]
        print(f"    {i+1}. dist={d:.6f}  {u}")
    if results:
        # 1位の distanceが0に近いほど同テーマとして検索精度良好
        check(f"ベクトル検索 '{name}': top1 distance < 0.5",
              results[0]["distance"] < 0.5,
              f"実際: {results[0]['distance']:.6f}")


# ---------------------------------------------------------------------------
# Section 5: ハイブリッド検索 (search) のスコアレポート
# ---------------------------------------------------------------------------
print("\n\n▶ Section 5: ハイブリッド検索 スコアレポート（RRF + 時間減衰）")
hr()

search_cases = [
    ("Python リスト", "Pythonチャンクが上位に来るはず"),
    ("SQLiteのWAL", "SQLiteチャンクが上位に来るはず"),
    ("猫の種類", "catチャンクが上位に来るはず"),
]

for query, note in search_cases:
    print(f"\n  クエリ: '{query}' ({note})")
    results = search(query, limit=5, db_path=TMP_DB)
    print(f"  {'順位':<4} {'score':>12} {'FTS+Vec?':>10}  ユーザーテキスト")
    print("  " + "-" * 60)
    for i, r in enumerate(results):
        u = r["user_text"][:45].replace("\n", " ")
        s = r["score"]
        print(f"  {i+1:<4} {s:12.8f}            {u}")

    check(f"search '{query}': 結果が返る", len(results) >= 1)
    if len(results) >= 2:
        check(f"search '{query}': スコア降順",
              all(results[j]["score"] >= results[j+1]["score"] for j in range(len(results)-1)))
    check(f"search '{query}': distanceフィールドなし",
          all("distance" not in r for r in results))
    check(f"search '{query}': scoreフィールドあり",
          all("score" in r for r in results))


# ---------------------------------------------------------------------------
# Section 6: search_by_timerange の時間フィルター検証
# ---------------------------------------------------------------------------
print("\n\n▶ Section 6: search_by_timerange 時間フィルター検証")
hr()

# days=7: 直近7日のみ → 30日前の古いデータは除外されるはず
results_7d = search_by_timerange("Python", days=7, limit=10, db_path=TMP_DB)
results_60d = search_by_timerange("Python", days=60, limit=10, db_path=TMP_DB)

old_ids = {
    r["session_id"] for r in get_all(limit=100, db_path=TMP_DB)
    if r["session_id"] == "s-old1"
}

print(f"\n  直近7日での 'Python' 検索: {len(results_7d)}件")
for r in results_7d:
    print(f"    [{r['session_id']}] score={r['score']:.8f} | {r['user_text'][:45]}")

print(f"\n  直近60日での 'Python' 検索: {len(results_60d)}件")
for r in results_60d:
    flag = "← 古い" if r["session_id"] == "s-old1" else ""
    print(f"    [{r['session_id']}] score={r['score']:.8f} | {r['user_text'][:45]} {flag}")

# 7日フィルターでは古いデータが含まれない
old_in_7d = any(r["session_id"] == "s-old1" for r in results_7d)
check("7日フィルター: 30日前データが除外される", not old_in_7d,
      "s-old1が含まれている" if old_in_7d else "")

# 60日フィルターでは古いデータが含まれる
old_in_60d = any(r["session_id"] == "s-old1" for r in results_60d)
check("60日フィルター: 30日前データが含まれる", old_in_60d,
      "s-old1が含まれていない" if not old_in_60d else "")

# 時間減衰により古いデータのスコアが新しいデータより低い
if old_in_60d and len(results_60d) >= 2:
    fresh_scores = [r["score"] for r in results_60d if r["session_id"] != "s-old1"]
    old_score = next(r["score"] for r in results_60d if r["session_id"] == "s-old1")
    avg_fresh = sum(fresh_scores) / len(fresh_scores) if fresh_scores else 0
    print(f"\n  時間減衰の効果: 古いデータのスコア={old_score:.8f}, 新データ平均={avg_fresh:.8f}")
    check("時間減衰: 30日前データのスコアが新データより低い",
          old_score < avg_fresh if avg_fresh > 0 else True,
          f"old={old_score:.8f}, avg_fresh={avg_fresh:.8f}")


# ---------------------------------------------------------------------------
# Section 7: save_chunks_text_only + embed_pending パイプライン検証
# ---------------------------------------------------------------------------
print("\n\n▶ Section 7: テキスト保存 → FTS即時 → ベクトル化後にベクトル検索可能")
hr()

TMP_DB2 = Path(tempfile.mktemp(suffix=".db"))
init_db(TMP_DB2)

from storage import save_chunks_text_only, embed_pending

late_chunks = [
    {"session_id": "s-late1", "project": "C:/proj/late",
     "user": "Pythonのdataclassについて教えて",
     "assistant": "@dataclassデコレーターはPythonクラスのボイラープレートを削減します",
     "timestamp": "2026-04-02T20:00:00"},
    {"session_id": "s-late2", "project": "C:/proj/late",
     "user": "TypeScriptのinterfaceとtypeの違い",
     "assistant": "interfaceは拡張可能でtypeはユニオン型など複雑な型定義に向いています",
     "timestamp": "2026-04-02T20:01:00"},
]

# Step 1: テキストのみ保存（StopHook相当）
n_text = save_chunks_text_only(late_chunks, TMP_DB2)
check("テキスト保存: 2件挿入", n_text == 2, f"実際: {n_text}")

# Step 2: FTSは即時使える
fts_before = fts_search("Python dataclass", limit=5, db_path=TMP_DB2)
check("テキスト保存直後にFTS検索可能", len(fts_before) >= 1,
      f"ヒット数: {len(fts_before)}")

# Step 3: ベクトル検索はembedding=NULLのためスキップされる
vec_before = vector_search(THEME_VECS["python"], limit=5, db_path=TMP_DB2)
check("embed_pending前: ベクトル検索で結果なし", len(vec_before) == 0,
      f"実際: {len(vec_before)}件（embedding=NULLのため0が正しい）")

print(f"\n  embed_pending前: FTS={len(fts_before)}件, Vector={len(vec_before)}件")

# Step 4: embed_pending実行（/sr相当）
n_emb = embed_pending(batch_size=50, db_path=TMP_DB2)
check("embed_pending: 2件ベクトル化", n_emb == 2, f"実際: {n_emb}")

# Step 5: ベクトル検索が使える
vec_after = vector_search(THEME_VECS["python"], limit=5, db_path=TMP_DB2)
check("embed_pending後: ベクトル検索で結果あり", len(vec_after) >= 1,
      f"実際: {len(vec_after)}件")

print(f"  embed_pending後: FTS={len(fts_before)}件, Vector={len(vec_after)}件")
if vec_after:
    print(f"  top1 vector hit: {vec_after[0]['user_text'][:50]}")

TMP_DB2.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Section 8: get_all にembeddingが含まれないことの確認
# ---------------------------------------------------------------------------
print("\n\n▶ Section 8: get_all — embedding除外の確認")
hr()

rows_all = get_all(limit=10, db_path=TMP_DB)
check("get_all: embeddingキーが含まれない",
      all("embedding" not in r for r in rows_all),
      f"embeddingあり件数: {sum(1 for r in rows_all if 'embedding' in r)}")
check("get_all: 必要フィールドは揃っている",
      all({"id","session_id","project","project_name","user_text","assistant_text",
           "timestamp","created_at"}.issubset(r.keys()) for r in rows_all))

print(f"  取得件数: {len(rows_all)}件")
print(f"  フィールド: {list(rows_all[0].keys()) if rows_all else 'なし'}")


# ---------------------------------------------------------------------------
# Section 9: WALモードの確認
# ---------------------------------------------------------------------------
print("\n\n▶ Section 9: WALモード設定の確認")
hr()

from storage import _get_conn
conn_check = _get_conn(TMP_DB)
cur_check = conn_check.cursor()
cur_check.execute("PRAGMA journal_mode")
mode = cur_check.fetchone()[0]
conn_check.close()
check("journal_mode = wal", mode == "wal", f"実際: {mode!r}")
print(f"  journal_mode: {mode!r}")


# ---------------------------------------------------------------------------
# Section 10: 重複保存の安全確認（テキスト/ベクトルを混在させても壊れない）
# ---------------------------------------------------------------------------
print("\n\n▶ Section 10: 重複保存の安全性確認")
hr()

TMP_DB3 = Path(tempfile.mktemp(suffix=".db"))
init_db(TMP_DB3)

base = [
    {"session_id": "dup-s1", "project": "C:/x",
     "user": "テスト質問A", "assistant": "テスト回答A", "timestamp": "2026-04-02T21:00:00"},
    {"session_id": "dup-s1", "project": "C:/x",
     "user": "テスト質問B", "assistant": "テスト回答B", "timestamp": "2026-04-02T21:01:00"},
]
extended = base + [
    {"session_id": "dup-s1", "project": "C:/x",
     "user": "テスト質問C", "assistant": "テスト回答C", "timestamp": "2026-04-02T21:02:00"},
]

# StopHookルート: テキストのみ2件 → 増分1件 → さらに増分0件
n1 = save_chunks_text_only(base, TMP_DB3)
n2 = save_chunks_text_only(extended, TMP_DB3)
n3 = save_chunks_text_only(extended, TMP_DB3)
check("dup: テキスト初回2件", n1 == 2, f"実際: {n1}")
check("dup: 増分1件", n2 == 1, f"実際: {n2}")
check("dup: 再保存0件", n3 == 0, f"実際: {n3}")

# /srルート: embed_pending後に再度save_chunks_text_onlyしても重複しない
embed_pending(db_path=TMP_DB3)
n4 = save_chunks_text_only(extended, TMP_DB3)
check("dup: embed_pending後も再保存0件", n4 == 0, f"実際: {n4}")

# save_chunksでも重複しない（全件既存）
n5 = save_chunks(extended, TMP_DB3)
check("dup: save_chunksでも再保存0件（全件既存）", n5 == 0, f"実際: {n5}")

total_rows = get_all(limit=100, db_path=TMP_DB3)
check("dup: 最終的に3件のみ（重複なし）", len(total_rows) == 3, f"実際: {len(total_rows)}")

print(f"  保存結果: {n1}+{n2}+{n3} (text-only), embed_pending後: {n4} (text-only), {n5} (full) → 合計 {len(total_rows)}件")

TMP_DB3.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# クリーンアップ
# ---------------------------------------------------------------------------
TMP_DB.unlink(missing_ok=True)

print(f"\n\n{'='*60}")
print(f"  結果: {PASS} PASS / {FAIL} FAIL / {PASS+FAIL} 合計")
if FAIL == 0:
    print("  全テスト通過")
else:
    print("  ※ FAILがある箇所を要確認")
    sys.exit(1)
