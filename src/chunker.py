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

# コンテキスト継続サマリーフィルター:
# Claude Codeがコンテキスト上限に達したとき、自動生成する要約テキストが
# user_text として混入する。これはユーザーの発言ではないのでスキップする。
_CONTEXT_SUMMARY_PREFIXES = (
    "This session is being continued from a previous conversation that ran out of context.",
    "このセッションは、コンテキストが不足したため、以前の会話から継続されています。",
)


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
    #
    # 返答は「次の本物のuser発言が来るまで」のassistantのtextを全部つないで1つにする（2026-11-23）。
    # 旧版は最初にtextが出たassistantで組を閉じていたため、「調べる」と一言書いてから道具を使い、
    # その後に本当の答えを書いたターンでは、前置きの一言だけが保存されていた（全体の約19%）。
    pending_user = None      # 対応するassistantを待っているuserメッセージ
    assistant_parts = []     # そのuserに対するassistantのtext（ターン内の全部）

    def _flush() -> None:
        """溜めたuserとassistantの組を、フィルターを通してchunksに入れる"""
        if pending_user is None or not assistant_parts:
            # 返答のtextがまだ無い発言（未回答・道具だけのターン）は保存しない
            return

        user_text = _extract_text(pending_user.get("message", {}).get("content", ""))
        assistant_text = "\n\n".join(assistant_parts).strip()

        # userテキストが空の場合はペアごとスキップ
        if not user_text or not assistant_text:
            return

        # ノイズフィルター: 短すぎる相槌・確認はスキップ
        # （"ok", "はい", "了解" 等。_MIN_USER_TEXT_LEN 文字未満）
        if len(user_text) < _MIN_USER_TEXT_LEN:
            return

        # コンテキスト継続サマリーフィルター:
        # "This session is being continued..." で始まるテキストは
        # Claude Codeが自動生成した要約であり、ユーザーの発言ではない。
        # ベクトル化・検索の対象から除外する。
        if any(user_text.startswith(prefix) for prefix in _CONTEXT_SUMMARY_PREFIXES):
            return

        # session_idとprojectはtranscript内のエントリから取得
        chunks.append({
            "user": user_text,
            "assistant": assistant_text,
            "timestamp": pending_user.get("timestamp", ""),
            "session_id": pending_user.get("sessionId", ""),
            "project": pending_user.get("cwd", ""),
        })

    for entry in lines:
        msg_type = entry.get("type")

        # file-history-snapshotは無視
        if msg_type == "file-history-snapshot":
            continue

        if msg_type == "user":
            # ツール結果のみのターン（type="tool_result" ブロックのみ）は
            # 組を閉じない（元の質問のまま、続く返答を溜め続ける）。
            user_content = entry.get("message", {}).get("content", "")
            if _extract_text(user_content):
                # 本物の発言が来た＝前の組はここで完結
                _flush()
                pending_user = entry
                assistant_parts = []

        elif msg_type == "assistant":
            if pending_user is None:
                # 対応するuserがない場合はスキップ
                continue

            # assistantのcontentからtextブロックのみ抽出（thinkingは除外）
            # textが空のエントリー（thinkingのみ・道具の呼び出しのみ）は足さない
            assistant_text = _extract_text(entry.get("message", {}).get("content", ""))
            if assistant_text:
                assistant_parts.append(assistant_text)

    # 最後の組（ファイル末尾まで次の発言が無い）を閉じる
    _flush()

    return chunks
