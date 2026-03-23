"""
test_chunker.py - chunker.pyのユニットテスト
"""

import json
import tempfile
from pathlib import Path
import sys

# srcディレクトリをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from chunker import load_chunks, _extract_text


def write_jsonl(path: Path, entries: list[dict]):
    """テスト用のjsonlファイルを書き出すヘルパー"""
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# --- _extract_text のテスト ---

def test_extract_text_string():
    """文字列のcontentはそのまま返る"""
    assert _extract_text("hello") == "hello"


def test_extract_text_list_text_only():
    """リスト形式からtextブロックのみ抽出される"""
    content = [
        {"type": "text", "text": "foo"},
        {"type": "tool_use", "text": "bar"},  # 除外されるべき
        {"type": "text", "text": "baz"},
    ]
    assert _extract_text(content) == "foo\nbaz"


def test_extract_text_excludes_thinking():
    """thinkingブロックは除外される"""
    content = [
        {"type": "thinking", "thinking": "内部思考..."},
        {"type": "text", "text": "最終回答"},
    ]
    assert _extract_text(content) == "最終回答"


def test_extract_text_empty_list():
    """空リストは空文字を返す"""
    assert _extract_text([]) == ""


# --- load_chunks のテスト ---

def test_basic_pair():
    """基本的なuser/assistantペアが1チャンクになる"""
    entries = [
        {
            "type": "user",
            "message": {"content": "こんにちは"},
            "timestamp": "2026-03-23T10:00:00.000Z",
            "sessionId": "session-001",
            "cwd": "C:\\Users\\bukol\\project",
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "こんにちは！"}]},
        },
    ]
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        tmp_path = f.name

    chunks = load_chunks(tmp_path)
    assert len(chunks) == 1
    assert chunks[0]["user"] == "こんにちは"
    assert chunks[0]["assistant"] == "こんにちは！"
    assert chunks[0]["timestamp"] == "2026-03-23T10:00:00.000Z"
    assert chunks[0]["session_id"] == "session-001"
    assert chunks[0]["project"] == "C:\\Users\\bukol\\project"
    print("  OK: 基本ペア")


def test_file_history_snapshot_ignored():
    """file-history-snapshotは無視される"""
    entries = [
        {"type": "file-history-snapshot", "files": []},
        {
            "type": "user",
            "message": {"content": "質問"},
            "timestamp": "2026-03-23T10:00:00.000Z",
            "sessionId": "s1",
            "cwd": "/tmp",
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "回答"}]},
        },
    ]
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
        tmp_path = f.name

    chunks = load_chunks(tmp_path)
    assert len(chunks) == 1
    print("  OK: file-history-snapshot無視")


def test_thinking_excluded():
    """assistantのthinkingブロックは除外される"""
    entries = [
        {
            "type": "user",
            "message": {"content": "考えて"},
            "timestamp": "2026-03-23T10:00:00.000Z",
            "sessionId": "s1",
            "cwd": "/tmp",
        },
        {
            "type": "assistant",
            "message": {"content": [
                {"type": "thinking", "thinking": "内部思考中..."},
                {"type": "text", "text": "結論です"},
            ]},
        },
    ]
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
        tmp_path = f.name

    chunks = load_chunks(tmp_path)
    assert len(chunks) == 1
    assert chunks[0]["assistant"] == "結論です"
    assert "内部思考中" not in chunks[0]["assistant"]
    print("  OK: thinkingブロック除外")


def test_multiple_pairs():
    """複数ペアが正しく分割される"""
    entries = []
    for i in range(3):
        entries.append({
            "type": "user",
            "message": {"content": f"質問{i}"},
            "timestamp": f"2026-03-23T10:0{i}:00.000Z",
            "sessionId": "s1",
            "cwd": "/tmp",
        })
        entries.append({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": f"回答{i}"}]},
        })

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
        tmp_path = f.name

    chunks = load_chunks(tmp_path)
    assert len(chunks) == 3
    for i, chunk in enumerate(chunks):
        assert chunk["user"] == f"質問{i}"
        assert chunk["assistant"] == f"回答{i}"
    print("  OK: 複数ペア")


def test_user_content_as_list():
    """userのcontentがリスト形式（tool_result等）でもtextのみ結合される"""
    entries = [
        {
            "type": "user",
            "message": {"content": [
                {"type": "tool_result", "content": "ツール結果"},
                {"type": "text", "text": "続きの質問"},
            ]},
            "timestamp": "2026-03-23T10:00:00.000Z",
            "sessionId": "s1",
            "cwd": "/tmp",
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "了解"}]},
        },
    ]
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
        tmp_path = f.name

    chunks = load_chunks(tmp_path)
    assert len(chunks) == 1
    assert chunks[0]["user"] == "続きの質問"
    print("  OK: userコンテンツのリスト形式")


if __name__ == "__main__":
    print("=== chunker テスト開始 ===")
    test_extract_text_string()
    print("  OK: extract_text 文字列")
    test_extract_text_list_text_only()
    print("  OK: extract_text リスト")
    test_extract_text_excludes_thinking()
    print("  OK: extract_text thinking除外")
    test_extract_text_empty_list()
    print("  OK: extract_text 空リスト")
    test_basic_pair()
    test_file_history_snapshot_ignored()
    test_thinking_excluded()
    test_multiple_pairs()
    test_user_content_as_list()
    print("=== 全テスト通過 ===")
