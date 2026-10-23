"""Host and cookie settings. Render sets RENDER_EXTERNAL_URL, so a custom domain is not required."""

from __future__ import annotations

import os


def public_base() -> str:
    for key in ("PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL", "APP_BASE_URL"):
        value = (os.environ.get(key) or "").strip().rstrip("/")
        if value:
            return value
    return "http://127.0.0.1:8787"


def is_public_https() -> bool:
    return public_base().startswith("https://")
