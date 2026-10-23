# Job Agent + Portal

Hybrid daily pipeline for Sushant’s remote job hunt:

1. **Discover** remote roles (Remotive API, stack-filtered)
2. **Score** against `profile.yaml` (geo skips, Laravel/PHP/Vue/Flutter, leadership)
3. **Shortlist** top 50 / day
4. **Portal** to review, update status, and clear the “needs you” queue
5. **Auto-apply** (Ashby/Lever/Greenhouse) — dry-run first; Playwright adapters next

## Quick start

```bash
cd /Users/sushantsapkota/Workspace/Job/job_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python cli.py init-db
python cli.py import-shortlist   # load today’s shortlist_50.md
python cli.py run-daily          # fetch + score new remote jobs

uvicorn portal.app:app --reload --port 8787
# → http://127.0.0.1:8787
```

## Daily schedule (macOS)

```bash
# crontab -e
0 9 * * * cd /Users/sushantsapkota/Workspace/Job/job_agent && .venv/bin/python cli.py run-subscribers >> data/cron.log 2>&1
```

`run-subscribers` refreshes the shared catalog, scores every saved profile, and auto-applies Ashby jobs for accounts paid through the current date (50 attempts per user per day). Captcha and login walls stay in the manual queue.

Keep the portal open (or bookmark it) to clear CAPTCHA / login items each morning. Accounts sign up at `/signup` and paste a CV. Billing is a Khalti card link for $9, confirmed for 30 days. Log in as the email in `profile.yaml` (or `ADMIN_EMAIL`) and use **Run the business**: make the link, wait for payment, confirm the NPR, then renew anyone ending within 7 days. Click **NPR is in my wallet** only after the rupees show in Khalti. `BILLING_DEV_ACTIVE=1` still shows a local activate button when Stripe is not configured.

## Portal pages

| Path | Purpose |
|------|---------|
| `/signup`, `/login` | Email and password |
| `/onboarding` | Paste or upload a CV, then confirm country, ask, and consent |
| `/today` | Auto-applied today, and jobs to apply yourself |
| `/jobs` | Scored jobs for the logged-in user |
| `/queue` | Manual queue (PARTIAL / BLOCKED / READY) |
| `/jobs/{id}` | Detail + status update |
| `/profile` | Extracted profile and refined CV |
| `/billing` | Khalti card link for $9. Operator pastes the link and confirms the NPR |

## Auto-apply

```bash
python cli.py run-subscribers               # discover, score, auto-apply subscribers
python cli.py auto-apply --user-id 1 --limit 5
python cli.py auto-apply --limit 3 --headed # every active subscriber, visible browser
```

Or use **Auto-apply next Ashby jobs** on the portal `/queue` page (subscribed accounts, up to 5 per click; the daily command does the rest of the cap).

### What we will never automate
- **CAPTCHA / reCAPTCHA / Turnstile** — illegal to bypass; solve yourself (Virgosoft, TechUp)
- **Account logins** — Upwork, Djinni, Turing, Relocate.me (you sign in)
- **mailto:** applications — send the draft yourself

### What next
- Greenhouse / Lever adapters
- More job sources (LaraJobs, company boards)
- Optional: reuse a browser profile *after you log in once* (still no CAPTCHA bypass)
