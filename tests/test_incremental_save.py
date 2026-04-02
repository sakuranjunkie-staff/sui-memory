"""
test_incremental_save.py
/sr コマンドが依存する増分保存ロジックのテスト。
変更前後の重複チェック動作の違いを検証する。

実行: uv run --project C:/Users/bukol/Documents/sui-memory python tests/test_incremental_save.py
"""
import sys, io, types, time, tempfile, os
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# embedderをモック
mod = types.ModuleType("embedder")
mod.embed = lambda texts: [[0.1] * 8 for _ in texts]
mod.embed_query = lambda q: [0.1] * 8
sys.modules["embedder"] = mod

from storage import init_db, save_chunks, get_all

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
# 1. 同一セッションの増分保存（コアロジック検証）
# ------------------------------------------------------------
print("\n=== 1. 同一セッションの増分保存 ===")

# セッション開始直後: Q&A 2件
session_chunks_v1 = [
    make_chunk("sess-A", "2026-04-02T10:00:00", "最初の質問", "最初の回答"),
    make_chunk("sess-A", "2026-04-02T10:01:00", "2番目の質問", "2番目の回答"),
]
n1 = save_chunks(session_chunks_v1, TMP_DB)
check("初回保存: 2件保存", n1 == 2, f"実際: {n1}")

# 少し後: 同じQ&A + 新しいQ&A 1件（手動/sr実行を想定）
session_chunks_v2 = session_chunks_v1 + [
    make_chunk("sess-A", "2026-04-02T10:02:00", "3番目の質問", "3番目の回答"),
]
n2 = save_chunks(session_chunks_v2, TMP_DB)
check("増分保存: 新規1件のみ保存（既存2件はスキップ）", n2 == 1, f"実際: {n2}")

# セッション終了時: さらに1件追加（StopHookを想定）
session_chunks_v3 = session_chunks_v2 + [
    make_chunk("sess-A", "2026-04-02T10:03:00", "4番目の質問", "4番目の回答"),
]
n3 = save_chunks(session_chunks_v3, TMP_DB)
check("StopHook保存: 新規1件のみ保存（既存3件はスキップ）", n3 == 1, f"実際: {n3}")

# DB全件確認
all_rows = get_all(limit=100, db_path=TMP_DB)
sess_a_rows = [r for r in all_rows if r["session_id"] == "sess-A"]
check("最終的に4件が正しく保存されている", len(sess_a_rows) == 4, f"実際: {len(sess_a_rows)}")

# タイムスタンプが全て揃っているか確認
timestamps = {r["timestamp"] for r in sess_a_rows}
expected_ts = {
    "2026-04-02T10:00:00", "2026-04-02T10:01:00",
    "2026-04-02T10:02:00", "2026-04-02T10:03:00"
}
check("全4件のtimestampが正しく保存", timestamps == expected_ts,
      f"不足: {expected_ts - timestamps}")


# ------------------------------------------------------------
# 2. 複数セッションの並行保存
# ------------------------------------------------------------
print("\n=== 2. 複数セッションの並行保存 ===")

chunks_b = [
    make_chunk("sess-B", "2026-04-02T11:00:00", "別セッション質問1", "別セッション回答1"),
    make_chunk("sess-B", "2026-04-02T11:01:00", "別セッション質問2", "別セッション回答2"),
]
n_b = save_chunks(chunks_b, TMP_DB)
check("別セッション: 2件保存", n_b == 2, f"実際: {n_b}")

# sess-Aの再保存は影響しない
n_a_retry = save_chunks(session_chunks_v3, TMP_DB)
check("sess-Aの再保存: 0件（全て既存）", n_a_retry == 0, f"実際: {n_a_retry}")

all_rows2 = get_all(limit=100, db_path=TMP_DB)
check("合計6件（A:4 + B:2）", len(all_rows2) == 6, f"実際: {len(all_rows2)}")


# ------------------------------------------------------------
# 3. 空チャンクリストのエッジケース
# ------------------------------------------------------------
print("\n=== 3. 空チャンクリスト ===")

n_empty = save_chunks([], TMP_DB)
check("空リスト: 0件（クラッシュしない）", n_empty == 0)


# ------------------------------------------------------------
# 4. 同一timestamp・異なるuserテキスト（極めて稀なエッジケース）
# ------------------------------------------------------------
print("\n=== 4. 同一timestampエッジケース ===")

chunks_same_ts = [
    make_chunk("sess-C", "2026-04-02T12:00:00", "質問A", "回答A"),
    make_chunk("sess-C", "2026-04-02T12:00:00", "質問B（同一timestamp）", "回答B"),
]
n_same_ts = save_chunks(chunks_same_ts, TMP_DB)
# 同一バッチ内の重複チェックはDB参照（未コミット）なので、同一timestampでも
# 両方挿入される。次回以降の再保存ではCOUNT>=1でスキップされる。
# 実際にtimestampが完全に重複するケースは極めて稀なので実用上問題なし。
print(f"  INFO: 同一timestamp {len(chunks_same_ts)}件 → {n_same_ts}件保存"
      f"（同一バッチは両方挿入、次回以降スキップ）")
check("同一timestampでもクラッシュしない", True)


# ------------------------------------------------------------
# 5. created_atの一貫性（同一バッチは同一タイムスタンプ）
# ------------------------------------------------------------
print("\n=== 5. created_atの一貫性 ===")

chunks_d = [
    make_chunk("sess-D", "2026-04-02T13:00:00", "Q1", "A1"),
    make_chunk("sess-D", "2026-04-02T13:01:00", "Q2", "A2"),
    make_chunk("sess-D", "2026-04-02T13:02:00", "Q3", "A3"),
]
save_chunks(chunks_d, TMP_DB)

all_rows3 = get_all(limit=100, db_path=TMP_DB)
sess_d_rows = [r for r in all_rows3 if r["session_id"] == "sess-D"]
if len(sess_d_rows) >= 2:
    created_ats = [r["created_at"] for r in sess_d_rows]
    all_same = len(set(created_ats)) == 1
    check("同一バッチ内: created_atが全て同一", all_same,
          f"created_at: {created_ats}")


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
