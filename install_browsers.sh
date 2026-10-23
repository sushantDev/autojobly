#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
unset PLAYWRIGHT_BROWSERS_PATH
export PLAYWRIGHT_BROWSERS_PATH="$HOME/Library/Caches/ms-playwright"
playwright install chromium
echo "Browsers ready at $PLAYWRIGHT_BROWSERS_PATH"