"""Portal accounts, CV facts, per-user scores, and the auto-apply gate."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_fd, _DB = tempfile.mkstemp(suffix=".sqlite")
os.close(_fd)
os.environ["JOB_AGENT_DB"] = _DB
os.environ["BILLING_DEV_ACTIVE"] = "1"
os.environ["SESSION_SECRET"] = "test-secret"
os.environ["JOB_AGENT_OFFLINE"] = "1"
os.environ.pop("STRIPE_SECRET", None)
os.environ.pop("STRIPE_PRICE_ID", None)

from fastapi.testclient import TestClient  # noqa: E402

from portal.app import app  # noqa: E402
from src.apply.ashby import location_answers, profile_summary  # noqa: E402
from src.cv import tailored_cv  # noqa: E402
from src.letters import answer_for_textarea, cover_letter  # noqa: E402
from src.apply.runner import apply_ready_jobs  # noqa: E402
from src.auth import authenticate, create_user, get_user_by_email  # noqa: E402
from src.cv import extract_cv, refine_cv  # noqa: E402
from src.db import db, init_db, utc_now, upsert_catalog_job  # noqa: E402

CV = """
Ada Lovelace
ada@example.com
+9779800000000
https://www.linkedin.com/in/ada-lovelace
https://github.com/ada
Nepal

Skills: Laravel, PHP, Vue, Flutter, MySQL
Engineering Manager at a product company
"""


class CvTests(unittest.TestCase):
    def test_extract_and_refine_use_only_cv_facts(self):
        facts = extract_cv(CV)
        self.assertEqual(facts["name"], "Ada Lovelace")
        stacked = extract_cv("Vue js\nLaravel PHP\nSushant Sapkota\nsushantsapkota07@gmail.com\nNepal\n")
        self.assertEqual(stacked["name"], "Sushant Sapkota")
        self.assertIn("Vue", stacked["skills"])
        self.assertEqual(facts["email"], "ada@example.com")
        self.assertEqual(facts["location_country"], "Nepal")
        self.assertIn("Laravel", facts["skills"])
        self.assertIn("linkedin.com/in/ada-lovelace", facts["linkedin"])
        refined = refine_cv({**facts, "ask_usd_monthly": 7000})
        self.assertIn("Ada Lovelace", refined)
        self.assertIn("Nepal", refined)
        self.assertIn("Laravel", refined)
        self.assertNotIn("product company", refined)
        self.assertNotIn("Baakhapaa", refined)

    def test_location_answers_follow_the_profile_country(self):
        nepal = location_answers({"location_country": "Nepal"})
        self.assertEqual(nepal, [("Are you physically located in the US?", "No")])
        usa = dict(location_answers({"location_country": "United States"}))
        self.assertEqual(usa["Are you physically located in the US?"], "Yes")
        summary = profile_summary(
            {
                "name": "Ada Lovelace",
                "location_country": "Kenya",
                "stack": {"primary": ["Laravel"]},
            }
        )
        self.assertIn("Kenya", summary)
        self.assertNotIn("Nepal", summary)
        self.assertNotIn("Baakhapaa", summary)
        source = (ROOT / "src" / "apply" / "ashby.py").read_text(encoding="utf-8")
        self.assertNotIn("Baakhapaa", source)
        self.assertNotIn('fill("Nepal")', source)

    def test_cover_letter_uses_cv_lines_and_skips_tenure(self):
        profile = {
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "location_country": "Nepal",
            "cv_text": "Built a Laravel API for a publishing house and shipped it to production.",
            "stack": {"primary": ["Laravel", "PHP"]},
            "compensation": {"ask_usd_monthly": 7000},
        }
        job = {"role": "Senior Laravel Engineer", "company": "Ashby Co"}
        letter = cover_letter(profile, job)
        self.assertIn("Senior Laravel Engineer", letter)
        self.assertIn("Ashby Co", letter)
        self.assertIn("Built a Laravel API", letter)
        self.assertNotIn("From my CV", letter)
        self.assertNotIn("5 years", letter)
        self.assertNotIn("Baakhapaa", letter)
        self.assertIsNone(answer_for_textarea("Years of professional experience", profile, job))
        stated = {**profile, "years_experience": 8, "needs_sponsorship": "no", "location_country": "Nepal"}
        self.assertEqual(answer_for_textarea("Years of professional experience", stated, job), "8")
        self.assertEqual(answer_for_textarea("Will you require visa sponsorship?", stated, job), "No")
        self.assertIsNone(answer_for_textarea("Are you legally authorized to work in the United States?", stated, job))
        written = answer_for_textarea("Cover letter", profile, job)
        assert written is not None
        self.assertIn("Ashby Co", written)
        self.assertEqual(answer_for_textarea("How did you hear about this role?", profile, job), "Job board")

    def test_each_job_gets_keywords_already_on_the_cv(self):
        profile = {
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "location_country": "Nepal",
            "cv_text": "\n".join(
                [
                    "Built a Laravel API for a publishing house and shipped it to production.",
                    "Shipped a Vue interface for the same product and kept the pages fast.",
                    "Wrote Python scripts for internal reports and scheduled them.",
                ]
            ),
            "stack": {"primary": ["Laravel", "PHP", "Vue", "Python"]},
        }
        laravel = {
            "id": 1,
            "role": "Laravel Engineer",
            "company": "Ashby Co",
            "description": "We need Laravel, PHP, and Vue.",
        }
        python = {
            "id": 2,
            "role": "Python Engineer",
            "company": "Other Co",
            "description": "Python and Django. React is required.",
        }
        letter_laravel = cover_letter(profile, laravel)
        letter_python = cover_letter(profile, python)
        cv_laravel = tailored_cv(profile, laravel)
        cv_python = tailored_cv(profile, python)
        self.assertIn("Laravel", letter_laravel)
        self.assertIn("Vue", letter_laravel)
        self.assertIn("PHP", letter_laravel)
        self.assertNotIn("Python", letter_laravel)
        self.assertNotIn("React", letter_laravel)
        self.assertIn("Python", letter_python)
        self.assertNotIn("React", letter_python)
        self.assertNotIn("Django", letter_python)
        self.assertNotEqual(letter_laravel, letter_python)
        self.assertNotEqual(cv_laravel, cv_python)
        self.assertLess(cv_laravel.index("Vue interface"), cv_laravel.index("Python scripts"))
        self.assertLess(cv_python.index("Python scripts"), cv_python.index("Vue interface"))
        self.assertNotIn("React", cv_python)
        self.assertNotIn("Django", cv_python)
        self.assertNotIn("Prepared for", cv_laravel)
        self.assertNotIn("Compensation ask", cv_laravel)
        self.assertNotIn("which this role asks for", letter_laravel)
        self.assertNotIn("as stated on my profile", letter_laravel)
        self.assertNotIn("seeking", cover_letter({**profile, "compensation": {"ask_usd_monthly": 3000}}, laravel).lower())
        self.assertNotIn("3000", cover_letter({**profile, "compensation": {"ask_usd_monthly": 3000}}, laravel))
        jammed = cover_letter(
            {
                **profile,
                "cv_text": "\n".join(
                    [
                        "- Led migration of the Laravel checkout and cut the load time in half.",
                        "Frontend engineer with 6 years building Vue products for retailers.",
                        "- Built checkout flows in PHP for a catalog used by merchants.",
                    ]
                ),
            },
            laravel,
        )
        self.assertNotIn(" - ", jammed)
        self.assertIn("Led migration of the Laravel checkout", jammed)
        self.assertIn("Built checkout flows in PHP", jammed)

    def test_contact_years_and_score_follow_the_cv(self):
        from src.discover import _prefer_apply_url
        from src.score import score_job

        facts = extract_cv(
            "\n".join(
                [
                    "Riya Shah",
                    "React and TypeScript developer",
                    "6 years of experience",
                    "Phone +91 98765 43210",
                    "linkedin.com/in/riya-shah",
                    "github.com/riyashah",
                    "Built Next.js and Jest tests for a storefront.",
                ]
            )
        )
        self.assertEqual(facts["phone"], "+91 98765 43210")
        self.assertEqual(facts["years_experience"], "6")
        self.assertEqual(facts["linkedin"], "https://linkedin.com/in/riya-shah")
        self.assertEqual(facts["github"], "https://github.com/riyashah")
        self.assertIn("Next.js", facts["skills"])
        self.assertIn("Jest", facts["skills"])
        self.assertIn("React", facts["skills"])
        profile = {"stack": {"primary": ["react", "typescript", "next.js", "jest"]}}
        rails, rails_reason = score_job(
            {"role": "Tech Lead Full-Stack Rails Engineer", "description": "Remote Rails and React"},
            profile,
        )
        assistant, _reason = score_job(
            {"role": "Remote Office Assistant", "description": "Excel and email"},
            profile,
        )
        react, _react_reason = score_job(
            {"role": "Senior React Engineer", "description": "Remote React TypeScript Next.js Jest"},
            profile,
        )
        self.assertLess(rails, 70)
        self.assertIn("rails", rails_reason)
        self.assertLess(assistant, 70)
        self.assertGreaterEqual(react, 70)
        direct = _prefer_apply_url(
            "https://remotive.com/remote-jobs/software-dev/react",
            "Apply at https://jobs.lever.co/example/123",
        )
        self.assertIn("lever.co", direct)
        from src.discover import _matches, ordered_terms

        self.assertFalse(_matches({"role": "JavaScript developer", "description": ""}, ["java"]))
        self.assertFalse(_matches({"role": "Google", "description": "a good fit"}, ["go"]))
        self.assertTrue(_matches({"role": "Java developer", "description": ""}, ["java"]))
        self.assertTrue(_matches({"role": "Go engineer", "description": ""}, ["go"]))
        terms = ordered_terms(["Django", "Figma", "Kotlin"])
        self.assertLess(terms.index("Django"), terms.index("react"))
        self.assertIn("Django", terms[:12])
        self.assertIn("Figma", terms[:12])
        self.assertIn("Kotlin", terms[:12])

    def test_location_blocks_other_countries_and_titles_stay_honest(self):
        from src.discover import select_board_jobs
        from src.score import location_block, score_job

        india = "India"
        for place in (
            "New York, NY (HQ)",
            "Tel Aviv, Israel",
            "United Kingdom",
            "Portugal",
            "Remote, European Union",
            "North America",
        ):
            self.assertIsNotNone(location_block(place, india), place)
        for place in ("Remote", "Worldwide", "Bangalore, India", "Remote, India", "Remote (Global)", "IN", "Bengaluru, IN", ""):
            self.assertIsNone(location_block(place, india), place)
        self.assertIsNotNone(location_block("Remote - Americas", india))
        self.assertIsNotNone(location_block("Office in London", india))
        self.assertIsNotNone(location_block("Indianapolis, IN", india))
        self.assertIsNotNone(location_block("Fort Wayne, IN", india))
        self.assertIsNotNone(location_block("Remote, Indiana", india))
        self.assertIsNone(location_block("Indianapolis, IN", "United States"))
        self.assertIsNone(location_block("Remote, Indiana", "United States"))
        self.assertIsNone(location_block("Home based - Worldwide", "Nepal"))
        self.assertIsNone(location_block("Remote", "Nepal"))
        self.assertIsNone(location_block("Distributed", "Nepal"))
        self.assertIsNone(location_block("Remote, APAC", "Nepal"))
        self.assertIsNotNone(location_block("Remote, Poland", "Nepal"))
        self.assertIsNotNone(location_block("Remote, AMER", "Nepal"))
        self.assertIsNotNone(location_block("Bangalore, India", "Nepal"))
        self.assertIsNone(location_block("Kathmandu", "Nepal"))
        self.assertIsNone(location_block("Lalitpur", "Nepal"))
        self.assertIsNone(location_block("Remote - Asia", "Nepal"))
        self.assertIsNone(location_block("Home Based - Americas; Home Based - APAC", "Nepal"))
        self.assertIsNone(location_block("Home Based - Americas; Home based - Worldwide", "Nepal"))
        self.assertIsNotNone(location_block("Home Based - Americas", "Nepal"))
        self.assertIsNotNone(location_block("Home Based - Americas; Home based - EMEA", "Nepal"))
        self.assertIsNone(location_block("Manila", "Philippines"))
        self.assertIsNone(location_block("Lahore", "Pakistan"))
        self.assertIsNone(location_block("New York, NY", ""))
        self.assertIsNone(location_block("New York, NY (HQ)", "United States"))

        profile = {
            "location_country": "India",
            "stack": {"primary": ["react", "typescript", "next.js", "jest"], "leadership": False},
        }
        description = "Remote React TypeScript Next.js Jest"
        new_york, _reason = score_job(
            {"role": "Backend Engineer, Ops Engineering", "location": "New York, NY (HQ)", "description": description},
            profile,
        )
        backend, backend_reason = score_job(
            {"role": "Backend Engineer, Ops Engineering", "location": "Bangalore, India", "description": description},
            profile,
        )
        manager, manager_reason = score_job(
            {"role": "Engineering Manager", "location": "Remote", "description": description},
            profile,
        )
        frontend, _frontend_reason = score_job(
            {"role": "Senior Frontend Engineer", "location": "Bangalore, India", "description": description},
            profile,
        )
        self.assertEqual(new_york, 0)
        ai, _ai_reason = score_job(
            {"role": "Senior Applied AI Engineer", "location": "Bangalore, India", "description": description},
            profile,
        )
        architecture, _architecture_reason = score_job(
            {"role": "Enterprise Architecture", "location": "Gurugram, India", "description": description},
            profile,
        )
        principal, principal_reason = score_job(
            {"role": "Tech Lead / Sr. Principal Engineer", "location": "Remote - India", "description": description},
            profile,
        )
        engineer, _engineer_reason = score_job(
            {
                "role": "Software Engineer 3",
                "location": "Bengaluru, India",
                "description": "React, Next.js, and TypeScript",
            },
            profile,
        )
        self.assertLess(ai, 70)
        self.assertLess(architecture, 70)
        self.assertLess(principal, 70)
        self.assertIn("level", principal_reason)
        self.assertGreaterEqual(engineer, 70)
        self.assertGreater(engineer, ai)
        self.assertLess(backend, 70)
        self.assertIn("backend", backend_reason)
        self.assertLess(manager, 70)
        self.assertIn("manager", manager_reason)
        self.assertGreaterEqual(frontend, 70)
        web, web_reason = score_job(
            {
                "role": "Web Frontend Engineer - JS, CSS, React, Flutter",
                "location": "Home based - Worldwide",
                "description": "Build the web client.",
            },
            profile,
        )
        flutter_only, flutter_reason = score_job(
            {
                "role": "Flutter Engineer",
                "location": "Remote",
                "description": "React TypeScript",
            },
            profile,
        )
        auth, _auth_reason = score_job(
            {
                "role": "Software Engineer - Auth",
                "location": "Remote, Global",
                "description": "The auth team uses Go, TypeScript, Docker, and Redis.",
            },
            {"location_country": "Nepal", "stack": {"primary": ["laravel", "php", "docker", "redis"], "leadership": False}},
        )
        auth_react, _auth_react_reason = score_job(
            {
                "role": "Software Engineer - Auth",
                "location": "Remote, Global",
                "description": "The auth team uses Go, TypeScript, Docker, and Redis.",
            },
            profile,
        )
        security, _security_reason = score_job(
            {
                "role": "Security Software Engineer",
                "location": "Home based - Worldwide",
                "description": description,
            },
            profile,
        )
        alliances, alliances_reason = score_job(
            {
                "role": "Alliances Field Engineer",
                "location": "Home based - Worldwide",
                "description": "Python Kubernetes",
            },
            {"location_country": "Nepal", "stack": {"primary": ["python", "kubernetes", "go"], "leadership": False}},
        )
        python_role, _python_reason = score_job(
            {
                "role": "Software Engineer - Python - Container Images",
                "location": "Home Based - Americas; Home Based - APAC",
                "description": "Python and Kubernetes.",
            },
            {"location_country": "Nepal", "stack": {"primary": ["python", "kubernetes", "go"], "leadership": False}},
        )
        self.assertGreaterEqual(web, 70)
        self.assertNotIn("title asks", web_reason)
        self.assertLess(flutter_only, 70)
        self.assertIn("title asks", flutter_reason)
        self.assertLess(auth, 70)
        self.assertLess(auth_react, 70)
        self.assertLess(security, 70)
        self.assertLess(alliances, 70)
        self.assertIn("different kind of work", alliances_reason)
        self.assertGreaterEqual(python_role, 70)
        accountant, _accountant_reason = score_job(
            {"role": "Accountant", "location": "Remote", "description": "Month-end close and bookkeeping."},
            {"location_country": "Nepal", "stack": {"primary": ["accountant", "bookkeeping"], "leadership": False}},
        )
        engineer_for_accountant, _ = score_job(
            {"role": "Python Engineer", "location": "Remote", "description": "Python services."},
            {"location_country": "Nepal", "stack": {"primary": ["accountant", "bookkeeping"], "leadership": False}},
        )
        self.assertGreaterEqual(accountant, 70)
        self.assertLess(engineer_for_accountant, 70)

        board = [
            {
                "external_id": f"ny-{i}",
                "company": "gitlab",
                "role": "Backend Engineer",
                "description": "React",
                "location": "New York, NY",
            }
            for i in range(20)
        ]
        board.append(
            {
                "external_id": "blr",
                "company": "gitlab",
                "role": "Senior Frontend Engineer",
                "description": "React TypeScript",
                "location": "Bangalore, India",
            }
        )
        picked = select_board_jobs(board, ["react"], ["India"], 5)
        self.assertEqual([job["external_id"] for job in picked], ["blr"])
        ranked = select_board_jobs(
            [
                {"external_id": "body", "role": "Office helper", "description": "React", "location": "Remote"},
                {"external_id": "title", "role": "React Engineer", "description": "React", "location": "Remote"},
            ],
            ["react"],
            ["India"],
            1,
        )
        self.assertEqual(ranked[0]["external_id"], "title")

        letter = cover_letter(
            {
                "name": "Riya Shah",
                "location_country": "India",
                "years_experience": "6",
                "stack": {"primary": ["React", "TypeScript"]},
                "cv_text": "\n".join(
                    [
                        "- Led migration of the React checkout and cut the load time in half.",
                        "Frontend engineer with 6 years building React products for retailers.",
                        "- Built checkout flows in TypeScript for a catalog used by merchants.",
                    ]
                ),
            },
            {"role": "Senior React Engineer", "company": "Linear", "description": "React and TypeScript"},
        )
        self.assertEqual(letter.lower().count("6 years"), 1)
        self.assertLess(letter.index("Led migration"), letter.index("Built checkout"))
        self.assertNotIn("Frontend engineer with 6 years", letter)

    def test_title_family_follows_the_kind_of_work_on_the_cv(self):
        from src.discover import job_key, select_board_jobs
        from src.score import cv_tracks, score_job

        def fit(role, description, skills, country="United States"):
            return score_job(
                {"role": role, "location": "Remote", "description": description},
                {"location_country": country, "stack": {"primary": skills, "leadership": False}},
            )[0]

        java = ["java", "spring", "kotlin", "postgresql", "kafka"]
        analyst = ["sql", "excel", "power bi", "tableau", "python"]
        python = ["python", "django", "fastapi", "postgresql", "aws", "docker"]
        self.assertEqual(cv_tracks(analyst), {"data"})
        self.assertIn("backend", cv_tracks(python))
        self.assertGreaterEqual(fit("Senior back-end Engineer", "Java and Spring services.", java), 70)
        self.assertLess(fit("Senior back-end Engineer", "Java and Spring services.", ["react", "typescript"]), 70)
        self.assertGreaterEqual(fit("Python Engineer", "Python services on Kubernetes.", python), 70)
        self.assertLess(fit("Software Engineer, Backend (Python)", "Python and SQL.", analyst), 70)
        self.assertLess(fit("Python and Kubernetes Software Engineer - Data", "Python SQL.", analyst), 70)
        self.assertGreaterEqual(fit("Senior Business Analyst", "SQL, Excel, and Power BI reporting.", analyst), 70)
        self.assertLess(fit("Senior Business Analyst", "SQL and Excel.", ["react", "typescript", "node.js"]), 70)
        self.assertGreaterEqual(fit("Sr React Native Engineer", "React Native and TypeScript.", ["react", "typescript"]), 70)
        self.assertLess(fit("Senior Software Engineer, Android SDUI", "Kotlin and Java.", java), 70)
        self.assertLess(fit("Senior Full-Stack Developer", "Kotlin backend.", ["flutter", "dart", "firebase", "kotlin"]), 70)
        self.assertLess(fit("Senior Site Reliability Engineer", "Python and Django.", python), 70)
        self.assertLess(fit("Python and Kubernetes Software Engineer - Data, Workflows, Analytics", "Python SQL.", analyst), 70)
        self.assertLess(fit("Junior Illustrator", "Our React app needs drawings.", ["react", "typescript"]), 70)
        self.assertLess(fit("Software Development Engineer in Test II", "React TypeScript tests.", ["react", "typescript"]), 70)
        self.assertLess(fit("Desarrollador Flutter", "Flutter y Dart.", ["flutter", "dart"]), 70)
        junior = score_job(
            {"role": "Product Engineering Lead", "location": "Remote", "description": "React TypeScript"},
            {"location_country": "Nepal", "years_experience": "2", "stack": {"primary": ["react", "typescript"], "leadership": False}},
        )[0]
        self.assertLess(junior, 70)

        # A board full of React roles still gives the Laravel customer a turn.
        board = [
            {"external_id": f"r{i}", "role": f"React Engineer {i}", "description": "React", "location": "Remote"}
            for i in range(10)
        ] + [{"external_id": "php", "role": "Laravel Developer", "description": "Laravel PHP", "location": "Remote"}]
        picked = select_board_jobs(board, ["react", "laravel"], ["Nepal"], 4)
        self.assertIn("php", [job["external_id"] for job in picked])

        self.assertEqual(
            job_key({"company": "Lemon.io", "role": "Senior React Full-stack Developer"}),
            job_key({"company": "Lemon io", "role": "Senior React Full stack Developer"}),
        )

    def test_greenhouse_posting_letter_uses_real_skills(self):
        from src.discover import _normalize
        from src.letters import _letter_sentences

        filler = "We care about our customers and companies across the world. "
        self.assertGreater(len(filler * 90), 4000)
        posting = (
            "&lt;div class=&quot;content&quot;&gt;&lt;p&gt;"
            + (filler * 90)
            + "&lt;/p&gt;&lt;p&gt;Experience with React, TypeScript, JavaScript and GraphQL is required. The migration is already underway.&lt;/p&gt;&lt;/div&gt;"
        )
        self.assertGreater(posting.find("GraphQL"), 4000)
        job = _normalize(
            {
                "id": "99",
                "title": "Senior Frontend Engineer",
                "company_name": "gitlab",
                "url": "https://boards.greenhouse.io/gitlab/jobs/99",
                "location": "Bangalore, India",
                "description": posting,
            },
            "greenhouse",
        )
        self.assertEqual(job["company"], "GitLab")
        self.assertNotIn("&lt;", job["description"])
        self.assertNotIn("<div", job["description"])
        self.assertGreater(job["description"].find("GraphQL"), 4000)
        kept = _letter_sentences("Built the React checkout in production from 2022 - Present and kept it fast.")
        self.assertTrue(any("2022 - Present" in sentence for sentence in kept))
        self.assertNotIn("2022.", " ".join(kept))
        letter = cover_letter(
            {
                "name": "Riya Shah",
                "location_country": "India",
                "years_experience": "6",
                "stack": {"primary": ["React", "TypeScript", "JavaScript", "GraphQL"]},
                "cv_text": "\n".join(
                    [
                        "Senior React Engineer, PayLeaf (Remote) 2022 - Present",
                        "- Built React and TypeScript checkout flows for a catalog used by merchants.",
                        "- Led the React migration and cut the load time in half.",
                        "Worked with many companies and teams on storefronts.",
                    ]
                ),
            },
            job,
        )
        opening = letter.split("I have worked with", 1)[1].split(".", 1)[0]
        self.assertIn("React", opening)
        self.assertIn("TypeScript", opening)
        self.assertIn("JavaScript", opening)
        self.assertIn("GraphQL", opening)
        self.assertNotIn("companies", opening.lower())
        self.assertNotIn("migration", opening.lower())
        self.assertNotIn("Frontend", opening)
        self.assertIn("GitLab", letter)
        self.assertIn("Built React and TypeScript checkout", letter)
        self.assertNotIn("PayLeaf", letter)
        self.assertNotIn("2022.", letter)
        short = cover_letter(
            {
                "name": "Riya Shah",
                "location_country": "India",
                "stack": {"primary": ["React", "TypeScript", "PostgreSQL"]},
                "cv_text": "\n".join(
                    [
                        "React, TypeScript, PostgreSQL.",
                        "- Led migration of the React checkout and cut the load time in half.",
                    ]
                ),
            },
            {"role": "Senior Frontend Engineer", "company": "GitLab", "description": "React TypeScript PostgreSQL"},
        )
        self.assertIn("Led migration of the React checkout", short)
        self.assertNotIn("React, TypeScript, PostgreSQL.", short.splitlines())


class LoginTests(unittest.TestCase):
    def test_google_and_linkedin_buttons_without_keys(self):
        os.environ.pop("GOOGLE_CLIENT_ID", None)
        os.environ.pop("GOOGLE_CLIENT_SECRET", None)
        os.environ.pop("LINKEDIN_CLIENT_ID", None)
        os.environ.pop("LINKEDIN_CLIENT_SECRET", None)
        with TestClient(app, follow_redirects=False) as client:
            page = client.get("/login")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Autojobly", page.text)
            self.assertIn("Continue with Google", page.text)
            self.assertIn("Continue with LinkedIn", page.text)
            signup = client.get("/signup")
            self.assertIn("Continue with Google", signup.text)
            self.assertIn("Continue with LinkedIn", signup.text)
            home = client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertIn("$9/month", home.text)
            self.assertIn("your cv", home.text.lower())
            self.assertNotIn("software developers", home.text.lower())
            self.assertIn('name="description"', home.text)
            self.assertNotIn("aggregateRating", home.text)
            robots = client.get("/robots.txt")
            self.assertEqual(robots.status_code, 200)
            self.assertIn("Allow: /", robots.text)
            self.assertIn("Disallow: /billing", robots.text)
            self.assertIn("Ashby", home.text)
            self.assertIn("Greenhouse", home.text)
            self.assertIn("free", home.text.lower())
            self.assertIn("None yet", home.text)
            self.assertNotIn("guaranteed", home.text.lower())
            terms = client.get("/terms")
            self.assertEqual(terms.status_code, 200)
            self.assertIn("30 days", terms.text)
            start = client.get("/auth/google")
            self.assertEqual(start.status_code, 303)
            self.assertIn("not+connected", start.headers["location"])
            linkedin = client.get("/auth/linkedin")
            self.assertIn("not+connected", linkedin.headers["location"])

    def test_google_and_linkedin_create_an_account(self):
        from unittest.mock import patch
        from urllib.parse import parse_qs, urlparse

        from src.auth import get_user_by_email, uses_oauth_password

        os.environ["GOOGLE_CLIENT_ID"] = "google-client"
        os.environ["GOOGLE_CLIENT_SECRET"] = "google-secret"
        os.environ["LINKEDIN_CLIENT_ID"] = "linkedin-client"
        os.environ["LINKEDIN_CLIENT_SECRET"] = "linkedin-secret"
        try:
            with TestClient(app, follow_redirects=False) as client:
                start = client.get("/auth/google")
                self.assertEqual(start.status_code, 303)
                google_url = start.headers["location"]
                self.assertIn("accounts.google.com", google_url)
                state = parse_qs(urlparse(google_url).query)["state"][0]
                with patch(
                    "portal.app.exchange_code",
                    return_value={"email": "priya@gmail.com", "name": "Priya Sharma"},
                ):
                    done = client.get(f"/auth/google/callback?code=ok&state={state}")
                self.assertEqual(done.status_code, 303)
                self.assertEqual(done.headers["location"], "/onboarding")
                user = get_user_by_email("priya@gmail.com")
                assert user is not None
                self.assertTrue(uses_oauth_password(user))
                self.assertEqual(user.get("oauth_provider"), "google")
                password = client.post(
                    "/login",
                    data={"email": "priya@gmail.com", "password": "password1"},
                )
                self.assertIn("Google+or+LinkedIn", password.headers["location"])

                again = client.get("/auth/linkedin")
                linkedin_url = again.headers["location"]
                self.assertIn("linkedin.com/oauth", linkedin_url)
                state = parse_qs(urlparse(linkedin_url).query)["state"][0]
                with patch(
                    "portal.app.exchange_code",
                    return_value={"email": "priya@gmail.com", "name": "Priya Sharma"},
                ):
                    linked = client.get(f"/auth/linkedin/callback?code=ok&state={state}")
                self.assertEqual(linked.headers["location"], "/onboarding")
        finally:
            os.environ.pop("GOOGLE_CLIENT_ID", None)
            os.environ.pop("GOOGLE_CLIENT_SECRET", None)
            os.environ.pop("LINKEDIN_CLIENT_ID", None)
            os.environ.pop("LINKEDIN_CLIENT_SECRET", None)


class PortalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        with db() as conn:
            upsert_catalog_job(
                conn,
                {
                    "external_id": "test:ashby",
                    "company": "Ashby Co",
                    "role": "Senior Laravel Engineer",
                    "url": "https://jobs.ashbyhq.com/ashby-co/abc",
                    "source": "test",
                    "location": "Worldwide",
                    "description": "Remote Laravel PHP Vue role",
                    "comp_band": "",
                    "ats": "ashby",
                },
            )
            upsert_catalog_job(
                conn,
                {
                    "external_id": "test:greenhouse",
                    "company": "Manual Co",
                    "role": "Senior Laravel Engineer",
                    "url": "https://job-boards.greenhouse.io/manual/jobs/1",
                    "source": "test",
                    "location": "Worldwide",
                    "description": "Remote Laravel PHP Vue role",
                    "comp_band": "",
                    "ats": "greenhouse",
                },
            )
            upsert_catalog_job(
                conn,
                {
                    "external_id": "test:breezy",
                    "company": "Self Co",
                    "role": "Senior Laravel Engineer",
                    "url": "https://self.breezy.hr/p/role",
                    "source": "test",
                    "location": "Worldwide",
                    "description": "Remote Laravel PHP Vue role",
                    "comp_band": "",
                    "ats": "breezy",
                },
            )
            upsert_catalog_job(
                conn,
                {
                    "external_id": "test:eu",
                    "company": "Blocked Co",
                    "role": "Laravel Engineer",
                    "url": "https://example.com/eu-only",
                    "source": "test",
                    "location": "Europe only",
                    "description": "Remote Laravel PHP Vue",
                    "comp_band": "",
                    "ats": "ashby",
                },
            )

    def test_signup_scores_and_splits_lanes(self):
        with TestClient(app, follow_redirects=False) as client:
            created = client.post(
                "/signup",
                data={"email": "ada@example.com", "password": "password1"},
            )
            self.assertEqual(created.status_code, 303)
            self.assertEqual(created.headers["location"], "/onboarding")

            pasted = client.post("/onboarding", data={"cv_text": CV})
            self.assertEqual(pasted.status_code, 303)
            self.assertEqual(pasted.headers["location"], "/onboarding/review")

            saved = client.post(
                "/onboarding/review",
                data={
                    "name": "Ada Lovelace",
                    "email": "ada@example.com",
                    "phone": "+9779800000000",
                    "location_country": "Nepal",
                    "ask_usd_monthly": "7000",
                    "skills": "Laravel, PHP, Vue, Flutter",
                    "consent": "yes",
                },
            )
            self.assertEqual(saved.status_code, 303)
            self.assertIn("/today", saved.headers["location"])

            jobs = client.get("/jobs")
            self.assertEqual(jobs.status_code, 200)
            self.assertIn("Ashby Co", jobs.text)
            self.assertIn("Manual Co", jobs.text)
            self.assertNotIn("Blocked Co", jobs.text)
            self.assertIn("Matches", jobs.text)

            today = client.get("/today")
            manual = today.text.split("<h2>Apply yourself</h2>", 1)[1]
            manual = manual.split("<h2>Recent score runs</h2>", 1)[0]
            self.assertIn("Self Co", manual)
            self.assertNotIn("Ashby Co", manual)
            self.assertNotIn("Manual Co", manual)

            blocked = client.post("/actions/auto-apply")
            self.assertEqual(blocked.status_code, 303)
            self.assertIn("/billing", blocked.headers["location"])

        user = get_user_by_email("ada@example.com")
        assert user is not None
        denied = apply_ready_jobs(user["id"], limit=5)
        self.assertIn("subscription", denied["error"].lower())

        with TestClient(app, follow_redirects=False) as client:
            client.post("/login", data={"email": "ada@example.com", "password": "password1"})
            turned_on = client.post("/billing/dev-activate")
            self.assertEqual(turned_on.status_code, 303)
            self.assertIn("notice", turned_on.headers["location"])

        with db() as conn:
            for _ in range(50):
                conn.execute(
                    """
                    INSERT INTO events (job_id, user_id, event_type, message, created_at)
                    VALUES (NULL, ?, 'auto_apply_start', '', ?)
                    """,
                    (user["id"], utc_now()),
                )
        capped = apply_ready_jobs(user["id"], limit=50)
        self.assertEqual(capped["applied"], 0)
        self.assertIn("Daily cap", capped["note"])

        with TestClient(app, follow_redirects=False) as other:
            other.post("/signup", data={"email": "bea@example.com", "password": "password1"})
            hidden = other.get("/jobs/1")
            self.assertEqual(hidden.status_code, 303)
            self.assertEqual(hidden.headers["location"], "/onboarding")

    def test_khalti_link_activates_thirty_days(self):
        os.environ["ADMIN_EMAIL"] = "mina@example.com"
        try:
            with TestClient(app, follow_redirects=False) as client:
                created = client.post(
                    "/signup",
                    data={"email": "mina@example.com", "password": "password1"},
                )
                self.assertEqual(created.status_code, 303)
                requested = client.post("/billing/request-link")
                self.assertEqual(requested.status_code, 303)
                page = client.get("/billing")
                self.assertIn("is being prepared", page.text)
                self.assertIn("None yet", page.text)
                self.assertNotIn("5 stars", page.text.lower())
                desk = client.get("/run")
                self.assertEqual(desk.status_code, 200)
                self.assertIn("mina@example.com", desk.text)
                self.assertIn("Currency: USD", desk.text)
                self.assertIn("Save link", desk.text)

                from src.auth import get_user_by_email
                from src.billing import latest_payment

                user = get_user_by_email("mina@example.com")
                assert user is not None
                payment = latest_payment(user["id"])
                assert payment is not None
                saved = client.post(
                    f"/admin/payments/{payment['id']}/link",
                    data={"khalti_url": "https://pay.example.com/mina"},
                )
                self.assertEqual(saved.status_code, 303)
                claimed = client.post("/billing/claim", data={"reference": "KH-12345"})
                self.assertEqual(claimed.status_code, 303)
                approved = client.post(f"/admin/payments/{payment['id']}/approve")
                self.assertIn("Active+until", approved.headers["location"])
                billing = client.get("/billing")
                self.assertIn("Active until", billing.text)
            refreshed = get_user_by_email("mina@example.com")
            assert refreshed is not None
            self.assertEqual(refreshed["subscription_status"], "active")
            self.assertRegex(refreshed["paid_until"], r"^\d{4}-\d{2}-\d{2}$")
        finally:
            os.environ.pop("ADMIN_EMAIL", None)

    def test_dodo_return_turns_access_on_once(self):
        from src.auth import get_user_by_email
        from src.billing import confirm_dodo_payment

        remote = {
            "status": "succeeded",
            "currency": "USD",
            "total_amount": 900,
            "customer": {"email": "paid@customers.test"},
        }
        with TestClient(app, follow_redirects=True) as client:
            created = client.post(
                "/signup",
                data={"email": "paid@customers.test", "password": "password1"},
            )
            self.assertEqual(created.status_code, 200)
            client.post("/billing/request-link")
            waiting = client.get("/billing")
            self.assertIn("is being prepared", waiting.text)
            user = get_user_by_email("paid@customers.test")
            assert user is not None
            until = confirm_dodo_payment(user, "pay_testonce", remote=remote)
            again = confirm_dodo_payment(user, "pay_testonce")
            self.assertEqual(until, again)
            self.assertRegex(until, r"^\d{4}-\d{2}-\d{2}$")
            page = client.get("/billing")
            self.assertIn("Auto-apply is on", page.text)
            self.assertNotIn("is being prepared", page.text)
            with self.assertRaises(ValueError):
                confirm_dodo_payment(
                    user,
                    "pay_someoneelse",
                    remote={**remote, "customer": {"email": "other@customers.test"}},
                )
            refreshed = get_user_by_email("paid@customers.test")
            assert refreshed is not None
            self.assertEqual(refreshed["paid_until"], until)

    def test_dodo_webhook_follows_the_subscription(self):
        import base64
        import json
        import time

        from standardwebhooks.webhooks import Webhook

        from src.auth import get_user_by_email, user_is_paying

        secret = "whsec_" + base64.b64encode(b"autojobly-test-webhook-key-32byte").decode()
        os.environ["DODO_PAYMENTS_WEBHOOK_KEY"] = secret
        signer = Webhook(secret)

        def send(client, event_id, kind, data, signature=None):
            body = json.dumps({"type": kind, "data": data, "business_id": "bus_test"})
            stamp = int(time.time())
            from datetime import datetime, timezone

            sign = signature or signer.sign(event_id, datetime.fromtimestamp(stamp, tz=timezone.utc), body)
            return client.post(
                "/billing/dodo-webhook",
                content=body,
                headers={"webhook-id": event_id, "webhook-timestamp": str(stamp), "webhook-signature": sign},
            )

        try:
            with TestClient(app, follow_redirects=False) as client:
                client.post("/signup", data={"email": "card@customers.test", "password": "password1"})
                user = get_user_by_email("card@customers.test")
                assert user is not None
                subscription = {
                    "subscription_id": "sub_card1",
                    "status": "active",
                    "next_billing_date": "2031-01-15T10:00:00Z",
                    "cancel_at_next_billing_date": False,
                    "metadata": {"user_id": str(user["id"])},
                    "customer": {"customer_id": "cus_card1", "email": "card@customers.test"},
                }
                forged = send(client, "msg_forged", "subscription.active", subscription, signature="v1,AAAA")
                self.assertEqual(forged.status_code, 401)
                self.assertFalse(user_is_paying(get_user_by_email("card@customers.test")))

                self.assertEqual(send(client, "msg_1", "subscription.active", subscription).status_code, 200)
                paid = get_user_by_email("card@customers.test")
                self.assertTrue(user_is_paying(paid))
                self.assertEqual(paid["paid_until"], "2031-01-16")
                self.assertEqual(paid["dodo_customer_id"], "cus_card1")
                # A retried delivery is acknowledged and changes nothing.
                self.assertEqual(send(client, "msg_1", "subscription.on_hold", {**subscription, "status": "on_hold"}).status_code, 200)
                self.assertTrue(user_is_paying(get_user_by_email("card@customers.test")))

                renewed = {**subscription, "next_billing_date": "2031-02-15T10:00:00Z"}
                send(client, "msg_2", "subscription.renewed", renewed)
                self.assertEqual(get_user_by_email("card@customers.test")["paid_until"], "2031-02-16")

                cancelled = {**renewed, "status": "cancelled", "cancel_at_next_billing_date": True}
                send(client, "msg_3", "subscription.cancelled", cancelled)
                self.assertTrue(user_is_paying(get_user_by_email("card@customers.test")))

                send(client, "msg_4", "subscription.on_hold", {**renewed, "status": "on_hold"})
                self.assertFalse(user_is_paying(get_user_by_email("card@customers.test")))
                page = client.get("/billing")
                self.assertIn("Change card, see invoices, or cancel", page.text)
        finally:
            os.environ.pop("DODO_PAYMENTS_WEBHOOK_KEY", None)

    def test_deleted_account_notice_and_captcha_stop(self):
        from src.apply.boards import has_captcha

        class _Box:
            def __init__(self, count):
                self._count = count

            def count(self):
                return self._count

        class _Page:
            def inner_text(self, _selector):
                return "Submit application"

            def locator(self, selector):
                return _Box(1 if "g-recaptcha-response" in selector else 0)

        self.assertTrue(has_captcha(_Page()))
        with TestClient(app, follow_redirects=False) as client:
            client.post("/signup", data={"email": "gone@customers.test", "password": "password1"})
            deleted = client.post("/account/delete")
            self.assertIn("Account+deleted", deleted.headers["location"])
            home = client.get(deleted.headers["location"])
            self.assertIn("Account deleted", home.text)

    def test_each_board_keeps_its_own_share(self):
        from src import discover

        calls: list[str] = []

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, params=None):
                calls.append(url)
                if "greenhouse.io" in url:
                    board = url.split("/boards/")[1].split("/")[0]
                    jobs = [
                        {
                            "id": f"{board}-{i}",
                            "title": f"React engineer {i}",
                            "absolute_url": f"https://boards.greenhouse.io/{board}/{i}",
                            "content": "React",
                            "location": {},
                        }
                        for i in range(30)
                    ]
                    return FakeResponse({"jobs": jobs})
                if "lever.co" in url:
                    site = url.rstrip("/").split("/")[-1]
                    return FakeResponse(
                        [
                            {
                                "id": f"{site}-{i}",
                                "text": f"React engineer {i}",
                                "hostedUrl": f"https://jobs.lever.co/{site}/{i}",
                                "descriptionPlain": "React",
                            }
                            for i in range(15)
                        ]
                    )
                board = url.rstrip("/").split("/")[-1]
                return FakeResponse(
                    {
                        "jobs": [
                            {
                                "id": f"{board}-{i}",
                                "title": f"React engineer {i}",
                                "jobUrl": f"https://jobs.ashbyhq.com/{board}/{i}",
                                "descriptionPlain": "React",
                            }
                            for i in range(12)
                        ]
                    }
                )

        original = discover.httpx.Client
        discover.httpx.Client = lambda *args, **kwargs: FakeClient()
        try:
            jobs = discover.fetch_company_boards(["react"], per_board=5)
        finally:
            discover.httpx.Client = original
        expected = {
            discover.board_display_name(slug)
            for slug in (*discover.GREENHOUSE_BOARDS, *discover.LEVER_SITES, *discover.ASHBY_BOARDS)
        }
        counts: dict[str, int] = {}
        for job in jobs:
            counts[job["company"]] = counts.get(job["company"], 0) + 1
        self.assertEqual(set(counts), expected)
        self.assertTrue(all(count == 5 for count in counts.values()))
        self.assertEqual(len(calls), len(expected))
        self.assertGreater(discover.company_board_count(), 80)
        self.assertNotIn("netflix", discover.LEVER_SITES)
        self.assertEqual(discover.board_display_name("openai"), "OpenAI")

    def test_public_feeds_keep_open_remote_roles(self):
        from src.discover import jobs_from_arbeitnow, jobs_from_himalayas, jobs_from_remoteok

        himalayas, _returned = jobs_from_himalayas(
            {
                "jobs": [
                    {
                        "guid": "spain",
                        "title": "React Engineer",
                        "companyName": "MiniMed",
                        "description": "React",
                        "locationRestrictions": ["Spain"],
                        "applicationLink": "https://himalayas.app/spain",
                    },
                    {
                        "guid": "wide",
                        "title": "React Engineer",
                        "companyName": "Wide",
                        "description": "React",
                        "locationRestrictions": [],
                        "applicationLink": "https://himalayas.app/wide",
                    },
                    {
                        "guid": "form",
                        "title": "React Engineer",
                        "companyName": "Form",
                        "description": "React role https://jobs.ashbyhq.com/form/1",
                        "locationRestrictions": [],
                        "applicationLink": "https://himalayas.app/form",
                    },
                ]
            },
            ["react"],
            ["Nepal"],
            10,
        )
        by_company = {job["company"]: job for job in himalayas}
        self.assertNotIn("MiniMed", by_company)
        self.assertEqual(by_company["Wide"]["location"], "Remote")
        self.assertIn("ashbyhq.com", by_company["Form"]["url"])
        self.assertEqual(by_company["Form"]["ats"], "ashby")
        self.assertEqual(by_company["Form"]["listing_url"], "https://himalayas.app/form")

        arbeitnow, _returned = jobs_from_arbeitnow(
            {
                "data": [
                    {
                        "slug": "office",
                        "company_name": "OfficeCo",
                        "title": "React Engineer",
                        "description": "React",
                        "remote": False,
                        "url": "https://www.arbeitnow.com/office",
                        "location": "",
                        "tags": [],
                    },
                    {
                        "slug": "wide",
                        "company_name": "Wide",
                        "title": "React Engineer",
                        "description": "React",
                        "remote": True,
                        "url": "https://www.arbeitnow.com/wide",
                        "location": "",
                        "tags": ["python"],
                    },
                    {
                        "slug": "emea",
                        "company_name": "EuCo",
                        "title": "React Engineer",
                        "description": "React",
                        "remote": True,
                        "url": "https://www.arbeitnow.com/emea",
                        "location": "",
                        "tags": ["EMEA"],
                    },
                ]
            },
            ["react"],
            ["Nepal"],
            10,
        )
        self.assertEqual({job["company"] for job in arbeitnow}, {"Wide"})

        remoteok, _returned = jobs_from_remoteok(
            [
                {"legal": "skip me"},
                {
                    "id": "1",
                    "position": "React Engineer",
                    "company": "SpainCo",
                    "location": "Spain",
                    "url": "https://remoteok.com/spain",
                    "apply_url": "https://remoteok.com/spain",
                    "description": "React",
                    "tags": ["react"],
                },
                {
                    "id": "2",
                    "position": "React Engineer",
                    "company": "Wide",
                    "location": "",
                    "url": "https://remoteok.com/wide",
                    "apply_url": "https://remoteok.com/wide",
                    "description": "React",
                    "tags": ["react"],
                },
            ],
            ["react"],
            ["Nepal"],
            10,
        )
        self.assertEqual({job["company"] for job in remoteok}, {"Wide"})
        self.assertEqual(remoteok[0]["listing_url"], "https://remoteok.com/wide")

    def test_reply_is_counted_only_after_autojobly_sends(self):
        from src.accounts import public_apply_stats
        from src.db import add_event

        with TestClient(app, follow_redirects=False) as client:
            created = client.post(
                "/signup",
                data={"email": "riya@customers.test", "password": "password1"},
            )
            self.assertEqual(created.status_code, 303)
            user = get_user_by_email("riya@customers.test")
            assert user is not None
            with db() as conn:
                job_id = int(conn.execute("SELECT id FROM jobs ORDER BY id LIMIT 1").fetchone()["id"])
                conn.execute(
                    "INSERT INTO profiles (user_id, name, email, consent, updated_at) VALUES (?, ?, ?, 1, ?)",
                    (user["id"], "Riya", "riya@customers.test", utc_now()),
                )
                conn.execute(
                    """
                    INSERT INTO matches (
                        user_id, job_id, fit_percent, lane, status, created_at, updated_at
                    ) VALUES (?, ?, 80, 'auto', 'QUEUED', ?, ?)
                    """,
                    (user["id"], job_id, utc_now(), utc_now()),
                )
            denied = client.post(f"/jobs/{job_id}/status", data={"status": "REPLIED"})
            self.assertIn("only+after", denied.headers["location"])
            forged = client.post(f"/jobs/{job_id}/status", data={"status": "APPLIED"})
            self.assertIn("cannot+be+set", forged.headers["location"])
            self.assertEqual(public_apply_stats(), (0, 0))
            page = client.get(f"/jobs/{job_id}")
            self.assertNotIn('value="APPLIED"', page.text)
            self.assertNotIn('value="REPLIED"', page.text)
            self.assertNotIn("I got a reply", page.text)

            with db() as conn:
                conn.execute(
                    "UPDATE matches SET status = 'APPLIED' WHERE user_id = ? AND job_id = ?",
                    (user["id"], job_id),
                )
                add_event(conn, job_id, "sample_apply", "APPLIED: submitted", user_id=user["id"])
                add_event(conn, job_id, "sample_apply", "APPLIED: submitted", user_id=user["id"])
            sent_page = client.get(f"/jobs/{job_id}")
            self.assertIn("I got a reply", sent_page.text)
            replied = client.post(f"/jobs/{job_id}/status", data={"status": "REPLIED"})
            self.assertEqual(replied.headers["location"], f"/jobs/{job_id}")
            self.assertEqual(public_apply_stats(), (1, 1))

            os.environ["ADMIN_EMAIL"] = "riya@customers.test"
            try:
                self.assertEqual(public_apply_stats(), (0, 0))
            finally:
                os.environ.pop("ADMIN_EMAIL", None)
            self.assertEqual(public_apply_stats(), (1, 1))
            with TestClient(app) as public:
                page = public.get("/")
            self.assertIn("1 reply reported from 1 sent application.", page.text)
            self.assertNotIn("1 replies", page.text)

    def test_public_roles_and_skip_stay_off_the_send_list(self):
        from src.accounts import list_public_roles
        from src.pipeline import score_user

        guest = TestClient(app)
        index = guest.get("/roles")
        self.assertEqual(index.status_code, 200)
        self.assertIn("Open roles", index.text)
        self.assertIn("Ashby Co", index.text)
        laravel = guest.get("/roles/laravel")
        self.assertEqual(laravel.status_code, 200)
        self.assertIn("Ashby Co", laravel.text)
        react = guest.get("/roles/react")
        self.assertNotIn("Ashby Co", react.text)
        self.assertNotIn("/onboarding", laravel.headers.get("location", ""))
        sitemap = guest.get("/sitemap.xml")
        self.assertIn("/roles/laravel", sitemap.text)
        self.assertTrue(any(job["company"] == "Ashby Co" for job in list_public_roles("laravel")))

        with TestClient(app, follow_redirects=False) as client:
            client.post("/signup", data={"email": "skipper@customers.test", "password": "password1"})
            client.post("/onboarding", data={"cv_text": CV})
            client.post(
                "/onboarding/review",
                data={
                    "name": "Ada Lovelace",
                    "email": "skipper@customers.test",
                    "phone": "+9779800000000",
                    "location_country": "Nepal",
                    "ask_usd_monthly": "7000",
                    "skills": "Laravel, PHP, Vue",
                    "consent": "yes",
                },
            )
            user = get_user_by_email("skipper@customers.test")
            assert user is not None
            with db() as conn:
                row = conn.execute(
                    "SELECT job_id FROM matches WHERE user_id = ? AND status != 'SKIP' ORDER BY job_id LIMIT 1",
                    (user["id"],),
                ).fetchone()
            job_id = int(row["job_id"])
            page = client.get(f"/jobs/{job_id}")
            self.assertIn("Skip this role", page.text)
            skipped = client.post(
                f"/jobs/{job_id}/status",
                data={"status": "CLOSED", "notes": "Skipped by the customer"},
            )
            self.assertEqual(skipped.status_code, 303)
            score_user(user["id"])
            with db() as conn:
                status = conn.execute(
                    "SELECT status FROM matches WHERE user_id = ? AND job_id = ?",
                    (user["id"], job_id),
                ).fetchone()["status"]
            self.assertEqual(status, "CLOSED")

    def test_password_roundtrip(self):
        user = create_user("cara@example.com", "password1")
        self.assertIsNotNone(authenticate("cara@example.com", "password1"))
        self.assertIsNone(authenticate("cara@example.com", "nope"))
        self.assertEqual(user["subscription_status"], "inactive")


if __name__ == "__main__":
    unittest.main()
