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
            # 前のuserが未ペアのまま残っている場合は破棄して上書き
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
