"""
test_chunker_v2.py
chunker.py の2つの改修を検証する。

1. tool-callスキップバグ修正
   「質問 → ツールコール × N → 最終回答」でチャンクが正しく作られること

2. ノイズフィルター
   "ok" / "はい" / "了解" 等の短い相槌が除外され、
   "確認して" / "ありがとう" 等は残ること

実行: uv run --project C:/Users/bukol/Documents/sui-memory python tests/test_chunker_v2.py
"""
import sys, io, json, tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from chunker import load_chunks, _MIN_USER_TEXT_LEN

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

def write_jsonl(entries: list[dict]) -> Path:
    """エントリのリストを一時JSOLファイルに書き出す"""
    tmp = Path(tempfile.mktemp(suffix=".jsonl"))
    with open(tmp, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return tmp

def make_user(text: str, session: str = "s1", ts: str = "2026-04-02T10:00:00") -> dict:
    """テキストのみのuserエントリを作る"""
    return {
        "type": "user",
        "sessionId": session,
        "cwd": "C:/proj",
        "timestamp": ts,
        "message": {"content": [{"type": "text", "text": text}]},
    }

def make_tool_result(session: str = "s1", ts: str = "2026-04-02T10:01:00") -> dict:
    """ツール結果のみのuserエントリ（type="tool_result"ブロックのみ）"""
    return {
        "type": "user",
        "sessionId": session,
        "cwd": "C:/proj",
        "timestamp": ts,
        "message": {"content": [{"type": "tool_result", "tool_use_id": "tool_1", "content": "ファイルを読みました"}]},
    }

def make_assistant_text(text: str) -> dict:
    """テキストのみのassistantエントリ"""
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    }

def make_assistant_tool_use() -> dict:
    """ツールコールのみのassistantエントリ（textブロックなし）"""
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": "tool_1", "name": "Read", "input": {"file_path": "/tmp/test.txt"}}]},
    }


# ============================================================
# Section 1: tool-callスキップバグ修正の検証
# ============================================================
print("\n=== Section 1: tool-callスキップバグ修正 ===")


def test_simple_qa():
    """基本: ツールなしの単純なQ&A → チャンクが1件作られる"""
    entries = [
        make_user("Pythonでリストをソートするには？"),
        make_assistant_text("sorted()またはlist.sort()を使います"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("単純Q&A: 1チャンク", len(chunks) == 1, f"実際: {len(chunks)}")
    if chunks:
        check("単純Q&A: user_textが正しい", "Python" in chunks[0]["user"])
        check("単純Q&A: assistant_textが正しい", "sorted" in chunks[0]["assistant"])


def test_single_tool_call():
    """ツールコール1回: 質問 → tool_use → tool_result → 最終回答"""
    entries = [
        make_user("ファイルの中身を確認して"),            # 1: 本物の質問
        make_assistant_tool_use(),                        # 2: ツールコール（text無）
        make_tool_result(),                              # 3: ツール結果（tool_resultのみ）
        make_assistant_text("ファイルには3行のコードが含まれています"),  # 4: 最終回答
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("1回ツールコール: 1チャンク生成", len(chunks) == 1, f"実際: {len(chunks)}")
    if chunks:
        check("1回ツールコール: user_text = 元の質問",
              "ファイル" in chunks[0]["user"] and "確認" in chunks[0]["user"],
              f"実際: {chunks[0]['user']!r}")
        check("1回ツールコール: assistant_text = 最終回答",
              "3行" in chunks[0]["assistant"],
              f"実際: {chunks[0]['assistant']!r}")


def test_multiple_tool_calls():
    """ツールコール複数回: 質問 → tool × 3回 → 最終回答"""
    entries = [
        make_user("3つのファイルを全部読んで内容をまとめて"),  # 本物の質問
        make_assistant_tool_use(),
        make_tool_result(),
        make_assistant_tool_use(),
        make_tool_result(),
        make_assistant_tool_use(),
        make_tool_result(),
        make_assistant_text("3ファイルの内容をまとめました。A.py, B.py, C.pyです"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("複数ツールコール: 1チャンク生成", len(chunks) == 1, f"実際: {len(chunks)}")
    if chunks:
        check("複数ツールコール: user_text = 元の質問",
              "3つ" in chunks[0]["user"],
              f"実際: {chunks[0]['user']!r}")
        check("複数ツールコール: assistant_text = 最終回答",
              "A.py" in chunks[0]["assistant"],
              f"実際: {chunks[0]['assistant']!r}")


def test_consecutive_qa_with_tool():
    """Q&Aが連続: Q1(ツールあり) → Q2(ツールなし) → 2チャンクできる"""
    entries = [
        make_user("ファイルAを読んで", ts="2026-04-02T10:00:00"),
        make_assistant_tool_use(),
        make_tool_result(ts="2026-04-02T10:00:01"),
        make_assistant_text("ファイルAには100行あります"),

        make_user("ファイルBは何行？", ts="2026-04-02T10:01:00"),
        make_assistant_tool_use(),
        make_tool_result(ts="2026-04-02T10:01:01"),
        make_assistant_text("ファイルBには50行あります"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("連続Q&A(ツールあり): 2チャンク", len(chunks) == 2, f"実際: {len(chunks)}")
    if len(chunks) == 2:
        check("Q1 user_text = ファイルA", "ファイルA" in chunks[0]["user"])
        check("Q2 user_text = ファイルB", "ファイルB" in chunks[1]["user"])
        check("Q1 assistant_text = 100行", "100行" in chunks[0]["assistant"])
        check("Q2 assistant_text = 50行", "50行" in chunks[1]["assistant"])


def test_tool_result_only_session():
    """ツール結果のみ（本物の質問がない）: 0チャンク"""
    entries = [
        make_tool_result(),
        make_assistant_text("ツール結果を処理しました"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("ツール結果のみ: 0チャンク（pending_userなし）",
          len(chunks) == 0, f"実際: {len(chunks)}")


def test_user_after_tool_result():
    """ツール結果の後に新しい質問 → その質問とペアになる"""
    entries = [
        make_user("最初の質問"),
        make_assistant_tool_use(),
        make_tool_result(),
        make_assistant_text("ツール結果を処理しました"),   # ← 最初の質問とペア

        make_user("次の質問"),
        make_assistant_text("次の回答"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("ツール後の新質問: 2チャンク", len(chunks) == 2, f"実際: {len(chunks)}")
    if len(chunks) >= 1:
        check("1チャンク目: 最初の質問", "最初" in chunks[0]["user"])
    if len(chunks) >= 2:
        check("2チャンク目: 次の質問", "次の質問" in chunks[1]["user"])


# テスト実行
test_simple_qa()
test_single_tool_call()
test_multiple_tool_calls()
test_consecutive_qa_with_tool()
test_tool_result_only_session()
test_user_after_tool_result()


# ============================================================
# Section 2: ノイズフィルターの検証
# ============================================================
print(f"\n=== Section 2: ノイズフィルター（閾値: {_MIN_USER_TEXT_LEN}文字未満でスキップ） ===")


def test_noise_filter_ok():
    """'ok'（2文字）はスキップ"""
    entries = [
        make_user("ok"),
        make_assistant_text("かしこまりました"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("'ok'(2字): スキップ", len(chunks) == 0, f"実際: {len(chunks)}")


def test_noise_filter_hai():
    """'はい'（2文字）はスキップ"""
    entries = [
        make_user("はい"),
        make_assistant_text("承知しました"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("'はい'(2字): スキップ", len(chunks) == 0, f"実際: {len(chunks)}")


def test_noise_filter_ryokai():
    """'了解'（2文字）はスキップ"""
    entries = [
        make_user("了解"),
        make_assistant_text("了解です"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("'了解'(2字): スキップ", len(chunks) == 0, f"実際: {len(chunks)}")


def test_noise_filter_yes():
    """'yes'（3文字）= 閾値ちょうど → 残す"""
    entries = [
        make_user("yes"),
        make_assistant_text("了解しました"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("'yes'(3字): 残す（閾値ちょうど）", len(chunks) == 1, f"実際: {len(chunks)}")


def test_noise_filter_kakunin():
    """'確認して'（4文字）は残す"""
    entries = [
        make_user("確認して"),
        make_assistant_text("確認しました。問題ありませんでした"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("'確認して'(4字): 残す", len(chunks) == 1, f"実際: {len(chunks)}")


def test_noise_filter_arigatou():
    """'ありがとう'（5文字）は残す"""
    entries = [
        make_user("ありがとう"),
        make_assistant_text("どういたしまして"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("'ありがとう'(5字): 残す", len(chunks) == 1, f"実際: {len(chunks)}")


def test_noise_mixed():
    """ノイズと実質的なQ&Aが混在 → 実質的なものだけ残る"""
    entries = [
        make_user("Pythonの型ヒントについて教えてください", ts="2026-04-02T10:00:00"),
        make_assistant_text("型ヒントはコードの可読性を上げます"),

        make_user("ok", ts="2026-04-02T10:01:00"),       # ← ノイズ
        make_assistant_text("次にいきましょう"),

        make_user("了解", ts="2026-04-02T10:02:00"),     # ← ノイズ
        make_assistant_text("はい"),

        make_user("では実装してください", ts="2026-04-02T10:03:00"),
        make_assistant_text("実装しました。コードはこちらです..."),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("混在: 2チャンクのみ残る（4件中ノイズ2件除外）",
          len(chunks) == 2, f"実際: {len(chunks)}")
    if len(chunks) >= 1:
        check("1チャンク目: Python型ヒント", "Python" in chunks[0]["user"])
    if len(chunks) >= 2:
        check("2チャンク目: 実装してください", "実装" in chunks[1]["user"])


def test_noise_filter_empty():
    """空文字列（0文字）はスキップ"""
    entries = [
        {"type": "user", "sessionId": "s1", "cwd": "C:/proj",
         "timestamp": "2026-04-02T10:00:00",
         "message": {"content": [{"type": "text", "text": ""}]}},
        make_assistant_text("はい"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("空文字: スキップ", len(chunks) == 0, f"実際: {len(chunks)}")


def test_noise_filter_whitespace_only():
    """スペースのみ（空白）はスキップ"""
    entries = [
        {"type": "user", "sessionId": "s1", "cwd": "C:/proj",
         "timestamp": "2026-04-02T10:00:00",
         "message": {"content": [{"type": "text", "text": "  "}]}},
        make_assistant_text("はい"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    # スペースのみは _extract_text がstrip()するので "" になる → userテキスト空でスキップ
    check("スペースのみ: スキップ", len(chunks) == 0, f"実際: {len(chunks)}")


# テスト実行
test_noise_filter_ok()
test_noise_filter_hai()
test_noise_filter_ryokai()
test_noise_filter_yes()
test_noise_filter_kakunin()
test_noise_filter_arigatou()
test_noise_mixed()
test_noise_filter_empty()
test_noise_filter_whitespace_only()


# ============================================================
# Section 3: 組み合わせテスト（ツールコール + ノイズ混在）
# ============================================================
print("\n=== Section 3: 組み合わせテスト ===")


def test_tool_call_after_noise():
    """ノイズ → ツールコール付きQ&A → ノイズ → 正常Q&A"""
    entries = [
        make_user("ok", ts="2026-04-02T09:59:00"),           # ノイズ
        make_assistant_text("承知しました"),

        make_user("ファイルを読んで要約して", ts="2026-04-02T10:00:00"),  # 正常
        make_assistant_tool_use(),
        make_tool_result(),
        make_assistant_text("ファイルには重要な設定が記載されています"),

        make_user("了解", ts="2026-04-02T10:01:00"),          # ノイズ
        make_assistant_text("次に進みましょう"),

        make_user("次はテストを書いて", ts="2026-04-02T10:02:00"),  # 正常
        make_assistant_text("テストを書きました"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("組み合わせ: 2チャンクのみ（ノイズ2件除外）",
          len(chunks) == 2, f"実際: {len(chunks)}")
    if len(chunks) >= 1:
        check("1チャンク目: ファイル要約の質問", "ファイル" in chunks[0]["user"])
        check("1チャンク目: ツールコール後の最終回答", "重要な設定" in chunks[0]["assistant"])
    if len(chunks) >= 2:
        check("2チャンク目: テスト作成の質問", "テスト" in chunks[1]["user"])


test_tool_call_after_noise()


# ============================================================
# Section 4: 既存の挙動が壊れていないこと（回帰テスト）
# ============================================================
print("\n=== Section 4: 回帰テスト ===")


def test_file_history_snapshot_skip():
    """file-history-snapshotは無視される"""
    entries = [
        {"type": "file-history-snapshot", "files": []},
        make_user("質問です"),
        make_assistant_text("回答です"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("file-history-snapshot: スキップ後も正常にチャンク生成", len(chunks) == 1)


def test_empty_file():
    """空のJSONLファイル → 0チャンク"""
    tmp = Path(tempfile.mktemp(suffix=".jsonl"))
    tmp.write_text("", encoding="utf-8")
    chunks = load_chunks(str(tmp))
    check("空ファイル: 0チャンク", len(chunks) == 0)
    tmp.unlink()


def test_assistant_without_user():
    """userなしのassistantエントリはスキップ"""
    entries = [
        make_assistant_text("突然の回答"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("user無しのassistant: 0チャンク", len(chunks) == 0)


def test_session_id_preserved():
    """session_idとprojectが正しく保存される"""
    entries = [
        {"type": "user", "sessionId": "abc123", "cwd": "C:/myproject",
         "timestamp": "2026-04-02T10:00:00",
         "message": {"content": [{"type": "text", "text": "テスト質問"}]}},
        make_assistant_text("テスト回答"),
    ]
    chunks = load_chunks(str(write_jsonl(entries)))
    check("session_id保存", len(chunks) == 1 and chunks[0]["session_id"] == "abc123")
    check("project保存", len(chunks) == 1 and chunks[0]["project"] == "C:/myproject")
    check("timestamp保存", len(chunks) == 1 and chunks[0]["timestamp"] == "2026-04-02T10:00:00")


test_file_history_snapshot_skip()
test_empty_file()
test_assistant_without_user()
test_session_id_preserved()


# ============================================================
# 結果
# ============================================================
print(f"\n{'='*50}")
print(f"結果: {PASS} PASS / {FAIL} FAIL / {PASS+FAIL} 合計")
if FAIL == 0:
    print("全テスト通過")
else:
    print("※ FAILがある箇所を要確認")
    sys.exit(1)
