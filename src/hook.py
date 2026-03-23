"""
hook.py - Claude CodeのStopHookから呼ばれるエントリーポイント。
transcriptを読み込んでメモリDBに保存する。

stdinからJSON形式でフック情報を受け取り、transcript_pathを取得して処理する。
stdoutには何も出力しない（Claude Codeがstdoutを解釈するため）。
例外が発生してもstderrにログを出して正常終了する（Claude Codeの動作を止めないため）。
"""

import json
import sys
from pathlib import Path

# srcパッケージとして実行される場合とスクリプト直接実行の両方に対応
_src_dir = Path(__file__).parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from chunker import load_chunks
from storage import init_db, save_chunks


def _log(message: str) -> None:
    """stderrにフォーマット済みログを出力する（stdoutは使わない）"""
    print(f"[sui-memory] {message}", file=sys.stderr)


def main() -> None:
    """
    StopHookのエントリーポイント。
    stdinからJSONを読み込み、transcript_pathのtranscriptをメモリDBに保存する。
    """
    try:
        # stdinからフック情報を読み込む
        raw = sys.stdin.read()
        hook_data = json.loads(raw)
        transcript_path = hook_data.get("transcript_path", "")

        if not transcript_path:
            _log("transcript_pathが見つかりません。スキップします。")
            return

        # transcriptファイルの存在確認
        path = Path(transcript_path)
        if not path.exists():
            _log(f"警告: transcriptファイルが存在しません: {transcript_path}")
            return

        # transcriptをQ&Aチャンクに分割
        chunks = load_chunks(str(path))

        if not chunks:
            _log("チャンクが0件のため保存をスキップします。")
            return

        # DBを初期化（既存テーブルがあればスキップ）
        init_db()

        # チャンクをDBに保存（session_id重複は自動スキップ）
        inserted = save_chunks(chunks)

        # セッションIDは先頭チャンクから取得（短縮表示用）
        session_short = chunks[0].get("session_id", "")[:8]
        _log(f"{inserted}件のメモリを保存しました（セッション: {session_short}）")

    except json.JSONDecodeError as e:
        # stdinのJSONパースに失敗した場合
        _log(f"エラー: JSONのパースに失敗しました: {e}")

    except Exception as e:
        # その他の予期しないエラー（Claude Codeの動作は止めない）
        _log(f"エラー: {e}")


if __name__ == "__main__":
    main()
