import logging
import random
import re
from collections import Counter, OrderedDict

from backend.app.schemas.programming_question_v2 import ProgrammingMainQuestionV2
from backend.app.services.assessment.programming_question_builders import build_question

logger = logging.getLogger("jobpilot.assessment.coding")

# ============================================================
# JOBPILOT AI - CODING QUESTIONS WITHOUT A GENERIC BANK
#
# 1. Parametric templates: role-flavoured problems (backend / data / general) whose inputs are
#    random and whose expected output is COMPUTED by the solver below, so an answer key can
#    never be wrong and no two candidates get the same set. Used whenever Gemini is
#    unavailable and to replace any Gemini question that fails validation.
# 2. Reference-solution check for Gemini questions: Gemini supplies a Python reference solution
#    with every question; the server RUNS it on each test input and uses ITS output as the
#    expected result, so Gemini can never ship a wrong expected output.
# ============================================================

WORDS = ["alpha", "beta", "gamma", "delta", "omega", "sigma", "kappa", "theta", "lambda", "zeta", "orbit", "pixel", "cloud", "stack", "queue"]
PATHS = ["/login", "/users", "/orders", "/health", "/search", "/cart", "/pay", "/items"]
METHODS = ["GET", "POST", "PUT", "DELETE"]
TEAMS = ["red", "blue", "green", "gold", "silver", "black", "white"]
NAMES = ["asha", "kabir", "meera", "rohan", "diya", "vikram", "nisha", "arjun"]


def _lines(*items) -> str:
    return "\n".join(str(i) for i in items)


# ---------------- solvers + generators (each returns stdin text; solve() turns stdin into stdout)

def _gen_status(rng, size):
    rows = [f"{rng.choice(METHODS)} {rng.choice(PATHS)} {rng.choice([200, 200, 201, 204, 301, 302, 400, 401, 404, 500, 503])}" for _ in range(size)]
    return _lines(len(rows), *rows)


def _solve_status(stdin):
    lines = stdin.split("\n")
    counts = Counter()
    for row in lines[1:1 + int(lines[0])]:
        counts[row.split()[2][0] + "xx"] += 1
    return "\n".join(f"{c}: {counts.get(c, 0)}" for c in ("2xx", "3xx", "4xx", "5xx"))


def _gen_rate(rng, size):
    limit, window = rng.choice([2, 3, 4]), rng.choice([5, 10, 20])
    times, t = [], rng.randint(0, 3)
    for _ in range(size):
        t += rng.choice([0, 1, 1, 2, 3, 5])
        times.append(t)
    return _lines(f"{limit} {window}", *times)


def _solve_rate(stdin):
    lines = stdin.split("\n")
    limit, window = map(int, lines[0].split())
    allowed, out = [], []
    for value in lines[1:]:
        t = int(value)
        recent = [a for a in allowed if a > t - window]
        if len(recent) < limit:
            allowed.append(t)
            out.append("ALLOW")
        else:
            out.append("DENY")
    return "\n".join(out)


def _gen_lru(rng, size):
    capacity, ops, keys = rng.choice([2, 3]), [], rng.sample(range(1, 8), 5)
    for _ in range(size):
        if rng.random() < 0.55:
            ops.append(f"PUT {rng.choice(keys)} {rng.randint(10, 99)}")
        else:
            ops.append(f"GET {rng.choice(keys)}")
    ops.append(f"GET {keys[0]}")
    return _lines(capacity, *ops)


def _solve_lru(stdin):
    lines = stdin.split("\n")
    capacity, cache, out = int(lines[0]), OrderedDict(), []
    for op in lines[1:]:
        parts = op.split()
        if parts[0] == "PUT":
            key, value = int(parts[1]), int(parts[2])
            if key in cache:
                cache.move_to_end(key)
            cache[key] = value
            if len(cache) > capacity:
                cache.popitem(last=False)
        else:
            key = int(parts[1])
            if key in cache:
                cache.move_to_end(key)
                out.append(str(cache[key]))
            else:
                out.append("-1")
    return "\n".join(out) if out else "-1"


def _gen_topk(rng, size):
    k = rng.choice([2, 3])
    words = [rng.choice(WORDS[:8]) for _ in range(size)]
    return _lines(k, " ".join(words))


def _solve_topk(stdin):
    first, second = stdin.split("\n")
    counts = Counter(second.split())
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:int(first)]
    return "\n".join(f"{w} {c}" for w, c in ranked)


def _gen_group_sum(rng, size):
    rows = [f"{rng.choice(TEAMS)},{rng.randint(1, 50)}" for _ in range(size)]
    return _lines(len(rows), *rows)


def _solve_group_sum(stdin):
    lines = stdin.split("\n")
    totals = Counter()
    for row in lines[1:1 + int(lines[0])]:
        team, score = row.split(",")
        totals[team] += int(score)
    return "\n".join(f"{t} {totals[t]}" for t in sorted(totals))


def _gen_intervals(rng, size):
    rows = []
    for _ in range(size):
        a = rng.randint(0, 30)
        rows.append(f"{a} {a + rng.randint(1, 6)}")
    return _lines(len(rows), *rows)


def _solve_intervals(stdin):
    lines = stdin.split("\n")
    spans = sorted(tuple(map(int, r.split())) for r in lines[1:1 + int(lines[0])])
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return "\n".join(f"{a} {b}" for a, b in merged)


def _gen_brackets(rng, size):
    if rng.random() < 0.5:
        stack, out = [], []
        for _ in range(size):
            if stack and rng.random() < 0.5:
                out.append({"(": ")", "[": "]", "{": "}"}[stack.pop()])
            else:
                opener = rng.choice("([{")
                stack.append(opener)
                out.append(opener)
        while stack:
            out.append({"(": ")", "[": "]", "{": "}"}[stack.pop()])
        text = "".join(out)
        if rng.random() < 0.4 and len(text) > 2:
            i = rng.randrange(len(text))
            text = text[:i] + rng.choice(")]}") + text[i + 1:]
        return text
    return "".join(rng.choice("()[]{}") for _ in range(size + 2))


def _solve_brackets(stdin):
    pairs, stack = {")": "(", "]": "[", "}": "{"}, []
    for ch in stdin.strip():
        if ch in "([{":
            stack.append(ch)
        elif not stack or stack.pop() != pairs[ch]:
            return "NO"
    return "NO" if stack else "YES"


def _gen_dup(rng, size):
    pool = rng.sample(range(1, 40), min(size, 30))
    nums = pool[:]
    if rng.random() < 0.75:
        nums.insert(rng.randint(len(nums) // 2, len(nums)), rng.choice(nums))
    return " ".join(map(str, nums))


def _solve_dup(stdin):
    seen = set()
    for token in stdin.split():
        if token in seen:
            return token
        seen.add(token)
    return "-1"


def _gen_pairs(rng, size):
    nums = [rng.randint(1, 15) for _ in range(size)]
    return _lines(rng.randint(8, 22), " ".join(map(str, nums)))


def _solve_pairs(stdin):
    target, nums = stdin.split("\n")
    target, values = int(target), list(map(int, nums.split()))
    return str(sum(1 for i in range(len(values)) for j in range(i + 1, len(values)) if values[i] + values[j] == target))


def _gen_password(rng, size):
    minimum = rng.choice([8, 10])
    rows = []
    for _ in range(size):
        length = rng.randint(4, 14)
        chars = rng.choices("abcdefghXYZ0123456789", k=length)
        rows.append("".join(chars))
    return _lines(minimum, *rows)


def _solve_password(stdin):
    lines = stdin.split("\n")
    minimum, out = int(lines[0]), []
    for pw in lines[1:]:
        ok = len(pw) >= minimum and any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)
        out.append("VALID" if ok else "INVALID")
    return "\n".join(out)


def _gen_emails(rng, size):
    rows = []
    for _ in range(size):
        name = rng.choice(NAMES)
        rows.append(rng.choice([name, name.upper(), name.capitalize()]) + "@" + rng.choice(["example.com", "Example.com", "mail.org"]))
    return _lines(len(rows), *rows)


def _solve_emails(stdin):
    lines = stdin.split("\n")
    unique = sorted({e.lower() for e in lines[1:1 + int(lines[0])]})
    return "\n".join([str(len(unique))] + unique)


def _gen_bank(rng, size):
    rows = []
    for _ in range(size):
        rows.append(f"{rng.choice(['DEPOSIT', 'WITHDRAW'])} {rng.randint(1, 9) * 10}")
    return _lines(len(rows), *rows)


def _solve_bank(stdin):
    lines = stdin.split("\n")
    balance, out = 0, []
    for row in lines[1:1 + int(lines[0])]:
        action, amount = row.split()
        amount = int(amount)
        if action == "DEPOSIT":
            balance += amount
        elif amount > balance:
            out.append("INSUFFICIENT")
        else:
            balance -= amount
    out.append(f"BALANCE {balance}")
    return "\n".join(out)


def _gen_cart(rng, size):
    rows = [f"{rng.randint(1, 9) * 100} {rng.randint(1, 4)}" for _ in range(size)]
    return _lines(len(rows), *rows)


def _solve_cart(stdin):
    lines = stdin.split("\n")
    total = sum(int(p) * int(q) for p, q in (r.split() for r in lines[1:1 + int(lines[0])]))
    discount = 10 if total >= 3000 else 5 if total >= 1500 else 0
    return f"{total - total * discount // 100}"


def _gen_longest(rng, size):
    return "".join(rng.choice("abcde") for _ in range(size + 3))


def _solve_longest(stdin):
    text, best, start, seen = stdin.strip(), 0, 0, {}
    for i, ch in enumerate(text):
        if ch in seen and seen[ch] >= start:
            start = seen[ch] + 1
        seen[ch] = i
        best = max(best, i - start + 1)
    return str(best)


def _gen_matrix(rng, size):
    r, c = rng.randint(2, 4), rng.randint(2, 4)
    return _lines(f"{r} {c}", *[" ".join(str(rng.randint(1, 9)) for _ in range(c)) for _ in range(r)])


def _solve_matrix(stdin):
    lines = stdin.split("\n")
    r, c = map(int, lines[0].split())
    grid = [list(map(int, row.split())) for row in lines[1:1 + r]]
    out, top, bottom, left, right = [], 0, r - 1, 0, c - 1
    while top <= bottom and left <= right:
        out.extend(grid[top][left:right + 1])
        top += 1
        out.extend(grid[i][right] for i in range(top, bottom + 1))
        right -= 1
        if top <= bottom:
            out.extend(reversed(grid[bottom][left:right + 1]))
            bottom -= 1
        if left <= right:
            out.extend(grid[i][left] for i in range(bottom, top - 1, -1))
            left += 1
    return " ".join(map(str, out))


# title, scenario, description, input_spec, output_spec, difficulty, domain, generator, solver, sizes (typical, tiny, large)
TEMPLATES = [
    ("Response status summary", "You are reading an API gateway's access log.",
     "Read N, then N log lines of the form 'METHOD PATH STATUS'. Count the responses by status class and print four lines: 2xx, 3xx, 4xx and 5xx.",
     "First line N; then N lines 'METHOD PATH STATUS'.", "Four lines: '2xx: a', '3xx: b', '4xx: c', '5xx: d'.", "easy", "backend", _gen_status, _solve_status, (6, 1, 40)),
    ("Sliding-window rate limiter", "A public API allows each client only a few requests per time window.",
     "The first line has LIMIT and WINDOW. Then one integer timestamp per request (non-decreasing). A request is ALLOWED if fewer than LIMIT ALLOWED requests happened at times t' with t - WINDOW < t' <= t; otherwise it is DENIED (denied requests do not count). Print ALLOW or DENY for each request.",
     "Line 1: LIMIT WINDOW. Then one timestamp per line.", "One line per request: ALLOW or DENY.", "medium", "backend", _gen_rate, _solve_rate, (8, 2, 40)),
    ("LRU cache", "You are building an in-memory cache for a web service.",
     "The first line is the capacity. Then commands: 'PUT key value' stores a value (making that key most recently used, evicting the least recently used key when the cache is over capacity) and 'GET key' prints the value (making the key most recently used) or -1 if absent.",
     "Line 1: capacity. Then one command per line.", "One line for each GET command.", "hard", "backend", _gen_lru, _solve_lru, (10, 2, 40)),
    ("Top-K words", "A dashboard shows the most used search terms.",
     "The first line is K. The second line holds space-separated words. Print the K most frequent words with their counts, most frequent first; ties are broken alphabetically. Print 'word count' per line.",
     "Line 1: K. Line 2: words.", "K lines 'word count'.", "medium", "data", _gen_topk, _solve_topk, (12, 4, 60)),
    ("Score totals per team", "An analyst summarises a CSV export of match scores.",
     "Read N, then N lines 'team,score'. Print each team and its total score, sorted by team name, as 'team total'.",
     "Line 1: N. Then N lines 'team,score'.", "One line per team: 'team total'.", "easy", "data", _gen_group_sum, _solve_group_sum, (7, 1, 40)),
    ("Merge time slots", "A calendar service merges overlapping meeting slots.",
     "Read N, then N lines 'start end'. Merge slots that overlap or touch (a slot ending at 5 and one starting at 5 merge) and print the merged slots in ascending order as 'start end'.",
     "Line 1: N. Then N lines 'start end'.", "Merged slots, one per line.", "medium", "data", _gen_intervals, _solve_intervals, (6, 1, 35)),
    ("Bracket validator", "A code editor checks that brackets are balanced.",
     "Read one line containing only the characters ()[]{} and print YES if every opening bracket is closed by the same type in the right order, otherwise NO.",
     "One line of bracket characters.", "YES or NO.", "easy", "general", _gen_brackets, _solve_brackets, (8, 2, 50)),
    ("First repeated value", "A data-cleaning script finds the first duplicate id.",
     "Read one line of space-separated integers and print the first value that is seen a second time when scanning left to right, or -1 if no value repeats.",
     "One line of integers.", "The value or -1.", "easy", "general", _gen_dup, _solve_dup, (10, 3, 30)),
    ("Pairs with a target sum", "A billing tool looks for two charges that add up to a target.",
     "The first line is the target. The second line holds integers. Print how many pairs of positions i < j have values that add up to the target.",
     "Line 1: target. Line 2: integers.", "A single integer.", "medium", "general", _gen_pairs, _solve_pairs, (10, 3, 40)),
    ("Password checker", "A signup form validates passwords before they reach the database.",
     "The first line is the minimum length. Each following line is a password. Print VALID if it has at least that many characters and contains a lowercase letter, an uppercase letter and a digit, otherwise INVALID.",
     "Line 1: minimum length. Then one password per line.", "One VALID/INVALID line per password.", "easy", "backend", _gen_password, _solve_password, (7, 2, 40)),
    ("Unique emails", "A CRM removes duplicate customer emails.",
     "Read N, then N email addresses. Emails are equal ignoring letter case. Print the number of distinct emails, then the distinct emails in lowercase, sorted alphabetically, one per line.",
     "Line 1: N. Then N emails.", "Line 1: the count. Then the sorted emails.", "easy", "data", _gen_emails, _solve_emails, (8, 1, 40)),
    ("Account ledger", "A payments service applies deposits and withdrawals to one account that starts at 0.",
     "Read N, then N commands 'DEPOSIT amount' or 'WITHDRAW amount'. A withdrawal larger than the balance is rejected: print INSUFFICIENT and leave the balance unchanged. At the end print 'BALANCE x'.",
     "Line 1: N. Then N commands.", "INSUFFICIENT lines as they occur, then 'BALANCE x'.", "medium", "backend", _gen_bank, _solve_bank, (8, 1, 40)),
    ("Cart total with discount", "A checkout service prices a shopping cart in integer currency units.",
     "Read N, then N lines 'price quantity'. The total is the sum of price * quantity. If the total is at least 3000 apply a 10% discount, otherwise if it is at least 1500 apply 5%, otherwise none. The discount is total * percent // 100 (integer division). Print the final amount.",
     "Line 1: N. Then N lines 'price quantity'.", "One integer.", "easy", "backend", _gen_cart, _solve_cart, (5, 1, 30)),
    ("Longest unique run", "A log analyser looks for the longest stretch without a repeated character.",
     "Read one lowercase string and print the length of its longest substring in which no character repeats.",
     "One line: the string.", "One integer.", "hard", "general", _gen_longest, _solve_longest, (12, 2, 60)),
    ("Spiral order", "A layout engine reads a grid clockwise from the top-left corner.",
     "The first line has R and C, followed by R lines of C integers. Print all values in clockwise spiral order, space separated.",
     "Line 1: R C. Then R rows.", "One line of integers.", "hard", "general", _gen_matrix, _solve_matrix, (0, 0, 0)),
]

DIFFICULTY_LABEL = {"easy": "basic", "medium": "intermediate", "hard": "advanced"}

BAND_DIFFICULTIES = {
    "fresher": ["easy", "easy", "medium", "medium", "medium"],
    "junior": ["easy", "medium", "medium", "medium", "hard"],
    "senior": ["medium", "medium", "hard", "hard", "hard"],
}


def difficulty_slots(band: str, count: int) -> list[str]:
    return list(BAND_DIFFICULTIES.get(band, BAND_DIFFICULTIES["junior"]))[:count]


def _domain_preference(job_text: str) -> str:

    text = job_text.lower()

    if re.search(r"data|analyst|sql|etl|bi\b|report", text):
        return "data"

    if re.search(r"backend|back-end|(?<![a-z])api(?![a-z])|rest|server|django|flask|fastapi|spring|node|microservice|(?<![a-z])java(?![a-z])|python|golang|\.net|php|laravel", text):
        return "backend"

    return "general"


def parametric_coding_question(difficulty: str, allowed_languages: list[str], rng: random.Random,
                               domain: str = "general", used: set | None = None, return_domain: bool = False):
    """One parametric question; with return_domain=True also the template's domain (backend / data / general)."""

    used = used if used is not None else set()

    rank = {"easy": 0, "medium": 1, "hard": 2}
    pool = [t for t in TEMPLATES if t[5] == difficulty and t[0] not in used]
    preferred = [t for t in pool if t[6] == domain]

    if not preferred and domain != "general":
        # stay inside the job's own domain before ever going generic: take the domain's unused problems nearest in difficulty
        in_domain = [t for t in TEMPLATES if t[6] == domain and t[0] not in used]
        if in_domain:
            nearest = min(abs(rank[t[5]] - rank[difficulty]) for t in in_domain)
            preferred = [t for t in in_domain if abs(rank[t[5]] - rank[difficulty]) == nearest]

    choices = preferred or pool or [t for t in TEMPLATES if t[0] not in used] or TEMPLATES

    title, scenario, description, input_spec, output_spec, level, template_domain, generator, solver, sizes = rng.choice(choices)
    used.add(title)

    typical, tiny, large = sizes
    inputs = [generator(rng, typical), generator(rng, typical), generator(rng, tiny) if tiny else generator(rng, typical),
              generator(rng, large) if large else generator(rng, typical)]

    cases = []

    for index, stdin in enumerate(inputs):
        expected = solver(stdin).strip()
        cases.append({
            "stdin": stdin, "expected_stdout": expected or "0", "is_hidden": index != 0,
            "description": ["Sample input", "Typical input", "Smallest input", "Larger input"][index],
        })

    question = build_question(
        title=title, scenario=scenario, description=description, input_specification=input_spec, output_specification=output_spec,
        difficulty=level, allowed_languages=allowed_languages, cases=cases,
        examples=[{"input": cases[0]["stdin"], "output": cases[0]["expected_stdout"]}],
        skills_assessed=["problem solving", "data structures", "input parsing"], technology="algorithms",
    )

    return (question, template_domain) if return_domain else question


def parametric_coding_questions(count: int, band: str, allowed_languages: list[str], job_text: str = "",
                                rng: random.Random | None = None) -> list[ProgrammingMainQuestionV2]:

    rng = rng or random.Random()
    domain, used = _domain_preference(job_text), set()
    return [parametric_coding_question(d, allowed_languages, rng, domain, used) for d in difficulty_slots(band, count)]


# ------------------------------------------------------------
# Gemini questions: expected output comes from RUNNING the reference solution
# ------------------------------------------------------------

REFERENCE_KEY = "reference_solution_python"
MAX_MISMATCH_RATIO = 0.25


def apply_reference_solutions(raw_question: dict, execute) -> bool:
    """
    Replaces every test case's expected_stdout with the output of the question's Python reference solution.
    `execute(source, stdin)` returns (ok, stdout). Returns False when the question cannot be trusted
    (no reference, the reference fails, or it disagrees with Gemini's own expectations too often).
    """

    reference = raw_question.pop(REFERENCE_KEY, None)

    if not isinstance(reference, str) or len(reference.strip()) < 10:
        return False

    mismatches = total = 0

    for subpart in raw_question.get("subparts", []):
        for case in subpart.get("test_cases", []):

            ok, output = execute(reference, str(case.get("stdin", "")))

            if not ok or not output.strip():
                return False

            total += 1

            if output.strip() != str(case.get("expected_stdout", "")).strip():
                mismatches += 1
                case["expected_stdout"] = output.strip()

    if total == 0:
        return False

    if mismatches / total > MAX_MISMATCH_RATIO:
        logger.warning("Gemini expected outputs disagreed with its own reference in %s of %s cases.", mismatches, total)
        return False

    return True
