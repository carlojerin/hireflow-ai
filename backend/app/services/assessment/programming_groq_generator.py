import json
import logging
import random
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from backend.app.schemas.programming_question_v2 import ProgrammingMainQuestionV2
from backend.app.services.assessment.programming_question_builders import build_question
from backend.app.services.assessment.programming_scoring_v2 import _normalize_output

logger = logging.getLogger("jobpilot.assessment.programming_groq")

# ============================================================
# JOBPILOT AI - GROQ PROGRAMMING QUESTION GENERATION (backend / general coding)
#
# Provider: Groq, model openai/gpt-oss-120b, through the existing HostedChatProvider.
# No Gemini anywhere in this path.
#
# Nothing the model writes is trusted:
#   1. schema        : every field is checked, the difficulty must be the one asked for
#   2. relevance     : the question must cover skills the JOB names (or the blueprint lists)
#   3. duplicates    : no two questions of a set (or the pool) may be the same task
#   4. reference     : the model's Python reference solution is RUN on every test input; the
#                      expected outputs are ITS outputs, never the model's own claims
#   5. determinism   : the reference must give the same answer twice and must not be trivial
#   6. cross-check   : a second, independent Groq call solves each problem from the statement
#                      alone; any disagreement on any test rejects the question (ambiguous
#                      statement or wrong reference) - a bad reference rejects GENERATION,
#                      never a candidate's score
#   7. language      : the statement must be language-neutral; the allowed languages come from the
#                      job (never widened) and are ranked by the candidate's resume
# ============================================================

PROVIDER_NAME = "groq"
MODEL = "openai/gpt-oss-120b"

# Groq's free tier allows 8000 tokens PER MINUTE for openai/gpt-oss-120b (x-ratelimit-limit-tokens) and counts the requested
# max_tokens up front, so every call is kept small (one question per call), the calls are paced, and the whole attempt has a
# time budget. Whatever is validated inside the budget is used; the rest comes from the pool / role-aware fallback, and every
# validated question is added to the pool so later candidates are served from it.
ATTEMPTS = 3
DEADLINE_SECONDS = 70
REQUEST_TIMEOUT_SECONDS = 60
MAX_TOKENS = 2600
CROSS_CHECK_MAX_TOKENS = 2200
CHUNK = 1                                     # questions per generation call
CROSS_CHECK_EVERY = 2                         # staged questions per independent-solution call
TEMPERATURE = 0.7
RATE_LIMIT_BACKOFF_SECONDS = 20.0
MIN_TESTS, MAX_TESTS = 4, 8
MAX_STDIN_CHARS = 3000
MAX_WORKERS = 4

DIFFICULTY_BY_BAND = {
    "fresher": ["easy", "easy", "medium", "medium", "medium"],
    "junior": ["easy", "medium", "medium", "medium", "hard"],
    "senior": ["medium", "medium", "hard", "hard", "hard"],
}
_UP = {"easy": "medium", "medium": "hard", "hard": "hard"}
_DOWN = {"easy": "easy", "medium": "easy", "hard": "medium"}

GROUP_ORDER = ("typical", "edge", "stress")

LANGUAGE_MARKERS = re.compile(
    r"(```|\bdef\s+\w+\(|\bpublic\s+static\b|System\.out|console\.log|#include\b|\bfn\s+main\b|\bfunc\s+main\b|\bimport\s+java\b)"
)

STOP_TOKENS = {
    "the", "and", "for", "with", "that", "this", "from", "your", "you", "are", "into", "each", "then", "print", "read", "input",
    "output", "line", "lines", "integer", "integers", "string", "given", "return", "value", "values", "number", "numbers",
    "list", "first", "last", "when", "which", "will", "have", "not", "can", "may", "must", "all", "any", "one", "two",
}


class Rejected(Exception):
    """A generated question that failed validation (never shown to a candidate)."""


# ------------------------------------------------------------ difficulty, skills, prompt

def difficulty_slots(band: str, blueprint, count: int) -> list[str]:
    """Experience band first, then the blueprint's dominant difficulty shifts it one level."""

    slots = list(DIFFICULTY_BY_BAND.get(band, DIFFICULTY_BY_BAND["junior"]))

    distribution = getattr(blueprint, "difficulty_distribution", None)

    if distribution is not None:
        counts = {"basic": distribution.basic, "intermediate": distribution.intermediate, "advanced": distribution.advanced}
        dominant = max(counts, key=counts.get)
        if dominant == "advanced" and counts["advanced"] > counts["intermediate"]:
            slots = [_UP[s] for s in slots]
        elif dominant == "basic" and counts["basic"] > counts["intermediate"]:
            slots = [_DOWN[s] for s in slots]

    return slots[:count]


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z][a-z0-9+#.]{1,}", str(text).lower()) if t not in STOP_TOKENS and len(t) > 2}


def job_skill_names(job, blueprint) -> list[str]:
    """The skills the job names (and the blueprint prioritises): the vocabulary role relevance is checked against."""

    names = []

    for item in getattr(blueprint, "skills_to_assess", []) or []:
        names.append(str(item.skill))

    for field in ("required_skills", "technologies", "preferred_skills"):
        value = getattr(job, field, "") or ""
        names.extend(part.strip() for part in re.split(r"[,;\n]", str(value)) if part.strip())

    names.append(str(getattr(job, "title", "") or ""))

    return list(dict.fromkeys(n for n in names if n))


def job_terms(job, blueprint, extra: list[str] | None = None) -> set[str]:

    terms = set()

    for name in job_skill_names(job, blueprint) + list(extra or []):
        terms |= _tokens(name)

    terms |= _tokens(getattr(job, "description", "") or "")
    return terms


def focus_skills(blueprint, job, count: int) -> list[str]:

    from backend.app.services.assessment.programming_allocation import PRIORITY_WEIGHT, classify_skill

    ranked = sorted(
        (item for item in getattr(blueprint, "skills_to_assess", []) or [] if classify_skill(item.skill) == "coding"),
        key=lambda item: -PRIORITY_WEIGHT.get(str(item.priority).lower(), 1),
    )
    names = [item.skill for item in ranked] or [n for n in job_skill_names(job, blueprint) if classify_skill(n) == "coding"] \
        or [str(getattr(job, "title", "") or "software development")]

    return [names[i % len(names)] for i in range(count)]


def build_prompt(job, blueprint, band: str, slots: list[tuple[str, str]], allowed_languages: list[str],
                 candidate_skills: list[str], variation: str) -> str:

    slot_lines = "\n".join(f"{i}. difficulty={d}; main job skill to exercise: {skill}" for i, (d, skill) in enumerate(slots, start=1))

    return f"""
You write programming-test questions for a real hiring pipeline. Each question is solved by reading standard input and printing
standard output, and is graded by hidden test inputs. The candidate may use any of these languages: {', '.join(allowed_languages)}.

JOB TITLE: {getattr(job, 'title', '')}
JOB DESCRIPTION: {(getattr(job, 'description', '') or 'Not specified')[:500]}
REQUIRED SKILLS: {getattr(job, 'required_skills', '') or 'Not specified'}
TECHNOLOGIES: {getattr(job, 'technologies', '') or 'Not specified'}
CANDIDATE EXPERIENCE BAND: {band}
CANDIDATE SKILLS FROM THE RESUME: {', '.join(candidate_skills) or 'Not specified'}
VARIATION CODE: {variation} (use it to choose fresh scenarios, names and numbers: every candidate gets a different test)

Write EXACTLY {len(slots)} questions, one per slot, in this order. Each must be a realistic task from THIS job's daily work
(for a backend role: request/response handling, validation, caching, rate limiting, log processing, aggregation, retries; for
other roles: their own real work), not a textbook puzzle, and must exercise the slot's job skill. Keep the difficulty of the slot exactly.
SLOTS:
{slot_lines}

Answer in EXACTLY this plain-text format (no JSON, no markdown), one block per question:

=== QUESTION 1 ===
TITLE: short title
DIFFICULTY: easy|medium|hard  (exactly the slot's)
SCENARIO: 1-2 sentences of real-world context
DESCRIPTION:
the precise task, as many lines as needed
REQUIREMENTS:
- checkable requirement
- another requirement
CONSTRAINTS: limits on the input
INPUT_SPEC: exact input format
OUTPUT_SPEC: exact output format
SKILLS_ASSESSED: skill, skill
JOB_SKILLS_COVERED: names taken from the REQUIRED SKILLS / TECHNOLOGIES / job title above that this question really exercises
COMPLEXITY: expected time complexity
--- TEST typical | what it checks
<the input text exactly as given on standard input; never the expected output>
--- END TEST
(give EXACTLY 6 tests: 2 typical, 3 edge (minimum, empty-ish, duplicates, ties, boundaries) and 1 stress (larger, but under 2000 characters))
--- REFERENCE (python)
a complete, correct Python 3 program that reads its input with input() ONLY (no sys, no os, no files, no network) and prints the
answer. Allowed modules: math, re, collections, itertools, functools, heapq, bisect, string, datetime, json, statistics, decimal,
fractions, operator, typing, random. It must print something for EVERY test input and be deterministic.
--- END REFERENCE
=== END QUESTION ===

RULES
- Read input ONLY like this (the sandbox has no sys/os): n = int(input()); rows = [input() for _ in range(n)]  or, to read everything:
  lines = []; then loop: try lines.append(input()) except EOFError: break.
- ALWAYS print at least one line, even when the answer is empty or zero (print 0 or NONE), so every test has visible output.
- The statement is language-neutral: no code, no language names, no library names, no code fences.
- Input and output formats are exact and unambiguous, so that two independent programmers produce identical output; define tie-breaks
  and formats (line order, separators, number formatting) explicitly. Integers only unless a rounding rule is stated.
- The questions must differ from each other in what they ask, not only in wording.
""".strip()


HEADERS = ("TITLE", "DIFFICULTY", "SCENARIO", "DESCRIPTION", "REQUIREMENTS", "CONSTRAINTS", "INPUT_SPEC", "OUTPUT_SPEC",
           "SKILLS_ASSESSED", "JOB_SKILLS_COVERED", "COMPLEXITY")

_HEADER_LINE = re.compile(r"^(" + "|".join(HEADERS) + r"):[ \t]*(.*)$", re.M)
_TEST = re.compile(r"^--- TEST[ \t]+(typical|edge|stress)[ \t]*(?:\|[ \t]*([^\n]*))?\n(.*?)\n?^--- END TEST[ \t]*$", re.M | re.S | re.I)
_REFERENCE = re.compile(r"^--- REFERENCE[^\n]*\n(.*?)\n?^--- END REFERENCE[ \t]*$", re.M | re.S | re.I)


_READ_ALL_HELPER = (
    "def __read_all():\n    lines = []\n    while True:\n        try:\n            lines.append(input())\n"
    "        except EOFError:\n            break\n    return \"\\n\".join(lines)\n\n"
)


def normalize_reference_source(code: str) -> str:
    """
    Reference/independent solutions often read with sys.stdin, which the sandbox (rightly) does not offer. The common
    stdin idioms are rewritten to input()-based equivalents; any other use of sys/os is left in place and gets rejected.
    """

    if "sys" not in code:
        return code

    text = re.sub(r"^\s*import\s+sys\s*$", "", code, flags=re.M)
    text = re.sub(r"^\s*from\s+sys\s+import\s+stdin(\s+as\s+\w+)?\s*$", "", text, flags=re.M)
    text = text.replace("sys.stdin.readline()", "input()")
    text = re.sub(r"sys\.stdin\.read\(\)", "__read_all()", text)
    split_lines = '__read_all().split("\\n")'                       # source text: __read_all().split("\n")
    text = text.replace("sys.stdin.readlines()", split_lines)
    text = re.sub(r"\bfor\s+(\w+)\s+in\s+sys\.stdin\s*:", lambda m: f"for {m.group(1)} in {split_lines}:", text)

    if "__read_all" in text:
        text = _READ_ALL_HELPER + text

    return text.strip("\n") + "\n"


def _strip_fences(code: str) -> str:
    code = code.strip("\n")
    code = re.sub(r"^```[a-zA-Z0-9]*\n", "", code)
    return re.sub(r"\n```\s*$", "", code).strip("\n")


def _as_list(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;]", value) if part.strip()]


def parse_questions(text: str) -> list[dict]:
    """The plain-text reply -> the same dicts validate_structure() checks. Malformed blocks come back empty and are rejected there."""

    blocks = re.split(r"^=== QUESTION \d+ ===[ \t]*$", text, flags=re.M)[1:]
    questions = []

    for block in blocks:

        block = block.split("=== END QUESTION ===")[0]
        head = re.split(r"^--- (?:TEST|REFERENCE)", block, maxsplit=1, flags=re.M | re.I)[0]

        marks = list(_HEADER_LINE.finditer(head))
        fields = {}

        for index, mark in enumerate(marks):
            end = marks[index + 1].start() if index + 1 < len(marks) else len(head)
            inline = mark.group(2).strip()
            rest = head[mark.end():end].strip("\n")
            fields[mark.group(1)] = (inline + ("\n" + rest if rest.strip() else "")).strip()

        requirements = [re.sub(r"^[-*\u2022]\s*", "", line).strip() for line in fields.get("REQUIREMENTS", "").split("\n") if line.strip()]
        reference = _REFERENCE.search(block)

        questions.append({
            "title": fields.get("TITLE", ""), "difficulty": fields.get("DIFFICULTY", "").split()[0].lower() if fields.get("DIFFICULTY") else "",
            "scenario": fields.get("SCENARIO", ""), "description": fields.get("DESCRIPTION", ""), "requirements": requirements,
            "constraints": fields.get("CONSTRAINTS", ""), "input_specification": fields.get("INPUT_SPEC", ""),
            "output_specification": fields.get("OUTPUT_SPEC", ""), "skills_assessed": _as_list(fields.get("SKILLS_ASSESSED", "")),
            "job_skills_covered": _as_list(fields.get("JOB_SKILLS_COVERED", "")), "expected_complexity": fields.get("COMPLEXITY", ""),
            "tests": [
                {"group": m.group(1).lower(), "description": (m.group(2) or "").strip(), "stdin": m.group(3).strip("\n")}
                for m in _TEST.finditer(block)
            ],
            "reference_solution_python": normalize_reference_source(_strip_fences(reference.group(1))) if reference else "",
        })

    return questions


def parse_solutions(text: str) -> dict[int, str]:
    return {
        int(m.group(1)): normalize_reference_source(_strip_fences(m.group(2)))
        for m in re.finditer(r"^=== SOLUTION (\d+) ===[ \t]*\n(.*?)\n?^=== END SOLUTION ===[ \t]*$", text, re.M | re.S)
    }


def make_provider():
    """The existing Groq hosted provider, openai/gpt-oss-120b. Nothing else can be selected here."""

    from backend.app.services.assessment.interview_llm_provider import HostedChatProvider

    provider = HostedChatProvider(PROVIDER_NAME, model=MODEL, timeout=REQUEST_TIMEOUT_SECONDS)
    provider.reasoning_effort = "low"                       # fewer hidden reasoning tokens; correctness is enforced by the validation

    return provider


# ------------------------------------------------------------ validation

def run_python(source: str, stdin: str) -> tuple[bool, str, str]:
    """(ok, stdout, why-not) for one run of a Python program in the sandbox."""

    from backend.app.services.assessment.language_runner import execute_code

    result = execute_code("python", source, stdin)

    why = "" if result.success else f"{result.error_type}: {(result.message or result.stderr or '')[:120]}"

    return bool(result.success), result.stdout or "", why


def validate_structure(raw: dict, target_difficulty: str, skill_names: list[str], terms: set[str], seen: set[str]) -> dict:
    """Schema, difficulty, relevance, language-neutrality and duplicate checks. Returns the clean spec."""

    from backend.app.services.assessment.programming_question_pool import fingerprint

    def text(field, low, high):
        value = str(raw.get(field, "")).strip()
        if not (low <= len(value) <= high):
            raise Rejected(f"{field} has {len(value)} characters (need {low}-{high})")
        return value

    spec = {
        "title": text("title", 3, 120),
        "scenario": str(raw.get("scenario", "")).strip()[:1500],
        "description": text("description", 60, 3500),
        "input_specification": text("input_specification", 10, 1000),
        "output_specification": text("output_specification", 5, 1000),
        "constraints": str(raw.get("constraints", "")).strip()[:1000],
        "expected_complexity": str(raw.get("expected_complexity", "")).strip()[:200] or None,
        "requirements": [str(r).strip() for r in raw.get("requirements", []) if str(r).strip()][:8],
        "skills_assessed": [str(r).strip() for r in raw.get("skills_assessed", []) if str(r).strip()][:8],
        "reference": str(raw.get("reference_solution_python", "")).strip(),
    }

    difficulty = str(raw.get("difficulty", "")).lower()

    if difficulty != target_difficulty:
        raise Rejected(f"difficulty '{difficulty}' but the slot needs '{target_difficulty}'")

    spec["difficulty"] = difficulty

    if len(spec["requirements"]) < 2:
        raise Rejected("fewer than 2 requirements")

    if LANGUAGE_MARKERS.search(spec["description"] + " " + spec["input_specification"] + " " + spec["output_specification"]):
        raise Rejected("the statement contains code or is tied to one language")

    reference = spec["reference"]

    if len(reference) < 20 or "input(" not in reference or re.search(r"\b(import\s+(sys|os|subprocess)|open\()", reference):
        raise Rejected("reference solution must read with input() and use no sys/os/files")

    covered = [str(c).strip() for c in raw.get("job_skills_covered", []) if str(c).strip()]
    normalized_skills = [s.lower() for s in skill_names]
    valid_covered = [c for c in covered if any(c.lower() in s or s in c.lower() for s in normalized_skills)]

    if not valid_covered:
        raise Rejected("no job skill covered (role relevance)")

    question_tokens = _tokens(f"{spec['title']} {spec['description']} {' '.join(spec['skills_assessed'])} {' '.join(valid_covered)}")

    if not (question_tokens & terms):
        raise Rejected("no overlap with the job's own vocabulary (role relevance)")

    spec["job_skills_covered"] = valid_covered

    tests = raw.get("tests")

    if not isinstance(tests, list) or not (MIN_TESTS <= len(tests) <= MAX_TESTS):
        raise Rejected(f"need {MIN_TESTS}-{MAX_TESTS} tests")

    clean_tests, stdins = [], set()

    for test in tests:

        group = str(test.get("group", "")).lower()
        stdin = str(test.get("stdin", ""))

        if group not in GROUP_ORDER or not stdin.strip() or len(stdin) > MAX_STDIN_CHARS:
            raise Rejected("a test has a bad group, empty input or an input over the size limit")

        if stdin in stdins:
            raise Rejected("duplicate test input")

        stdins.add(stdin)
        clean_tests.append({"group": group, "description": str(test.get("description", ""))[:200], "stdin": stdin})

    groups = {t["group"] for t in clean_tests}

    if "typical" not in groups or "edge" not in groups:
        raise Rejected("tests must include typical and edge inputs")

    # public example first, then typical, edge, stress (subparts follow this order)
    clean_tests.sort(key=lambda t: GROUP_ORDER.index(t["group"]))
    spec["tests"] = clean_tests

    key = fingerprint(spec["title"], spec["description"])

    if key in seen:
        raise Rejected("duplicate of another question")

    spec["fingerprint"] = key

    return spec


def materialize(spec: dict, allowed_languages: list[str], executor: ThreadPoolExecutor | None = None) -> tuple[ProgrammingMainQuestionV2, list[str]]:
    """
    Runs the reference solution on every test input (in the sandbox) and builds the question from ITS outputs.
    Returns (question, expected outputs). Raises Rejected when the reference is not trustworthy.
    """

    tests = spec["tests"]

    def run(test):
        return run_python(spec["reference"], test["stdin"])

    if executor is not None:
        results = list(executor.map(run, tests))
    else:
        results = [run(t) for t in tests]

    outputs = []

    for test, (ok, output, why) in zip(tests, results):
        if not ok or not output.strip():
            raise Rejected(f"reference solution failed or printed nothing on a {test['group']} input ({why or 'no output'})")
        outputs.append(output.strip())

    for index in (0, len(tests) - 1):                                        # determinism: same answer on a second run
        ok, again, _why = run_python(spec["reference"], tests[index]["stdin"])
        if not ok or _normalize_output(again) != _normalize_output(outputs[index]):
            raise Rejected("reference solution is not deterministic")

    if len({_normalize_output(o) for o in outputs}) < 2:
        raise Rejected("every test has the same expected output (trivial question)")

    if all(_normalize_output(o) == _normalize_output(t["stdin"]) for o, t in zip(outputs, tests)):
        raise Rejected("the expected output just echoes the input")

    cases = []

    for index, (test, output) in enumerate(zip(tests, outputs)):
        cases.append({
            "stdin": test["stdin"], "expected_stdout": output, "group": test["group"],
            "is_hidden": index != 0, "description": "Sample input" if index == 0 else test["description"] or "Hidden test",
        })

    question = build_question(
        title=spec["title"], scenario=spec["scenario"] or None, description=spec["description"],
        input_specification=spec["input_specification"], output_specification=spec["output_specification"],
        constraints=spec["constraints"], difficulty=spec["difficulty"], allowed_languages=allowed_languages, cases=cases,
        examples=[{"input": cases[0]["stdin"], "output": cases[0]["expected_stdout"]}],
        requirements=spec["requirements"], skills_assessed=spec["skills_assessed"] or spec["job_skills_covered"],
        technology=spec["job_skills_covered"][0] if spec.get("job_skills_covered") else None,
        expected_complexity=spec["expected_complexity"],
    )

    return question, outputs


def cross_check(provider, specs: list[dict], outputs: list[list[str]], executor: ThreadPoolExecutor) -> list[str | None]:
    """
    A second, independent Groq call writes a solution from each STATEMENT ALONE (it never sees the reference or the
    test inputs). Returns per question None when its solution reproduces every expected output, else the reason.
    """

    statements = "\n\n".join(
        f"QUESTION {i}\nTitle: {s['title']}\n{s['description']}\nInput: {s['input_specification']}\nOutput: {s['output_specification']}\n"
        f"Constraints: {s['constraints']}"
        for i, s in enumerate(specs)
    )

    prompt = (
        "Solve each programming problem with a complete Python 3 program that reads input with input() ONLY (no sys, no os) and prints "
        "the answer exactly as the statement specifies. Allowed modules: math, re, collections, itertools, functools, heapq, bisect, "
        "string, datetime, json, statistics, decimal, fractions, operator, typing.\n\n"
        f"{statements}\n\n"
        "Answer with one block per question, exactly:\n=== SOLUTION <number> ===\n<the program>\n=== END SOLUTION ===\n"
        "No explanations, no markdown."
    )

    text = provider.generate_json(prompt, max_tokens=CROSS_CHECK_MAX_TOKENS, temperature=0.2, raw_text=True)
    solutions = parse_solutions(text)

    verdicts: list[str | None] = []

    for index, spec in enumerate(specs):

        solution = solutions.get(index)

        if not solution:
            verdicts.append("the independent solver returned no solution")
            continue

        def run(test, code=solution):
            return run_python(code, test["stdin"])

        results = list(executor.map(run, spec["tests"]))
        reason = None

        for (ok, output, _why), expected, test in zip(results, outputs[index], spec["tests"]):
            if not ok:
                reason = "the independent solution failed to run on a test input"
                break
            if _normalize_output(output) != _normalize_output(expected):
                reason = f"the independent solution disagrees with the reference on a {test['group']} input"
                break

        verdicts.append(reason)

    return verdicts


# ------------------------------------------------------------ generation

def generate_ai_questions(
    count: int, job, blueprint, band: str, allowed_languages: list[str], candidate_skills: list[str],
    seen: set[str], targets: list[str], provider=None, clock=time.monotonic, sleep=time.sleep,
) -> tuple[list[tuple[ProgrammingMainQuestionV2, dict]], dict]:
    """
    Returns ([(question, spec)], report). May return fewer than `count` (the caller falls back for the rest);
    provider failures never raise: they are recorded in the report.
    """

    provider = provider or make_provider()
    skills = job_skill_names(job, blueprint)
    terms = job_terms(job, blueprint, candidate_skills)
    focus = focus_skills(blueprint, job, count)

    accepted: dict[int, tuple[ProgrammingMainQuestionV2, dict]] = {}
    report = {"provider": PROVIDER_NAME, "model": MODEL, "attempts": 0, "rejected": [], "errors": [], "calls": 0}
    started = clock()
    staged: list[tuple[int, dict, ProgrammingMainQuestionV2, list[str]]] = []

    def out_of_time() -> bool:
        return clock() - started >= DEADLINE_SECONDS

    def pace_after_rate_limit(exc) -> None:
        if "429" in str(exc) or "413" in str(exc) or "rate_limit" in str(exc):
            if clock() - started + RATE_LIMIT_BACKOFF_SECONDS < DEADLINE_SECONDS:
                sleep(RATE_LIMIT_BACKOFF_SECONDS)                              # tokens-per-minute window: wait for it to reopen

    def run_cross_check(executor) -> None:

        nonlocal staged

        if not staged:
            return

        batch, staged = staged, []

        try:
            report["calls"] += 1
            verdicts = cross_check(provider, [b[1] for b in batch], [b[3] for b in batch], executor)
        except Exception as exc:
            report["errors"].append(f"cross-check: {type(exc).__name__}: {str(exc)[:120]}")
            logger.warning("Groq cross-check failed: %s", exc)
            pace_after_rate_limit(exc)
            return                                                             # nothing is accepted without an independent solution

        for (slot, spec, question, _outputs), verdict in zip(batch, verdicts):
            if verdict is not None:
                report["rejected"].append({"slot": slot, "reason": verdict})
                logger.warning("Groq question for slot %s rejected by the cross-check: %s", slot, verdict)
            else:
                accepted[slot] = (question, spec)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:

        for attempt in range(1, ATTEMPTS + 1):

            missing = [i for i in range(count) if i not in accepted]

            if not missing or out_of_time():
                break

            report["attempts"] = attempt

            for slot in missing:

                if out_of_time():
                    break

                prompt = build_prompt(
                    job, blueprint, band, [(targets[slot], focus[slot])], allowed_languages, candidate_skills, uuid.uuid4().hex[:10],
                )

                try:

                    report["calls"] += 1
                    raw_questions = parse_questions(
                        provider.generate_json(prompt, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, raw_text=True)
                    )

                except Exception as exc:

                    report["errors"].append(f"generation: {type(exc).__name__}: {str(exc)[:120]}")
                    logger.warning("Groq programming generation failed (attempt %s): %s", attempt, exc)
                    pace_after_rate_limit(exc)
                    continue

                if not raw_questions:
                    report["rejected"].append({"slot": slot, "reason": "the reply held no question in the expected format"})
                    continue

                batch_seen = set(seen) | {item[1]["fingerprint"] for item in accepted.values()} | {s_[1]["fingerprint"] for s_ in staged}

                try:
                    spec = validate_structure(raw_questions[0], targets[slot], skills, terms, batch_seen)
                    question, outputs = materialize(spec, allowed_languages, executor)
                except Rejected as bad:
                    report["rejected"].append({"slot": slot, "reason": str(bad)})
                    logger.warning("Groq question for slot %s rejected: %s", slot, bad)
                    continue
                except Exception as exc:
                    report["rejected"].append({"slot": slot, "reason": f"validation error: {type(exc).__name__}"})
                    continue

                staged.append((slot, spec, question, outputs))

                if len(staged) >= CROSS_CHECK_EVERY:
                    run_cross_check(executor)

            run_cross_check(executor)                                            # whatever is still waiting for its independent check

    report["accepted"] = len(accepted)

    return [accepted[i] for i in sorted(accepted)], {**report, "slots": sorted(accepted)}
