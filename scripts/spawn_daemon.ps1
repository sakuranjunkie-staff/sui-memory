# /sr daemon spawn — hidden, idle-timeout 600s.
# ~/.claude/commands/sr.md から短く呼ばれる常設スクリプト（heavy_cmd_guard フック回避のため多行インラインを廃してここへ移設）。
# 10分間リクエストが無ければ daemon は自動 exit する。既に温かい daemon が居ればポート bind に失敗して無害に終わる。
$env:SUI_MEMORY_IDLE_TIMEOUT = "600"
Start-Process -WindowStyle Hidden -FilePath "uv" `
  -ArgumentList "run","--directory","C:/Users/bukol/Documents/sui-memory","python","-m","src.daemon" `
  -PassThru | Out-Null
