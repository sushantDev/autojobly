"""ATS auto-apply adapters. Never bypass CAPTCHA or steal logins."""

from .runner import apply_job, apply_ready_jobs

__all__ = ["apply_job", "apply_ready_jobs"]