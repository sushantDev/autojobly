"""Google and LinkedIn sign-in. The buttons stay on login, signup, and the landing page."""

from __future__ import annotations

import os
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx

from .auth import get_user_by_email, login_oauth, set_oauth_provider
from .settings import public_base


def google_configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def linkedin_configured() -> bool:
    return bool(os.environ.get("LINKEDIN_CLIENT_ID") and os.environ.get("LINKEDIN_CLIENT_SECRET"))


def new_state() -> str:
    return secrets.token_urlsafe(24)


def authorization_url(provider: str, state: str) -> str | None:
    redirect = f"{public_base()}/auth/{provider}/callback"
    if provider == "google":
        if not google_configured():
            return None
        query = urlencode(
            {
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "prompt": "select_account",
            }
        )
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"
    if provider == "linkedin":
        if not linkedin_configured():
            return None
        query = urlencode(
            {
                "response_type": "code",
                "client_id": os.environ["LINKEDIN_CLIENT_ID"],
                "redirect_uri": redirect,
                "state": state,
                "scope": "openid profile email",
            }
        )
        return f"https://www.linkedin.com/oauth/v2/authorization?{query}"
    return None


def _email_from_profile(provider: str, profile: dict[str, Any]) -> str:
    email = (profile.get("email") or "").strip().lower()
    if provider == "google" and profile.get("email_verified") is False:
        return ""
    if provider == "linkedin" and profile.get("email_verified") is False:
        return ""
    return email


def _name_from_profile(profile: dict[str, Any]) -> str:
    name = (profile.get("name") or "").strip()
    if name:
        return name
    given = (profile.get("given_name") or "").strip()
    family = (profile.get("family_name") or "").strip()
    return " ".join(part for part in (given, family) if part)


def exchange_code(provider: str, code: str) -> dict[str, str]:
    """Return email and name from Google or LinkedIn. Email is empty when the provider omits one."""
    redirect = f"{public_base()}/auth/{provider}/callback"
    if provider == "google":
        token_url = "https://oauth2.googleapis.com/token"
        userinfo_url = "https://openidconnect.googleapis.com/v1/userinfo"
        data = {
            "code": code,
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        }
    elif provider == "linkedin":
        token_url = "https://www.linkedin.com/oauth/v2/accessToken"
        userinfo_url = "https://api.linkedin.com/v2/userinfo"
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect,
            "client_id": os.environ["LINKEDIN_CLIENT_ID"],
            "client_secret": os.environ["LINKEDIN_CLIENT_SECRET"],
        }
    else:
        return {"email": "", "name": ""}
    with httpx.Client(timeout=20) as client:
        token = client.post(token_url, data=data)
        token.raise_for_status()
        access = token.json().get("access_token") or ""
        if not access:
            return {"email": "", "name": ""}
        info = client.get(userinfo_url, headers={"Authorization": f"Bearer {access}"})
        info.raise_for_status()
        profile = info.json()
        return {
            "email": _email_from_profile(provider, profile),
            "name": _name_from_profile(profile),
        }


def account_for_email(email: str, provider: str = "") -> dict[str, Any] | None:
    if not email or "@" not in email:
        return None
    existing = get_user_by_email(email)
    if existing:
        if str(existing.get("password_hash") or "").startswith("oauth$") and provider:
            set_oauth_provider(int(existing["id"]), provider)
            existing["oauth_provider"] = provider
        return existing
    return login_oauth(email, provider)
