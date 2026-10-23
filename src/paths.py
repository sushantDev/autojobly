import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CV_DIR = DATA / "cvs"
PROFILE_PATH = ROOT / "profile.yaml"
JOB_WORKSPACE = ROOT.parent


def db_path() -> Path:
    override = os.environ.get("JOB_AGENT_DB")
    if override:
        return Path(override)
    return DATA / "jobs.db"


def ensure_dirs() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    CV_DIR.mkdir(parents=True, exist_ok=True)