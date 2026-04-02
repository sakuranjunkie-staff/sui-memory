"""
test_lazy_embed.py
save_chunks_text_only / embed_pending の動作テスト。
テキスト保存とベクトル化を分離したアーキテクチャの検証。

実行: uv run --project C:/Users/bukol/Documents/sui-memory python tests/test_lazy_embed.py
"""
import sys, io, types, tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# embedderをモック（実際のモデルは起動しない）
EMBED_CALL_COUNT = 0

def mock_embed(texts):
    global EMBED_CALL_COUNT
    EMBED_CALL_COUNT += 1
    return [[0.1] * 8 for _ in texts]

mod = types.ModuleType("embedder")
mod.embed = mock_embed
mod.embed_query = lambda q: [0.1] * 8
sys.modules["embedder"] = mod

from storage import init_db, save_chunks_text_only, embed_pending, get_all, vector_search

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

def make_chunk(session_id, timestamp, user, asst, project="C:/test/proj"):
    return {
        "session_id": session_id,
        "project": project,
        "user": user,
        "assistant": asst,
        "timestamp": timestamp,
    }

TMP_DB = Path(tempfile.mktemp(suffix=".db"))
init_db(TMP_DB)


# ------------------------------------------------------------
# 1. save_chunks_text_only: embedderを呼ばないこと
# ------------------------------------------------------------
print("\n=== 1. save_chunks_text_only: embedder非呼び出し ===")

before_count = EMBED_CALL_COUNT
chunks_a = [
    make_chunk("sess-A", "2026-04-02T10:00:00", "質問1", "回答1"),
    make_chunk("sess-A", "2026-04-02T10:01:00", "質問2", "回答2"),
]
n = save_chunks_text_only(chunks_a, TMP_DB)
check("2件テキスト保存", n == 2, f"実際: {n}")
check("embedderを呼ばない", EMBED_CALL_COUNT == before_count,
      f"呼び出し回数: {EMBED_CALL_COUNT - before_count}")


# ------------------------------------------------------------
# 2. embedding=NULLで保存されること
# ------------------------------------------------------------
print("\n=== 2. embedding=NULL保存確認 ===")

import sqlite3
conn = sqlite3.connect(str(TMP_DB))
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("SELECT embedding FROM memories WHERE session_id = 'sess-A'")
rows = cur.fetchall()
conn.close()

check("2件存在", len(rows) == 2, f"実際: {len(rows)}")
check("embeddingがNULL", all(r["embedding"] is None for r in rows),
      f"非NULL件数: {sum(1 for r in rows if r['embedding'] is not None)}")


# ------------------------------------------------------------
# 3. embed_pending: NULLのみベクトル化すること
# ------------------------------------------------------------
print("\n=== 3. embed_pending: NULL件数分だけ処理 ===")

before_count = EMBED_CALL_COUNT
embedded = embed_pending(batch_size=50, db_path=TMP_DB)
check("2件ベクトル化", embedded == 2, f"実際: {embedded}")
check("embedderが呼ばれた", EMBED_CALL_COUNT > before_count)


# ------------------------------------------------------------
# 4. embed_pending後: embeddingが埋まっていること
# ------------------------------------------------------------
print("\n=== 4. embed_pending後のembedding確認 ===")

conn2 = sqlite3.connect(str(TMP_DB))
conn2.row_factory = sqlite3.Row
cur2 = conn2.cursor()
cur2.execute("SELECT embedding FROM memories WHERE session_id = 'sess-A'")
rows2 = cur2.fetchall()
conn2.close()

check("embeddingが全てNOT NULL", all(r["embedding"] is not None for r in rows2),
      f"NULL件数: {sum(1 for r in rows2 if r['embedding'] is None)}")
# float32 8次元 = 32バイト
check("embeddingのサイズ正常(8dim=32bytes)",
      all(len(r["embedding"]) == 32 for r in rows2),
      f"サイズ: {[len(r['embedding']) for r in rows2]}")


# ------------------------------------------------------------
# 5. embed_pending: 処理済み（NULL=0件）なら0を返す
# ------------------------------------------------------------
print("\n=== 5. embed_pending: 全件処理済みなら0 ===")

embedded2 = embed_pending(batch_size=50, db_path=TMP_DB)
check("0件（再実行は何もしない）", embedded2 == 0, f"実際: {embedded2}")


# ------------------------------------------------------------
# 6. save_chunks_text_only後にFTS検索が使える（テキストのみでもFTSは動作）
# ------------------------------------------------------------
print("\n=== 6. テキスト保存後すぐFTS検索できる ===")

from storage import fts_search
chunks_b = [
    make_chunk("sess-B", "2026-04-02T11:00:00", "Supabase移行について", "NeonからSupabaseに切り替えます"),
]
save_chunks_text_only(chunks_b, TMP_DB)

fts_results = fts_search("Supabase", limit=5, db_path=TMP_DB)
check("FTS検索: Supabaseでヒット", len(fts_results) >= 1,
      f"ヒット数: {len(fts_results)}")


# ------------------------------------------------------------
# 7. embed_pending後にベクトル検索が使える
# ------------------------------------------------------------
print("\n=== 7. embed_pending後にベクトル検索できる ===")

embed_pending(batch_size=50, db_path=TMP_DB)

import sqlite_vec, struct
def _vec_to_blob(vec):
    return struct.pack(f"{len(vec)}f", *vec)

import sqlite3
conn3 = sqlite3.connect(str(TMP_DB))
conn3.enable_load_extension(True)
sqlite_vec.load(conn3)
conn3.enable_load_extension(False)
conn3.row_factory = sqlite3.Row

query_blob = _vec_to_blob([0.1] * 8)
cur3 = conn3.cursor()
cur3.execute(
    "SELECT id, vec_distance_cosine(embedding, ?) AS d FROM memories WHERE embedding IS NOT NULL ORDER BY d LIMIT 3",
    (query_blob,)
)
vec_rows = cur3.fetchall()
conn3.close()

check("ベクトル検索: 結果が返る", len(vec_rows) >= 1, f"ヒット数: {len(vec_rows)}")


# ------------------------------------------------------------
# 8. batch_size制限が機能すること
# ------------------------------------------------------------
print("\n=== 8. embed_pending: batch_size制限 ===")

TMP_DB2 = Path(tempfile.mktemp(suffix=".db"))
init_db(TMP_DB2)

chunks_c = [
    make_chunk(f"sess-C{i}", f"2026-04-02T12:0{i}:00", f"質問{i}", f"回答{i}")
    for i in range(5)
]
save_chunks_text_only(chunks_c, TMP_DB2)

# batch_size=2 → 最大2件ずつ
embedded_batch = embed_pending(batch_size=2, db_path=TMP_DB2)
check("batch_size=2: 2件処理", embedded_batch == 2, f"実際: {embedded_batch}")

embedded_rest = embed_pending(batch_size=2, db_path=TMP_DB2)
check("2回目: 残り2件処理", embedded_rest == 2, f"実際: {embedded_rest}")

embedded_last = embed_pending(batch_size=2, db_path=TMP_DB2)
check("3回目: 残り1件処理", embedded_last == 1, f"実際: {embedded_last}")

embedded_done = embed_pending(batch_size=2, db_path=TMP_DB2)
check("4回目: 0件（全完了）", embedded_done == 0, f"実際: {embedded_done}")

TMP_DB2.unlink(missing_ok=True)


# ------------------------------------------------------------
# 9. save_chunks_text_only: 増分保存が機能すること
# ------------------------------------------------------------
print("\n=== 9. テキスト保存の増分チェック ===")

chunks_d = [
    make_chunk("sess-D", "2026-04-02T13:00:00", "最初", "最初"),
    make_chunk("sess-D", "2026-04-02T13:01:00", "2番目", "2番目"),
]
n1 = save_chunks_text_only(chunks_d, TMP_DB)
check("初回: 2件保存", n1 == 2, f"実際: {n1}")

chunks_d2 = chunks_d + [make_chunk("sess-D", "2026-04-02T13:02:00", "3番目", "3番目")]
n2 = save_chunks_text_only(chunks_d2, TMP_DB)
check("増分: 新規1件のみ", n2 == 1, f"実際: {n2}")

n3 = save_chunks_text_only(chunks_d2, TMP_DB)
check("再保存: 0件（全スキップ）", n3 == 0, f"実際: {n3}")


# ------------------------------------------------------------
# 10. 空リスト
# ------------------------------------------------------------
print("\n=== 10. 空リストエッジケース ===")

n_empty = save_chunks_text_only([], TMP_DB)
check("空リスト: 0件でクラッシュしない", n_empty == 0)

embedded_empty = embed_pending(batch_size=0, db_path=TMP_DB)
check("batch_size=0: 0件でクラッシュしない", embedded_empty == 0)


# ------------------------------------------------------------
# クリーンアップ
# ------------------------------------------------------------
TMP_DB.unlink(missing_ok=True)

print(f"\n{'='*40}")
print(f"結果: {PASS} PASS / {FAIL} FAIL / {PASS+FAIL} 合計")
if FAIL > 0:
    sys.exit(1)
else:
    print("全テスト通過")
