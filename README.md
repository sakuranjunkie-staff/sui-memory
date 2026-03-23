# sui-memory

Claude Codeにセッション間の長期記憶を持たせるツール。

外部サービス不要・LLM不使用・完全ローカル動作。

## インスピレーション

[noprogllama氏のZenn記事](https://zenn.dev/noprogllama/articles/7c24b2c2410213)に触発されて実装しました。アーキテクチャ設計の思想はこの記事に基づいています。

## 特徴

- **完全ローカル**: データは`~/.sui-memory/memory.db`に保存。外部サービス不要
- **LLM不使用**: 記憶の保存にLLMを使わないのでコストゼロ
- **日本語特化**: 埋め込みモデルにRuri v3-310m（日本語特化）を使用
- **ハイブリッド検索**: FTS5キーワード検索＋ベクトル検索をRRFで統合
- **時間減衰**: 古い記憶ほどスコアが下がる（半減期30日）
- **自動動作**: セッション終了時に自動保存、プロンプト入力時に自動注入

## 仕組み

```
セッション終了
    ↓ StopHook
transcript.jsonl → チャンク分割 → ベクトル化 → SQLite保存

次のセッションでプロンプト入力
    ↓ UserPromptSubmitHook
クエリ → FTS5検索 + ベクトル検索 → RRF統合 → 時間減衰 → Claude Codeに注入
```

## 必要環境

- Python 3.10以上
- [uv](https://github.com/astral-sh/uv)
- Claude Code

## インストール

```bash
git clone https://github.com/YOUR_USERNAME/sui-memory.git
cd sui-memory
uv sync
```

`~/.claude/settings.json`に以下を追加する（既存の設定とマージせよ）:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project /path/to/sui-memory python /path/to/sui-memory/src/hook.py"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project /path/to/sui-memory python /path/to/sui-memory/src/injector.py"
          }
        ]
      }
    ]
  }
}
```

`/path/to/sui-memory`は実際のパスに置き換えること。

## 技術スタック

| 技術 | 用途 |
|------|------|
| SQLite (FTS5) | キーワード検索 |
| sqlite-vec | ベクトル検索 |
| sentence-transformers | テキストのベクトル化 |
| cl-nagoya/ruri-v3-310m | 日本語特化埋め込みモデル |

## ファイル構成

```
sui-memory/
├── src/
│   ├── chunker.py      # transcript→チャンク分割
│   ├── embedder.py     # テキスト→ベクトル化
│   ├── storage.py      # SQLite保存・検索
│   ├── retriever.py    # ハイブリッド検索・RRF・時間減衰
│   ├── hook.py         # StopHookエントリーポイント
│   └── injector.py     # UserPromptSubmitHookエントリーポイント
└── tests/
```

## ライセンス

MIT

## 謝辞

アーキテクチャ設計の着想は[noprogllama](https://zenn.dev/noprogllama)氏の記事から得ました。
