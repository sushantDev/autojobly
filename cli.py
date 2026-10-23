#!/usr/bin/env python3
"""CLI for the hybrid job agent."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.db import init_db  # noqa: E402
from src.import_markdown import import_shortlist_50  # noqa: E402
from src.paths import db_path  # noqa: E402
from src.pipeline import run_daily, run_subscribers  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Job apply agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="Create SQLite schema")
    sub.add_parser("import-shortlist", help="Import shortlist_50.md into DB")

    p_run = sub.add_parser("run-daily", help="Discover + score every saved profile (no submit)")
    p_run.add_argument("--apply", action="store_true", help="Also auto-apply active subscribers")
    p_run.add_argument("--limit", type=int, default=150)

    sub.add_parser("run-subscribers", help="Discover, score, and auto-apply paying users (cap 50)")

    p_apply = sub.add_parser("auto-apply", help="Auto-apply Ashby jobs (no CAPTCHA/login bypass)")
    p_apply.add_argument("--user-id", type=int, default=0, help="One user. Omit to run every active subscriber")
    p_apply.add_argument("--limit", type=int, default=50)
    p_apply.add_argument("--headed", action="store_true", help="Show browser window")

    sub.add_parser("serve", help="Print portal start command")

    args = parser.parse_args()

    if args.cmd == "init-db":
        init_db()
        print("DB ready:", db_path())
    elif args.cmd == "import-shortlist":
        n = import_shortlist_50()
        print(f"Imported {n} jobs from shortlist_50.md")
    elif args.cmd == "run-daily":
        if args.apply:
            result = run_subscribers(discover_limit=args.limit)
        else:
            result = run_daily(discover_limit=args.limit)
        print(json.dumps(result, indent=2))
    elif args.cmd == "run-subscribers":
        result = run_subscribers()
        print(json.dumps(result, indent=2))
    elif args.cmd == "auto-apply":
        from src.apply.runner import apply_for_subscribers, apply_ready_jobs

        if args.user_id:
            result = apply_ready_jobs(args.user_id, limit=args.limit, headless=not args.headed)
        else:
            result = apply_for_subscribers(limit=args.limit, headless=not args.headed)
        print(json.dumps(result, indent=2))
    elif args.cmd == "serve":
        print("cd", ROOT)
        print("uvicorn portal.app:app --reload --port 8787")
        print("Open http://127.0.0.1:8787")


if __name__ == "__main__":
    main()