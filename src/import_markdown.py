from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .db import add_event, db, init_db, upsert_job
from .paths import JOB_WORKSPACE
from .score import detect_ats


ROW_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]*)\|"
)


def parse_shortlist(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line.strip())
        if not m:
            continue
        num, company, role, fit, comp, location, url, status, notes = m.groups()
        company = company.strip()
        if company.lower() == "company":
            continue
        fit_s = fit.strip().replace("%", "")
        try:
            fit_i = int(re.search(r"\d+", fit_s).group()) if re.search(r"\d+", fit_s) else 0
        except Exception:
            fit_i = 0
        status_n = status.strip().upper()
        status_map = {
            "SKIPPED": "SKIP",
            "SKIP": "SKIP",
            "APPLIED": "APPLIED",
            "CLOSED": "CLOSED",
            "BLOCKED": "BLOCKED",
            "PARTIAL": "PARTIAL",
            "READY": "READY",
            "QUEUED": "QUEUED",
            "APPLYING": "APPLYING",
        }
        status_n = status_map.get(status_n, status_n)
        url = url.strip()
        rows.append(
            {
                "external_id": f"shortlist50:{num.strip()}",
                "company": company,
                "role": role.strip(),
                "url": url,
                "source": "shortlist_50",
                "location": location.strip(),
                "description": "",
                "fit_percent": fit_i,
                "comp_band": comp.strip(),
                "status": status_n,
                "notes": notes.strip(),
                "ats": detect_ats(url),
                "shortlist_date": "2026-10-22",
            }
        )
    return rows


def import_shortlist_50() -> int:
    init_db()
    path = JOB_WORKSPACE / "shortlist_50.md"
    if not path.exists():
        return 0
    jobs = parse_shortlist(path)
    with db() as conn:
        for job in jobs:
            jid = upsert_job(conn, job)
            add_event(conn, jid, "imported", "from shortlist_50.md")
    return len(jobs)