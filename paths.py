"""Shared locations and loaders. Code lives next to this file; everything personal lives in DATA (default ./data, or $JOBHUNT_DATA)
and is git-ignored. Defaults for job sources live in defaults/config.json; the user's own settings in DATA/config.json override them."""
import json, os, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("JOBHUNT_DATA") or ROOT / "data")
OUT = DATA / "out"
DATA.mkdir(parents=True, exist_ok=True)


def _merge(a, b):
    for k, v in b.items():
        a[k] = _merge(dict(a[k]), v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return a


def load_config():
    cfg = json.loads((ROOT / "defaults" / "config.json").read_text())
    user = DATA / "config.json"
    if user.exists():
        cfg = _merge(cfg, json.loads(user.read_text()))
    return cfg


def has_profile():
    p = DATA / "profile.json"
    return p.exists() and bool(json.loads(p.read_text() or "{}").get("name"))


def load_profile():
    p = DATA / "profile.json"
    return json.loads(p.read_text()) if p.exists() else {}


def file_prefix():
    """'Jane_Doe' from the profile name, used for CV / cover letter file names."""
    name = load_profile().get("name") or "Candidate"
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") or "Candidate"


def first_name():
    return (load_profile().get("name") or "").split()[0] if load_profile().get("name") else ""


def read_json(name, default):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else default
