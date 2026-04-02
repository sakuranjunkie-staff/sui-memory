# sui-memory

**Claude Code にセッションをまたいだ長期記憶を持たせるツール**  
**A long-term memory system for Claude Code that persists conversation history across sessions**

外部サービス不要・LLM不使用・完全ローカル動作。  
No external services. No LLM. Runs entirely locally.

---

## 概要 / Overview

### 日本語

Claude Code はセッションが終わると会話内容をすべて忘れます。`sui-memory` はこの問題を解決します。

セッション終了時（StopHook）に会話の全 Q&A ペアを自動的に SQLite データベースへ保存します。次のセッション開始時には、ユーザーの入力に意味的に近い過去の会話を検索して Claude の文脈に注入します（注入部分は姉妹ツール [kizami](https://github.com/sakuranjunkie-staff/kizami) が担当）。

**何の役に立つのか？**
- 「前回どこまでやったか」を Claude が自分で思い出せるようになる
- 過去に下した設計判断や技術的な方針を再確認できる
- 同じ背景・経緯を何度も説明し直す手間がなくなる
- 数日・数週間ぶりに戻ってきても文脈が途切れない

### English

Claude Code forgets everything when a session ends. `sui-memory` solves this.

On session end (StopHook), it automatically saves all Q&A pairs from the conversation transcript to a SQLite database. At the start of the next session, it retrieves semantically similar past conversations and injects them into Claude's context (the injection part is handled by the companion tool [kizami](https://github.com/sakuranjunkie-staff/kizami)).

**What is it good for?**
- Claude can recall where you left off without being told
- Past design decisions and architectural choices are preserved and retrievable
- No need to re-explain the same context repeatedly
- Seamless continuity even after days or weeks away

---

## 仕組み / How It Works

### 遅延ベクトル化アーキテクチャ / Lazy Vectorization Architecture

StopHook とベクトル化を分離することで、StopHook のフリーズ問題を解消しています。  
By separating StopHook from vectorization, the StopHook freeze issue is eliminated.

```
セッション終了時 (StopHook) ← 非同期・数秒で完了
    ↓
transcript (.jsonl) を読み込む
    ↓
Q&A ペアに分割 (chunker.py)
    ↓
テキストのみ SQLite に保存 (storage.py) ← embedding=NULL
    ↓
FTS5 インデックスは即時更新（キーワード検索はすぐ使える）

────────────────────────────────────────────────

/sr コマンド実行時（任意・手動）
    ↓
未ベクトル化分（embedding=NULL）をまとめて Ruri v3-310m でベクトル化
    ↓
ベクトル検索も有効になる

────────────────────────────────────────────────

セッション開始時 (UserPromptSubmitHook ← kizami が担当)
    ↓
ユーザーの入力プロンプトを受け取る
    ↓
FTS5 キーワード検索 + ベクトル検索 (retriever.py)
    ↓
RRF (Reciprocal Rank Fusion) でスコア統合
    ↓
時間減衰を適用（半減期 30 日）
    ↓
上位 5 件をシステムプロンプトに注入 (← kizami が出力)
```

### 検索の仕組み / How Search Works

| 要素 / Component | 説明 / Description |
|---|---|
| **FTS5 全文検索** | SQLite 組み込みエンジン。trigram トークナイザーで部分一致対応 |
| **ベクトル検索** | [Ruri v3-310m](https://huggingface.co/cl-nagoya/ruri-v3-310m)（日本語特化モデル）+ [sqlite-vec](https://github.com/asg017/sqlite-vec) |
| **RRF 統合** | 両検索の順位スコアを統合して最終順位を決定 |
| **時間減衰** | 古いメモリのスコアを下げる。`score = rrf * 0.5^(経過日数/30)` |

embedding=NULL のチャンクは FTS5 のみでヒットします（ベクトル検索はスキップ）。  
Chunks with `embedding=NULL` are matched by FTS5 only (vector search is skipped gracefully).

---

## ファイル構成 / File Structure

```
sui-memory/
├── src/
│   ├── hook.py         # StopHook エントリーポイント（テキスト保存のみ・高速）
│   ├── chunker.py      # transcript → Q&A チャンク分割
│   ├── embedder.py     # テキスト → ベクトル化（Ruri v3-310m）
│   ├── storage.py      # SQLite 保存・FTS5・ベクトル検索
│   └── retriever.py    # ハイブリッド検索 + RRF + 時間減衰
└── tests/
```

---

## 必要環境 / Requirements

- Python 3.10+
- [uv](https://github.com/astral-sh/uv)
- Claude Code

---

## インストール / Installation

```bash
git clone https://github.com/sakuranjunkie-staff/sui-memory.git
cd sui-memory
uv sync
```

---

## 設定 / Setup

`~/.claude/settings.json` に StopHook を追加します。`async: true` を指定することで Claude の応答をブロックしません。  
Add the StopHook to `~/.claude/settings.json`. Use `async: true` to avoid blocking Claude's responses.

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project /path/to/sui-memory python /path/to/sui-memory/src/hook.py",
            "async": true
          }
        ]
      }
    ]
  }
}
```

`/path/to/sui-memory` を実際のパスに置き換えてください。  
Replace `/path/to/sui-memory` with the actual clone path.

> **セッション開始時のメモリ注入には** 姉妹ツール [kizami](https://github.com/sakuranjunkie-staff/kizami) が必要です。セットで使うことを強く推奨します。  
> **For memory injection at session start**, the companion tool [kizami](https://github.com/sakuranjunkie-staff/kizami) is required. Using both together is strongly recommended.

---

## スラッシュコマンド / Slash Commands

### `/sr` — 手動保存 + ベクトル化

`~/.claude/commands/sr.md` を追加すると `/sr` コマンドが使えるようになります。

```
/sr の処理フロー:
1. 現在セッションのテキストを増分保存（重複スキップ）
2. embedding=NULL の全チャンクをまとめてベクトル化
```

長いセッションでは途中で `/sr` を打つことで、ベクトル検索が常に最新の状態に保たれます。  
ベクトル化は非常に重い処理（Ruri v3-310m、CPU推論）のため、StopHook 側では行わず `/sr` に委ねています。

### `/recall` — 過去の会話を検索

```
/recall                    → 直近 20 件を時系列表示
/recall Prisma移行         → キーワードで直近 7 日を検索
/recall Supabase 30        → 直近 30 日でキーワード検索
/recall "あの話" 0         → 全期間検索（days=0）
```

0件のときは検索範囲を段階的に広げる拡張フローを提示します（1ヶ月/3ヶ月/6ヶ月/1年/全期間）。

---

## データ保存先 / Data Storage

`~/.sui-memory/memory.db` （SQLite）

```sql
CREATE TABLE memories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT    NOT NULL,   -- Claude Code セッション ID
    project        TEXT,               -- プロジェクトパス (cwd)
    project_name   TEXT,               -- プロジェクト名（例: "fx-shield"）
    user_text      TEXT    NOT NULL,   -- ユーザーの発言
    assistant_text TEXT    NOT NULL,   -- Claude の返答
    timestamp      TEXT    NOT NULL,   -- ISO8601 タイムスタンプ
    created_at     REAL    NOT NULL,   -- Unix timestamp（時間減衰計算用）
    embedding      BLOB                -- float32 ベクトル（1024 次元）。/sr 前は NULL
);
```

---

## 検索 API / Search API

他のツールから直接利用できます。  
Can be used directly from other tools:

```python
import sys
sys.path.insert(0, "/path/to/sui-memory/src")
from retriever import search, search_recent, search_by_timerange

# 全期間からハイブリッド検索
results = search("Supabase 移行", limit=5)

# 直近7日以内
results = search_recent("認証の実装", limit=5)

# 直近 N 日以内
results = search_by_timerange("バグ修正", days=14, limit=5)

# 各 result には以下のフィールドが含まれる
# id, session_id, project, project_name, user_text, assistant_text, timestamp, created_at, score
```

---

## kizami との連携 / Integration with kizami

| ツール | 役割 |
|---|---|
| **sui-memory** | 会話の**保存** / Saves conversations |
| **[kizami](https://github.com/sakuranjunkie-staff/kizami)** | 時間経過の把握 + 関連メモリの**注入** / Time awareness + memory injection |

両ツールは `~/.sui-memory/memory.db` を共有します。  
Both tools share `~/.sui-memory/memory.db`.

---

## 動作環境 / Platform Support

| OS | 状態 |
|---|---|
| Windows | ✅ 動作確認済み |
| macOS | 🔧 対応予定 |
| Linux | 🔧 対応予定 |

---

## インスピレーション / Inspiration

アーキテクチャ設計の着想は [noprogllama](https://zenn.dev/noprogllama) 氏の [Zenn 記事](https://zenn.dev/noprogllama/articles/7c24b2c2410213) から得ました。  
Architecture design was inspired by [this article](https://zenn.dev/noprogllama/articles/7c24b2c2410213) by noprogllama.

---

## ライセンス / License

MIT
