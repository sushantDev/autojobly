from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from .paths import db_path, ensure_dirs


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    ensure_dirs()
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT UNIQUE,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT,
    location TEXT,
    description TEXT,
    fit_percent INTEGER DEFAULT 0,
    comp_band TEXT,
    status TEXT NOT NULL DEFAULT 'QUEUED',
    notes TEXT,
    ats TEXT,
    listing_url TEXT,
    shortlist_date TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    user_id INTEGER,
    event_type TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    run_date TEXT NOT NULL,
    discovered INTEGER DEFAULT 0,
    shortlisted INTEGER DEFAULT 0,
    applied INTEGER DEFAULT 0,
    skipped INTEGER DEFAULT 0,
    queued_manual INTEGER DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_shortlist ON jobs(shortlist_date);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    subscription_status TEXT NOT NULL DEFAULT 'inactive',
    paid_until TEXT,
    oauth_provider TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    email TEXT,
    phone TEXT,
    linkedin TEXT,
    github TEXT,
    portfolio TEXT,
    location_country TEXT,
    ask_usd_monthly INTEGER,
    ask_eur_monthly INTEGER,
    skills TEXT,
    cv_text TEXT,
    cv_path TEXT,
    refined_cv TEXT,
    consent INTEGER NOT NULL DEFAULT 0,
    timezone TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    job_id INTEGER NOT NULL,
    fit_percent INTEGER DEFAULT 0,
    reason TEXT,
    lane TEXT NOT NULL DEFAULT 'skip',
    status TEXT NOT NULL DEFAULT 'QUEUED',
    shortlist_date TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, job_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_matches_user_status ON matches(user_id, status);
CREATE INDEX IF NOT EXISTS idx_matches_user_shortlist ON matches(user_id, shortlist_date);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount_usd INTEGER NOT NULL DEFAULT 9,
    status TEXT NOT NULL DEFAULT 'needs_link',
    khalti_url TEXT,
    reference TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id, status);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    event_cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    if "user_id" not in event_cols:
        conn.execute("ALTER TABLE events ADD COLUMN user_id INTEGER")
    run_cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    if "user_id" not in run_cols:
        conn.execute("ALTER TABLE runs ADD COLUMN user_id INTEGER")
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if user_cols and "paid_until" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN paid_until TEXT")
    if user_cols and "dodo_customer_id" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN dodo_customer_id TEXT")
    if user_cols and "dodo_subscription_id" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN dodo_subscription_id TEXT")
    if user_cols and "oauth_provider" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN oauth_provider TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_events (
            webhook_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    profile_cols = {row[1] for row in conn.execute("PRAGMA table_info(profiles)")}
    if profile_cols and "years_experience" not in profile_cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN years_experience INTEGER")
    if profile_cols and "needs_sponsorship" not in profile_cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN needs_sponsorship TEXT")
    job_cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if job_cols and "listing_url" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN listing_url TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id)")


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def upsert_job(conn: sqlite3.Connection, job: dict[str, Any]) -> int:
    now = utc_now()
    existing = conn.execute(
        "SELECT id FROM jobs WHERE external_id = ? OR (company = ? AND role = ? AND url = ?)",
        (
            job.get("external_id"),
            job["company"],
            job["role"],
            job["url"],
        ),
    ).fetchone()

    if existing:
        job_id = existing["id"]
        conn.execute(
            """
            UPDATE jobs SET
                company=?, role=?, url=?, source=?, location=?, description=?,
                fit_percent=?, comp_band=?, status=COALESCE(?, status), notes=COALESCE(?, notes),
                ats=?, shortlist_date=COALESCE(?, shortlist_date), updated_at=?
            WHERE id=?
            """,
            (
                job["company"],
                job["role"],
                job["url"],
                job.get("source"),
                job.get("location"),
                job.get("description"),
                job.get("fit_percent", 0),
                job.get("comp_band"),
                job.get("status"),
                job.get("notes"),
                job.get("ats"),
                job.get("shortlist_date"),
                now,
                job_id,
            ),
        )
        return job_id

    cur = conn.execute(
        """
        INSERT INTO jobs (
            external_id, company, role, url, source, location, description,
            fit_percent, comp_band, status, notes, ats, shortlist_date, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job.get("external_id"),
            job["company"],
            job["role"],
            job["url"],
            job.get("source"),
            job.get("location"),
            job.get("description"),
            job.get("fit_percent", 0),
            job.get("comp_band"),
            job.get("status", "QUEUED"),
            job.get("notes"),
            job.get("ats"),
            job.get("shortlist_date"),
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def add_event(
    conn: sqlite3.Connection,
    job_id: Optional[int],
    event_type: str,
    message: str = "",
    user_id: Optional[int] = None,
) -> None:
    conn.execute(
        "INSERT INTO events (job_id, event_type, message, created_at, user_id) VALUES (?, ?, ?, ?, ?)",
        (job_id, event_type, message, utc_now(), user_id),
    )


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def upsert_catalog_job(conn: sqlite3.Connection, job: dict[str, Any]) -> int:
    """Insert or refresh a shared listing. Does not touch per-user match status."""
    now = utc_now()
    existing = conn.execute(
        "SELECT id FROM jobs WHERE external_id = ? OR url = ?",
        (job.get("external_id"), job["url"]),
    ).fetchone()
    if existing:
        job_id = int(existing["id"])
        conn.execute(
            """
            UPDATE jobs SET
                company=?, role=?, url=?, source=?, location=?, description=?,
                comp_band=?, ats=?, listing_url=?, updated_at=?
            WHERE id=?
            """,
            (
                job["company"],
                job["role"],
                job["url"],
                job.get("source"),
                job.get("location"),
                job.get("description"),
                job.get("comp_band"),
                job.get("ats"),
                job.get("listing_url"),
                now,
                job_id,
            ),
        )
        return job_id

    cur = conn.execute(
        """
        INSERT INTO jobs (
            external_id, company, role, url, source, location, description,
            fit_percent, comp_band, status, notes, ats, listing_url, shortlist_date, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 'QUEUED', NULL, ?, ?, NULL, ?, ?)
        """,
        (
            job.get("external_id"),
            job["company"],
            job["role"],
            job["url"],
            job.get("source"),
            job.get("location"),
            job.get("description"),
            job.get("comp_band"),
            job.get("ats"),
            job.get("listing_url"),
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def list_jobs(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 200,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        if status == "needs_you":
            clauses.append("status IN ('PARTIAL', 'BLOCKED', 'READY', 'APPLYING')")
        else:
            clauses.append("status = ?")
            params.append(status)
    if q:
        clauses.append("(company LIKE ? OR role LIKE ? OR notes LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    return list(
        conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY fit_percent DESC, updated_at DESC LIMIT ?",
            params,
        )
    )


def counts_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM jobs GROUP BY status"
    ).fetchall()
    return {r["status"]: r["c"] for r in rows}


def recent_runs(conn: sqlite3.Connection, limit: int = 14) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    )