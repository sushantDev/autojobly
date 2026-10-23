from __future__ import annotations

import html
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from typing import Any

import httpx

from .score import detect_ats, location_block

_log = logging.getLogger("autojobly")
_BOARD_REPORT: list[str] = []


def last_board_report() -> list[str]:
    """How many jobs each board returned on the last fetch, and how many were kept."""
    return list(_BOARD_REPORT)


def _note_board(name: str, returned: int | None = None, kept: int | None = None, error: str = "") -> None:
    if error:
        line = f"{name}: failed ({error})"
    else:
        line = f"{name}: {returned} returned, {kept} kept"
    _BOARD_REPORT.append(line)
    if not _log.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        _log.addHandler(handler)
        _log.setLevel(logging.INFO)
    _log.info(line)

SEARCH_TERMS = (
    "react",
    "typescript",
    "javascript",
    "python",
    "node",
    "next.js",
    "java",
    "golang",
    "laravel",
    "php",
    "vue",
    "flutter",
    "ruby",
)

_ATS_URL = re.compile(
    r"https?://[^\s\"'<>]+(?:greenhouse\.io|lever\.co|jobs\.ashbyhq\.com|ashbyhq\.com)[^\s\"'<>]*",
    re.I,
)

# Public board slugs checked on 23 Oct 2026. A failed board is skipped.
# Netflix's Lever board returns 404, so it is not listed.
GREENHOUSE_BOARDS = (
    "gitlab",
    "cloudflare",
    "stripe",
    "figma",
    "airtable",
    "groww",
    "hackerrank",
    "inmobi",
    "rubrik",
    "druva",
    "elastic",
    "mongodb",
    "twilio",
    "databricks",
    "thoughtworks",
    "canonical",
    "netlify",
    "datadog",
    "okta",
    "brex",
    "coinbase",
    "affirm",
    "block",
    "airbnb",
    "lyft",
    "pinterest",
    "robinhood",
    "reddit",
    "instacart",
    "intercom",
    "asana",
    "gusto",
    "chime",
    "duolingo",
    "sofi",
    "discord",
    "dropbox",
    "slice",
    "lattice",
    # Checked live on 23 Oct 2026: every board below returned open jobs.
    "anthropic", "hellofresh", "zscaler", "sumup", "verkada", "samsara", "roblox", "adyen",
    "deliveroo", "celonis", "wolt", "flexport", "riotgames", "scaleai", "fivetran", "appian",
    "doctolib", "epicgames", "grafanalabs", "mozilla", "zoominfo", "vercel", "smartsheet",
    "tripadvisor", "togetherai", "carta", "mercury", "monzo", "getyourguide", "faire", "wrike",
    "hubspotjobs", "tailscale", "twitch", "newrelic", "launchdarkly", "pagerduty", "mixpanel",
    "applovin", "singlestore", "n26", "starburst", "commercetools", "algolia", "truecaller",
    "coursera", "squarespace", "attentive", "yext", "gocardless", "razorpaysoftwareprivatelimited",
    "honeycomb", "contentful", "wise", "cockroachlabs", "assemblyai", "wikimedia", "trivago",
    "yugabyte", "storyblok", "circleci", "planetscale", "typeform", "dremio", "stackblitz",
    "khanacademy",
)
LEVER_SITES = (
    "palantir", "spotify", "binance", "cred", "meesho",
    "zoox", "paytm", "qonto", "toptal", "zeta", "outreach", "glide", "contentsquare", "logrocket",
)
ASHBY_BOARDS = (
    "ashby",
    "ramp",
    "linear",
    "supabase",
    "posthog",
    "zapier",
    "openai",
    "snowflake",
    "notion",
    "cursor",
    "perplexity",
    "cohere",
    "temporal",
    "docker",
    "replit",
    "sentry",
    "modal",
    "twenty",
    "amplitude",
    "hex",
    "render",
    "plane",
    "workos",
    "warp",
    "andela",
    "webflow",
    "resend",
    "mintlify",
    "airbyte",
    "railway",
    "triggerdev",
    "neon",
    "gitbook",
    "clerk",
    "inngest",
    "hightouch",
    "harvey", "clickhouse", "elevenlabs", "plaid", "alan", "baseten", "langchain", "deepgram",
    "1password", "kong", "vanta", "lovable", "benchling", "mollie", "clickup", "writer",
    "wealthsimple", "n8n", "watershed", "supercell", "sanity", "backmarket", "pleo", "multiverse",
    "oyster", "nerdwallet", "anyscale", "miro", "astronomer", "paddle", "midjourney", "gorgias",
    "fullstory", "pinecone", "prefect", "atlan", "influxdata", "materialize",
)


def company_board_count() -> int:
    return len(GREENHOUSE_BOARDS) + len(LEVER_SITES) + len(ASHBY_BOARDS)

_BOARD_NAMES = {
    "gitlab": "GitLab",
    "cloudflare": "Cloudflare",
    "stripe": "Stripe",
    "figma": "Figma",
    "airtable": "Airtable",
    "netflix": "Netflix",
    "palantir": "Palantir",
    "ashby": "Ashby",
    "ramp": "Ramp",
    "linear": "Linear",
    "groww": "Groww",
    "hackerrank": "HackerRank",
    "inmobi": "InMobi",
    "rubrik": "Rubrik",
    "druva": "Druva",
    "elastic": "Elastic",
    "mongodb": "MongoDB",
    "twilio": "Twilio",
    "databricks": "Databricks",
    "thoughtworks": "Thoughtworks",
    "cred": "CRED",
    "meesho": "Meesho",
    "canonical": "Canonical",
    "netlify": "Netlify",
    "supabase": "Supabase",
    "posthog": "PostHog",
    "zapier": "Zapier",
    "datadog": "Datadog",
    "okta": "Okta",
    "brex": "Brex",
    "coinbase": "Coinbase",
    "affirm": "Affirm",
    "block": "Block",
    "airbnb": "Airbnb",
    "lyft": "Lyft",
    "pinterest": "Pinterest",
    "robinhood": "Robinhood",
    "reddit": "Reddit",
    "instacart": "Instacart",
    "intercom": "Intercom",
    "asana": "Asana",
    "gusto": "Gusto",
    "chime": "Chime",
    "duolingo": "Duolingo",
    "sofi": "SoFi",
    "discord": "Discord",
    "dropbox": "Dropbox",
    "slice": "Slice",
    "lattice": "Lattice",
    "spotify": "Spotify",
    "binance": "Binance",
    "openai": "OpenAI",
    "snowflake": "Snowflake",
    "notion": "Notion",
    "cursor": "Cursor",
    "perplexity": "Perplexity",
    "cohere": "Cohere",
    "temporal": "Temporal",
    "docker": "Docker",
    "replit": "Replit",
    "sentry": "Sentry",
    "modal": "Modal",
    "twenty": "Twenty",
    "amplitude": "Amplitude",
    "hex": "Hex",
    "render": "Render",
    "plane": "Plane",
    "workos": "WorkOS",
    "warp": "Warp",
    "andela": "Andela",
    "webflow": "Webflow",
    "resend": "Resend",
    "mintlify": "Mintlify",
    "airbyte": "Airbyte",
    "railway": "Railway",
    "triggerdev": "Trigger.dev",
    "neon": "Neon",
    "gitbook": "GitBook",
    "clerk": "Clerk",
    "inngest": "Inngest",
    "hightouch": "Hightouch",
    "hellofresh": "HelloFresh",
    "sumup": "SumUp",
    "riotgames": "Riot Games",
    "scaleai": "Scale AI",
    "epicgames": "Epic Games",
    "grafanalabs": "Grafana Labs",
    "zoominfo": "ZoomInfo",
    "tripadvisor": "Tripadvisor",
    "togetherai": "Together AI",
    "getyourguide": "GetYourGuide",
    "hubspotjobs": "HubSpot",
    "newrelic": "New Relic",
    "launchdarkly": "LaunchDarkly",
    "pagerduty": "PagerDuty",
    "applovin": "AppLovin",
    "singlestore": "SingleStore",
    "n26": "N26",
    "razorpaysoftwareprivatelimited": "Razorpay",
    "cockroachlabs": "Cockroach Labs",
    "assemblyai": "AssemblyAI",
    "circleci": "CircleCI",
    "planetscale": "PlanetScale",
    "stackblitz": "StackBlitz",
    "khanacademy": "Khan Academy",
    "yugabyte": "Yugabyte",
    "contentsquare": "Contentsquare",
    "logrocket": "LogRocket",
    "clickhouse": "ClickHouse",
    "elevenlabs": "ElevenLabs",
    "langchain": "LangChain",
    "1password": "1Password",
    "clickup": "ClickUp",
    "wealthsimple": "Wealthsimple",
    "backmarket": "Back Market",
    "nerdwallet": "NerdWallet",
    "fullstory": "FullStory",
    "influxdata": "InfluxData",
    "veeva": "Veeva",
    "paytm": "Paytm",
    "zeta": "Zeta",
    "n8n": "n8n",
}

_TAG_GEO = re.compile(
    r"\b(emea|apac|latam|europe|european|americas|asia|africa|united states|usa|u\.s\.|india|uk|united kingdom|germany|canada|australia|brazil|singapore|japan)\b",
    re.I,
)
_SOURCE_LABELS = {
    "remoteok": "Remote OK",
    "himalayas": "Himalayas",
    "arbeitnow": "Arbeitnow",
    "jobicy": "Jobicy",
    "remotive": "Remotive",
    "workingnomads": "Working Nomads",
}


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip = True
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def plain_text(value: str) -> str:
    """Turn Greenhouse HTML, including escaped tags, into the words of the posting."""
    text = value or ""
    for _ in range(2):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    parser = _HTMLText()
    parser.feed(text)
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def board_display_name(name: str) -> str:
    cleaned = (name or "").strip()
    mapped = _BOARD_NAMES.get(cleaned.lower())
    if mapped:
        return mapped
    if cleaned.islower() and " " not in cleaned:
        return cleaned.capitalize()
    return cleaned or "Unknown"


def _prefer_apply_url(url: str, description: str) -> str:
    """Use the company form when the listing text contains one. A remotive.com page is not an application."""
    match = _ATS_URL.search(description or "")
    if match:
        return match.group(0).rstrip(").,]")
    return url


def _normalize(raw: dict[str, Any], source: str) -> dict[str, Any]:
    raw_description = raw.get("description") or ""
    description = plain_text(raw_description)
    url = _prefer_apply_url(raw.get("apply_url") or raw.get("url") or "", f"{raw_description} {description}")
    tags = raw.get("tags") or []
    tag_str = " ".join(tags) if isinstance(tags, list) else str(tags)
    if tag_str:
        description = f"{tag_str} {description}".strip()
    return {
        "external_id": f"{source}:{raw.get('id') or url}",
        "company": board_display_name(raw.get("company_name") or raw.get("company") or ""),
        "role": (raw.get("title") or raw.get("role") or "Untitled").strip(),
        "url": url,
        "source": source,
        "location": raw.get("candidate_required_location") or raw.get("location") or "",
        "description": description,
        "comp_band": raw.get("salary") or "",
        "ats": detect_ats(url),
        "listing_url": raw.get("listing_url") or "",
    }


def source_label(source: str) -> str:
    return _SOURCE_LABELS.get((source or "").lower(), source or "")


def _tags_text(tags: Any) -> str:
    if isinstance(tags, str):
        return tags
    if isinstance(tags, list):
        return " ".join(str(tag) for tag in tags if tag)
    return ""


def _remote_location(location: str, tags: Any = None) -> str:
    """A blank place on a remote feed is worldwide. A region named in the tags is not."""
    loc = (location or "").strip()
    if loc:
        return loc
    hint = _tags_text(tags).strip()
    if hint and _TAG_GEO.search(hint):
        return hint
    return "Remote"


def _is_remote_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "remote"}


def _listing_job(raw: dict[str, Any], source: str, listing_url: str) -> dict[str, Any]:
    raw = dict(raw)
    raw["listing_url"] = listing_url
    job = _normalize(raw, source)
    if listing_url and not job.get("listing_url"):
        job["listing_url"] = listing_url
    return job


def jobs_from_remoteok(
    payload: Any,
    terms: list[str],
    countries: list[str] | None,
    per_board: int,
) -> tuple[list[dict[str, Any]], int]:
    items = payload if isinstance(payload, list) else []
    jobs: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("position"):
            continue
        listing = item.get("url") or item.get("apply_url") or ""
        low = item.get("salary_min")
        high = item.get("salary_max")
        salary = f"{low}–{high}" if low and high else ""
        jobs.append(
            _listing_job(
                {
                    "id": item.get("id"),
                    "title": item.get("position"),
                    "company_name": item.get("company"),
                    "url": item.get("apply_url") or listing,
                    "location": _remote_location(item.get("location") or "", item.get("tags")),
                    "description": item.get("description") or "",
                    "tags": item.get("tags") or [],
                    "salary": salary,
                },
                "remoteok",
                listing,
            )
        )
    return select_board_jobs(jobs, terms, countries, per_board), len(jobs)


def jobs_from_himalayas(
    payload: Any,
    terms: list[str],
    countries: list[str] | None,
    per_board: int,
) -> tuple[list[dict[str, Any]], int]:
    items = (payload or {}).get("jobs") or []
    jobs: list[dict[str, Any]] = []
    for item in items:
        restrictions = item.get("locationRestrictions") or []
        if isinstance(restrictions, list):
            location = "; ".join(str(part).strip() for part in restrictions if str(part).strip())
        else:
            location = str(restrictions or "").strip()
        listing = item.get("applicationLink") or item.get("guid") or ""
        low = item.get("minSalary")
        high = item.get("maxSalary")
        salary = f"{low}–{high} {item.get('currency') or ''}".strip() if low and high else ""
        jobs.append(
            _listing_job(
                {
                    "id": item.get("guid") or listing,
                    "title": item.get("title"),
                    "company_name": item.get("companyName"),
                    "url": listing,
                    "location": location or "Remote",
                    "description": item.get("description") or item.get("excerpt") or "",
                    "salary": salary,
                },
                "himalayas",
                listing,
            )
        )
    return select_board_jobs(jobs, terms, countries, per_board), len(jobs)


def jobs_from_arbeitnow(
    payload: Any,
    terms: list[str],
    countries: list[str] | None,
    per_board: int,
) -> tuple[list[dict[str, Any]], int]:
    items = (payload or {}).get("data") or []
    jobs: list[dict[str, Any]] = []
    for item in items:
        if not _is_remote_flag(item.get("remote")):
            continue
        listing = item.get("url") or ""
        jobs.append(
            _listing_job(
                {
                    "id": item.get("slug") or listing,
                    "title": item.get("title"),
                    "company_name": item.get("company_name"),
                    "url": listing,
                    "location": _remote_location(item.get("location") or "", item.get("tags")),
                    "description": item.get("description") or "",
                    "tags": item.get("tags") or [],
                },
                "arbeitnow",
                listing,
            )
        )
    return select_board_jobs(jobs, terms, countries, per_board), len(jobs)


def jobs_from_jobicy(
    payload: Any,
    terms: list[str],
    countries: list[str] | None,
    per_board: int,
) -> tuple[list[dict[str, Any]], int]:
    items = (payload or {}).get("jobs") or []
    jobs: list[dict[str, Any]] = []
    for item in items:
        listing = item.get("url") or ""
        jobs.append(
            _listing_job(
                {
                    "id": item.get("id") or listing,
                    "title": item.get("jobTitle"),
                    "company_name": item.get("companyName"),
                    "url": listing,
                    "location": _remote_location(item.get("jobGeo") or ""),
                    "description": item.get("jobDescription") or item.get("jobExcerpt") or "",
                },
                "jobicy",
                listing,
            )
        )
    return select_board_jobs(jobs, terms, countries, per_board), len(jobs)


def _fetch_json(url: str, params: dict[str, Any] | None = None) -> Any:
    headers = {"User-Agent": "Autojobly/1.0"}
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()


# Searching a feed for these returns every job on it. The customer's language or framework is the search.
_FEED_SKIP = frozenset({
    "docker", "redis", "aws", "gcp", "azure", "terraform", "mysql", "postgresql", "mongodb",
    "graphql", "css", "html", "sql", "excel", "git", "jest", "redux", "tailwind", "javascript",
})
FEED_SEARCHES = 15
# Jobs kept per company board on one refresh. Scoring picks each customer's 15 from the whole catalog.
PER_BOARD = 30


def _feed_terms(terms: list[str]) -> list[str]:
    picked = [term for term in terms if term.strip().lower() not in _FEED_SKIP and len(_feed_tag(term)) >= 2]
    return picked[:FEED_SEARCHES]


def _feed_tag(term: str) -> str:
    text = term.strip().lower().replace("node.js", "nodejs").replace("next.js", "nextjs").replace("vue.js", "vue")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _fetch_many(calls: list[tuple[str, dict[str, Any] | None]]) -> list[Any]:
    """Run several feed searches at once. A failed search is skipped, the rest still count."""

    def one(call: tuple[str, dict[str, Any] | None]) -> Any:
        try:
            return _fetch_json(call[0], call[1])
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        payloads = list(pool.map(one, calls))
    if calls and all(payload is None for payload in payloads):
        raise RuntimeError("every search failed")
    return [payload for payload in payloads if payload is not None]


def _unique(items: list[Any], key) -> list[Any]:
    seen: set[str] = set()
    kept: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        value = str(key(item) or "")
        if not value or value in seen:
            continue
        seen.add(value)
        kept.append(item)
    return kept


def _keep_feed(
    name: str,
    loader,
    terms: list[str],
    countries: list[str] | None,
    per_board: int,
) -> list[dict[str, Any]]:
    try:
        payload = loader()
        kept, returned = None, None
        if name == "Remote OK":
            kept, returned = jobs_from_remoteok(payload, terms, countries, per_board)
        elif name == "Himalayas":
            kept, returned = jobs_from_himalayas(payload, terms, countries, per_board)
        elif name == "Arbeitnow":
            kept, returned = jobs_from_arbeitnow(payload, terms, countries, per_board)
        elif name == "Working Nomads":
            kept, returned = jobs_from_workingnomads(payload, terms, countries, per_board)
        else:
            kept, returned = jobs_from_jobicy(payload, terms, countries, per_board)
        _note_board(name, returned, len(kept))
        return kept
    except Exception as exc:
        _note_board(name, error=exc.__class__.__name__)
        return []


def fetch_remoteok(terms: list[str], per_board: int, countries: list[str] | None) -> list[dict[str, Any]]:
    def load() -> list[Any]:
        calls = [("https://remoteok.com/api", None)]
        calls += [("https://remoteok.com/api", {"tag": _feed_tag(term)}) for term in _feed_terms(terms)]
        items: list[Any] = []
        for payload in _fetch_many(calls):
            if isinstance(payload, list):
                items.extend(payload)
        return _unique(items, lambda item: item.get("id") if item.get("position") else "")

    return _keep_feed("Remote OK", load, terms, countries, per_board)


def fetch_himalayas(terms: list[str], per_board: int, countries: list[str] | None) -> list[dict[str, Any]]:
    def load() -> dict[str, Any]:
        jobs: list[Any] = []
        cursor = None
        for _ in range(3):
            params: dict[str, Any] = {"limit": 50}
            if cursor:
                params["cursor"] = cursor
            page = _fetch_json("https://himalayas.app/jobs/api", params)
            jobs.extend(page.get("jobs") or [])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        searches = [("https://himalayas.app/jobs/api/search", {"q": term}) for term in _feed_terms(terms)]
        for payload in _fetch_many(searches) if searches else []:
            jobs.extend((payload or {}).get("jobs") or [])
        return {"jobs": _unique(jobs, lambda item: item.get("guid") or item.get("applicationLink"))}

    return _keep_feed("Himalayas", load, terms, countries, per_board)


def jobs_from_workingnomads(
    payload: Any,
    terms: list[str],
    countries: list[str] | None,
    per_board: int,
) -> tuple[list[dict[str, Any]], int]:
    items = payload if isinstance(payload, list) else []
    jobs: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        listing = item.get("url") or ""
        jobs.append(
            _listing_job(
                {
                    "id": listing,
                    "title": item.get("title"),
                    "company_name": item.get("company_name"),
                    "url": listing,
                    "location": _remote_location(item.get("location") or ""),
                    "description": item.get("description") or "",
                    "tags": item.get("tags") or "",
                },
                "workingnomads",
                listing,
            )
        )
    return select_board_jobs(jobs, terms, countries, per_board), len(jobs)


def fetch_workingnomads(terms: list[str], per_board: int, countries: list[str] | None) -> list[dict[str, Any]]:
    return _keep_feed(
        "Working Nomads",
        lambda: _fetch_json("https://www.workingnomads.com/api/exposed_jobs/"),
        terms,
        countries,
        per_board,
    )


def fetch_arbeitnow(terms: list[str], per_board: int, countries: list[str] | None) -> list[dict[str, Any]]:
    return _keep_feed(
        "Arbeitnow",
        lambda: _fetch_json("https://www.arbeitnow.com/api/job-board-api"),
        terms,
        countries,
        per_board,
    )


def fetch_jobicy(terms: list[str], per_board: int, countries: list[str] | None) -> list[dict[str, Any]]:
    def load() -> dict[str, Any]:
        calls: list[tuple[str, dict[str, Any] | None]] = [("https://jobicy.com/api/v2/remote-jobs", {"count": 50})]
        calls += [
            ("https://jobicy.com/api/v2/remote-jobs", {"count": 50, "tag": _feed_tag(term)})
            for term in _feed_terms(terms)
        ]
        jobs: list[Any] = []
        for payload in _fetch_many(calls):
            jobs.extend((payload or {}).get("jobs") or [])
        return {"jobs": _unique(jobs, lambda item: item.get("id"))}

    return _keep_feed("Jobicy", load, terms, countries, per_board)


def fetch_remotive(
    limit: int = 40,
    terms: list[str] | None = None,
    countries: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Public Remotive API. Search terms come from the customer's skills when a profile exists."""
    seen: set[str] = set()
    jobs: list[dict[str, Any]] = []
    search_terms = terms or list(SEARCH_TERMS)
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        queries: list[dict[str, Any]] = [{"category": "software-dev", "limit": 50}]
        for term in search_terms[:20]:
            queries.append({"search": term, "limit": 30})

        for params in queries:
            if len(jobs) >= 200:
                break
            r = client.get("https://remotive.com/api/remote-jobs", params=params)
            r.raise_for_status()
            for item in r.json().get("jobs") or []:
                job = _normalize(item, "remotive")
                key = job["external_id"]
                if key in seen:
                    continue
                seen.add(key)
                jobs.append(job)
    kept = select_board_jobs(jobs, search_terms, countries, limit)
    _note_board("Remotive", len(jobs), len(kept))
    return kept


def ordered_terms(skills: list[str], defaults: tuple[str, ...] = SEARCH_TERMS) -> list[str]:
    """Customer skills lead, then the default stack. Remotive only searches the front of this list."""
    terms: list[str] = []
    seen: set[str] = set()
    for skill in skills:
        cleaned = skill.strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            terms.append(cleaned)
    for term in defaults:
        if term.lower() not in seen:
            seen.add(term.lower())
            terms.append(term)
    return terms


def _matches(job: dict[str, Any], terms: list[str]) -> bool:
    blob = f"{job.get('role', '')} {job.get('description', '')}"
    return any(re.search(rf"\b{re.escape(term.strip())}\b", blob, re.I) for term in terms if term.strip())


def _term_hits(text: str, terms: list[str]) -> int:
    return sum(1 for term in terms if term.strip() and re.search(rf"\b{re.escape(term.strip())}\b", text, re.I))


def _place_ok(job: dict[str, Any], countries: list[str] | None) -> bool:
    if not countries:
        return True
    location = job.get("location") or ""
    return any(location_block(location, country) is None for country in countries)


def select_board_jobs(
    jobs: list[dict[str, Any]],
    terms: list[str],
    countries: list[str] | None,
    per_board: int,
) -> list[dict[str, Any]]:
    """Keep the strongest matches for this board. A place the customer cannot work does not take a slot.

    When the board has more matches than slots, each search term takes a turn, so a Laravel
    customer still gets Laravel roles on a board full of React roles.
    """
    matched = [job for job in jobs if _matches(job, terms) and _place_ok(job, countries)]
    matched.sort(
        key=lambda job: (
            _term_hits(job.get("role") or "", terms),
            _term_hits(job.get("description") or "", terms),
        ),
        reverse=True,
    )
    if len(matched) <= per_board:
        return matched
    queues: list[list[dict[str, Any]]] = []
    for term in terms:
        queue = [job for job in matched if _matches(job, [term])]
        queue.sort(key=lambda job: _term_hits(job.get("role") or "", [term]), reverse=True)
        if queue:
            queues.append(queue)
    picked: list[dict[str, Any]] = []
    taken: set[int] = set()
    while len(picked) < per_board and any(queues):
        for queue in queues:
            while queue and id(queue[0]) in taken:
                queue.pop(0)
            if queue:
                job = queue.pop(0)
                taken.add(id(job))
                picked.append(job)
            if len(picked) >= per_board:
                break
    return picked


def _load_board(kind: str, slug: str) -> tuple[str, list[dict[str, Any]], str]:
    headers = {"User-Agent": "Autojobly/1.0"}
    pool: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=25.0, follow_redirects=True, headers=headers) as client:
            if kind == "greenhouse":
                response = client.get(
                    f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                    params={"content": "true"},
                )
                response.raise_for_status()
                for item in response.json().get("jobs") or []:
                    job = _normalize(
                        {
                            "id": item.get("id"),
                            "title": item.get("title"),
                            "company_name": slug,
                            "url": item.get("absolute_url") or "",
                            "location": (item.get("location") or {}).get("name") or "",
                            "description": item.get("content") or "",
                        },
                        "greenhouse",
                    )
                    job["ats"] = "greenhouse"
                    pool.append(job)
            elif kind == "lever":
                response = client.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
                response.raise_for_status()
                for item in response.json() or []:
                    job = _normalize(
                        {
                            "id": item.get("id"),
                            "title": item.get("text"),
                            "company_name": slug,
                            "url": item.get("hostedUrl") or item.get("applyUrl") or "",
                            "location": (item.get("categories") or {}).get("location") or "",
                            "description": item.get("descriptionPlain") or item.get("description") or "",
                        },
                        "lever",
                    )
                    job["ats"] = "lever"
                    pool.append(job)
            else:
                response = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
                response.raise_for_status()
                for item in response.json().get("jobs") or []:
                    job = _normalize(
                        {
                            "id": item.get("id"),
                            "title": item.get("title"),
                            "company_name": slug,
                            "url": item.get("jobUrl") or item.get("applyUrl") or "",
                            "location": item.get("location") or "",
                            "description": item.get("descriptionPlain") or item.get("descriptionHtml") or "",
                        },
                        "ashby",
                    )
                    job["ats"] = "ashby"
                    pool.append(job)
        return slug, pool, ""
    except Exception as exc:
        return slug, [], exc.__class__.__name__


def fetch_company_boards(
    terms: list[str],
    per_board: int = 15,
    countries: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Greenhouse, Lever, and Ashby publish job lists. Each board keeps its own share."""
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    tasks = (
        [("greenhouse", board) for board in GREENHOUSE_BOARDS]
        + [("lever", site) for site in LEVER_SITES]
        + [("ashby", board) for board in ASHBY_BOARDS]
    )
    loaded: dict[tuple[str, str], tuple[str, list[dict[str, Any]], str]] = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(_load_board, kind, slug): (kind, slug) for kind, slug in tasks}
        for future in as_completed(futures):
            kind, slug = futures[future]
            loaded[(kind, slug)] = future.result()

    for kind, slug in tasks:
        _slug, pool, error = loaded[(kind, slug)]
        if error:
            _note_board(board_display_name(slug), error=error)
            continue
        kept = select_board_jobs(pool, terms, countries, per_board)
        _note_board(board_display_name(slug), len(pool), len(kept))
        for job in kept:
            key = job["external_id"]
            if not key or key in seen:
                continue
            seen.add(key)
            jobs.append(job)
    return jobs


def discover_candidates(
    limit: int = 400,
    terms: list[str] | None = None,
    countries: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Each board and remote feed keeps its own share. One full board cannot crowd out the rest."""
    search_terms = [term.strip() for term in (terms or list(SEARCH_TERMS)) if term and term.strip()]
    if not search_terms:
        search_terms = list(SEARCH_TERMS)
    _BOARD_REPORT.clear()
    per_board = max(PER_BOARD, limit // (company_board_count() + 6))
    # A feed is searched once per skill, so it gets a share for each of those searches.
    per_feed = per_board * max(2, len(_feed_terms(search_terms)))
    raw: list[dict[str, Any]] = []
    try:
        raw.extend(fetch_remotive(limit=per_feed, terms=search_terms, countries=countries))
    except Exception as exc:
        _note_board("Remotive", error=exc.__class__.__name__)
    for fetch in (fetch_remoteok, fetch_himalayas, fetch_jobicy, fetch_workingnomads, fetch_arbeitnow):
        raw.extend(fetch(search_terms, per_feed, countries))
    try:
        raw.extend(fetch_company_boards(search_terms, per_board=per_board, countries=countries))
    except Exception as exc:
        _note_board("Company boards", error=exc.__class__.__name__)
    # A company's own form beats a feed listing of the same job.
    raw.sort(key=lambda job: 0 if job.get("ats") else 1)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    source_of: dict[str, str] = {}
    for job in raw:
        if not _matches(job, search_terms) or not job.get("external_id") or job["external_id"] in seen:
            continue
        # One board can list a title in several cities. Two feeds listing it is one job.
        key = job_key(job)
        if source_of.setdefault(key, job.get("source") or "") != (job.get("source") or ""):
            continue
        seen.add(job["external_id"])
        deduped.append(job)
    return deduped


def job_key(job: dict[str, Any]) -> str:
    """The same opening posted on two feeds has the same company and title."""
    company = re.sub(r"[^a-z0-9]+", "", str(job.get("company") or "").lower())
    role = re.sub(r"[^a-z0-9]+", " ", str(job.get("role") or "").lower()).strip()
    return f"{company}|{role}"