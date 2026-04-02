"""
chunker.py - Claude Code transcriptをQ&Aペアのチャンクに分割するモジュール
"""

import json
from pathlib import Path


def _extract_text(content) -> str:
    """
    contentからテキストを抽出する。
    - 文字列の場合はそのまま返す
    - リスト形式の場合はtype="text"のブロックのみ結合して返す
      （tool_result、thinkingブロック等は除外）
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts).strip()

    return ""


# ノイズフィルター: この文字数未満のユーザーテキストはスキップする
# "ok", "はい", "了解" 等の相槌・確認のみのやり取りを除外する
# 保守的に低く設定（3未満）: "確認して"(4字)・"ありがとう"(5字)は残す
_MIN_USER_TEXT_LEN = 3


def load_chunks(transcript_path: str) -> list[dict]:
    """
    transcriptファイル（jsonl）を読み込み、Q&Aペアのチャンクリストを返す。

    Args:
        transcript_path: .jsonlファイルのパス

    Returns:
        チャンクのリスト。各チャンクは以下のキーを持つdict:
        {
            "user": str,        # ユーザーの発言テキスト
            "assistant": str,   # アシスタントの返答テキスト
            "timestamp": str,   # userメッセージのtimestamp（ISO8601）
            "session_id": str,  # セッションID
            "project": str,     # プロジェクトパス（cwd）
        }
    """
    path = Path(transcript_path)
    chunks = []

    # jsonlを1行ずつ読み込んでパース
    lines = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                # 不正な行はスキップ
                continue

    # userとassistantのペアを抽出する
    # file-history-snapshotは無視する
    pending_user = None  # 対応するassistantを待っているuserメッセージ

    for entry in lines:
        msg_type = entry.get("type")

        # file-history-snapshotは無視
        if msg_type == "file-history-snapshot":
            continue

        if msg_type == "user":
            # ツール結果のみのターン（type="tool_result" ブロックのみ）は
            # pending_user を上書きしない。
            # 「質問 → ツールコール × N → 最終回答」の流れで元の質問が
            # ツール結果ターンに上書きされてしまうバグを防ぐ。
            user_content = entry.get("message", {}).get("content", "")
            if _extract_text(user_content):
                pending_user = entry

        elif msg_type == "assistant":
            if pending_user is None:
                # 対応するuserがない場合はスキップ
                continue

            # assistantのcontentからtextブロックのみ抽出（thinkingは除外）
            assistant_content = entry.get("message", {}).get("content", "")
            if isinstance(assistant_content, list):
                text_parts = []
                for block in assistant_content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                assistant_text = "\n".join(text_parts).strip()
            else:
                assistant_text = _extract_text(assistant_content)

            # userのテキストを抽出
            user_content = pending_user.get("message", {}).get("content", "")
            user_text = _extract_text(user_content)

            # assistantのtextが空の場合（thinkingのみのストリーミング中間エントリー等）は
            # pending_userを保持したまま次のassistantエントリーを待つ
            if not assistant_text:
                continue

            # userテキストが空の場合はペアごとスキップ
            if not user_text:
                pending_user = None
                continue

            # ノイズフィルター: 短すぎる相槌・確認はスキップ
            # （"ok", "はい", "了解" 等。_MIN_USER_TEXT_LEN 文字未満）
            if len(user_text) < _MIN_USER_TEXT_LEN:
                pending_user = None
                continue

            # session_idとprojectはtranscript内のエントリから取得
            session_id = pending_user.get("sessionId", "")
            project = pending_user.get("cwd", "")
            timestamp = pending_user.get("timestamp", "")

            chunks.append({
                "user": user_text,
                "assistant": assistant_text,
                "timestamp": timestamp,
                "session_id": session_id,
                "project": project,
            })

            # ペアを消費したのでリセット
            pending_user = None

    return chunks
