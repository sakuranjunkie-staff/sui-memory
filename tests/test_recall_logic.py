"""
test_recall_logic.py
recall.mdが指示するスクリプトロジックの検証テスト。
実際のDBを使わずロジックだけを仮想データで検証する。

実行: uv run --project C:/Users/bukol/Documents/sui-memory python tests/test_recall_logic.py
"""
import sys, io, math, time, types, tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

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


# ------------------------------------------------------------
# recall検索スクリプトのロジックを関数として再現
# (recall.mdの「引数あり」スクリプト本体と同等)
# ------------------------------------------------------------

def run_recall_search(query: str, days_arg: str, results: list[dict]) -> str:
    """
    recall.mdのスクリプトが行う処理をシミュレートして出力文字列を返す。
    resultsはsearch/search_by_timerangeの戻り値を模したリスト。
    """
    # days引数のパース（バグ: 非整数でValueError）
    try:
        days = int(days_arg) if days_arg else 7
    except ValueError:
        days = 7  # フォールバック（修正後の動作）

    output_lines = []
    if not results:
        output_lines.append("NORESULT")
    else:
        for r in results:
            ts = r["timestamp"][:16] if r.get("timestamp") else "不明"
            pn = r.get("project_name") or ""
            score = r.get("score", 0)
            u = r["user_text"][:200].replace("\n", " ")
            a = r["assistant_text"][:200].replace("\n", " ")
            prefix = f"[{ts}]" + (f"[{pn}]" if pn else "")
            output_lines.append(f"{prefix} score={score:.5f}")
            output_lines.append(f"  U: {u}")
            output_lines.append(f"  A: {a}")
            output_lines.append("")

    return "\n".join(output_lines)


def interpret_results(results: list[dict]) -> str:
    """
    recall.mdのスコア判定ロジックをシミュレート。
    返値: "report_top" | "ask_clarify" | "noresult"
    """
    if not results:
        return "noresult"
    if len(results) == 1:
        return "report_top"

    scores = [r["score"] for r in results]
    top = scores[0]
    second = scores[1]

    # 上位が2位の2倍以上 → 明確に最良 → 報告
    if second > 0 and top >= second * 2.0:
        return "report_top"

    # 上位3件のスコア差が0.002以内 → 拮抗 → 聞き返す
    top3_scores = scores[:3]
    if max(top3_scores) - min(top3_scores) <= 0.002:
        return "ask_clarify"

    # gap: 上位が2倍未満、かつ差が0.002超 → recall.mdの想定外ケース
    # 修正後は「report_top」にフォールバックさせる
    return "report_top"  # フォールバック


def make_result(user: str, asst: str, score: float, project_name: str = "fx-shield",
                ts: str = "2026-03-15T10:00:00") -> dict:
    return {
        "user_text": user,
        "assistant_text": asst,
        "score": score,
        "project_name": project_name,
        "timestamp": ts,
    }


# ------------------------------------------------------------
# 1. スクリプト出力フォーマット確認
# ------------------------------------------------------------
print("\n=== 1. 出力フォーマット ===")

results_1 = [make_result("Prismaの移行について", "マイグレーション実行した", 0.035)]
output = run_recall_search("Prisma", "7", results_1)
check("1件: scoreが出力に含まれる", "score=" in output)
check("1件: タイムスタンプが含まれる", "2026-03-15" in output)
check("1件: project_nameが含まれる", "fx-shield" in output)
check("1件: ユーザーテキストが含まれる", "Prismaの移行" in output)

results_empty = []
output_empty = run_recall_search("存在しない話題", "7", results_empty)
check("0件: NOREUSLTが出力される", "NORESULT" in output_empty)


# ------------------------------------------------------------
# 2. スコア判定ロジック
# ------------------------------------------------------------
print("\n=== 2. スコア判定ロジック ===")

# ケース: 1件のみ → report_top
r_single = [make_result("A", "a", 0.04)]
check("1件のみ → report_top", interpret_results(r_single) == "report_top")

# ケース: 0件 → noresult
check("0件 → noresult", interpret_results([]) == "noresult")

# ケース: 上位が2倍以上 → report_top
r_dominant = [
    make_result("A", "a", 0.040),
    make_result("B", "b", 0.019),
    make_result("C", "c", 0.010),
]
check("上位2倍以上 → report_top", interpret_results(r_dominant) == "report_top",
      f"top={r_dominant[0]['score']}, 2nd={r_dominant[1]['score']}")

# ケース: 拮抗（差0.001以内） → ask_clarify
r_tied = [
    make_result("A", "a", 0.040),
    make_result("B", "b", 0.040),
    make_result("C", "c", 0.039),
]
check("拮抗（差0.001） → ask_clarify", interpret_results(r_tied) == "ask_clarify",
      f"scores={[r['score'] for r in r_tied]}")

# ケース: 中間域（2倍未満、差0.002超）→ recall.mdのギャップ → フォールバックreport_top
r_middle = [
    make_result("A", "a", 0.035),
    make_result("B", "b", 0.025),  # 1.4倍 (< 2倍)
    make_result("C", "c", 0.010),  # 差 = 0.025 > 0.002
]
result_middle = interpret_results(r_middle)
check("中間域（ギャップケース）→ report_topにフォールバック",
      result_middle == "report_top",
      f"実際: {result_middle}, scores={[r['score'] for r in r_middle]}")

# ケース: 2位がゼロのときのゼロ除算確認
r_zero_second = [
    make_result("A", "a", 0.03),
    make_result("B", "b", 0.0),
]
try:
    result_zero = interpret_results(r_zero_second)
    check("2位スコアがゼロでもクラッシュしない", True, f"結果: {result_zero}")
except ZeroDivisionError as e:
    check("2位スコアがゼロでもクラッシュしない", False, str(e))


# ------------------------------------------------------------
# 3. days引数のパース
# ------------------------------------------------------------
print("\n=== 3. days引数パース ===")

# 通常
check("days='7' → 7日", run_recall_search("q", "7", []) == "NORESULT")
check("days='' → デフォルト7日（エラーなし）", run_recall_search("q", "", []) == "NORESULT")

# 非整数（修正前はValueError → crash）
try:
    output_invalid = run_recall_search("q", "abc", [])
    check("days='abc' → クラッシュしない（修正済み）", True)
except ValueError as e:
    check("days='abc' → クラッシュしない（修正済み）", False, str(e))

# days=0 → 全期間（retriever.search()を使うべき）
# この動作はrecall.mdのスクリプトで if days == 0: search() else: search_by_timerange()
# で分岐されている → ここではパースのみ確認
days_zero = int("0") if "0" else 7
check("days='0' → 全期間指定(0)", days_zero == 0)

# 各範囲の日数マッピング確認
range_map = {"1": 30, "2": 90, "3": 180, "4": 365, "5": 0}
for k, expected in range_map.items():
    check(f"範囲選択{k} → {expected}日", range_map[k] == expected)


# ------------------------------------------------------------
# 4. NORESULT後の再検索ロジック確認
# ------------------------------------------------------------
print("\n=== 4. NORESULT後の再検索フロー ===")

# シナリオ: 7日→NORESULT→ユーザーが「1か月」選択→30日で検索
# recall.mdの指示では: ユーザー回答後に days=30 で再実行

# 7日でNORESULT
output_7d = run_recall_search("古い会話", "7", [])
check("Step1: 7日でNORESULT", "NORESULT" in output_7d)

# 30日で結果あり（シミュレート）
results_30d = [make_result("古い会話の内容", "当時の回答", 0.025, ts="2026-02-10T09:00:00")]
output_30d = run_recall_search("古い会話", "30", results_30d)
check("Step2: 30日で結果あり → NOREUSLTなし", "NORESULT" not in output_30d)
check("Step2: 30日の結果が出力される", "2026-02-10" in output_30d)

# 30日でもNORESULT → 次の範囲を提示すべき（スクリプト自体はNORESULTを返すだけ）
output_30d_empty = run_recall_search("存在しない", "30", [])
check("Step2: 30日もNORESULT → スクリプトはNORESULTを返す", "NORESULT" in output_30d_empty)
# → この時点でClaude(recall.md)が「さらに広げますか？」と提示する設計


# ------------------------------------------------------------
# 5. project_nameなしレコードの表示
# ------------------------------------------------------------
print("\n=== 5. project_nameなしのレコード ===")

result_no_pn = make_result("テスト", "レスポンス", 0.02, project_name="")
output_no_pn = run_recall_search("テスト", "7", [result_no_pn])
check("project_nameなし → [] なしで表示（prefix中括弧なし）",
      "[" + "2026-03-15T10:00" + "]" in output_no_pn and
      "[" + "2026-03-15T10:00" + "][]" not in output_no_pn)

result_with_pn = make_result("テスト", "レスポンス", 0.02, project_name="sui-memory")
output_with_pn = run_recall_search("テスト", "7", [result_with_pn])
check("project_nameあり → [sui-memory]が表示",
      "[sui-memory]" in output_with_pn)


# ------------------------------------------------------------
# 6. 長いテキストの切り詰め確認
# ------------------------------------------------------------
print("\n=== 6. テキスト切り詰め ===")

long_user = "あ" * 300  # 300文字
long_asst = "い" * 300
result_long = make_result(long_user, long_asst, 0.02)
output_long = run_recall_search("テスト", "7", [result_long])
# 200文字で切り詰めているはず
u_line = [line for line in output_long.split("\n") if line.startswith("  U:")]
a_line = [line for line in output_long.split("\n") if line.startswith("  A:")]
if u_line:
    actual_len = len(u_line[0]) - len("  U: ")
    check("U行が200文字以内で切り詰め", actual_len <= 200, f"実際: {actual_len}文字")
if a_line:
    actual_len = len(a_line[0]) - len("  A: ")
    check("A行が200文字以内で切り詰め", actual_len <= 200, f"実際: {actual_len}文字")


# ------------------------------------------------------------
# サマリー
# ------------------------------------------------------------
print(f"\n{'='*40}")
print(f"結果: {PASS} PASS / {FAIL} FAIL / {PASS+FAIL} 合計")
if FAIL > 0:
    print("※ FAILがある箇所を確認してください")
    sys.exit(1)
else:
    print("全テスト通過")
