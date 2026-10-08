import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "backend/app/api/routes/assessment_sessions.py"


def test_every_owned_application_lookup_is_null_checked():
    lines = SRC.read_text(encoding="utf-8").splitlines()
    sites = [i for i, l in enumerate(lines) if "get_owned_application(" in l and "=" in l]
    assert len(sites) >= 13
    for i in sites:
        nxt = next(l for l in lines[i + 1:] if l.strip())
        assert re.match(r"\s*if not application", nxt), f"unguarded lookup at line {i + 1}"
