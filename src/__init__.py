"""sui-memory パッケージ。

`python -m src.daemon` 経由で daemon を起動するためにこのファイルが必要。
egg-info / pyproject.toml の entry_points は古い venv に反映されない場合があるため、
直接 `-m` 起動できるようにこのパッケージマーカーを置いている。
"""
