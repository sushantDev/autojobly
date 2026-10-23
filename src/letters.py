"""Cover letters and form answers built only from the customer's CV and profile."""

from __future__ import annotations

import re
from typing import Any

from .defaults import SKILL_PATTERNS

_EMAIL = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_URL = re.compile(r"https?://", re.I)
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")

_NARRATIVE = (
    "cover",
    "letter",
    "motivation",
    "why do you",
    "why are you",
    "about you",
    "about yourself",
    "summary",
    "additional",
    "message",
    "interest",
    "introduce",
    "pitch",
    "anything else",
    "tell us",
    "describe",
)

_SKIP = (
    "how many year",
    "years of",
    "years experience",
    "year of experience",
    "visa",
    "sponsor",
    "authorized to work",
    "legally authorized",
    "work authorization",
    "gender",
    "race",
    "ethnicity",
    "disability",
    "veteran",
    "date of birth",
    "salary expectation",
    "compensation",
)


def _mentions(text: str, keyword: str) -> bool:
    return re.search(rf"\b{re.escape(keyword)}\b", text, re.I) is not None


def _owned_text(profile: dict[str, Any]) -> str:
    skills = (profile.get("stack") or {}).get("primary") or []
    if isinstance(skills, str):
        skills = [part.strip() for part in skills.split(",") if part.strip()]
    return f"{profile.get('cv_text') or ''}\n{' '.join(skills)}"


def shared_keywords(profile: dict[str, Any], job: dict[str, Any] | None = None) -> list[str]:
    """Tools and posting words that already appear on the CV. Nothing else is added."""
    job = job or {}
    from .discover import plain_text

    posting = plain_text(_URL.sub(" ", f"{job.get('role') or ''} {job.get('description') or ''}"))
    owned = _URL.sub(" ", _EMAIL.sub(" ", _owned_text(profile)))
    if not posting.strip() or not owned.strip():
        return []
    found: list[str] = []
    seen: set[str] = set()

    def add(label: str) -> None:
        key = label.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(label)

    for label, pattern in SKILL_PATTERNS:
        if re.search(pattern, posting, re.I) and re.search(pattern, owned, re.I):
            add(label)
    longer = {item.lower() for item in found}
    return [
        item
        for item in found
        if not any(len(other) > len(item) >= 8 and other.startswith(item.lower()) for other in longer)
    ]


def _cv_lines(profile: dict[str, Any], limit: int = 40) -> list[str]:
    name = (profile.get("name") or "").strip().lower()
    lines: list[str] = []
    for raw in (profile.get("cv_text") or "").splitlines():
        line = " ".join(raw.split())
        if len(line) < 24 or len(line) > 220:
            continue
        if _EMAIL.search(line) or _URL.search(line) or _PHONE.search(line):
            continue
        if line.lower() == name:
            continue
        if line not in lines:
            lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def _word_per_line(text: str) -> bool:
    parts = [line.strip() for line in text.splitlines() if line.strip()]
    if len(parts) < 30:
        return False
    short = sum(1 for line in parts if len(line.split()) <= 2)
    return short / len(parts) >= 0.7


def _join_word_lines(text: str, limit: int) -> list[str]:
    """Turn a one-word-per-line CV back into the sentences that were on the page."""
    sentences: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if not current:
            return
        sentence = " ".join(current)
        sentence = re.sub(r"\s+([,.;:!?])", r"\1", sentence)
        sentence = re.sub(r"\(\s+", "(", sentence)
        sentence = re.sub(r"\s+\)", ")", sentence)
        current.clear()
        if len(sentence) < 40 or _EMAIL.search(sentence) or _URL.search(sentence):
            return
        parts = re.split(r" (?=[A-Z][A-Za-z]+(?:/[A-Za-z]+)?:)", sentence) if sentence.count(":") >= 2 else [sentence]
        for part in parts:
            part = part.strip(" |")
            if len(part) < 40 or part in sentences:
                continue
            sentences.append(part)

    for raw in text.splitlines():
        token = " ".join(raw.split())
        if not token:
            continue
        marker = token.lstrip("●•·-*–— ").strip()
        if token[:1] in "●•·" or (token[:1] in "-–—" and marker and marker != token):
            flush()
            token = marker
            if not token:
                continue
        letters = re.sub(r"[^A-Za-z]", "", token)
        if letters.isupper() and len(letters) > 3:
            flush()
            continue
        if letters.isupper() and token.lower().rstrip(":") in {"summary", "experience", "education", "skills", "projects"}:
            flush()
            continue
        if re.fullmatch(r"[-–—•●·*]+", token):
            flush()
            continue
        current.append(token)
        if token.endswith((".", "!", "?")):
            flush()
        if len(sentences) >= limit:
            break
    flush()
    return sentences[:limit]


def _cv_content(profile: dict[str, Any], limit: int = 40) -> list[str]:
    text = profile.get("cv_text") or ""
    if _word_per_line(text):
        return _join_word_lines(text, limit)
    return _cv_lines(profile, limit)


def _is_skill_token(token: str) -> bool:
    text = token.strip(" .")
    if not text:
        return False
    for label, pattern in SKILL_PATTERNS:
        if text.lower() == label.lower() or re.fullmatch(pattern, text, re.I):
            return True
    return False


def _is_skill_list(line: str) -> bool:
    """A line of only known technologies, such as 'React, TypeScript, PostgreSQL.'"""
    text = re.sub(r"^[-–—•●*\s]+", "", line.strip())
    text = re.sub(r"^(skills|tech|technologies|stack)\s*:\s*", "", text, flags=re.I).strip(" .")
    parts = [part.strip(" .") for part in re.split(r",|\band\b", text) if part.strip(" .")]
    return len(parts) >= 2 and all(_is_skill_token(part) for part in parts)


def _is_inventory(line: str) -> bool:
    return _is_skill_list(line) or line.count(",") >= 6 or line.count(":") >= 2


def _is_achievement(line: str) -> bool:
    low = re.sub(r"^[-–—•●*\s]+", "", line.lower())
    return bool(
        re.match(
            r"(led|built|shipped|wrote|designed|launched|migrated|created|developed|implemented|owned|reduced|improved|delivered)\b",
            low,
        )
    )


def ranked_cv_lines(profile: dict[str, Any], keywords: list[str], limit: int = 12) -> list[str]:
    """CV lines, matching this job's keywords first. Skill lists follow the experience lines."""
    lines = _cv_content(profile)

    def hits(line: str) -> int:
        return sum(1 for keyword in keywords if _mentions(line, keyword))

    narrative = [line for line in lines if not _is_inventory(line)]
    inventory = [line for line in lines if _is_inventory(line)]
    ordered = sorted(narrative, key=hits, reverse=True) + sorted(inventory, key=hits, reverse=True)
    return ordered[:limit]


def _years_value(profile: dict[str, Any]) -> str:
    raw = profile.get("years_experience")
    if raw is None or str(raw).strip() == "":
        return ""
    text = str(raw).strip()
    return text if text.isdigit() else ""


def _sponsorship(profile: dict[str, Any]) -> str:
    value = (profile.get("needs_sponsorship") or "").strip().lower()
    return value if value in {"yes", "no"} else ""


def _is_role_header(line: str) -> bool:
    """A job title or 'Title, Company 2022 - Present' line is not something the person did."""
    text = re.sub(r"^[-–—•●*\s]+", "", line.strip())
    if not text or _is_achievement(text) or len(text) > 90:
        return False
    if not re.search(r"\b(engineer|developer|designer|manager|architect|intern)\b", text, re.I):
        return False
    if re.search(
        r"\b(built|led|shipped|wrote|designed|developed|launched|migrated|created|implemented|owned|worked|using|building|with)\b",
        text,
        re.I,
    ):
        return False
    return len(text.split()) <= 16


def _letter_sentences(line: str) -> list[str]:
    """Split a CV line into sentences and drop bullet dashes that were glued together."""
    protected = re.sub(
        r"(\d{4})\s*[-–—]\s*(Present|Current|Now|\d{4})",
        lambda match: f"{match.group(1)}\x00{match.group(2)}",
        line.strip(),
        flags=re.I,
    )
    pieces = re.split(r"(?:^|\s)[-–—•●]\s+", protected)
    sentences: list[str] = []
    for piece in pieces:
        piece = piece.replace("\x00", " - ")
        piece = re.sub(r"\s+", " ", piece).strip(" -–—•●\t")
        if len(piece) < 20:
            continue
        if not piece.endswith((".", "!", "?")):
            piece += "."
        sentences.append(piece)
    return sentences


def cover_letter(profile: dict[str, Any], job: dict[str, Any] | None = None) -> str:
    """A letter for one role. Sentences use the CV and profile, and add no employers."""
    from .discover import board_display_name

    job = job or {}
    name = (profile.get("name") or "Candidate").strip()
    country = (profile.get("location_country") or "").strip()
    email = (profile.get("email") or "").strip()
    skills = (profile.get("stack") or {}).get("primary") or []
    role = (job.get("role") or "this role").strip()
    company = board_display_name((job.get("company") or "your team").strip())
    years = _years_value(profile)
    keywords = shared_keywords(profile, job)

    where = f", based in {country}" if country else ""
    opening = f"I am applying for the {role} role at {company}. I am {name}{where}."
    if keywords:
        opening += " I have worked with " + ", ".join(keywords[:6]) + "."
    elif skills:
        opening += " I have worked with " + ", ".join(skills[:6]) + "."
    if years:
        opening += f" I have {years} years of experience."

    ranked = ranked_cv_lines(profile, keywords, limit=8)
    narrative = [
        line
        for line in ranked
        if not _is_inventory(line) and any(_mentions(line, keyword) for keyword in keywords)
    ]
    matching = narrative or [line for line in ranked if any(_mentions(line, keyword) for keyword in keywords)]
    pool = [line for line in (matching or ranked) if not _is_role_header(line) and not _is_skill_list(line)]
    achievements = [line for line in pool if _is_achievement(line)]
    summaries = [line for line in pool if not _is_achievement(line)]
    if years:
        summaries = [line for line in summaries if f"{years} year" not in line.lower()]
    chosen = (achievements + summaries)[:3]
    evidence: list[str] = []
    for line in chosen:
        evidence.extend(_letter_sentences(line))
    evidence = evidence[:4]

    parts = ["Dear hiring team,", "", opening]
    if evidence:
        parts.append("")
        parts.extend(evidence)
    parts.extend(["", "Thank you,", name])
    if email:
        parts.append(email)
    return "\n".join(parts).strip() + "\n"


def _named_country_mismatch(label: str, profile: dict[str, Any]) -> bool:
    low = label.lower()
    country = (profile.get("location_country") or "").strip().lower()
    places = ("united states", "u.s.", "usa", "united kingdom", "europe", "canada", "germany", "france")
    named = [place for place in places if place in low]
    if not named:
        return False
    return not country or country not in low


def answer_for_label(label: str, profile: dict[str, Any], job: dict[str, Any] | None = None) -> str | None:
    """Return text for a form label, or None when the profile does not support an answer."""
    low = (label or "").lower()
    years = _years_value(profile)
    if years and any(token in low for token in ("how many year", "years of", "years experience", "year of experience")):
        return years
    sponsor = _sponsorship(profile)
    if sponsor and any(token in low for token in ("sponsor", "visa", "authorized to work", "legally authorized", "work authorization")):
        if _named_country_mismatch(low, profile):
            return None
        if "sponsor" in low or "visa" in low:
            return "Yes" if sponsor == "yes" else "No"
        return "No" if sponsor == "yes" else "Yes"
    if any(token in low for token in _SKIP):
        return None
    if "how did you hear" in low or "where did you hear" in low or "how did you find" in low:
        return "Job board"
    if any(token in low for token in ("linkedin",)):
        return profile.get("linkedin") or None
    if "github" in low:
        return profile.get("github") or None
    if any(token in low for token in ("portfolio", "website", "personal site")):
        return profile.get("portfolio") or None
    if any(token in low for token in ("city", "country", "location", "where are you based", "where do you live")):
        return profile.get("location_country") or None
    if any(token in low for token in _NARRATIVE):
        return cover_letter(profile, job)
    return None


def answer_for_textarea(label: str, profile: dict[str, Any], job: dict[str, Any] | None = None) -> str | None:
    """Written answers. Unknown prose questions use the cover letter. Tenure and legal questions stay blank."""
    specific = answer_for_label(label, profile, job)
    if specific:
        return specific
    low = (label or "").lower()
    if any(token in low for token in _SKIP):
        return None
    return cover_letter(profile, job)
