"""
injector.py - UserPromptSubmitHook エントリーポイント

セッション開始時にユーザーのクエリで関連メモリを検索し、
Claude Codeのシステムプロンプトに注入するためのテキストをstdoutに出力する。
"""

import io
import json
import sys
from pathlib import Path

# Windowsのcp932問題を回避するためstdout/stderrをUTF-8に再設定する
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# src/ をパスに追加してローカルモジュールをimportできるようにする
sys.path.insert(0, str(Path(__file__).parent))

from storage import init_db
from retriever import search


def format_memories(memories: list[dict]) -> str:
    """
    検索結果のメモリリストを読みやすいテキストに整形する。

    Args:
        memories: search()が返すdictのリスト

    Returns:
        Markdownフォーマットの整形済みテキスト
    """
    lines = [
        "## 過去の関連セッションから",
        "（注: これはsui-memoryが自動で取得した過去の会話の断片です）",
        "",
    ]

    for i, mem in enumerate(memories, start=1):
        # timestampから日付部分だけ取り出す（例: "2026-03-23T12:34:56" → "2026-03-23"）
        timestamp = mem.get("timestamp", "")
        date_str = timestamp[:10] if timestamp else "不明"

        # アシスタントの返答は最大200文字で切る
        assistant_text = mem.get("assistant_text", "")
        if len(assistant_text) > 200:
            assistant_text = assistant_text[:200] + "…"

        lines.append(f"### {i}. {date_str}")
        lines.append(f"**あなた**: {mem.get('user_text', '')}")
        lines.append(f"**Claude**: {assistant_text}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    """
    UserPromptSubmitHookのメインエントリーポイント。

    stdinからJSONを読み込み、queryフィールドを使って
    関連メモリを検索し、整形結果をstdoutに出力する。
    """
    # stdinからJSONを読み込む
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        # JSON解析エラーは無視して終了（フックが壊れないようにする）
        print(f"[sui-memory injector] stdin解析エラー: {e}", file=sys.stderr)
        sys.exit(0)

    # queryフィールドを取得する。なければ終了
    query = data.get("query")
    if not query:
        sys.exit(0)

    # DBを初期化する（初回起動時にテーブルを作成）
    init_db()

    # 関連メモリを検索する（最大5件）
    try:
        memories = search(query, limit=5)
    except Exception as e:
        print(f"[sui-memory injector] 検索エラー: {e}", file=sys.stderr)
        sys.exit(0)

    # 結果が0件なら何も出力せず終了
    if not memories:
        print("[sui-memory injector] 関連メモリなし", file=sys.stderr)
        sys.exit(0)

    # 整形してstdoutに出力する（Claude Codeがこれを読んでシステムプロンプトに追加する）
    formatted = format_memories(memories)
    print(formatted)

    # 件数をstderrにログ出力する
    print(f"[sui-memory injector] {len(memories)}件のメモリを注入", file=sys.stderr)


if __name__ == "__main__":
    main()
