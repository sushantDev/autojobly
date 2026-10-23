from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from sqlite3 import IntegrityError  # noqa: E402

from src.accounts import (  # noqa: E402
    PUBLIC_SKILLS,
    counts_for_user,
    get_profile_row,
    application_was_sent,
    get_user_job,
    list_matches,
    list_public_roles,
    public_apply_stats,
    public_skill,
    profile_for_user,
    save_profile,
    today_boards,
    update_match_status,
)
from src.auth import authenticate, create_user, get_user, get_user_by_email, set_subscription, user_is_paying, uses_oauth_password  # noqa: E402
from src.billing import (  # noqa: E402
    PRICE_LABEL,
    PRICE_USD,
    apply_dodo_subscription,
    apply_dodo_webhook,
    apply_webhook,
    approve_payment,
    confirm_dodo_payment,
    business_desk,
    claim_payment,
    create_checkout,
    create_dodo_checkout,
    dev_billing_enabled,
    dodo_configured,
    dodo_portal_url,
    fetch_dodo_subscription,
    is_owner,
    latest_payment,
    open_instant_payment,
    pay_url,
    reject_payment,
    request_payment,
    save_khalti_link,
    save_pay_url,
    stripe_configured,
)
from src.cv import extract_cv, pdf_to_text  # noqa: E402
from src.db import add_event, db, init_db  # noqa: E402
from src.oauth import (  # noqa: E402
    account_for_email,
    authorization_url,
    exchange_code,
    google_configured,
    linkedin_configured,
    new_state,
)
from src.paths import CV_DIR  # noqa: E402
from src.defaults import FREE_APPLICATIONS  # noqa: E402
from src.discover import company_board_count  # noqa: E402
from src.pipeline import refresh_and_score, score_user  # noqa: E402
from src.settings import is_public_https, public_base  # noqa: E402

PORTAL_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PORTAL_DIR / "templates"))


templates.env.globals["public_base"] = public_base

app = FastAPI(title="Autojobly")
app.mount("/static", StaticFiles(directory=str(PORTAL_DIR / "static")), name="static")

OPEN_PATHS = {
    "/login",
    "/signup",
    "/billing/webhook",
    "/billing/dodo-webhook",
    "/terms",
    "/privacy",
    "/roles",
    "/robots.txt",
    "/sitemap.xml",
    "/health",
}
class LoginRequired:
    """Runs inside SessionMiddleware so scope['session'] is already loaded."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if (
            path.startswith("/static")
            or path.startswith("/auth/")
            or path.startswith("/roles")
            or path in OPEN_PATHS
        ):
            await self.app(scope, receive, send)
            return
        user_id = (scope.get("session") or {}).get("user_id")
        if not user_id:
            if path == "/":
                await self.app(scope, receive, send)
                return
            response = RedirectResponse("/login", status_code=303)
            await response(scope, receive, send)
            return
        gated = (
            path == "/"
            or path.startswith("/jobs")
            or path.startswith("/today")
            or path.startswith("/queue")
            or path.startswith("/profile")
            or path.startswith("/actions")
        )
        if gated and get_profile_row(int(user_id)) is None:
            response = RedirectResponse("/onboarding", status_code=303)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


app.add_middleware(LoginRequired)
def _session_secret() -> str:
    secret = os.environ.get("SESSION_SECRET", "").strip()
    if secret:
        return secret
    if is_public_https():
        raise RuntimeError("Set SESSION_SECRET before serving Autojobly on https")
    return "dev-job-agent-session-change-me"


app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    same_site="lax",
    https_only=is_public_https(),
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _uid(request: Request) -> int:
    return int(request.session["user_id"])


def _ctx(request: Request, **extra):
    user = get_user(_uid(request))
    profile = profile_for_user(user["id"]) if user else None
    counts = counts_for_user(user["id"]) if user and profile else {}
    notice = request.query_params.get("notice") or ""
    error = request.query_params.get("error") or ""
    base = {
        "request": request,
        "user": user,
        "profile": profile,
        "counts": counts,
        "total": sum(count for status, count in counts.items() if status != "SKIP"),
        "status_labels": {
            "QUEUED": "Ready to send",
            "APPLYING": "Sending",
            "APPLIED": "Sent",
            "REPLIED": "Got a reply",
            "PARTIAL": "Needs you",
            "BLOCKED": "Stopped",
            "READY": "You send this",
            "SKIP": "Not a match",
            "CLOSED": "Closed",
        },
        "lane_labels": {
            "auto": "We send this",
            "manual": "You send this",
            "skip": "Not a match",
        },
        "needs_you": sum(counts.get(s, 0) for s in ("PARTIAL", "BLOCKED", "READY", "APPLYING")),
        "paying": user_is_paying(user),
        "owner": is_owner(user),
        "price_label": PRICE_LABEL,
        "price_usd": PRICE_USD,
        "notice": notice,
        "error": error,
        "status": request.query_params.get("status"),
    }
    base.update(extra)
    return base


def _valid_email(email: str) -> bool:
    return "@" in email and "." in email.split("@")[-1]


def _auth_flags(request: Request) -> dict:
    return {
        "request": request,
        "error": request.query_params.get("error", ""),
        "notice": request.query_params.get("notice", ""),
        "google_on": google_configured(),
        "linkedin_on": linkedin_configured(),
        "price_label": PRICE_LABEL,
    }


def _enter(request: Request, user: dict) -> RedirectResponse:
    request.session["user_id"] = user["id"]
    if get_profile_row(user["id"]) is None:
        return RedirectResponse("/onboarding", status_code=303)
    return RedirectResponse("/today", status_code=303)


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "signup.html", _auth_flags(request))


@app.post("/signup")
def signup_post(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    if not _valid_email(email):
        return RedirectResponse("/signup?error=Enter+a+valid+email", status_code=303)
    if len(password) < 8:
        return RedirectResponse("/signup?error=Password+must+be+at+least+8+characters", status_code=303)
    try:
        user = create_user(email, password)
    except IntegrityError:
        return RedirectResponse("/signup?error=An+account+with+that+email+already+exists", status_code=303)
    request.session["user_id"] = user["id"]
    return RedirectResponse("/onboarding", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", _auth_flags(request))


@app.post("/login")
def login_post(request: Request, email: str = Form(...), password: str = Form(...)):
    existing = get_user_by_email(email)
    if uses_oauth_password(existing):
        return RedirectResponse(
            "/login?error=This+account+uses+Google+or+LinkedIn.+Continue+with+that+button.",
            status_code=303,
        )
    user = authenticate(email, password)
    if not user:
        return RedirectResponse("/login?error=Email+or+password+is+wrong", status_code=303)
    return _enter(request, user)


@app.get("/auth/{provider}")
def oauth_start(request: Request, provider: str):
    if provider not in {"google", "linkedin"}:
        return RedirectResponse("/login?error=Unknown+sign-in", status_code=303)
    state = new_state()
    request.session["oauth_state"] = state
    request.session["oauth_provider"] = provider
    url = authorization_url(provider, state)
    if not url:
        label = "Google" if provider == "google" else "LinkedIn"
        return RedirectResponse(
            f"/login?error={label}+sign-in+is+not+connected+yet.+Use+email+and+password.",
            status_code=303,
        )
    return RedirectResponse(url, status_code=303)


@app.get("/auth/{provider}/callback")
def oauth_callback(request: Request, provider: str, code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse("/login?error=Sign-in+was+cancelled", status_code=303)
    expected = request.session.get("oauth_state") or ""
    if not expected or state != expected or request.session.get("oauth_provider") != provider:
        return RedirectResponse("/login?error=Sign-in+expired.+Try+again.", status_code=303)
    request.session.pop("oauth_state", None)
    request.session.pop("oauth_provider", None)
    try:
        profile = exchange_code(provider, code)
    except Exception:
        return RedirectResponse("/login?error=Sign-in+could+not+be+confirmed", status_code=303)
    email = (profile.get("email") or "").strip()
    user = account_for_email(email, provider)
    if not user:
        return RedirectResponse(
            "/login?error=That+account+did+not+share+an+email.+Use+email+and+password.",
            status_code=303,
        )
    if profile.get("name"):
        request.session["oauth_name"] = profile["name"]
    return _enter(request, user)


@app.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/onboarding", response_class=HTMLResponse)
def onboarding_form(request: Request):
    return templates.TemplateResponse(
        request,
        "onboarding.html",
        _ctx(request, page="onboarding"),
    )


@app.post("/onboarding")
async def onboarding_post(
    request: Request,
    cv_text: str = Form(""),
    cv_file: UploadFile | None = File(None),
):
    user_id = _uid(request)
    text = (cv_text or "").strip()
    stored_path = ""
    if cv_file is not None and cv_file.filename:
        raw = await cv_file.read()
        suffix = Path(cv_file.filename).suffix.lower()
        if suffix not in {".pdf", ".txt", ".md"}:
            return RedirectResponse("/onboarding?error=Upload+a+PDF+or+text+CV", status_code=303)
        dest = CV_DIR / f"{user_id}{suffix}"
        dest.write_bytes(raw)
        stored_path = str(dest)
        if suffix == ".pdf":
            try:
                text = pdf_to_text(str(dest)) or text
            except Exception:
                return RedirectResponse("/onboarding?error=Could+not+read+that+PDF.+Paste+the+text+instead", status_code=303)
        else:
            text = raw.decode("utf-8", errors="replace")
    if len(text) < 40:
        return RedirectResponse("/onboarding?error=Paste+more+of+the+CV+so+it+can+be+read", status_code=303)
    facts = extract_cv(text)
    facts["cv_path"] = stored_path
    facts["consent"] = False
    existing = get_profile_row(user_id)
    if existing and existing.get("ask_usd_monthly"):
        facts["ask_usd_monthly"] = existing["ask_usd_monthly"]
    if existing and existing.get("ask_eur_monthly"):
        facts["ask_eur_monthly"] = existing["ask_eur_monthly"]
    user = get_user(user_id)
    if not facts["email"] and user:
        facts["email"] = user["email"]
    if not facts.get("name"):
        facts["name"] = (request.session.get("oauth_name") or "").strip()
    save_profile(user_id, facts)
    return RedirectResponse("/onboarding/review", status_code=303)


@app.get("/onboarding/review", response_class=HTMLResponse)
def review_form(request: Request):
    row = get_profile_row(_uid(request))
    if not row:
        return RedirectResponse("/onboarding", status_code=303)
    return templates.TemplateResponse(
        request,
        "review.html",
        _ctx(request, row=row, page="onboarding"),
    )


@app.post("/onboarding/review")
def review_post(
    request: Request,
    background: BackgroundTasks,
    name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    linkedin: str = Form(""),
    github: str = Form(""),
    portfolio: str = Form(""),
    location_country: str = Form(""),
    ask_usd_monthly: str = Form(""),
    ask_eur_monthly: str = Form(""),
    skills: str = Form(""),
    years_experience: str = Form(""),
    needs_sponsorship: str = Form(""),
    cv_text: str = Form(""),
    cv_path: str = Form(""),
    consent: str = Form(""),
):
    if consent != "yes":
        return RedirectResponse("/onboarding/review?error=Consent+is+required+before+applications+go+out", status_code=303)
    if not location_country.strip() or not ask_usd_monthly.strip().isdigit():
        return RedirectResponse(
            "/onboarding/review?error=Country+and+a+monthly+USD+ask+are+required",
            status_code=303,
        )
    user_id = _uid(request)
    previous = get_profile_row(user_id) or {}
    save_profile(
        user_id,
        {
            "name": name.strip(),
            "email": email.strip(),
            "phone": phone.strip(),
            "linkedin": linkedin.strip(),
            "github": github.strip(),
            "portfolio": portfolio.strip(),
            "location_country": location_country.strip(),
            "ask_usd_monthly": int(ask_usd_monthly),
            "ask_eur_monthly": int(ask_eur_monthly) if ask_eur_monthly.strip().isdigit() else None,
            "skills": skills,
            "years_experience": years_experience.strip(),
            "needs_sponsorship": needs_sponsorship.strip().lower(),
            "cv_text": cv_text or previous.get("cv_text") or "",
            "cv_path": cv_path or previous.get("cv_path") or "",
            "consent": True,
        },
    )
    score_user(user_id)
    if os.environ.get("JOB_AGENT_OFFLINE") != "1":
        terms = [part.strip() for part in skills.split(",") if part.strip()]
        background.add_task(refresh_and_score, user_id, terms or None)
        return RedirectResponse(
            "/today?notice=Profile+saved.+The+boards+are+being+searched+for+your+skills.+Refresh+in+2+minutes+for+more",
            status_code=303,
        )
    return RedirectResponse("/today?notice=Profile+saved.+Here+are+your+scored+jobs", status_code=303)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not request.session.get("user_id"):
        replies, sent = public_apply_stats()
        return templates.TemplateResponse(
            request,
            "landing.html",
            {
                "request": request,
                "price_label": PRICE_LABEL,
                "replies": replies,
                "sent": sent,
                "board_count": company_board_count(),
                "notice": request.query_params.get("notice", ""),
                "error": request.query_params.get("error", ""),
            },
        )
    return today_page(request)


@app.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request):
    return templates.TemplateResponse(
        request,
        "terms.html",
        {"request": request, "price_label": PRICE_LABEL},
    )


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {"request": request, "price_label": PRICE_LABEL})


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /today",
        "Disallow: /jobs",
        "Disallow: /queue",
        "Disallow: /profile",
        "Disallow: /billing",
        "Disallow: /onboarding",
        "Disallow: /run",
    ]
    base = public_base()
    if base.startswith("https://"):
        lines.append(f"Sitemap: {base}/sitemap.xml")
    return PlainTextResponse("\n".join(lines) + "\n")


@app.get("/health")
def health() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/sitemap.xml")
def sitemap_xml():
    base = public_base()
    paths = ["/", "/signup", "/login", "/roles", "/terms", "/privacy"]
    paths.extend(f"/roles/{slug}" for slug, _label, _pattern in PUBLIC_SKILLS)
    urls = "\n".join(f"  <url><loc>{base}{path}</loc></url>" for path in paths)
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls}
</urlset>
"""
    return PlainTextResponse(body, media_type="application/xml")


@app.get("/roles", response_class=HTMLResponse)
def roles_index(request: Request):
    jobs = list_public_roles(limit=40)
    return templates.TemplateResponse(
        request,
        "roles.html",
        {
            "request": request,
            "price_label": PRICE_LABEL,
            "skills": PUBLIC_SKILLS,
            "jobs": jobs,
            "skill": None,
            "skill_label": "",
        },
    )


@app.get("/roles/{slug}", response_class=HTMLResponse)
def roles_skill(request: Request, slug: str):
    chosen = public_skill(slug)
    if not chosen:
        return RedirectResponse("/roles", status_code=303)
    jobs = list_public_roles(chosen[0], limit=80)
    return templates.TemplateResponse(
        request,
        "roles.html",
        {
            "request": request,
            "price_label": PRICE_LABEL,
            "skills": PUBLIC_SKILLS,
            "jobs": jobs,
            "skill": chosen[0],
            "skill_label": chosen[1],
        },
    )


@app.get("/today", response_class=HTMLResponse)
def today_page(request: Request):
    boards = today_boards(_uid(request))
    with db() as conn:
        runs = conn.execute(
            "SELECT * FROM runs WHERE user_id = ? ORDER BY id DESC LIMIT 8",
            (_uid(request),),
        ).fetchall()
    return templates.TemplateResponse(
        request,
        "today.html",
        _ctx(request, boards=boards, runs=runs, page="today"),
    )


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    status: str | None = None,
    q: str | None = None,
):
    jobs = list_matches(_uid(request), status=status, q=q, limit=300)
    return templates.TemplateResponse(
        request,
        "jobs.html",
        _ctx(request, jobs=jobs, status=status, q=q or "", page="jobs"),
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    job = get_user_job(_uid(request), job_id)
    if not job:
        return RedirectResponse("/jobs", status_code=303)
    with db() as conn:
        events = conn.execute(
            """
            SELECT * FROM events
            WHERE job_id = ? AND user_id = ?
            ORDER BY id DESC LIMIT 50
            """,
            (job_id, _uid(request)),
        ).fetchall()
    ctx = _ctx(request, job=job, events=events, page="jobs")
    profile = ctx.get("profile")
    letter = ""
    years_answer = ""
    sponsor_answer = ""
    if profile:
        from src.cv import tailored_cv
        from src.letters import answer_for_label, cover_letter

        letter = cover_letter(profile, job)
        ctx["tailored_cv"] = tailored_cv(profile, job)
        years_answer = answer_for_label("Years of experience", profile, job) or ""
        sponsor_answer = answer_for_label("Will you require visa sponsorship?", profile, job) or ""
    from src.apply.runner import applied_count

    ctx["letter"] = letter
    ctx["years_answer"] = years_answer
    ctx["sponsor_answer"] = sponsor_answer
    ctx["app_submitted"] = application_was_sent(_uid(request), job_id)
    ctx["free_apply"] = (
        not ctx["paying"]
        and applied_count(_uid(request)) < FREE_APPLICATIONS
        and (job.get("ats") in {"ashby", "greenhouse", "lever"})
        and job.get("status") in {"QUEUED", "READY", "PARTIAL"}
    )
    return templates.TemplateResponse(request, "job_detail.html", ctx)


@app.post("/jobs/{job_id}/sample")
def send_free_application(request: Request, job_id: int):
    from src.apply.runner import send_sample

    result = send_sample(_uid(request), job_id, headless=True)
    if result.get("error"):
        return RedirectResponse(f"/jobs/{job_id}?error=" + result["error"].replace(" ", "+"), status_code=303)
    status = result.get("status") or "PARTIAL"
    return RedirectResponse(
        f"/jobs/{job_id}?notice=Free+application+finished.+Status+{status}",
        status_code=303,
    )


_STATUS_CHOICES = {"QUEUED", "APPLYING", "PARTIAL", "BLOCKED", "READY", "SKIP", "CLOSED"}


@app.post("/jobs/{job_id}/status")
def update_status(request: Request, job_id: int, status: str = Form(...), notes: str = Form("")):
    new = status.strip().upper()
    user_id = _uid(request)
    job = get_user_job(user_id, job_id)
    if not job:
        return RedirectResponse("/jobs?error=That+job+is+not+on+this+account", status_code=303)
    current = (job.get("status") or "").upper()
    if new == "REPLIED":
        if current != "APPLIED" or not application_was_sent(user_id, job_id):
            return RedirectResponse(
                f"/jobs/{job_id}?error=A+reply+can+be+marked+only+after+Autojobly+has+sent+this+application",
                status_code=303,
            )
    elif new not in _STATUS_CHOICES or current in {"APPLIED", "REPLIED"}:
        return RedirectResponse(
            f"/jobs/{job_id}?error=That+status+cannot+be+set+from+here",
            status_code=303,
        )
    update_match_status(user_id, job_id, new, notes.strip())
    with db() as conn:
        add_event(conn, job_id, "status_change", f"→ {new}", user_id=user_id)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request):
    jobs = list_matches(_uid(request), status="needs_you", limit=200)
    return templates.TemplateResponse(
        request,
        "queue.html",
        _ctx(request, jobs=jobs, page="queue"),
    )


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request):
    ctx = _ctx(request, page="profile")
    sample = ""
    if ctx.get("profile"):
        from src.letters import cover_letter

        sample = cover_letter(ctx["profile"], {"role": "the open role", "company": "the company"})
    ctx["cover_letter"] = sample
    return templates.TemplateResponse(request, "profile.html", ctx)


@app.get("/profile/refined.txt")
def refined_download(request: Request):
    row = get_profile_row(_uid(request))
    if not row or not row.get("refined_cv"):
        return RedirectResponse("/onboarding", status_code=303)
    return PlainTextResponse(row["refined_cv"], headers={"Content-Disposition": "inline; filename=refined-cv.txt"})


@app.get("/billing", response_class=HTMLResponse)
def billing_page(request: Request):
    user = get_user(_uid(request))
    payment_id = (request.query_params.get("payment_id") or "").strip()
    subscription_id = (request.query_params.get("subscription_id") or "").strip()
    dodo_error = ""
    # The return link only says where to look. Dodo's own record decides whether access turns on.
    if user and payment_id:
        try:
            confirm_dodo_payment(user, payment_id)
            user = get_user(user["id"])
        except ValueError as exc:
            dodo_error = str(exc)
        except Exception:
            dodo_error = "The payment could not be checked yet. Refresh this page."
    elif user and subscription_id.startswith("sub_") and len(subscription_id) <= 80:
        try:
            subscription = fetch_dodo_subscription(subscription_id)
            with db() as conn:
                apply_dodo_subscription(conn, subscription, expected_user_id=int(user["id"]))
            user = get_user(user["id"])
            if not user_is_paying(user):
                dodo_error = "Dodo has not confirmed the card yet. This page updates when it does."
        except ValueError as exc:
            dodo_error = str(exc)
        except Exception:
            dodo_error = "The payment could not be checked yet. Refresh this page."
    payment = latest_payment(user["id"]) if user else None
    if user and not user_is_paying(user):
        instant = open_instant_payment(user["id"])
        if instant:
            payment = instant
    open_payment = payment if payment and payment["status"] in {"needs_link", "link_ready", "claimed"} else None
    ctx = _ctx(
        request,
        page="billing",
        stripe_on=stripe_configured(),
        dodo_on=dodo_configured(),
        dodo_customer=bool(user and user.get("dodo_customer_id")),
        dev_on=dev_billing_enabled(),
        checkout=request.query_params.get("checkout", ""),
        payment=open_payment,
    )
    if dodo_error and not ctx.get("error"):
        ctx["error"] = dodo_error
    return templates.TemplateResponse(request, "billing.html", ctx)


@app.get("/run", response_class=HTMLResponse)
def run_page(request: Request):
    user = get_user(_uid(request))
    if not is_owner(user):
        return RedirectResponse("/billing", status_code=303)
    return templates.TemplateResponse(
        request,
        "run.html",
        _ctx(request, page="run", desk=business_desk(), pay_url=pay_url()),
    )


@app.post("/admin/pay-url")
def admin_pay_url(request: Request, pay_link: str = Form(...)):
    user = get_user(_uid(request))
    if not is_owner(user):
        return RedirectResponse("/billing", status_code=303)
    try:
        save_pay_url(pay_link)
    except ValueError:
        return RedirectResponse("/run?error=The+pay+link+must+start+with+https", status_code=303)
    return RedirectResponse("/run?notice=Customers+can+pay+from+that+link+immediately", status_code=303)


@app.post("/admin/users/{user_id}/renew")
def admin_renew(request: Request, user_id: int):
    user = get_user(_uid(request))
    if not is_owner(user):
        return RedirectResponse("/billing", status_code=303)
    request_payment(user_id)
    return RedirectResponse("/run?notice=Renewal+is+in+Make+a+link", status_code=303)


@app.post("/billing/request-link")
def billing_request_link(request: Request):
    request_payment(_uid(request))
    return RedirectResponse("/billing?notice=Payment+request+sent.+The+card+link+appears+here+when+it+is+ready", status_code=303)


@app.post("/billing/claim")
def billing_claim(request: Request, reference: str = Form(...)):
    try:
        claim_payment(_uid(request), reference)
    except ValueError as exc:
        return RedirectResponse("/billing?error=" + str(exc).replace(" ", "+"), status_code=303)
    return RedirectResponse("/billing?notice=Reference+saved.+Auto-apply+turns+on+after+the+NPR+is+confirmed", status_code=303)


@app.post("/admin/payments/{payment_id}/link")
def admin_save_link(request: Request, payment_id: int, khalti_url: str = Form(...)):
    user = get_user(_uid(request))
    if not is_owner(user):
        return RedirectResponse("/billing?error=Only+the+operator+can+add+a+payment+link", status_code=303)
    try:
        save_khalti_link(payment_id, khalti_url)
    except ValueError as exc:
        return RedirectResponse("/billing?error=" + str(exc).replace(" ", "+"), status_code=303)
    return RedirectResponse("/run?notice=Link+saved.+The+customer+can+pay+it+on+Billing", status_code=303)


@app.post("/admin/payments/{payment_id}/approve")
def admin_approve(request: Request, payment_id: int):
    user = get_user(_uid(request))
    if not is_owner(user):
        return RedirectResponse("/billing?error=Only+the+operator+can+confirm+payment", status_code=303)
    try:
        until = approve_payment(payment_id)
    except ValueError as exc:
        return RedirectResponse("/billing?error=" + str(exc).replace(" ", "+"), status_code=303)
    return RedirectResponse(f"/run?notice=Active+until+{until}", status_code=303)


@app.post("/admin/payments/{payment_id}/reject")
def admin_reject(request: Request, payment_id: int):
    user = get_user(_uid(request))
    if not is_owner(user):
        return RedirectResponse("/billing?error=Only+the+operator+can+reject+a+payment", status_code=303)
    reject_payment(payment_id)
    return RedirectResponse("/run?notice=Payment+closed+without+activating", status_code=303)


@app.post("/billing/checkout")
def billing_checkout(request: Request):
    user = get_user(_uid(request))
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if dodo_configured():
        try:
            url = create_dodo_checkout(user)
        except Exception:
            return RedirectResponse("/billing?error=Card+checkout+could+not+start.+Try+again+in+a+minute", status_code=303)
        return RedirectResponse(url, status_code=303)
    if not stripe_configured():
        return RedirectResponse("/billing?error=Card+checkout+is+not+connected+on+this+server", status_code=303)
    try:
        url = create_checkout(user)
    except Exception:
        return RedirectResponse("/billing?error=Could+not+start+checkout", status_code=303)
    return RedirectResponse(url, status_code=303)


@app.post("/billing/manage")
def billing_manage(request: Request):
    user = get_user(_uid(request))
    try:
        url = dodo_portal_url(user or {})
    except ValueError as exc:
        return RedirectResponse("/billing?error=" + str(exc).replace(" ", "+"), status_code=303)
    except Exception:
        return RedirectResponse("/billing?error=The+billing+page+could+not+open.+Try+again+in+a+minute", status_code=303)
    return RedirectResponse(url, status_code=303)


@app.post("/billing/dodo-webhook")
async def dodo_webhook(request: Request):
    body = await request.body()
    try:
        apply_dodo_webhook(body, {key.lower(): value for key, value in request.headers.items()})
    except PermissionError:
        return PlainTextResponse("invalid signature", status_code=401)
    except RuntimeError:
        return PlainTextResponse("not configured", status_code=503)
    return PlainTextResponse("ok")


@app.post("/billing/dev-activate")
def billing_dev(request: Request):
    if not dev_billing_enabled():
        return RedirectResponse("/billing?error=Dev+activation+is+off", status_code=303)
    set_subscription(_uid(request), "active")
    return RedirectResponse("/billing?notice=Dev+subscription+is+active", status_code=303)


@app.post("/billing/webhook")
async def billing_webhook(request: Request):
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        apply_webhook(payload, signature)
    except Exception:
        return PlainTextResponse("invalid", status_code=400)
    return PlainTextResponse("ok")


@app.post("/actions/refresh-scores")
def action_refresh(request: Request):
    result = score_user(_uid(request))
    return RedirectResponse(
        f"/today?notice=Scored+jobs.+Shortlist+{result.get('shortlisted', 0)}",
        status_code=303,
    )


@app.post("/actions/fetch-jobs")
def action_fetch(request: Request, background: BackgroundTasks):
    background.add_task(refresh_and_score, _uid(request))
    return RedirectResponse(
        "/today?notice=Searching+every+board+now.+Refresh+in+2+minutes+for+new+jobs",
        status_code=303,
    )


@app.post("/account/delete")
def delete_account(request: Request):
    user_id = _uid(request)
    row = get_profile_row(user_id) or {}
    cv_path = str(row.get("cv_path") or "")
    with db() as conn:
        for table in ("events", "matches", "payments", "runs", "profiles"):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    if cv_path:
        stored = Path(cv_path)
        try:
            if stored.is_file() and stored.resolve().is_relative_to(CV_DIR.resolve()):
                stored.unlink()
        except Exception:
            pass
    request.session.clear()
    return RedirectResponse("/?notice=Account+deleted", status_code=303)


@app.post("/actions/auto-apply")
def action_auto_apply(request: Request):
    user = get_user(_uid(request))
    if not user_is_paying(user):
        return RedirectResponse("/billing?error=Subscribe+to+turn+on+auto-apply", status_code=303)
    profile = profile_for_user(_uid(request))
    if not profile or not profile.get("consent"):
        return RedirectResponse("/onboarding/review?error=Consent+is+required", status_code=303)
    from src.apply.runner import apply_ready_jobs

    result = apply_ready_jobs(_uid(request), limit=5, headless=True)
    if result.get("error"):
        return RedirectResponse("/today?error=" + result["error"].replace(" ", "+"), status_code=303)
    return RedirectResponse(
        f"/today?notice=Auto-apply+finished.+Submitted+{result.get('applied', 0)}",
        status_code=303,
    )
