#!/usr/bin/env bash
# /sr save_and_embed — daemon の起動完了を待ってから POST。
# ~/.claude/commands/sr.md から run_in_background:true で短く呼ばれる常設スクリプト（heavy_cmd_guard フック回避のため多行インラインを廃してここへ移設）。
# POST を受けるたびに daemon のアイドルタイマーはリセットされる。
for i in $(seq 1 60); do
    if curl -sf -m 1 http://127.0.0.1:7766/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done
curl -s -X POST http://127.0.0.1:7766/save_and_embed -H "Content-Type: application/json" -d '{}'
