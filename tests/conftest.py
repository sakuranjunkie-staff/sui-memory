# スクリプト型テスト（sys.stdout乗っ取り＋モジュール直下で本体実行＋sys.exit）を
# pytestの直接収集から除外する。importしただけで本体が走り、stdoutの乗っ取りが
# pytestのcaptureを壊して一括実行がクラッシュしていた（2026/7/12修正）。
# これらは test_scripts.py がサブプロセスとして実行し、終了コードで合否を橋渡しする。
collect_ignore = [
    "test_chunker_v2.py",
    "test_e2e_score_report.py",
    "test_incremental_save.py",
    "test_lazy_embed.py",
    "test_recall_logic.py",
    "test_storage_review.py",
]
