"""Test-suite wide settings."""

import os

# The AI assessment planner makes a real LLM call the first time it sees a job. Tests never want a network call: they use
# the rules planner unless a test explicitly turns the LLM path on (and stubs it).
os.environ["ASSESSMENT_PLANNER_LLM"] = "off"

# Practical (work-sample) assessments are on in the product. The older tests exercise the situational-judgment path for non-technical jobs, so they
# run with the practical stage off; tests of the practical stage switch it on explicitly (see tests/test_practical_assessments.py).
os.environ["ASSESSMENT_PRACTICAL"] = "off"

# The voice role-play needs the live voice stack. Tests run with it off (the written task takes its place); the role-play tests switch it on and
# stub the voice availability (see tests/test_practical_roleplay.py).
os.environ["ASSESSMENT_ROLEPLAY"] = "off"

# The design test's vision model is a real network call: off in tests (they use the text-only path or stub the look); the vision tests stub it explicitly.
os.environ["AI_DESIGN_VISION_MODELS"] = "off"

# Tests must NEVER send a real email. The developer's .env holds real SMTP / Gmail credentials, and python-dotenv does not override a variable that is already
# set, so blanking them here (before any backend module loads the .env) makes the mailer skip sending. Tests of the mailer set their own values.
for _name in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_EMAIL", "GOOGLE_REFRESH_TOKEN", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GMAIL_SENDER_EMAIL"):
    os.environ[_name] = ""

# Likewise no test may spend the developer's real AI quota (Groq / Gemini): a test that needs a key sets its own dummy value, and anything that
# would otherwise call out fails fast and takes the offline fallback path.
for _name in ("GROQ_API_KEY", "GEMINI_API_KEY"):
    os.environ[_name] = ""

# The tests must not depend on the developer's own .env (their provider / model choices, keys, mail settings). Every backend module calls load_dotenv() when
# it is imported; with that turned into a no-op, a test sees only the values this file and the test itself set. (The real app is unaffected.)
import dotenv as _dotenv

_dotenv.load_dotenv = lambda *args, **kwargs: False
