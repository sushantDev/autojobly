from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .paths import PROFILE_PATH


def load_profile(path: Path | None = None) -> dict[str, Any]:
    p = path or PROFILE_PATH
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}