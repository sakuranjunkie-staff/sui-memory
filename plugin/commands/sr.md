現在のセッションのQ&A記録を手動でsui-memoryに保存します。
PCクラッシュや強制終了に備えた随時バックアップ用コマンドです。
テキスト保存 + ベクトル化（embedding=NULLの未処理分もまとめて処理）を行います。

## 重要: 非ブロッキング実行（必須）

save_and_embed の curl は **必ず `run_in_background: true` で実行**すること。
投げたら即「バックグラウンドで処理中」とユーザーに伝え、**会話を続行**する。
完了通知が来たらレスポンスを読んで結果を報告する。
ユーザーの会話をブロックするな。2回投げるな。

## 実行フロー（常駐 + アイドルタイムアウト daemon）

sui-memory daemon を **アイドルタイムアウト付きで常駐**させる。モデル（ruri-v3-310m）の
コールドロードは ~9秒かかるため、毎回使い捨てにすると /sr のたびに9秒待つことになる。
そこで一度起動したら10分だけ居座らせ、連続で叩けば2回目以降は温まった daemon が即応答、
無操作が10分続けば 1.2GB のモデルを抱えたまま居座らずに自動 exit する。

**health check の垂れ流しナレーションはするな。** 常に「spawn → 待機ループ → POST」の
一本道で処理する。待機ループが「既に生きている」ケースを吸収する（生きていれば即 break で
温かい daemon に即 POST）。重複 spawn はポート bind に失敗して即 exit するだけで無害。
**「生存確認 → DEAD」のような途中経過を垂れ流すな。**

### Step 1: daemon を hidden で spawn（PowerShell）

環境変数 `SUI_MEMORY_IDLE_TIMEOUT=600` で起動する。10分間リクエストが無ければ自動 exit する。

```powershell
$env:SUI_MEMORY_IDLE_TIMEOUT = "600"
Start-Process -WindowStyle Hidden -FilePath "uv" `
  -ArgumentList "run","--directory","C:/Users/bukol/Documents/sui-memory","python","-m","src.daemon" `
  -PassThru | Out-Null
```

PowerShell ツール経由で上記を実行。バックグラウンド spawn なので即制御が戻る。
（既に温かい daemon が居れば、この spawn はポート bind に失敗して無害に終わる。）

### Step 2: 起動完了を待機 → POST（bash・バックグラウンド）

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

POST を受けるたびに daemon のアイドルタイマーはリセットされる。無操作が10分続くと自動 exit する。
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
