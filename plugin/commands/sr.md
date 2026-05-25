現在のセッションのQ&A記録を手動でsui-memoryに保存します。
PCクラッシュや強制終了に備えた随時バックアップ用コマンドです。
テキスト保存 + ベクトル化（embedding=NULLの未処理分もまとめて処理）を行います。

## 重要: 非ブロッキング実行（必須）

save_and_embed の curl は **必ず `run_in_background: true` で実行**すること。
投げたら即「バックグラウンドで処理中」とユーザーに伝え、**会話を続行**する。
完了通知が来たらレスポンスを読んで結果を報告する。
ユーザーの会話をブロックするな。2回投げるな。

## 実行フロー（オンデマンド daemon）

sui-memory daemon を **使う時だけ** 起動して、処理完了後に自動シャットダウンする。
連続叩きはほぼ無いユースケースなので「常駐 + アイドルタイムアウト」より効率的。

### Step 1: daemon 生存確認

```bash
curl -sf -m 2 http://127.0.0.1:7766/health > /dev/null && echo ALIVE || echo DEAD
```

### Step 2-A: ALIVE の場合（既に他用途で起動中）

そのまま処理を投げる。daemon は kill しない（他用途を尊重）。
**`run_in_background: true` で実行し、即座にユーザーに「処理中」と伝えて会話を続行する。**

```bash
curl -s -X POST http://127.0.0.1:7766/save_and_embed -H "Content-Type: application/json" -d '{}'
```

### Step 2-B: DEAD の場合（オンデマンド起動 → 処理 → 自動 exit）

PowerShell で hidden プロセスとして spawn する。
環境変数 `SUI_MEMORY_SHUTDOWN_AFTER=1` で起動するので、処理完了後に daemon が自動 exit する。

```powershell
$env:SUI_MEMORY_SHUTDOWN_AFTER = "1"
Start-Process -WindowStyle Hidden -FilePath "uv" `
  -ArgumentList "run","--directory","C:/Users/bukol/Documents/sui-memory","python","-m","src.daemon" `
  -PassThru | Out-Null
```

PowerShell ツール経由で上記を実行。バックグラウンド spawn なので即制御が戻る。

その後、bash で daemon の起動完了を待機 → 処理を投げる。
**この bash コマンド全体を `run_in_background: true` で実行し、即座にユーザーに「処理中」と伝えて会話を続行する。**

```bash
for i in $(seq 1 60); do
    if curl -sf -m 1 http://127.0.0.1:7766/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done
curl -s -X POST http://127.0.0.1:7766/save_and_embed -H "Content-Type: application/json" -d '{}'
```

レスポンス受信後、daemon は **自動的に exit** する（環境変数 SHUTDOWN_AFTER により）。
完了通知が来たら出力ファイルを読んで結果を報告する。**2回投げるな。**

## レスポンス形式

```json
{"inserted":3,"skipped":12,"embedded":3,"transcript":"<session-id>.jsonl"}
```

- `inserted` = 新規保存件数
- `skipped` = 既存スキップ件数
- `embedded` = ベクトル化件数

## ユーザーへの報告

実行結果を確認して以下のいずれかを端的に伝える:
- 「テキスト保存: N 件 / ベクトル化: M 件」
- 「新規チャンクなし（既に保存済み）」

エラーが出た場合はそのまま報告。

## daemon の手動操作（参考）

明示的に常駐させたい場合（複数の /sr を連続して叩く想定など）:

```bash
# 環境変数なしで起動 → 自動 exit しない常駐 daemon
uv run --directory C:/Users/bukol/Documents/sui-memory python -m src.daemon
```

ポート 7766（環境変数 `SUI_MEMORY_DAEMON_PORT` で上書き可）。バインド 127.0.0.1 のみ。
