"""
recall_dump.py - /recall コマンド用。直近30件のメモリを時系列順で標準出力に出す。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent / "src"))
from storage import get_all

rows = get_all(limit=30)
for r in reversed(rows):
    ts = r.get("timestamp", "")[:10] or "不明"
    u = r.get("user_text", "")[:120].replace("\n", " ")
    a = r.get("assistant_text", "")[:120].replace("\n", " ")
    print(f"[{ts}] U: {u}")
    print(f"       A: {a}")
    print()
