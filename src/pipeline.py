from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from .accounts import applied_companies, get_profile_row, scoring_profile, upsert_match
from .db import add_event, db, init_db, upsert_catalog_job, utc_now, utc_today
from .defaults import DAILY_CAP, MIN_FIT_PERCENT
from .discover import discover_candidates, job_key, last_board_report

# A listing missing from every refresh for this long has been taken down.
STALE_DAYS = 5
from .score import can_auto_apply, detect_ats, score_job

MANUAL_URL_TOKENS = ("upwork", "djinni", "turing", "relocate.me", "mailto:")


def _manual_token(job: dict[str, Any]) -> str | None:
    blob = f"{job.get('url', '')} {job.get('notes', '')}".lower()
    for token in MANUAL_URL_TOKENS:
        if token in blob:
            return token
    return None


def _catalog_terms() -> list[str]:
    """Customer skills, the most shared first. Feeds are searched only for the front of this list."""
    from collections import Counter

    from .discover import ordered_terms

    counts: Counter[str] = Counter()
    first: dict[str, str] = {}
    with db() as conn:
        rows = conn.execute("SELECT skills FROM profiles").fetchall()
    for row in rows:
        for part in (row["skills"] or "").split(","):
            skill = part.strip()
            if skill:
                counts[skill.lower()] += 1
                first.setdefault(skill.lower(), skill)
    return ordered_terms([first[key] for key, _count in counts.most_common()])


def _profile_countries() -> list[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT location_country FROM profiles WHERE location_country IS NOT NULL AND location_country != ''"
        ).fetchall()
    return [row["location_country"] for row in rows]


def refresh_catalog(limit: int = 150, terms: list[str] | None = None) -> int:
    """Fetch remote listings once into the shared jobs table."""
    init_db()
    discovered = discover_candidates(
        limit=limit,
        terms=terms or _catalog_terms(),
        countries=_profile_countries(),
    )
    with db() as conn:
        for job in discovered:
            job["ats"] = job.get("ats") or detect_ats(job.get("url") or "")
            upsert_catalog_job(conn, job)
        detail = "; ".join(last_board_report())
        add_event(conn, None, "catalog_refresh", f"discovered={len(discovered)}; {detail}"[:4000])
    return len(discovered)


def score_user(user_id: int) -> dict[str, Any]:
    """Score the shared catalog for one user and keep their top daily_cap matches."""
    init_db()
    row = get_profile_row(user_id)
    if not row:
        return {"user_id": user_id, "shortlisted": 0, "skipped": 0, "error": "no profile"}

    today = utc_today()
    with db() as conn:
        companies = applied_companies(conn, user_id)
        profile = scoring_profile(row, companies)
        min_fit = int(profile["filters"]["min_fit_percent"])
        cap = int(profile["filters"]["daily_cap"] or DAILY_CAP)
        jobs = conn.execute("SELECT * FROM jobs").fetchall()
        existing_rows = conn.execute(
            "SELECT job_id, status FROM matches WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        existing_status = {int(r["job_id"]): r["status"] for r in existing_rows}

        prepared: list[dict[str, Any]] = []
        skipped = 0
        stale_before = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        for raw in jobs:
            job = dict(raw)
            job["ats"] = job.get("ats") or detect_ats(job.get("url") or "")
            fit, reason = score_job(job, profile)
            job["fit_percent"] = fit
            manual = _manual_token(job)
            if (job.get("updated_at") or "") < stale_before:
                lane, status = "skip", "SKIP"
                notes = f"Not on the company board for {STALE_DAYS} days. It is probably closed."
                skipped += 1
            elif fit <= 0 or fit < min_fit:
                lane, status, notes = "skip", "SKIP", reason if fit <= 0 else f"Fit {fit}% < {min_fit}% — {reason}"
                skipped += 1
            elif manual:
                lane, status = "manual", "READY"
                notes = f"{reason} | needs you ({manual})"
            elif can_auto_apply(job, profile):
                lane, status, notes = "auto", "QUEUED", reason
            else:
                lane, status = "manual", "READY"
                notes = f"{reason} | You send this one. This company site has no automatic form."
            prepared.append(
                {
                    "key": job_key(job),
                    "job_id": job["id"],
                    "fit": fit,
                    "reason": reason,
                    "lane": lane,
                    "status": status,
                    "notes": notes,
                    "preserved": existing_status.get(int(job["id"])) in {"APPLIED", "APPLYING", "PARTIAL", "BLOCKED", "CLOSED", "REPLIED"},
                }
            )

        eligible = [
            item for item in prepared if item["lane"] in {"auto", "manual"} and not item["preserved"]
        ]
        eligible.sort(key=lambda item: (item["fit"], item["lane"] == "auto"), reverse=True)
        # The same opening from two feeds is one application. Already sent counts as taken.
        taken = {item["key"] for item in prepared if item["preserved"]}
        shortlisted_ids: set[int] = set()
        for item in eligible:
            if len(shortlisted_ids) >= cap:
                break
            if item["key"] in taken:
                continue
            taken.add(item["key"])
            shortlisted_ids.add(item["job_id"])
        queued_manual = 0

        for item in prepared:
            if item["job_id"] in shortlisted_ids:
                item["shortlist_date"] = today
                if item["lane"] == "manual":
                    queued_manual += 1
            elif item["lane"] != "skip":
                item["status"] = "SKIP"
                item["lane"] = "skip"
                item["notes"] = (item["notes"] or "") + " | below daily shortlist cut"
                item["shortlist_date"] = None
                skipped += 1
            else:
                item["shortlist_date"] = None

            upsert_match(
                conn,
                user_id,
                item["job_id"],
                fit_percent=item["fit"],
                reason=item["reason"],
                lane=item["lane"],
                status=item["status"],
                shortlist_date=item["shortlist_date"],
                notes=item["notes"],
            )

        conn.execute(
            """
            INSERT INTO runs (
                user_id, run_date, discovered, shortlisted, applied, skipped, queued_manual, notes, created_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                user_id,
                today,
                len(jobs),
                len(shortlisted_ids),
                skipped,
                queued_manual,
                "score",
                utc_now(),
            ),
        )
        add_event(
            conn,
            None,
            "scored",
            f"shortlisted={len(shortlisted_ids)} skipped={skipped}",
            user_id=user_id,
        )

    return {
        "user_id": user_id,
        "shortlisted": len(shortlisted_ids),
        "skipped": skipped,
        "queued_manual": queued_manual,
        "date": today,
    }


_REFRESH_LOCK = threading.Lock()


def refresh_and_score(user_id: int, terms: list[str] | None = None) -> None:
    """Search the boards for these skills, then rescore this customer.

    Runs after the page has answered. One search runs at a time; a second request waits for it
    and rescores against what it found instead of fetching every board again.
    """
    if _REFRESH_LOCK.acquire(blocking=False):
        try:
            refresh_catalog(terms=terms)
        except Exception as exc:
            with db() as conn:
                add_event(conn, None, "catalog_refresh_failed", str(exc)[:500], user_id=user_id)
        finally:
            _REFRESH_LOCK.release()
    else:
        with _REFRESH_LOCK:
            pass
    score_user(user_id)


def score_all_profiles() -> list[dict[str, Any]]:
    init_db()
    with db() as conn:
        rows = conn.execute("SELECT user_id FROM profiles").fetchall()
    return [score_user(int(row["user_id"])) for row in rows]


def run_daily(dry_run_apply: bool = True, discover_limit: int = 150) -> dict[str, Any]:
    """Discover once and score every saved profile. Does not submit applications."""
    del dry_run_apply  # kept so older CLI flags still parse
    try:
        discovered = refresh_catalog(discover_limit)
    except Exception as exc:
        discovered = 0
        discover_error = str(exc)
    else:
        discover_error = ""

    scored = score_all_profiles()
    return {
        "discovered": discovered,
        "users": len(scored),
        "shortlisted": sum(item.get("shortlisted", 0) for item in scored),
        "applied": 0,
        "dry_run_apply": True,
        "date": utc_today(),
        "error": discover_error,
        "min_fit_percent": MIN_FIT_PERCENT,
    }


def run_subscribers(discover_limit: int = 150, apply_limit: int = DAILY_CAP) -> dict[str, Any]:
    """Daily worker: refresh catalog, score everyone, auto-apply paying users."""
    result = run_daily(dry_run_apply=True, discover_limit=discover_limit)
    from .apply.runner import apply_for_subscribers

    apply_result = apply_for_subscribers(limit=apply_limit, headless=True)
    result["applied"] = apply_result.get("applied", 0)
    result["apply"] = apply_result
    result["dry_run_apply"] = False
    return result
