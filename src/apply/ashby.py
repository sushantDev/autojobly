from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError as PWTimeout

from ..letters import answer_for_label, answer_for_textarea, cover_letter
from ..paths import CV_DIR


def _has_visible_captcha(page: Page) -> bool:
    """Detect a CAPTCHA challenge the user must solve (not just invisible sitekey)."""
    try:
        body = page.inner_text("body").lower()
    except Exception:
        body = ""
    if "cf-turnstile" in body or "hcaptcha" in body:
        return True
    # Challenge iframe (not the tiny badge)
    if page.locator("iframe[src*='recaptcha'][src*='bframe'], iframe[src*='hcaptcha.com'], iframe[src*='turnstile']").count():
        return True
    if page.locator("text=/verify you are human|i'm not a robot/i").count():
        return True
    return False


def _application_url(url: str) -> str:
    u = url.rstrip("/")
    if u.endswith("/application"):
        return u
    parts = urlparse(u)
    if "ashbyhq.com" in parts.netloc and parts.path.count("/") >= 2:
        return u + "/application"
    return u


def _field(page: Page, question: str):
    """Ashby field container that includes the question text."""
    loc = page.locator(".ashby-application-form-field-entry").filter(has_text=question)
    if loc.count() == 0:
        loc = page.locator(".ashby-application-form-input-radio-group").filter(has_text=question)
    if loc.count() == 0:
        loc = page.locator("[class*='_fieldEntry_']").filter(has_text=question)
    return loc.first if loc.count() else None


def _answer(page: Page, question: str, answer: str) -> bool:
    """Click Yes/No (data-option) or radio/label text inside a field."""
    try:
        field = _field(page, question)
        if field is None:
            return False

        # Prompt Yes/No: <button data-option="yes|no">
        opt = answer.strip().lower()
        if opt in ("yes", "no"):
            btn = field.locator(
                f"button.ashby-application-form-input-yesno-option[data-option='{opt}']"
            )
            if btn.count() == 0:
                btn = field.locator(f"button[data-option='{opt}']")
            if btn.count():
                btn.first.click(force=True, timeout=4000)
                page.wait_for_timeout(200)
                return True
            # Fallback: exact button text
            by_text = field.locator("button").filter(has_text=answer)
            if by_text.count():
                by_text.first.click(force=True, timeout=4000)
                page.wait_for_timeout(200)
                return True

        # Experience radios / plain option text
        label = field.get_by_text(answer, exact=True)
        if label.count():
            label.last.click(force=True, timeout=4000)
            page.wait_for_timeout(200)
            return True
    except Exception:
        return False
    return False


def _fill_label(page: Page, label: str, value: str) -> bool:
    if not value:
        return False
    try:
        loc = page.get_by_label(label, exact=False)
        if loc.count() == 0:
            return False
        loc.first.fill(value)
        return True
    except Exception:
        return False


_US_NAMES = {"united states", "united states of america", "usa", "us", "u.s.", "u.s.a."}


def location_answers(profile: dict[str, Any]) -> list[tuple[str, str]]:
    """Yes/no answers we can support from the country on the profile. No invented tenure."""
    country = (profile.get("location_country") or "").strip()
    if not country:
        return []
    in_us = country.lower() in _US_NAMES
    return [("Are you physically located in the US?", "Yes" if in_us else "No")]


def profile_summary(profile: dict[str, Any]) -> str:
    """Short note built only from name, country, and stated skills."""
    name = (profile.get("name") or "Candidate").strip()
    country = (profile.get("location_country") or "").strip()
    skills = (profile.get("stack") or {}).get("primary") or []
    parts = [name]
    if country:
        parts.append(f"based in {country}")
    sentence = " ".join(parts)
    if skills:
        sentence += ". Skills stated on the CV: " + ", ".join(skills[:12])
    return sentence


def compensation_text(profile: dict[str, Any]) -> str:
    ask = profile.get("compensation") or {}
    usd = ask.get("ask_usd_monthly") or 0
    eur = ask.get("ask_eur_monthly") or 0
    if usd and eur:
        return f"${usd} / €{eur} per month"
    if usd:
        return f"${usd} per month"
    if eur:
        return f"€{eur} per month"
    return ""


def _cover_file(profile: dict[str, Any], job: dict[str, Any]) -> Path:
    CV_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", (profile.get("email") or "applicant").lower()).strip("-")[:40]
    job_id = job.get("id") or "job"
    path = CV_DIR / f"{slug or 'applicant'}-{job_id}-cover.txt"
    path.write_text(cover_letter(profile, job), encoding="utf-8")
    return path


def _fill_written_fields(page: Page, profile: dict[str, Any], job: dict[str, Any]) -> None:
    """Fill cover letters and other written answers. Skip tenure and legal questions."""
    letter_path = _cover_file(profile, job)
    fields = page.locator(".ashby-application-form-field-entry, [class*='_fieldEntry_']")
    total = min(fields.count(), 40)
    for index in range(total):
        field = fields.nth(index)
        try:
            label = field.inner_text(timeout=1000)[:400]
        except Exception:
            continue
        low = label.lower()
        file_input = field.locator('input[type="file"]')
        if file_input.count() and ("cover" in low or "letter" in low):
            try:
                file_input.first.set_input_files(str(letter_path))
            except Exception:
                pass
        area = field.locator("textarea")
        if area.count():
            text = answer_for_textarea(label, profile, job)
            if text:
                try:
                    area.first.fill(text[:5000])
                except Exception:
                    pass
            continue
        text = answer_for_label(label, profile, job)
        if not text or "\n" in text:
            continue
        if text in {"Yes", "No"}:
            _answer(page, label.split("\n", 1)[0][:80], text)
        box = field.locator("input[type='text'], input[type='number'], input:not([type])")
        if box.count():
            try:
                box.first.fill(text[:300])
            except Exception:
                pass


def apply_ashby(page: Page, job: dict[str, Any], profile: dict[str, Any], cv_path: Path) -> dict[str, Any]:
    """Fill + submit Ashby forms (Hostinger-style + Prompt-style)."""
    url = _application_url(job["url"])
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)

    if _has_visible_captcha(page):
        return {
            "ok": False,
            "status": "BLOCKED",
            "message": "CAPTCHA present — solve manually (cannot auto-bypass)",
        }

    if cv_path.exists():
        files = page.locator('input[type="file"]')
        if files.count() > 0:
            files.first.set_input_files(str(cv_path))
            page.wait_for_timeout(3500)

    name = profile.get("name") or ""
    country = (profile.get("location_country") or "").strip()
    _fill_label(page, "Full name", name)
    _fill_label(page, "Name", name)
    _fill_label(page, "Email", profile.get("email") or "")
    _fill_label(page, "Phone", profile.get("phone") or "")
    _fill_label(page, "LinkedIn", profile.get("linkedin") or "")
    _fill_label(page, "GitHub", profile.get("github") or "")
    _fill_label(page, "Portfolio", profile.get("portfolio") or "")

    comp = compensation_text(profile)
    if comp:
        _fill_label(page, "compensation", comp)
        _fill_label(page, "salary", comp)
        _fill_label(page, "What are your compensation expectations", comp)

    if country and country.lower() != "lithuania" and page.get_by_text("Outside Lithuania", exact=True).count():
        page.get_by_text("Outside Lithuania", exact=True).first.click()
        page.wait_for_timeout(500)
        loc = page.get_by_placeholder("Start typing")
        if loc.count():
            loc.first.click()
            loc.first.fill(country)
            page.wait_for_timeout(1200)
            opt = page.get_by_role("option", name=country, exact=True)
            if opt.count():
                opt.first.click()

    answered = 0
    for question, answer in location_answers(profile):
        if _answer(page, question, answer):
            answered += 1
        page.wait_for_timeout(80)

    _fill_written_fields(page, profile, job)

    if page.get_by_text("I agree", exact=True).count():
        page.get_by_text("I agree", exact=True).first.click()

    submit = page.get_by_role("button", name="Submit Application")
    if submit.count() == 0:
        submit = page.get_by_role("button", name="Submit")
    if submit.count() == 0:
        return {
            "ok": False,
            "status": "PARTIAL",
            "message": "Submit button not found — finish manually",
        }

    captcha_hit = {"seen": False}

    def _on_req(req) -> None:
        u = req.url.lower()
        if "recaptcha" in u or "hcaptcha" in u or "turnstile" in u:
            captcha_hit["seen"] = True

    page.on("request", _on_req)
    try:
        submit.first.click()
        page.wait_for_timeout(5500)
    except PWTimeout:
        return {"ok": False, "status": "PARTIAL", "message": "Submit timed out"}
    finally:
        try:
            page.remove_listener("request", _on_req)
        except Exception:
            pass

    text = page.inner_text("body")
    low = text.lower()
    if (
        "successfully submitted" in low
        or "application was submitted" in low
        or "thank you for applying" in low
        or ("success" in low and "application" in low)
        or "thanks for applying" in low
        or "application received" in low
    ):
        return {
            "ok": True,
            "status": "APPLIED",
            "message": f"Ashby success confirmation (answered {answered} profile fields)",
        }

    if captcha_hit["seen"] or _has_visible_captcha(page):
        return {
            "ok": False,
            "status": "BLOCKED",
            "message": (
                f"Form filled ({answered} answers) but reCAPTCHA blocked submit — "
                "open the Ashby URL in a normal browser, complete CAPTCHA, submit manually"
            ),
        }

    if "needs corrections" in low or "missing entry" in low:
        err = next(
            (
                line.strip()
                for line in text.splitlines()
                if "missing" in line.lower() or "required" in line.lower()
            ),
            "required fields remain",
        )
        return {
            "ok": False,
            "status": "PARTIAL",
            "message": f"Validation failed — {err}",
        }

    if "Submit Application" in text:
        return {
            "ok": False,
            "status": "PARTIAL",
            "message": (
                f"Still on form after submit (answered {answered}). "
                "Open in browser and finish remaining fields."
            ),
        }

    return {
        "ok": False,
        "status": "PARTIAL",
        "message": "No clear success message — verify manually",
    }
