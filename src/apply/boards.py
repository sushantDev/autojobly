"""Greenhouse and Lever application forms. Stops when a captcha is on the page."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from ..letters import answer_for_label, cover_letter


def has_captcha(page: Page) -> bool:
    try:
        body = page.inner_text("body").lower()
    except Exception:
        body = ""
    if "cf-turnstile" in body or "hcaptcha" in body or "g-recaptcha" in body:
        return True
    frame = page.locator(
        "iframe[src*='recaptcha'], iframe[src*='hcaptcha.com'], iframe[src*='turnstile'], "
        "textarea[name='g-recaptcha-response'], .g-recaptcha, [data-sitekey]"
    )
    if frame.count():
        return True
    if page.locator("text=/verify you are human|i'm not a robot/i").count():
        return True
    return False


def _fill(page: Page, selector: str, value: str) -> None:
    if not value:
        return
    box = page.locator(selector)
    if box.count():
        try:
            box.first.fill(value[:500])
        except Exception:
            pass


def _upload(page: Page, cv_path: Path) -> None:
    if not cv_path.exists():
        return
    files = page.locator('input[type="file"]')
    if files.count():
        try:
            files.first.set_input_files(str(cv_path))
        except Exception:
            pass


def _split_name(name: str) -> tuple[str, str]:
    parts = [part for part in (name or "").split() if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


def _success(page: Page) -> bool:
    try:
        body = page.inner_text("body").lower()
    except Exception:
        return False
    return any(
        phrase in body
        for phrase in (
            "thank you for applying",
            "application has been submitted",
            "application submitted",
            "we have received your application",
            "thanks for applying",
        )
    )


def _finish(page: Page, clicked: bool) -> dict[str, Any]:
    if has_captcha(page):
        return {"ok": False, "status": "BLOCKED", "message": "CAPTCHA present — solve manually (cannot auto-bypass)"}
    if not clicked:
        return {"ok": False, "status": "PARTIAL", "message": "Submit button not found — finish manually"}
    page.wait_for_timeout(2500)
    if has_captcha(page):
        return {"ok": False, "status": "BLOCKED", "message": "CAPTCHA present — solve manually (cannot auto-bypass)"}
    if _success(page):
        return {"ok": True, "status": "APPLIED", "message": "Application submitted"}
    return {"ok": False, "status": "PARTIAL", "message": "Form still needs a look — finish manually"}


def apply_greenhouse(page: Page, job: dict[str, Any], profile: dict[str, Any], cv_path: Path) -> dict[str, Any]:
    page.goto(job["url"], wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2000)
    if has_captcha(page):
        return {"ok": False, "status": "BLOCKED", "message": "CAPTCHA present — solve manually (cannot auto-bypass)"}
    first, last = _split_name(profile.get("name") or "")
    _fill(page, "#first_name, input[name='job_application[first_name]'], input[id*='first_name']", first)
    _fill(page, "#last_name, input[name='job_application[last_name]'], input[id*='last_name']", last)
    _fill(page, "#email, input[name='job_application[email]'], input[type='email']", profile.get("email") or "")
    _fill(page, "#phone, input[name='job_application[phone]'], input[type='tel']", profile.get("phone") or "")
    _upload(page, cv_path)
    letter = cover_letter(profile, job)
    area = page.locator("#cover_letter, textarea[name*='cover_letter'], textarea[id*='cover_letter'], textarea")
    if area.count():
        try:
            area.first.fill(letter[:5000])
        except Exception:
            pass
    years = answer_for_label("Years of experience", profile, job)
    if years:
        _fill(page, "input[id*='experience'], input[name*='experience']", years)
    sponsor = answer_for_label("Will you require visa sponsorship?", profile, job)
    if sponsor:
        choice = page.get_by_text(sponsor, exact=True)
        if choice.count():
            try:
                choice.first.click()
            except Exception:
                pass
    button = page.get_by_role("button", name="Submit application")
    if button.count() == 0:
        button = page.locator("input[type='submit'], button[type='submit']")
    clicked = False
    if button.count():
        try:
            button.first.click()
            clicked = True
        except Exception:
            clicked = False
    return _finish(page, clicked)


def apply_lever(page: Page, job: dict[str, Any], profile: dict[str, Any], cv_path: Path) -> dict[str, Any]:
    url = job["url"].rstrip("/")
    if not url.endswith("/apply"):
        url += "/apply"
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2000)
    if has_captcha(page):
        return {"ok": False, "status": "BLOCKED", "message": "CAPTCHA present — solve manually (cannot auto-bypass)"}
    _fill(page, "input[name='name']", profile.get("name") or "")
    _fill(page, "input[name='email']", profile.get("email") or "")
    _fill(page, "input[name='phone']", profile.get("phone") or "")
    _fill(page, "input[name='urls[LinkedIn]'], input[name='urls[LinkedIn]']", profile.get("linkedin") or "")
    _fill(page, "input[name='urls[GitHub]']", profile.get("github") or "")
    _fill(page, "input[name='urls[Portfolio]'], input[name='urls[Other]']", profile.get("portfolio") or "")
    _upload(page, cv_path)
    letter = cover_letter(profile, job)
    comments = page.locator("textarea[name='comments'], textarea")
    if comments.count():
        try:
            comments.first.fill(letter[:5000])
        except Exception:
            pass
    button = page.locator("button.template-btn-submit, button[type='submit']")
    clicked = False
    if button.count():
        try:
            button.first.click()
            clicked = True
        except Exception:
            clicked = False
    return _finish(page, clicked)
