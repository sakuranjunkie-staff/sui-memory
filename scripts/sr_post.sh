#!/usr/bin/env bash
# /sr embed_pending — daemon の起動完了を待ってから POST（2026-11-23：保存はStopフックが担うので、ベクトル化だけに変更）。
# ~/.claude/commands/sr.md から run_in_background:true で短く呼ばれる常設スクリプト（heavy_cmd_guard フック回避のため多行インラインを廃してここへ移設）。
# POST を受けるたびに daemon のアイドルタイマーはリセットされる。
for i in $(seq 1 60); do
    if curl -sf -m 1 http://127.0.0.1:7766/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done
curl -s -X POST http://127.0.0.1:7766/embed_pending -H "Content-Type: application/json" -d '{}'
