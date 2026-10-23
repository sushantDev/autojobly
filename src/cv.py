"""Pull contact facts out of a pasted or uploaded CV. Never invent employers."""

from __future__ import annotations

import re
from typing import Any

from .defaults import COUNTRIES, SKILL_PATTERNS

_EMAIL = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_PHONE = re.compile(r"(?:\+\d{1,3}[\s.\-]?)?(?:\(?\d{2,4}\)?[\s.\-]?)?\d{3,4}[\s.\-]?\d{3,4}")
_LINKEDIN = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[A-Za-z0-9_\-%/]+", re.I)
_GITHUB = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_\-]+", re.I)
_PLUS_PHONE = re.compile(r"\+\d[\d\s().-]{7,}\d")
_BARE_PHONE = re.compile(r"(?<!\d)(?:\d[\s.-]?){10,15}(?!\d)")
_YEARS = re.compile(r"\b(\d{1,2})\s*\+?\s+years?\b", re.I)
_URL = re.compile(r"https?://[^\s)>]+", re.I)
_TECH_WORD = re.compile(
    r"^(js|javascript|ts|typescript|css|html|php|sql|api|ui|ux|aws|ios|sdk|qa|node|react|vue|angular)$",
    re.I,
)


def _skill_word(word: str) -> bool:
    if _TECH_WORD.match(word):
        return True
    for _label, pattern in SKILL_PATTERNS:
        if re.search(pattern, word, re.I):
            return True
    return False


def pdf_to_text(path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


def _name_from_lines(text: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > 60:
            continue
        if _EMAIL.search(line) or _URL.search(line) or _PHONE.search(line):
            continue
        words = re.findall(r"[A-Za-z][A-Za-z'\-]+", line)
        if not (2 <= len(words) <= 4 and len(line) == len(" ".join(words))):
            continue
        if any(not word[0].isupper() for word in words):
            continue
        if any(_skill_word(word) for word in words):
            continue
        return " ".join(words)
    return ""


def _country_from_text(text: str) -> str:
    head = text[:800]
    for source in (head, text):
        for country in COUNTRIES:
            if re.search(rf"\b{re.escape(country)}\b", source, re.I):
                return country
    return ""


def _with_scheme(url: str) -> str:
    cleaned = url.rstrip(".,)")
    if cleaned.lower().startswith("http"):
        return cleaned
    return "https://" + cleaned


def _phone_from_text(text: str) -> str:
    best = ""
    best_len = 0
    for match in _PLUS_PHONE.finditer(text or ""):
        raw = " ".join(match.group().split())
        digits = re.sub(r"\D", "", raw)
        if 8 <= len(digits) <= 15 and len(digits) > best_len:
            best, best_len = raw, len(digits)
    if best:
        return best
    for match in _BARE_PHONE.finditer(text or ""):
        raw = " ".join(match.group().split())
        digits = re.sub(r"\D", "", raw)
        if 10 <= len(digits) <= 15 and len(digits) > best_len:
            best, best_len = raw, len(digits)
    return best


def _years_from_text(text: str) -> str:
    found = _YEARS.search(text or "")
    if not found:
        return ""
    years = int(found.group(1))
    if 1 <= years <= 40:
        return str(years)
    return ""


def _skills_from_text(text: str) -> list[str]:
    found: list[str] = []
    for label, pattern in SKILL_PATTERNS:
        if re.search(pattern, text, re.I) and label not in found:
            found.append(label)
    return found


def extract_cv(text: str) -> dict[str, Any]:
    """Return only facts that appear in the CV text."""
    blob = text or ""
    email = _EMAIL.search(blob)
    linkedin = _LINKEDIN.search(blob)
    github = _GITHUB.search(blob)
    urls = _URL.findall(blob)
    portfolio = ""
    for url in urls:
        low = url.lower()
        if "linkedin.com" in low or "github.com" in low:
            continue
        portfolio = url.rstrip(".,)")
        break
    return {
        "name": _name_from_lines(blob),
        "email": email.group() if email else "",
        "phone": _phone_from_text(blob),
        "linkedin": _with_scheme(linkedin.group()) if linkedin else "",
        "github": _with_scheme(github.group()) if github else "",
        "years_experience": _years_from_text(blob),
        "portfolio": portfolio,
        "location_country": _country_from_text(blob),
        "skills": _skills_from_text(blob),
        "cv_text": blob.strip(),
    }


def tailored_cv(profile: dict[str, Any], job: dict[str, Any] | None = None) -> str:
    """A CV for one job. Matching lines lead. Skills the CV does not list are left out."""
    from .letters import ranked_cv_lines, shared_keywords

    job = job or {}
    facts = profile
    name = (facts.get("name") or "Candidate").strip()
    skills = (facts.get("stack") or {}).get("primary") or facts.get("skills") or []
    if isinstance(skills, str):
        skills = [part.strip() for part in skills.split(",") if part.strip()]
    keywords = shared_keywords(profile, job)
    contact = [
        facts.get("email") or "",
        facts.get("phone") or "",
        facts.get("location_country") or "",
    ]
    links = [facts.get("linkedin") or "", facts.get("github") or "", facts.get("portfolio") or ""]
    lines = [name, " | ".join(bit for bit in contact if bit), ""]
    link_bits = [bit for bit in links if bit]
    if link_bits:
        lines.append("Links")
        lines.extend(link_bits)
        lines.append("")
    matched = [skill for skill in skills if any(skill.lower() == keyword.lower() for keyword in keywords)]
    rest = [skill for skill in skills if skill not in matched]
    extra = [keyword for keyword in keywords if keyword.lower() not in {skill.lower() for skill in skills}]
    focus = matched + extra
    if focus:
        lines.append("Skills for this role")
        lines.append(", ".join(focus))
        lines.append("")
    if rest:
        lines.append("Other skills stated on the CV")
        lines.append(", ".join(rest))
    elif not focus:
        lines.append("Skills stated on the CV")
        lines.append(", ".join(skills) if skills else "None detected. Edit the profile if a skill was missed.")
    body = ranked_cv_lines(profile, keywords, limit=12)
    if body:
        lines.append("")
        lines.append("From the CV")
        for line in body:
            cleaned = line.lstrip("-•● ").strip()
            if cleaned:
                lines.append(cleaned)
    return "\n".join(lines).strip() + "\n"


def refine_cv(facts: dict[str, Any]) -> str:
    """One-page fact sheet. Omits any employer, date, or title we did not extract."""
    name = (facts.get("name") or "Candidate").strip()
    skills = facts.get("skills") or []
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]
    contact = [
        facts.get("email") or "",
        facts.get("phone") or "",
        facts.get("location_country") or "",
    ]
    links = [facts.get("linkedin") or "", facts.get("github") or "", facts.get("portfolio") or ""]
    lines = [name, " | ".join(bit for bit in contact if bit), ""]
    link_bits = [bit for bit in links if bit]
    if link_bits:
        lines.append("Links")
        lines.extend(f"- {bit}" for bit in link_bits)
        lines.append("")
    lines.append("Skills stated on the CV")
    if skills:
        lines.append(", ".join(skills))
    else:
        lines.append("None detected. Edit the profile if a skill was missed.")
    lines.append("")
    country = facts.get("location_country") or ""
    if country and skills:
        lines.append(f"{name} is based in {country}. Skills stated on the CV: {', '.join(skills)}.")
    elif country:
        lines.append(f"{name} is based in {country}.")
    elif skills:
        lines.append(f"Skills stated on the CV: {', '.join(skills)}.")
    # A salary on the CV undercuts the negotiation. It goes only in a form field that asks for it.
    return "\n".join(lines).strip() + "\n"
