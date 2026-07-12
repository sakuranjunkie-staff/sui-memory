"""
test_scripts.py - スクリプト型テストをpytestに橋渡しするラッパー

conftest.py の collect_ignore で直接収集から外したスクリプト型テスト
（print + PASS/FAILカウンタ + sys.exit方式）を、サブプロセスとして実行して
終了コード0を検証する。FAILがあればスクリプトがexit 1するので、
pytest側でも正しく失敗として報告される。

各スクリプトは従来どおり単体でも実行できる:
  uv run python tests/test_recall_logic.py
"""
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent

# 橋渡し対象のスクリプト型テスト（conftest.py の collect_ignore と対で管理する）
SCRIPTS = [
    "test_chunker_v2.py",
    "test_e2e_score_report.py",
    "test_incremental_save.py",
    "test_lazy_embed.py",
    "test_recall_logic.py",
    "test_storage_review.py",
]


@pytest.mark.parametrize("script", SCRIPTS)
def test_script(script: str) -> None:
    """スクリプトをサブプロセス実行し、終了コード0（全PASS）を検証する"""
    proc = subprocess.run(
        [sys.executable, str(TESTS_DIR / script)],
        capture_output=True,
        timeout=600,  # embedderロードを含むe2eでも十分な上限
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    assert proc.returncode == 0, f"{script} が失敗 (exit={proc.returncode})\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
