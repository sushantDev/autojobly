from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..accounts import attempts_today, profile_for_user
from ..auth import get_user, user_is_paying
from ..db import add_event, db, utc_now
from ..defaults import DAILY_CAP
from ..paths import CV_DIR
from ..score import detect_ats
from .ashby import apply_ashby
from .boards import apply_greenhouse, apply_lever


def _ensure_browser_path() -> None:
    """Use the real user Playwright cache (not Cursor sandbox paths)."""
    user_cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    current = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if "cursor-sandbox-cache" in current or not current:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(user_cache)


# Statuses we will never try to auto-bypass
HARD_MANUAL = {
    "captcha",
    "recaptcha",
    "login",
    "upwork",
    "djinni",
    "turing",
    "relocate.me",
    "mailto:",
}


def _cv_path(profile: dict[str, Any], user_id: int, job: dict[str, Any]) -> Path:
    """Send the customer's own CV file. A text paste is sent unchanged."""
    del job
    raw = profile.get("cv_path") or ""
    if raw:
        path = Path(raw)
        if path.exists():
            return path
    CV_DIR.mkdir(parents=True, exist_ok=True)
    fallback = CV_DIR / f"{user_id}-cv.txt"
    fallback.write_text(profile.get("cv_text") or "", encoding="utf-8")
    return fallback


def _is_hard_manual(job: dict[str, Any]) -> str | None:
    blob = f"{job.get('url','')} {job.get('notes','')} {job.get('company','')}".lower()
    for token in HARD_MANUAL:
        if token in blob:
            return f"Requires human action ({token}) — cannot auto-bypass"
    if (job.get("status") or "") == "BLOCKED" and "captcha" in (job.get("notes") or "").lower():
        return "CAPTCHA — cannot auto-bypass"
    return None


def apply_job(job: dict[str, Any], profile: dict[str, Any], cv_path: Path, headless: bool = True) -> dict[str, Any]:
    reason = _is_hard_manual(job)
    if reason:
        return {"ok": False, "status": "BLOCKED", "message": reason}

    ats = job.get("ats") or detect_ats(job.get("url") or "")
    submitter = {"ashby": apply_ashby, "greenhouse": apply_greenhouse, "lever": apply_lever}.get(ats or "")
    if submitter is None:
        return {
            "ok": False,
            "status": job.get("status") or "READY",
            "message": f"No auto-apply adapter for ATS={ats or 'unknown'} yet",
        }

    from playwright.sync_api import sync_playwright

    _ensure_browser_path()
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=headless)
        except Exception as exc:
            msg = str(exc)
            if "Executable doesn't exist" in msg:
                return {
                    "ok": False,
                    "status": "BLOCKED",
                    "message": (
                        "Playwright browser missing. Run once:\n"
                        "  cd job_agent && source .venv/bin/activate && "
                        "PLAYWRIGHT_BROWSERS_PATH=\"$HOME/Library/Caches/ms-playwright\" "
                        "playwright install chromium"
                    ),
                }
            raise
        context = browser.new_context()
        page = context.new_page()
        try:
            result = submitter(page, job, profile, cv_path)
        finally:
            browser.close()
    return result


def _guard(user_id: int) -> dict[str, Any] | None:
    user = get_user(user_id)
    if not user_is_paying(user):
        return {
            "ok": False,
            "applied": 0,
            "failed": 0,
            "skipped": 0,
            "results": [],
            "error": "An active subscription is required before auto-apply runs.",
        }
    profile = profile_for_user(user_id)
    if not profile or not profile.get("consent"):
        return {
            "ok": False,
            "applied": 0,
            "failed": 0,
            "skipped": 0,
            "results": [],
            "error": "Confirm your CV and consent before auto-apply runs.",
        }
    return None


def apply_ready_jobs(
    user_id: int,
    limit: int = DAILY_CAP,
    headless: bool = True,
) -> dict[str, Any]:
    """Auto-apply this user's Ashby matches. Stops at 50 attempts per UTC day.

    Never bypasses CAPTCHA or logs into a third-party account.
    """
    blocked = _guard(user_id)
    if blocked:
        return blocked

    profile = profile_for_user(user_id)
    assert profile is not None
    applied = 0
    failed = 0
    skipped = 0
    results: list[dict[str, Any]] = []

    with db() as conn:
        used = attempts_today(conn, user_id)
        remaining = max(0, DAILY_CAP - used)
        take = min(limit, remaining)
        if take <= 0:
            return {
                "ok": True,
                "applied": 0,
                "failed": 0,
                "skipped": 0,
                "results": [],
                "note": f"Daily cap of {DAILY_CAP} auto-apply attempts already used.",
                "profile": profile.get("name"),
            }

        rows = conn.execute(
            """
            SELECT j.id, j.company, j.role, j.url, j.description, j.ats, m.status, m.notes, m.fit_percent, m.id AS match_id
            FROM matches m
            JOIN jobs j ON j.id = m.job_id
            WHERE m.user_id = ?
              AND m.lane = 'auto'
              AND m.status IN ('QUEUED', 'PARTIAL', 'READY')
              AND (
                    j.ats IN ('ashby', 'greenhouse', 'lever')
                    OR j.url LIKE '%ashbyhq.com%'
                    OR j.url LIKE '%greenhouse.io%'
                    OR j.url LIKE '%lever.co%'
                  )
            ORDER BY m.fit_percent DESC
            LIMIT ?
            """,
            (user_id, take),
        ).fetchall()

        if not rows:
            return {
                "ok": True,
                "applied": 0,
                "failed": 0,
                "skipped": 0,
                "results": [],
                "profile": profile.get("name"),
                "note": "No Ashby, Greenhouse, or Lever jobs left to auto-apply. Other matches stay in Apply yourself.",
            }

        for row in rows:
            job = dict(row)
            reason = _is_hard_manual(job)
            if reason:
                skipped += 1
                conn.execute(
                    "UPDATE matches SET status = 'BLOCKED', notes = ?, updated_at = ? WHERE id = ?",
                    (reason, utc_now(), job["match_id"]),
                )
                add_event(conn, job["id"], "auto_apply_result", f"BLOCKED: {reason}", user_id=user_id)
                results.append({"id": job["id"], "company": job["company"], "ok": False, "message": reason})
                continue

            conn.execute(
                "UPDATE matches SET status = 'APPLYING', updated_at = ? WHERE id = ?",
                (utc_now(), job["match_id"]),
            )
            add_event(conn, job["id"], "auto_apply_start", job["url"], user_id=user_id)
            conn.commit()

            result = apply_job(job, profile, _cv_path(profile, user_id, job), headless=headless)
            new_status = result.get("status") or ("APPLIED" if result.get("ok") else "PARTIAL")
            note = result.get("message") or ""
            lane = "auto" if new_status == "APPLIED" else "manual" if new_status in {"BLOCKED", "PARTIAL", "READY"} else "auto"
            conn.execute(
                "UPDATE matches SET status = ?, lane = ?, notes = ?, updated_at = ? WHERE id = ?",
                (new_status, lane, note, utc_now(), job["match_id"]),
            )
            add_event(conn, job["id"], "auto_apply_result", f"{new_status}: {note}", user_id=user_id)
            conn.commit()

            results.append({"id": job["id"], "company": job["company"], **result})
            if result.get("ok"):
                applied += 1
            else:
                failed += 1

    return {
        "ok": True,
        "applied": applied,
        "failed": failed,
        "skipped": skipped,
        "results": results,
        "profile": profile.get("name"),
        "note": f"Tried {len(results)} job(s). Daily cap is {DAILY_CAP}.",
    }


def applied_count(user_id: int) -> int:
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM matches WHERE user_id = ? AND status IN ('APPLIED', 'REPLIED')",
            (user_id,),
        ).fetchone()
    return int(row["c"] if row else 0)


def send_sample(user_id: int, job_id: int, headless: bool = True) -> dict[str, Any]:
    """Submit one real application before payment, then stop."""
    if user_is_paying(get_user(user_id)):
        return {"ok": False, "error": "This account is already paid. Use auto-apply."}
    from ..defaults import FREE_APPLICATIONS

    if applied_count(user_id) >= FREE_APPLICATIONS:
        return {"ok": False, "error": "The free applications were already sent."}
    profile = profile_for_user(user_id)
    if not profile or not profile.get("consent"):
        return {"ok": False, "error": "Confirm the CV and consent first."}
    with db() as conn:
        row = conn.execute(
            """
            SELECT j.id, j.company, j.role, j.url, j.description, j.ats, m.status, m.notes, m.fit_percent, m.id AS match_id
            FROM matches m
            JOIN jobs j ON j.id = m.job_id
            WHERE m.user_id = ? AND j.id = ?
            """,
            (user_id, job_id),
        ).fetchone()
    if not row:
        return {"ok": False, "error": "That job is not on this account."}
    job = dict(row)
    ats = job.get("ats") or detect_ats(job.get("url") or "")
    if ats not in {"ashby", "greenhouse", "lever"}:
        return {"ok": False, "error": "The free application has to be Ashby, Greenhouse, or Lever."}
    result = apply_job(job, profile, _cv_path(profile, user_id, job), headless=headless)
    new_status = result.get("status") or ("APPLIED" if result.get("ok") else "PARTIAL")
    with db() as conn:
        conn.execute(
            "UPDATE matches SET status = ?, notes = ?, updated_at = ? WHERE id = ?",
            (new_status, result.get("message") or "", utc_now(), job["match_id"]),
        )
        add_event(conn, job["id"], "sample_apply", f"{new_status}: {result.get('message') or ''}", user_id=user_id)
    return result


def apply_for_subscribers(limit: int = DAILY_CAP, headless: bool = True) -> dict[str, Any]:
    """Run auto-apply for every user with an active subscription and consent."""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT u.id
            FROM users u
            JOIN profiles p ON p.user_id = u.id
            WHERE u.subscription_status = 'active' AND p.consent = 1
            """
        ).fetchall()
    per_user = []
    applied = 0
    for row in rows:
        user = get_user(int(row["id"]))
        if not user_is_paying(user):
            continue
        result = apply_ready_jobs(int(row["id"]), limit=limit, headless=headless)
        per_user.append({"user_id": row["id"], **{k: result.get(k) for k in ("applied", "failed", "skipped", "error", "note")}})
        applied += int(result.get("applied") or 0)
    return {"applied": applied, "users": per_user}
