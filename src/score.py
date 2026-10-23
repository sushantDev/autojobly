from __future__ import annotations

import re
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse


def detect_ats(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    mapping = {
        "ashbyhq.com": "ashby",
        "jobs.ashbyhq.com": "ashby",
        "lever.co": "lever",
        "jobs.lever.co": "lever",
        "greenhouse.io": "greenhouse",
        "job-boards.greenhouse.io": "greenhouse",
        "job-boards.eu.greenhouse.io": "greenhouse",
        "recruitee.com": "recruitee",
        "breezy.hr": "breezy",
        "workable.com": "workable",
        "personio.com": "personio",
    }
    for key, ats in mapping.items():
        if key in host:
            return ats
    return None


def _blob(job: dict[str, Any]) -> str:
    parts = [
        job.get("role") or "",
        job.get("company") or "",
        job.get("location") or "",
        job.get("description") or "",
        job.get("comp_band") or "",
    ]
    return " ".join(parts).lower()


_OPEN_LOCATION = ("remote", "worldwide", "anywhere", "global", "distributed", "wfh")

# Phrase, canonical country. Longer phrases are tried first so "new york" wins over "ny".
_PLACES: tuple[tuple[str, str], ...] = (
    ("united states", "united states"),
    ("united kingdom", "united kingdom"),
    ("new york", "united states"),
    ("san francisco", "united states"),
    ("bay area", "united states"),
    ("los angeles", "united states"),
    ("mountain view", "united states"),
    ("palo alto", "united states"),
    ("seattle", "united states"),
    ("austin", "united states"),
    ("boston", "united states"),
    ("chicago", "united states"),
    ("washington", "united states"),
    ("california", "united states"),
    ("texas", "united states"),
    ("indianapolis", "united states"),
    ("indiana", "united states"),
    ("usa", "united states"),
    ("us", "united states"),
    ("ny", "united states"),
    ("nyc", "united states"),
    ("sf", "united states"),
    ("ca", "united states"),
    ("tx", "united states"),
    ("tel aviv", "israel"),
    ("jerusalem", "israel"),
    ("israel", "israel"),
    ("united kingdom", "united kingdom"),
    ("london", "united kingdom"),
    ("manchester", "united kingdom"),
    ("edinburgh", "united kingdom"),
    ("england", "united kingdom"),
    ("britain", "united kingdom"),
    ("uk", "united kingdom"),
    ("lisbon", "portugal"),
    ("porto", "portugal"),
    ("portugal", "portugal"),
    ("bangalore", "india"),
    ("bengaluru", "india"),
    ("mumbai", "india"),
    ("hyderabad", "india"),
    ("pune", "india"),
    ("chennai", "india"),
    ("new delhi", "india"),
    ("delhi", "india"),
    ("noida", "india"),
    ("gurgaon", "india"),
    ("gurugram", "india"),
    ("kolkata", "india"),
    ("india", "india"),
    ("manila", "philippines"),
    ("cebu", "philippines"),
    ("makati", "philippines"),
    ("taguig", "philippines"),
    ("quezon", "philippines"),
    ("davao", "philippines"),
    ("philippines", "philippines"),
    ("lahore", "pakistan"),
    ("karachi", "pakistan"),
    ("islamabad", "pakistan"),
    ("rawalpindi", "pakistan"),
    ("pakistan", "pakistan"),
    ("kathmandu", "nepal"),
    ("pokhara", "nepal"),
    ("lalitpur", "nepal"),
    ("bhaktapur", "nepal"),
    ("nepal", "nepal"),
    ("berlin", "germany"),
    ("munich", "germany"),
    ("germany", "germany"),
    ("paris", "france"),
    ("france", "france"),
    ("amsterdam", "netherlands"),
    ("netherlands", "netherlands"),
    ("dublin", "ireland"),
    ("ireland", "ireland"),
    ("madrid", "spain"),
    ("barcelona", "spain"),
    ("spain", "spain"),
    ("toronto", "canada"),
    ("vancouver", "canada"),
    ("montreal", "canada"),
    ("canada", "canada"),
    ("sydney", "australia"),
    ("melbourne", "australia"),
    ("australia", "australia"),
    ("singapore", "singapore"),
    ("tokyo", "japan"),
    ("japan", "japan"),
    ("dubai", "united arab emirates"),
    ("united arab emirates", "united arab emirates"),
)

_EU = frozenset({
    "austria", "belgium", "bulgaria", "croatia", "cyprus", "czech republic", "denmark",
    "estonia", "finland", "france", "germany", "greece", "hungary", "ireland", "italy",
    "latvia", "lithuania", "luxembourg", "malta", "netherlands", "poland", "portugal",
    "romania", "slovakia", "slovenia", "spain", "sweden",
})
_EUROPE = _EU | frozenset({"united kingdom", "norway", "switzerland", "iceland", "serbia", "ukraine"})
_APAC = frozenset({
    "india", "nepal", "bangladesh", "sri lanka", "pakistan", "singapore", "japan",
    "south korea", "australia", "new zealand", "china", "indonesia", "malaysia",
    "philippines", "thailand", "vietnam", "hong kong", "taiwan",
})
_AFRICA = frozenset({"south africa", "nigeria", "kenya", "egypt", "morocco", "ghana"})
_MIDDLE_EAST = frozenset({"israel", "united arab emirates", "saudi arabia", "qatar", "jordan", "lebanon", "turkey"})
_NORTH = frozenset({"united states", "canada", "mexico"})
_LATAM = frozenset({"mexico", "brazil", "argentina", "colombia", "chile", "peru"})
_REGIONS: tuple[tuple[str, frozenset[str]], ...] = (
    ("north america", _NORTH),
    ("latin america", _LATAM),
    ("americas", _NORTH | _LATAM),
    ("amer", _NORTH | _LATAM),
    ("european union", _EU),
    ("europe", _EUROPE),
    ("asia pacific", _APAC),
    ("apac", _APAC),
    ("asia", _APAC - frozenset({"australia", "new zealand"})),
    ("latam", _LATAM),
    ("middle east", _MIDDLE_EAST),
    ("emea", _EUROPE | _MIDDLE_EAST | _AFRICA),
    ("eu", _EU),
)
# "in" is India only as the whole location or after an Indian city. Otherwise it is Indiana.
_COUNTRY_CODES = {"ph": "philippines", "pk": "pakistan"}
_INDIA_CITIES = tuple(phrase for phrase, country_name in _PLACES if country_name == "india" and phrase != "india")


def _location_text(location: str) -> str:
    text = (location or "").lower().replace("u.s.a.", "usa").replace("u.s.", "usa")
    text = re.sub(r"[.,()/|–—-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=4096)
def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(phrase)}\b", re.I)


def _has_phrase(phrase: str, text: str) -> bool:
    return _phrase_pattern(phrase).search(text) is not None


def canonical_country(name: str) -> str:
    text = _location_text(name)
    if text == "in":
        return "india"
    if text in _COUNTRY_CODES:
        return _COUNTRY_CODES[text]
    for phrase, country in sorted(_PLACES, key=lambda item: len(item[0]), reverse=True):
        if text == phrase:
            return country
    return text


def location_block(location: str, country: str) -> str | None:
    """Block a listing whose place is not the customer's country.

    Remote is allowed only when the location names no other country or region.
    A blank country or a blank location is left alone.
    """
    home = canonical_country(country)
    if not home:
        return None
    raw = (location or "").strip()
    if not raw:
        return None
    parts = [part.strip() for part in raw.split(";") if part.strip()]
    if len(parts) > 1:
        reasons = [location_block(part, country) for part in parts]
        if any(reason is None for reason in reasons):
            return None
        return reasons[0]
    text = _location_text(raw)
    found: set[str] = set()
    for phrase, country_name in sorted(_PLACES, key=lambda item: len(item[0]), reverse=True):
        if _has_phrase(phrase, text):
            found.add(country_name)
    for phrase, members in sorted(_REGIONS, key=lambda item: len(item[0]), reverse=True):
        if _has_phrase(phrase, text):
            found.update(members)
    tokens = text.split()
    if tokens and tokens[-1] == "in":
        before = " ".join(tokens[:-1])
        if not before or any(_has_phrase(city, before) for city in _INDIA_CITIES):
            found.add("india")
        else:
            found.add("united states")
    elif tokens and tokens[-1] in _COUNTRY_CODES:
        found.add(_COUNTRY_CODES[tokens[-1]])
    if found:
        if home in found:
            return None
        return f"Location is {raw}, not {country.strip()}"
    filler = set(_OPEN_LOCATION) | {"home", "based", "work", "from", "the", "a", "an", "of", "for", "and", "or", "with"}
    leftover = [token for token in text.split() if token not in filler]
    if not leftover and any(_has_phrase(word, text) for word in _OPEN_LOCATION):
        return None
    return f"Location is {raw}, not {country.strip()}"


def hard_skip(job: dict[str, Any], profile: dict[str, Any]) -> str | None:
    text = _blob(job)
    company = (job.get("company") or "").strip().lower()
    for applied in profile.get("already_applied_companies") or []:
        if applied.strip().lower() == company or applied.strip().lower() in company:
            return f"Already applied company: {applied}"

    blocked = location_block(job.get("location") or "", profile.get("location_country") or "")
    if blocked:
        return blocked

    for phrase in (profile.get("filters") or {}).get("skip_geo_phrases") or []:
        if phrase.lower() in text:
            return f"Hard geo/auth/lang block: {phrase}"
    return None


_NON_SOFTWARE = (
    "office assistant",
    "virtual assistant",
    "data entry",
    "customer support",
    "receptionist",
    "sales representative",
    "accountant",
    "bookkeeper",
    "field engineer",
    "alliances",
    "sales",
    "solutions",
    "support engineer",
    "technical support",
    "customer success",
    "customer engineer",
    "consulting engineer",
    "recruiter",
    "talent",
)

# What kind of work a CV shows. A title family outside these is not this person's job.
_TRACK_SKILLS: dict[str, frozenset[str]] = {
    "frontend": frozenset({
        "react", "vue", "angular", "next.js", "nuxt", "svelte", "css", "html", "redux", "tailwind",
        "javascript", "typescript",
    }),
    "backend": frozenset({
        "python", "django", "fastapi", "flask", "java", "spring", "spring boot", "go", "golang",
        "node.js", "node", "express", "nestjs", "php", "laravel", "symfony", "ruby", "ruby on rails",
        "rails", "c#", ".net", "kotlin", "scala", "rust", "elixir", "kafka",
    }),
    "mobile": frozenset({"flutter", "dart", "swift", "react native", "ios", "android", "kotlin"}),
    "data": frozenset({
        "sql", "excel", "power bi", "tableau", "looker", "pandas", "spark", "airflow", "dbt", "snowflake",
    }),
    "ml": frozenset({"pytorch", "tensorflow", "machine learning", "scikit-learn", "llm", "nlp"}),
    "devops": frozenset({"kubernetes", "terraform", "ansible"}),
    "qa": frozenset({"selenium", "cypress", "playwright", "qa"}),
}
_ANALYST_TOOLS = frozenset({"excel", "power bi", "tableau", "looker"})
_BACKEND_FRAMEWORKS = frozenset({"django", "fastapi", "flask"})

# Title words, the family they name, and the tracks that can do that job.
_TITLE_FAMILIES: tuple[tuple[str, tuple[str, ...], frozenset[str]], ...] = (
    ("full stack", ("full stack", "full-stack", "fullstack"), frozenset({"frontend", "backend"})),
    ("frontend", ("frontend", "front-end", "front end", "ui engineer", "ui developer"), frozenset({"frontend"})),
    ("backend", ("backend", "back-end", "back end", "server-side", "api engineer"), frozenset({"backend"})),
    ("mobile", ("mobile", "ios", "android", "react native", "flutter"), frozenset({"mobile"})),
    ("data engineer", ("data engineer", "analytics engineer", "data platform"), frozenset({"data"})),
    ("analyst", ("analyst", "analytics", "business intelligence", "bi developer", "insights"), frozenset({"data"})),
    ("machine learning", ("machine learning", "ml engineer", "data scientist", "applied ai", "ai engineer", "ai", "research scientist", "research engineer"), frozenset({"ml"})),
    ("devops", ("devops", "site reliability", "sre", "platform engineer", "infrastructure", "cloud engineer"), frozenset({"devops"})),
    ("qa", ("qa", "quality engineering", "quality engineer", "quality assurance", "test engineer", "engineer in test", "test automation", "sdet", "automation engineer"), frozenset({"qa"})),
    ("security", ("security",), frozenset()),
    ("architect", ("architect", "architecture"), frozenset()),
)
_ENGINEERING_TITLE = re.compile(r"\b(engineer|engineering|developer|programmer|swe|sde)\b", re.I)


def cv_tracks(skills: list[str]) -> set[str]:
    """frontend, backend, mobile, data, ml, devops, qa. Python without a web framework next to Excel or BI is analysis."""
    have = {skill.lower().strip() for skill in skills if skill and skill.strip()}
    # Kotlin is Android next to Flutter or Swift, and backend next to Java or Spring.
    if "kotlin" in have:
        mobile = bool(have & (_TRACK_SKILLS["mobile"] - {"kotlin"}))
        jvm = bool(have & {"java", "spring", "spring boot", "scala"})
        if mobile and not jvm:
            have = have - {"kotlin"} | {"android"}
        elif jvm and not mobile:
            have = have - {"kotlin"} | {"java"}
    tracks = {track for track, members in _TRACK_SKILLS.items() if have & members}
    analyst = bool(have & _ANALYST_TOOLS)
    if analyst and not (have & _BACKEND_FRAMEWORKS) and not (have & (_TRACK_SKILLS["backend"] - {"python"})):
        tracks.discard("backend")
    # JavaScript or TypeScript alone is a Node backend when Node is there, and frontend otherwise.
    if "frontend" in tracks and not (have & (_TRACK_SKILLS["frontend"] - {"javascript", "typescript"})):
        if have & {"node.js", "node", "express", "nestjs"}:
            tracks.discard("frontend")
    return tracks


def title_family(role: str) -> tuple[str, frozenset[str]] | None:
    engineering = bool(_ENGINEERING_TITLE.search(role))
    for name, phrases, allowed in _TITLE_FAMILIES:
        # "Software Engineer - Data, Analytics" is an engineering job, not an analyst job.
        if name == "analyst" and engineering:
            continue
        if any(_has_phrase(phrase, role) for phrase in phrases):
            return name, allowed
    return None


# English letters and CVs go out. A posting written in another language wants that language.
_OTHER_LANGUAGE_TITLE = re.compile(
    r"\b(desarrollador[a]?|ingenier[oa]|programador[a]?|entwickler(?:in)?|d[ée]veloppeu(?:r|se)|ing[ée]nieur|"
    r"sviluppatore|desenvolvedor[a]?|analista|vertriebs\w*|praktikum|werkstudent)\b",
    re.I,
)

# Tools that show up in almost every posting. They do not make a generic title a fit.
_TOOLS = frozenset({
    "docker",
    "redis",
    "aws",
    "gcp",
    "azure",
    "terraform",
    "mysql",
    "postgresql",
    "mongodb",
    "graphql",
})

_ENGINEERING_TRACKS = frozenset({"frontend", "backend", "mobile", "ml", "devops"})

# A 6-year individual contributor is not a principal or a tech lead.
_LEVEL_ABOVE = (
    "principal",
    "staff",
    "tech lead",
    "technical lead",
    "distinguished",
    "fellow",
)


def _title_skills(role: str) -> list[str]:
    from .defaults import SKILL_PATTERNS

    found: list[str] = []
    for label, pattern in SKILL_PATTERNS:
        if re.search(pattern, role or "", re.I):
            found.append(label.lower())
    return found


def score_job(job: dict[str, Any], profile: dict[str, Any]) -> tuple[int, str]:
    """Return (fit_percent 0-100, reason). A role with none of the customer's skills stays under the send line."""
    skip = hard_skip(job, profile)
    if skip:
        return 0, skip

    text = _blob(job)
    role = (job.get("role") or "").lower()
    if _OTHER_LANGUAGE_TITLE.search(role):
        return 10, "The posting is not in English"
    primary = [k.lower() for k in (profile.get("stack") or {}).get("primary") or []]
    secondary = [k.lower() for k in (profile.get("stack") or {}).get("secondary") or []]
    tracks = cv_tracks(primary + secondary)
    engineer_cv = bool(tracks & _ENGINEERING_TRACKS)
    occupation = any(_has_phrase(phrase, " ".join(primary + secondary)) for phrase in _NON_SOFTWARE)
    # A Python CV does not get a sales title. A sales CV does.
    if any(_has_phrase(phrase, role) for phrase in _NON_SOFTWARE) and (engineer_cv or not occupation):
        return 12, "This role is a different kind of work than the CV"
    leadership = bool((profile.get("stack") or {}).get("leadership"))
    if not leadership and re.search(r"\b(manager|director|head of|vice president|cto|chief)\b", role):
        return 24, "This is a manager role"

    title_hits = [skill for skill in _title_skills(role) if skill in primary or skill in secondary]
    title_other = [skill for skill in _title_skills(role) if skill not in primary and skill not in secondary]
    def has(keyword: str, haystack: str) -> bool:
        return bool(keyword) and _has_phrase(keyword, haystack)

    hits: list[str] = []
    score = 0
    for kw in primary:
        if has(kw, text):
            score += 36 if has(kw, role) else 16
            hits.append(kw)
    for kw in secondary:
        if has(kw, text) and kw not in hits:
            score += 8
            hits.append(kw)
    if not hits:
        return 8, "None of the skills on the CV are in this role"
    if "remote" in text or "worldwide" in text or "anywhere" in text:
        score += 8
    # Title and level decide the ceiling. Skills buried in a long description cannot lift a different job over the send line.
    blocked = False
    if title_other and not title_hits:
        score = min(score, 36)
        hits.append("title asks for " + title_other[0])
        blocked = True
    title_main = [skill for skill in title_hits if skill not in _TOOLS]
    family = title_family(role)
    engineering_title = bool(_ENGINEERING_TITLE.search(role)) or any(
        _has_phrase(phrase, role) for phrase in ("web developer", "web engineer")
    )
    family_fits = False
    if family:
        name, allowed = family
        if tracks:
            family_fits = bool(tracks & allowed)
            # A title that names the customer's own framework is theirs, e.g. "React Native" for a React engineer.
            if not family_fits and not (title_main and engineer_cv and allowed):
                score = min(score, 36)
                hits.append(f"title is {name}")
                blocked = True
        elif not any(has(kw, role) for kw in primary):
            score = min(score, 36)
            hits.append(f"title is {name}")
            blocked = True
    elif engineering_title and tracks and not engineer_cv:
        score = min(score, 36)
        hits.append("title is an engineering role")
        blocked = True
    elif engineer_cv and not engineering_title and not title_main:
        # "Junior Illustrator" or "Meeting Coordinator" that mentions React somewhere.
        score = min(score, 36)
        hits.append("title is not an engineering role")
        blocked = True
    level = next((phrase for phrase in _LEVEL_ABOVE if _has_phrase(phrase, role)), "")
    years = str(profile.get("years_experience") or "").strip()
    if not level and years.isdigit() and int(years) < 5 and _has_phrase("lead", role):
        level = "lead"
    if level and not leadership:
        score = min(score, 36)
        hits.append("title is a level above this CV")
        blocked = True
    # Only the opening counts. A later "or laravel, or equivalent" list is not the job.
    described = [kw for kw in primary if kw not in _TOOLS and has(kw, text[:1800]) and not has(kw, role)]
    # Docker and Redis in a long description are not overlap. A title in the CV's own family needs one
    # language or framework in the opening; a plain "Software Engineer" needs two.
    if not blocked and title_main and (engineering_title or family):
        score = max(score, 74)
    elif not blocked and family_fits and described:
        score = max(score, 74)
    elif not blocked and not family and engineering_title and len(described) >= 2:
        score = max(score, 74)
    elif not blocked and not engineer_cv and not engineering_title and not family and any(
        has(kw, role) for kw in primary if kw not in _TOOLS
    ):
        score = max(score, 74)
    elif (engineering_title or family) and not title_main:
        score = min(score, 69)
    score = max(0, min(100, score))
    return score, ", ".join(hits[:8])


def can_auto_apply(job: dict[str, Any], profile: dict[str, Any]) -> bool:
    ats = job.get("ats") or detect_ats(job.get("url") or "")
    allowed = (profile.get("filters") or {}).get("ats_auto_apply") or []
    min_fit = (profile.get("filters") or {}).get("min_fit_percent", 70)
    if (job.get("fit_percent") or 0) < min_fit:
        return False
    return ats in allowed