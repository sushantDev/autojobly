"""Per-user profiles and match queries."""

from __future__ import annotations

import re
from typing import Any, Optional

from .cv import refine_cv
from .db import db, utc_now, utc_today
from .defaults import ATS_AUTO_APPLY, DAILY_CAP, MIN_FIT_PERCENT, SKIP_GEO_PHRASES

PRESERVE_STATUSES = {"APPLIED", "APPLYING", "PARTIAL", "BLOCKED", "CLOSED", "REPLIED"}
NEEDS_YOU = ("PARTIAL", "BLOCKED", "READY", "APPLYING")


def _skills_list(raw: str | None) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def get_profile_row(user_id: int) -> Optional[dict[str, Any]]:
    with db() as conn:
        row = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def save_profile(user_id: int, fields: dict[str, Any]) -> dict[str, Any]:
    skills = fields.get("skills") or []
    if isinstance(skills, str):
        skills_s = skills.strip()
    else:
        skills_s = ", ".join(s.strip() for s in skills if str(s).strip())
    refined = refine_cv({**fields, "skills": _skills_list(skills_s)})
    now = utc_now()
    payload = (
        fields.get("name") or "",
        fields.get("email") or "",
        fields.get("phone") or "",
        fields.get("linkedin") or "",
        fields.get("github") or "",
        fields.get("portfolio") or "",
        fields.get("location_country") or "",
        int(fields["ask_usd_monthly"]) if fields.get("ask_usd_monthly") else None,
        int(fields["ask_eur_monthly"]) if fields.get("ask_eur_monthly") else None,
        skills_s,
        fields.get("cv_text") or "",
        fields.get("cv_path") or "",
        refined,
        1 if fields.get("consent") else 0,
        fields.get("timezone") or "",
        int(fields["years_experience"]) if str(fields.get("years_experience") or "").isdigit() else None,
        fields.get("needs_sponsorship") if fields.get("needs_sponsorship") in {"yes", "no"} else "",
        now,
        user_id,
    )
    with db() as conn:
        existing = conn.execute("SELECT user_id FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE profiles SET
                    name=?, email=?, phone=?, linkedin=?, github=?, portfolio=?,
                    location_country=?, ask_usd_monthly=?, ask_eur_monthly=?, skills=?,
                    cv_text=?, cv_path=?, refined_cv=?, consent=?, timezone=?,
                    years_experience=?, needs_sponsorship=?, updated_at=?
                WHERE user_id=?
                """,
                payload,
            )
        else:
            conn.execute(
                """
                INSERT INTO profiles (
                    name, email, phone, linkedin, github, portfolio, location_country,
                    ask_usd_monthly, ask_eur_monthly, skills, cv_text, cv_path, refined_cv,
                    consent, timezone, years_experience, needs_sponsorship, updated_at, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        row = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row)


def applied_companies(conn, user_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT j.company
        FROM matches m
        JOIN jobs j ON j.id = m.job_id
        WHERE m.user_id = ? AND m.status = 'APPLIED'
        """,
        (user_id,),
    ).fetchall()
    return [r["company"] for r in rows]


def scoring_profile(row: dict[str, Any], companies: list[str]) -> dict[str, Any]:
    skills = _skills_list(row.get("skills"))
    cv_low = (row.get("cv_text") or "").lower()
    leadership = any(token in cv_low for token in ("tech lead", "engineering manager", "team lead", " cto", "director"))
    return {
        "name": row.get("name") or "",
        "email": row.get("email") or "",
        "phone": row.get("phone") or "",
        "linkedin": row.get("linkedin") or "",
        "github": row.get("github") or "",
        "portfolio": row.get("portfolio") or "",
        "location_country": row.get("location_country") or "",
        "cv_path": row.get("cv_path") or "",
        "cv_text": row.get("cv_text") or "",
        "refined_cv": row.get("refined_cv") or "",
        "consent": bool(row.get("consent")),
        "years_experience": row.get("years_experience") or "",
        "needs_sponsorship": row.get("needs_sponsorship") or "",
        "compensation": {
            "ask_usd_monthly": row.get("ask_usd_monthly") or 0,
            "ask_eur_monthly": row.get("ask_eur_monthly") or 0,
            "preference": "B2B",
        },
        "stack": {
            "primary": skills,
            "secondary": [],
            "leadership": leadership,
        },
        "filters": {
            "min_fit_percent": MIN_FIT_PERCENT,
            "daily_cap": DAILY_CAP,
            "skip_geo_phrases": list(SKIP_GEO_PHRASES),
            "prefer_keywords": [s.lower() for s in skills],
            "ats_auto_apply": list(ATS_AUTO_APPLY),
        },
        "already_applied_companies": companies,
    }


def profile_for_user(user_id: int) -> Optional[dict[str, Any]]:
    row = get_profile_row(user_id)
    if not row:
        return None
    with db() as conn:
        companies = applied_companies(conn, user_id)
    return scoring_profile(row, companies)


def _present(row: Any) -> dict[str, Any]:
    from .discover import board_display_name, plain_text, source_label

    job = dict(row)
    job["source_label"] = source_label(job.get("source") or "")
    if job.get("company"):
        job["company"] = board_display_name(job["company"])
    if job.get("description"):
        job["description"] = plain_text(job["description"])
    return job


def _match_select() -> str:
    return """
        SELECT
            j.id AS id,
            m.id AS match_id,
            j.company, j.role, j.url, j.source, j.location, j.description,
            j.comp_band, j.ats, j.listing_url,
            m.fit_percent, m.reason, m.lane, m.status, m.notes,
            m.shortlist_date, m.updated_at, m.user_id
        FROM matches m
        JOIN jobs j ON j.id = m.job_id
    """


def list_matches(
    user_id: int,
    status: str | None = None,
    q: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses = ["m.user_id = ?"]
    params: list[Any] = [user_id]
    if not status:
        clauses.append("m.status != 'SKIP'")
    elif status:
        if status == "needs_you":
            clauses.append("m.status IN ('PARTIAL', 'BLOCKED', 'READY', 'APPLYING')")
        else:
            clauses.append("m.status = ?")
            params.append(status)
    if q:
        clauses.append("(j.company LIKE ? OR j.role LIKE ? OR m.notes LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    params.append(limit)
    sql = f"{_match_select()} WHERE {' AND '.join(clauses)} ORDER BY m.fit_percent DESC, m.updated_at DESC LIMIT ?"
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_present(r) for r in rows]


def counts_for_user(user_id: int) -> dict[str, int]:
    with db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM matches WHERE user_id = ? GROUP BY status",
            (user_id,),
        ).fetchall()
    return {r["status"]: r["c"] for r in rows}


def get_user_job(user_id: int, job_id: int) -> Optional[dict[str, Any]]:
    with db() as conn:
        row = conn.execute(
            f"{_match_select()} WHERE m.user_id = ? AND j.id = ?",
            (user_id, job_id),
        ).fetchone()
    return _present(row) if row else None


def application_was_sent(user_id: int, job_id: int) -> bool:
    """True when this account's application was submitted by Autojobly, not marked by hand."""
    with db() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM events
            WHERE user_id = ? AND job_id = ?
              AND event_type IN ('auto_apply_result', 'sample_apply')
              AND message LIKE 'APPLIED:%'
            LIMIT 1
            """,
            (user_id, job_id),
        ).fetchone()
    return row is not None


def public_apply_stats() -> tuple[int, int]:
    """Replies and sends for the landing page.

    Sent means Autojobly submitted the form. The operator and example.com test
    accounts are left out.
    """
    from .billing import owner_email

    owner = owner_email()
    with db() as conn:
        sent = conn.execute(
            """
            SELECT COUNT(*) AS c FROM (
                SELECT DISTINCT e.user_id, e.job_id
                FROM events e
                JOIN users u ON u.id = e.user_id
                WHERE e.event_type IN ('auto_apply_result', 'sample_apply')
                  AND e.message LIKE 'APPLIED:%'
                  AND lower(u.email) != ?
                  AND lower(u.email) NOT LIKE '%@example.com'
                  AND lower(u.email) NOT LIKE '%@example.org'
                  AND lower(u.email) NOT LIKE '%@example.net'
            )
            """,
            (owner,),
        ).fetchone()
        replies = conn.execute(
            """
            SELECT COUNT(*) AS c FROM (
                SELECT DISTINCT m.user_id, m.job_id
                FROM matches m
                JOIN users u ON u.id = m.user_id
                JOIN events e ON e.user_id = m.user_id AND e.job_id = m.job_id
                WHERE m.status = 'REPLIED'
                  AND e.event_type IN ('auto_apply_result', 'sample_apply')
                  AND e.message LIKE 'APPLIED:%'
                  AND lower(u.email) != ?
                  AND lower(u.email) NOT LIKE '%@example.com'
                  AND lower(u.email) NOT LIKE '%@example.org'
                  AND lower(u.email) NOT LIKE '%@example.net'
            )
            """,
            (owner,),
        ).fetchone()
    return int(replies["c"] if replies else 0), int(sent["c"] if sent else 0)


def update_match_status(user_id: int, job_id: int, status: str, notes: str = "") -> None:
    with db() as conn:
        if notes:
            conn.execute(
                """
                UPDATE matches SET status = ?, notes = ?, updated_at = ?
                WHERE user_id = ? AND job_id = ?
                """,
                (status, notes, utc_now(), user_id, job_id),
            )
        else:
            conn.execute(
                """
                UPDATE matches SET status = ?, updated_at = ?
                WHERE user_id = ? AND job_id = ?
                """,
                (status, utc_now(), user_id, job_id),
            )


def today_boards(user_id: int) -> dict[str, list[dict[str, Any]]]:
    today = utc_today()
    with db() as conn:
        applied = conn.execute(
            f"""
            {_match_select()}
            WHERE m.user_id = ?
              AND m.status = 'APPLIED'
              AND m.lane = 'auto'
              AND m.updated_at LIKE ?
            ORDER BY m.updated_at DESC
            """,
            (user_id, today + "%"),
        ).fetchall()
        manual = conn.execute(
            f"""
            {_match_select()}
            WHERE m.user_id = ?
              AND m.shortlist_date = ?
              AND m.status NOT IN ('SKIP', 'APPLIED')
              AND (m.lane = 'manual' OR m.status IN ('PARTIAL', 'BLOCKED', 'READY'))
            ORDER BY m.fit_percent DESC
            """,
            (user_id, today),
        ).fetchall()
        waiting = conn.execute(
            """
            SELECT COUNT(*) AS c FROM matches
            WHERE user_id = ? AND shortlist_date = ? AND lane = 'auto'
              AND status IN ('QUEUED', 'APPLYING')
            """,
            (user_id, today),
        ).fetchone()
    return {
        "applied": [_present(r) for r in applied],
        "manual": [_present(r) for r in manual],
        "waiting": int(waiting["c"] if waiting else 0),
        "today": today,
    }


def attempts_today(conn, user_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM events
        WHERE user_id = ? AND event_type = 'auto_apply_start' AND created_at LIKE ?
        """,
        (user_id, utc_today() + "%"),
    ).fetchone()
    return int(row["c"] if row else 0)


def upsert_match(
    conn,
    user_id: int,
    job_id: int,
    *,
    fit_percent: int,
    reason: str,
    lane: str,
    status: str,
    shortlist_date: str | None,
    notes: str,
) -> None:
    existing = conn.execute(
        "SELECT id, status FROM matches WHERE user_id = ? AND job_id = ?",
        (user_id, job_id),
    ).fetchone()
    now = utc_now()
    if existing and existing["status"] in PRESERVE_STATUSES:
        conn.execute(
            "UPDATE matches SET fit_percent = ?, reason = ? WHERE id = ?",
            (fit_percent, reason, existing["id"]),
        )
        return
    if existing:
        conn.execute(
            """
            UPDATE matches SET
                fit_percent=?, reason=?, lane=?, status=?, shortlist_date=?, notes=?, updated_at=?
            WHERE id=?
            """,
            (fit_percent, reason, lane, status, shortlist_date, notes, now, existing["id"]),
        )
        return
    conn.execute(
        """
        INSERT INTO matches (
            user_id, job_id, fit_percent, reason, lane, status, shortlist_date, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, job_id, fit_percent, reason, lane, status, shortlist_date, notes, now, now),
    )


# Skills people search for. A public page exists for each one.
PUBLIC_SKILLS: tuple[tuple[str, str, str], ...] = (
    ("react", "React", r"\breact(?:\.?js)?\b"),
    ("python", "Python", r"\bpython\b"),
    ("javascript", "JavaScript", r"\bjavascript\b"),
    ("typescript", "TypeScript", r"\btypescript\b"),
    ("php", "PHP", r"\bphp\b"),
    ("laravel", "Laravel", r"\blaravel\b"),
    ("java", "Java", r"\bjava\b"),
    ("golang", "Go", r"\b(?:golang|go)\b"),
    ("flutter", "Flutter", r"\bflutter\b"),
    ("vue", "Vue", r"\bvue(?:\.?js)?\b"),
    ("nodejs", "Node.js", r"\bnode(?:\.?js)?\b"),
    ("django", "Django", r"\bdjango\b"),
    ("ruby", "Ruby", r"\brails\b|\bruby\b"),
    ("nextjs", "Next.js", r"\bnext\.?js\b"),
)


def public_skill(slug: str) -> tuple[str, str, str] | None:
    key = (slug or "").strip().lower()
    for item in PUBLIC_SKILLS:
        if item[0] == key:
            return item
    return None


def list_public_roles(skill: str | None = None, limit: int = 80) -> list[dict[str, Any]]:
    """Open listings anyone can read. No scores, no emails, no CV text."""
    chosen = public_skill(skill) if skill else None
    with db() as conn:
        rows = conn.execute(
            """
            SELECT company, role, location, url, ats, source
            FROM jobs
            WHERE url IS NOT NULL AND url != ''
            ORDER BY updated_at DESC
            LIMIT 800
            """
        ).fetchall()
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        role = row["role"] or ""
        if chosen and not re.search(chosen[2], role, re.I):
            continue
        key = f"{(row['company'] or '').lower()}|{role.lower()}"
        if key in seen:
            continue
        seen.add(key)
        jobs.append(
            {
                "company": row["company"],
                "role": role,
                "location": row["location"] or "",
                "url": row["url"],
                "ats": row["ats"] or "",
                "source": row["source"] or "",
            }
        )
        if len(jobs) >= limit:
            break
    return jobs
