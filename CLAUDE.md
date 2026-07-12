# CLAUDE.md - sui-memory プロジェクト規約

## プロジェクト概要
- **名前:** sui-memory
- **目的:** Claude Codeにセッション間の長期記憶を持たせるツール
- **言語:** Python
- **公開予定:** GitHub（MITライセンス）
- **対象OS:** Windows（主）、macOS・Linux（対応予定）

## 絶対ルール
1. git push を勝手に実行するな
2. 指示にない機能を勝手に追加するな
3. コードには必ず日本語コメントを書け
4. PayBalanceのコードには触れるな。別プロジェクトだ

## 技術スタック
- Python（uvで管理）
- SQLite（FTS5 + sqlite-vec）
- sentence-transformers（Ruri v3-310m）
- Claude Code Hooks（StopHook・PreCompactHook）

## アーキテクチャ
- セッション終了時にtranscript（jsonl）をチャンク分割してベクトル化・保存
- セッション開始時に関連メモリを検索して文脈として注入
- 検索はキーワード（FTS5）＋ベクトルのハイブリッド、RRFで統合
- 時間減衰あり（半減期30日）

## transcriptフォーマット
- 場所: `~/.claude/projects/{プロジェクト名}/{セッションID}.jsonl`
- StopHookのstdinから`transcript_path`で取得可能
- 1行1JSON。type: "user" | "assistant" | "file-history-snapshot"

## ディレクトリ構成
```
sui-memory/
├── src/
│   ├── chunker.py      # transcript→チャンク分割
│   ├── embedder.py     # チャンク→ベクトル化
│   ├── storage.py      # SQLite保存・検索
│   ├── retriever.py    # ハイブリッド検索＋RRF＋時間減衰
│   └── hook.py         # Claude Code Hook エントリーポイント
├── tests/
├── docs/
├── pyproject.toml
└── CLAUDE.md
```

## 作業ログ
- 2026/7/12(2回目): ④FTSクエリを文字種切り出し（英数/カタカナ/漢字の3文字以上の連続をOR結合）に変更＝スペースなし日本語文（kizamiの生プロンプト）が0件→ヒットするように ⑤時間減衰の半減期を明示検索でデフォルト180日に緩和（half_life_days引数化） ⑥期間の上下指定（until_days/until引数）を追加＝「3月ごろ」の窓検索が可能に ⑦pytest一括実行を修復（スクリプト型テスト6本をconftest.pyで収集除外＋test_scripts.pyがサブプロセス橋渡し）・test_chunkerの2文字テストデータ修正。63件全通過
- 2026/7/12: /recall低品質の原因を修正。①時間フィルタを後がけ→SQL側（since引数）に変更（候補の飢餓解消）②FTSをAND→OR結合に変更（複数キーワードで壊滅していた）③recall.mdの判定表をスコア絶対値基準→本文内容基準に書き換え（RRFスコアは上限0.033で「0.5未満=低スコア」が毎回発動していた）
- 2026/3/23: プロジェクト開始。transcriptフォーマット確認済み
- StopHookでtranscript_pathが渡されることを確認済み
- Windows環境（PowerShell）で開発中
