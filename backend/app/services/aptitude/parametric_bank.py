import contextlib
import io
import math
import random
from fractions import Fraction

# ============================================================
# JOBPILOT AI - PARAMETRIC APTITUDE QUESTIONS (no AI)
#
# The last-resort source when Groq is unavailable and the question pool is
# empty. Every question is BUILT FROM RANDOM NUMBERS and its answer is COMPUTED
# (maths by formula, code questions by really running the snippet), so:
#   * two candidates never receive the same set, and
#   * the answer key is correct by construction.
# Verbal questions are drawn from curated word lists.
# ============================================================

# ------------------------------------------------------------
# helpers
# ------------------------------------------------------------


def _fmt(value) -> str:
    """Compact number text: 12.0 -> '12', 12.5 -> '12.5'."""

    if isinstance(value, Fraction):
        if value.denominator == 1:
            return str(value.numerator)
        return f"{value.numerator}/{value.denominator}"

    if isinstance(value, float):
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.2f}".rstrip("0").rstrip(".")

    return str(value)


def _numeric_options(rng, correct, spread, integer=True, positive=True, unit="", prefix=""):
    """Four distinct options around `correct` (which is one of them); returns (options, correct_text)."""

    options = {correct}
    attempts = 0

    while len(options) < 4 and attempts < 200:
        attempts += 1
        delta = rng.choice([-3, -2, -1, 1, 2, 3, 4, -4]) * spread * rng.choice([1, 1, 2])
        candidate = correct + delta
        if integer:
            candidate = int(round(candidate))
        if positive and candidate <= 0:
            continue
        options.add(candidate)

    if len(options) < 4:
        raise ValueError("could not build distinct options")

    ordered = list(options)
    rng.shuffle(ordered)

    texts = [f"{prefix}{_fmt(o)}{unit}" for o in ordered]
    return texts, f"{prefix}{_fmt(correct)}{unit}"


def _text_options(rng, correct, wrong_pool):
    wrong = [w for w in dict.fromkeys(wrong_pool) if w != correct]

    if len(wrong) < 3:
        raise ValueError("not enough distractors")

    options = rng.sample(wrong, 3) + [correct]
    rng.shuffle(options)
    return options, correct


def _q(category, difficulty, question, options, correct, explanation=""):

    if correct not in options or len(set(options)) != 4:
        raise ValueError("invalid parametric question")

    return {
        "category": category,
        "difficulty": difficulty,
        "question": question,
        "options": list(options),
        "correct_answer": correct,
        "explanation": explanation,
    }


def _run(code: str) -> str:
    """Executes one of OUR OWN fixed snippets and returns what it prints."""

    buffer = io.StringIO()

    namespace = {"__builtins__": {"range": range, "len": len, "print": print, "sum": sum, "max": max, "min": min, "str": str, "int": int, "list": list}}

    with contextlib.redirect_stdout(buffer):
        exec(code, namespace)

    return buffer.getvalue().strip()


# ------------------------------------------------------------
# quantitative aptitude
# ------------------------------------------------------------

def _percent_of(rng):
    p = rng.choice([8, 12, 15, 18, 24, 35, 45, 55, 65, 75])
    n = rng.choice(range(40, 401, 20))
    answer = Fraction(p * n, 100)
    options, correct = _numeric_options(rng, float(answer), max(2, int(float(answer) // 8) or 2), integer=False)
    return _q("quantitative_aptitude", "basic", f"What is {p}% of {n}?", options, correct, f"{p}/100 x {n} = {_fmt(float(answer))}.")


def _discount(rng):
    price = rng.choice(range(400, 4001, 100))
    d = rng.choice([10, 15, 20, 25, 30, 35, 40])
    answer = price * (100 - d) // 100 if price * (100 - d) % 100 == 0 else price * (100 - d) / 100
    options, correct = _numeric_options(rng, answer, max(10, price // 40), prefix="Rs ")
    return _q("quantitative_aptitude", "basic", f"An item marked at Rs {price} is sold at a {d}% discount. What is the selling price?", options, correct, f"{price} x (100-{d})/100 = {_fmt(answer)}.")


def _profit_percent(rng):
    cost = rng.choice(range(200, 2001, 50))
    profit_pct = rng.choice([10, 12, 15, 20, 25, 30, 40])
    sell = cost * (100 + profit_pct) // 100 if cost * (100 + profit_pct) % 100 == 0 else None
    if sell is None:
        return _profit_percent(rng)
    options, correct = _numeric_options(rng, profit_pct, 3, unit="%")
    return _q("quantitative_aptitude", "intermediate", f"A trader buys an article for Rs {cost} and sells it for Rs {sell}. What is the profit percentage?", options, correct, f"Profit {sell - cost} on cost {cost} = {profit_pct}%.")


def _ratio_share(rng):
    a, b = rng.choice([(2, 3), (3, 5), (4, 5), (5, 7), (3, 4), (2, 5), (5, 6)])
    k = rng.choice(range(3, 15))
    total = (a + b) * k
    larger = max(a, b) * k
    options, correct = _numeric_options(rng, larger, max(2, k))
    return _q("quantitative_aptitude", "intermediate", f"Two numbers are in the ratio {a}:{b} and their sum is {total}. What is the larger number?", options, correct, f"Each part is {k}; larger = {max(a, b)} x {k} = {larger}.")


def _average_missing(rng):
    count = rng.choice([4, 5, 6])
    values = [rng.choice(range(10, 60)) for _ in range(count)]
    target = rng.choice(range(25, 45))
    answer = target * (count + 1) - sum(values)
    if answer <= 0:
        return _average_missing(rng)
    options, correct = _numeric_options(rng, answer, 3)
    return _q("quantitative_aptitude", "advanced", f"The average of {count} numbers ({', '.join(map(str, values))}) is to become {target} after one more number is added. What must the new number be?", options, correct, f"{target} x {count + 1} - {sum(values)} = {answer}.")


def _speed_distance(rng):
    speed = rng.choice(range(30, 91, 5))
    hours = rng.choice([1.5, 2, 2.5, 3, 4])
    distance = speed * hours
    if rng.random() < 0.5:
        options, correct = _numeric_options(rng, speed, 5, unit=" km/h")
        return _q("quantitative_aptitude", "basic", f"A vehicle covers {_fmt(distance)} km in {_fmt(hours)} hours. What is its average speed?", options, correct, f"{_fmt(distance)} / {_fmt(hours)} = {speed} km/h.")
    options, correct = _numeric_options(rng, distance, 10, unit=" km")
    return _q("quantitative_aptitude", "basic", f"A vehicle travels at {speed} km/h for {_fmt(hours)} hours. How far does it go?", options, correct, f"{speed} x {_fmt(hours)} = {_fmt(distance)} km.")


def _time_and_work(rng):
    x, y = rng.choice([(12, 6), (10, 15), (20, 30), (12, 4), (15, 10), (30, 20), (18, 9), (24, 8), (36, 18), (40, 10)])
    together = Fraction(x * y, x + y)
    if together.denominator != 1:
        return _time_and_work(rng)
    options, correct = _numeric_options(rng, int(together), 1, unit=" days")
    return _q("quantitative_aptitude", "intermediate", f"A can finish a job in {x} days and B can finish it in {y} days. Working together, how many days do they need?", options, correct, f"1/{x} + 1/{y} = 1/{int(together)}.")


def _simple_interest(rng):
    principal = rng.choice(range(2000, 20001, 1000))
    rate = rng.choice([4, 5, 6, 8, 10, 12])
    years = rng.choice([2, 3, 4, 5])
    interest = principal * rate * years // 100
    options, correct = _numeric_options(rng, interest, max(20, interest // 10), prefix="Rs ")
    return _q("quantitative_aptitude", "basic", f"What is the simple interest on Rs {principal} at {rate}% per annum for {years} years?", options, correct, f"{principal} x {rate} x {years} / 100 = {interest}.")


def _combinations(rng):
    n = rng.choice(range(5, 10))
    k = rng.choice([2, 3])
    answer = math.comb(n, k)
    options, correct = _numeric_options(rng, answer, max(2, answer // 8))
    return _q("quantitative_aptitude", "advanced", f"In how many ways can {k} people be chosen from a group of {n}?", options, correct, f"C({n},{k}) = {answer}.")


def _probability(rng):
    red, green, blue = rng.choice(range(2, 8)), rng.choice(range(2, 8)), rng.choice(range(1, 6))
    total = red + green + blue
    answer = Fraction(green, total)
    wrong = [Fraction(red, total), Fraction(blue, total), Fraction(green, total + 1), Fraction(green + 1, total), Fraction(total - green, total)]
    options, correct = _text_options(rng, _fmt(answer), [_fmt(w) for w in wrong])
    return _q("quantitative_aptitude", "intermediate", f"A bag has {red} red, {green} green and {blue} blue balls. One ball is drawn at random. What is the probability it is green?", options, correct, f"{green}/{total} simplified.")


def _remainder(rng):
    divisor = rng.choice(range(5, 14))
    number = rng.choice(range(100, 999))
    answer = number % divisor
    options, correct = _numeric_options(rng, answer, 1, positive=False)
    options = [o for o in options if int(o) >= 0]
    if len(set(options)) != 4 or correct not in options:
        return _remainder(rng)
    return _q("quantitative_aptitude", "basic", f"What is the remainder when {number} is divided by {divisor}?", options, correct, f"{number} = {divisor} x {number // divisor} + {answer}.")


def _percent_change(rng):
    old = rng.choice(range(40, 401, 20))
    pct = rng.choice([10, 20, 25, 30, 50])
    new = old * (100 + pct) // 100
    if old * (100 + pct) % 100 != 0:
        return _percent_change(rng)
    options, correct = _numeric_options(rng, pct, 5, unit="%")
    return _q("quantitative_aptitude", "intermediate", f"A quantity rises from {old} to {new}. What is the percentage increase?", options, correct, f"({new}-{old})/{old} x 100 = {pct}%.")


QUANT = [_percent_of, _discount, _profit_percent, _ratio_share, _average_missing, _speed_distance, _time_and_work,
         _simple_interest, _combinations, _probability, _remainder, _percent_change]


# ------------------------------------------------------------
# logical reasoning
# ------------------------------------------------------------

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _arithmetic_series(rng):
    start, step = rng.choice(range(2, 30)), rng.choice(range(2, 12))
    terms = [start + step * i for i in range(5)]
    answer = start + step * 5
    options, correct = _numeric_options(rng, answer, step)
    return _q("logical_reasoning", "basic", f"Find the next number: {', '.join(map(str, terms))}, ?", options, correct, f"The difference is {step}.")


def _growing_difference_series(rng):
    start, first = rng.choice(range(1, 20)), rng.choice([1, 2, 3])
    terms, value, diff = [start], start, first
    for _ in range(4):
        value += diff
        terms.append(value)
        diff += 1
    answer = value + diff
    options, correct = _numeric_options(rng, answer, 2)
    return _q("logical_reasoning", "intermediate", f"Find the next number: {', '.join(map(str, terms))}, ?", options, correct, "The differences increase by 1 each step.")


def _geometric_series(rng):
    start, ratio = rng.choice([1, 2, 3, 4, 5]), rng.choice([2, 3])
    terms = [start * ratio ** i for i in range(4)]
    answer = start * ratio ** 4
    options, correct = _numeric_options(rng, answer, max(2, answer // 6))
    return _q("logical_reasoning", "intermediate", f"Find the next number: {', '.join(map(str, terms))}, ?", options, correct, f"Each term is multiplied by {ratio}.")


def _letter_series(rng):
    shift, first = rng.choice([2, 3, 4, 5]), rng.choice(range(0, 10))
    letters = [LETTERS[(first + shift * i) % 26] for i in range(4)]
    answer = LETTERS[(first + shift * 4) % 26]
    options, correct = _text_options(rng, answer, [LETTERS[(first + shift * 4 + d) % 26] for d in (-2, -1, 1, 2, 3)])
    return _q("logical_reasoning", "intermediate", f"Find the next letter: {', '.join(letters)}, ?", options, correct, f"Each letter moves {shift} places forward.")


def _day_arithmetic(rng):
    day, n = rng.randrange(7), rng.choice(range(20, 200))
    answer = DAYS[(day + n) % 7]
    options, correct = _text_options(rng, answer, [d for d in DAYS])
    return _q("logical_reasoning", "advanced", f"If today is {DAYS[day]}, what day of the week will it be after {n} days?", options, correct, f"{n} mod 7 = {n % 7}.")


def _clock_angle(rng):
    hour, minute = rng.choice(range(1, 12)), rng.choice([0, 10, 15, 20, 30, 40, 45, 50])
    angle = abs(30 * hour - 5.5 * minute)
    angle = min(angle, 360 - angle)
    options, correct = _numeric_options(rng, angle, 15, integer=False, unit=" degrees")
    return _q("logical_reasoning", "advanced", f"What is the smaller angle between the hour and minute hands at {hour}:{minute:02d}?", options, correct, f"|30 x {hour} - 5.5 x {minute}| = {_fmt(angle)}.")


def _coding_decoding(rng):
    shift = rng.choice([1, 2, 3, 4])
    word = rng.choice(["TEAM", "CODE", "DATA", "LOGIC", "QUERY", "STACK", "BUILD", "CLOUD", "INDEX", "TABLE"])
    coded = "".join(LETTERS[(LETTERS.index(c) + shift) % 26] for c in word)
    wrong = ["".join(LETTERS[(LETTERS.index(c) + s) % 26] for c in word) for s in (shift + 1, shift - 1, shift + 2, shift - 2) if s % 26 != shift]
    options, correct = _text_options(rng, coded, wrong)
    return _q("logical_reasoning", "intermediate", f"In a certain code every letter is replaced by the letter {shift} places after it. How is '{word}' written in this code?", options, correct, f"Shift each letter by {shift}.")


def _odd_number_out(rng):
    base = rng.choice([3, 4, 6, 7, 9])
    good = rng.sample(range(2, 15), 3)
    odd = rng.choice([n for n in range(10, 100) if n % base != 0])
    numbers = [g * base for g in good] + [odd]
    rng.shuffle(numbers)
    options = [str(n) for n in numbers]
    return _q("logical_reasoning", "basic", "Find the odd one out: " + ", ".join(options) + ".", options, str(odd), f"Every other number is a multiple of {base}.")


def _direction_distance(rng):
    a, b, c = rng.choice([(3, 4, 5), (6, 8, 10), (5, 12, 13), (9, 12, 15), (8, 15, 17)])
    options, correct = _numeric_options(rng, c, 1, unit=" km")
    return _q("logical_reasoning", "advanced", f"A person walks {a} km north and then {b} km east. How far is the person from the starting point?", options, correct, f"sqrt({a}^2 + {b}^2) = {c}.")


def _syllogism(rng):
    a, b, c = rng.sample(["engineers", "developers", "testers", "managers", "analysts", "designers", "students", "writers"], 3)
    correct = f"All {a} are {c}"
    options = [correct, f"All {c} are {a}", f"No {a} is {c}", f"Some {c} are not {b}"]
    rng.shuffle(options)
    return _q("logical_reasoning", "intermediate", f"Statements: All {a} are {b}. All {b} are {c}. Which conclusion definitely follows?", options, correct, "The two statements chain.")


LOGICAL = [_arithmetic_series, _growing_difference_series, _geometric_series, _letter_series, _day_arithmetic,
           _clock_angle, _coding_decoding, _odd_number_out, _direction_distance, _syllogism]


# ------------------------------------------------------------
# verbal ability (curated lists)
# ------------------------------------------------------------

SYNONYMS = {
    "abundant": "plentiful", "candid": "frank", "diligent": "hardworking", "eloquent": "articulate",
    "frugal": "thrifty", "genuine": "authentic", "hostile": "unfriendly", "inevitable": "unavoidable",
    "meticulous": "careful", "obsolete": "outdated", "prudent": "wise", "resilient": "tough",
    "scarce": "rare", "tedious": "boring", "vivid": "bright", "obstinate": "stubborn",
    "ebullient": "exuberant", "lucid": "clear", "concise": "brief", "ambiguous": "unclear",
    "benevolent": "kind", "conceal": "hide", "durable": "long-lasting", "feasible": "possible",
}

ANTONYMS = {
    "abundant": "scarce", "ancient": "modern", "brave": "cowardly", "expand": "shrink", "generous": "stingy",
    "lethargic": "energetic", "opaque": "transparent", "rigid": "flexible", "victory": "defeat", "optimistic": "pessimistic",
    "temporary": "permanent", "ascend": "descend", "accept": "reject", "humble": "arrogant", "vague": "precise",
    "complex": "simple", "hostile": "friendly", "increase": "decrease", "shallow": "deep", "diligent": "lazy",
    "frequent": "rare", "cautious": "reckless", "elaborate": "simple", "innocent": "guilty",
}

FILL_BLANKS = [
    ("She has been working here ___ 2019.", "since", ["for", "from", "during"]),
    ("Neither the manager nor the developers ___ available today.", "are", ["is", "was", "has been"]),
    ("The team completed ___ project ahead of schedule.", "its", ["it's", "their's", "there"]),
    ("If I ___ you, I would review the code first.", "were", ["was", "am", "be"]),
    ("Each of the engineers ___ a laptop.", "has", ["have", "are having", "were having"]),
    ("He is senior ___ me by two years.", "to", ["than", "from", "over"]),
    ("The report, along with the charts, ___ ready.", "is", ["are", "were", "have been"]),
    ("She insisted ___ paying for the lunch.", "on", ["at", "to", "for"]),
    ("We look forward to ___ from you.", "hearing", ["hear", "heard", "have heard"]),
    ("The data ___ collected over three months.", "was", ["were being", "have", "are"]),
    ("He divided the tasks ___ the four interns.", "among", ["between", "with", "by"]),
    ("Please ___ the meeting notes before Friday.", "submit", ["submits", "submitting", "submitted by"]),
]

ODD_WORDS = [
    (["Python", "Java", "Ruby", "HTML"], "HTML"),
    (["Table", "Row", "Column", "Chair"], "Chair"),
    (["Mouse", "Keyboard", "Monitor", "Bread"], "Bread"),
    (["Linux", "Windows", "macOS", "Oracle"], "Oracle"),
    (["Stack", "Queue", "Array", "Router"], "Router"),
    (["Chrome", "Firefox", "Safari", "Excel"], "Excel"),
    (["Nurse", "Doctor", "Surgeon", "Carpenter"], "Carpenter"),
    (["River", "Lake", "Ocean", "Mountain"], "Mountain"),
]


def _synonym(rng):
    word = rng.choice(list(SYNONYMS))
    options, correct = _text_options(rng, SYNONYMS[word], [v for k, v in SYNONYMS.items() if k != word] + list(ANTONYMS.values()))
    return _q("verbal_reasoning", "basic", f"Choose the word closest in meaning to '{word.upper()}'.", options, correct, f"'{word}' means '{SYNONYMS[word]}'.")


def _antonym(rng):
    word = rng.choice(list(ANTONYMS))
    options, correct = _text_options(rng, ANTONYMS[word], [v for k, v in ANTONYMS.items() if k != word] + [SYNONYMS.get(word, "similar")])
    return _q("verbal_reasoning", "intermediate", f"Choose the word opposite in meaning to '{word.upper()}'.", options, correct, f"The opposite of '{word}' is '{ANTONYMS[word]}'.")


def _fill_blank(rng):
    sentence, right, wrong = rng.choice(FILL_BLANKS)
    options, correct = _text_options(rng, right, wrong)
    return _q("verbal_reasoning", "intermediate", f"Choose the word that best completes the sentence: {sentence}", options, correct, "Correct grammar and usage.")


def _odd_word(rng):
    words, odd = rng.choice(ODD_WORDS)
    options = list(words)
    rng.shuffle(options)
    return _q("verbal_reasoning", "basic", "Choose the word that does not belong with the others.", options, odd, f"'{odd}' is from a different group.")


VERBAL = [_synonym, _antonym, _fill_blank, _odd_word]


# ------------------------------------------------------------
# technical logic (snippets are really executed to get the answer)
# ------------------------------------------------------------

def _trace_option_set(rng, answer_text, numeric_answer):
    wrong = []
    for d in (-2, -1, 1, 2, 3, 5, -3):
        candidate = numeric_answer + d
        if candidate != numeric_answer and candidate >= 0:
            wrong.append(str(candidate))
    return _text_options(rng, answer_text, wrong)


def _t_loop_sum(rng):
    a, b, s = rng.choice(range(1, 6)), rng.choice(range(9, 30)), rng.choice([1, 2, 3])
    code = f"total = 0\nfor i in range({a}, {b}, {s}):\n    total += i\nprint(total)"
    answer = _run(code)
    options, correct = _trace_option_set(rng, answer, int(answer))
    return _q("technical_logic", "intermediate", f"What does this code print?\n\n{code}", options, correct, "Trace the loop.")


def _t_modulo_count(rng):
    n, m, k = rng.choice(range(20, 60)), rng.choice([3, 4]), rng.choice([5, 6, 7])
    code = f"count = 0\nfor n in range(1, {n} + 1):\n    if n % {m} == 0 or n % {k} == 0:\n        count += 1\nprint(count)"
    answer = _run(code)
    options, correct = _trace_option_set(rng, answer, int(answer))
    return _q("technical_logic", "advanced", f"What does this code print?\n\n{code}", options, correct, "Count numbers divisible by either value.")


def _t_slice(rng):
    items = rng.sample(range(1, 30), 6)
    i, j = rng.choice([0, 1, 2]), rng.choice([4, 5, 6])
    code = f"items = {items}\nprint(items[{i}:{j}])"
    answer = _run(code)
    wrong = [str(items[i:j + d]) for d in (-1, 1)] + [str(items[i + 1:j]), str(items[i:j][::-1])]
    options, correct = _text_options(rng, answer, wrong)
    return _q("technical_logic", "intermediate", f"What does this code print?\n\n{code}", options, correct, "Slices exclude the end index.")


def _t_halving(rng):
    n = rng.choice(range(20, 500))
    code = f"n = {n}\nsteps = 0\nwhile n > 1:\n    n //= 2\n    steps += 1\nprint(steps)"
    answer = _run(code)
    options, correct = _trace_option_set(rng, answer, int(answer))
    return _q("technical_logic", "advanced", f"What does this code print?\n\n{code}", options, correct, "Count the halvings until n reaches 1.")


def _t_default_arg(rng):
    a, b, c = rng.choice(range(2, 9)), rng.choice(range(2, 9)), rng.choice(range(1, 9))
    code = f"def calc(x, y={b}):\n    return x * y + {c}\n\nprint(calc({a}))"
    answer = _run(code)
    options, correct = _trace_option_set(rng, answer, int(answer))
    return _q("technical_logic", "basic", f"What does this code print?\n\n{code}", options, correct, "The default value of y is used.")


def _t_off_by_one(rng):
    n = rng.choice(range(4, 12))
    code = f"def sum_upto(n):\n    total = 0\n    for i in range(1, n):\n        total += i\n    return total\n\nprint(sum_upto({n}))"
    answer = _run(code)
    options, correct = _trace_option_set(rng, answer, int(answer))
    return _q("technical_logic", "intermediate", f"This function was meant to add 1..n. What does the code print?\n\n{code}", options, correct, "range(1, n) stops before n.")


def _t_recursion(rng):
    n = rng.choice(range(3, 7))
    code = f"def f(n):\n    if n <= 1:\n        return 1\n    return n * f(n - 1)\n\nprint(f({n}))"
    answer = _run(code)
    options, correct = _trace_option_set(rng, answer, int(answer))
    return _q("technical_logic", "advanced", f"What does this code print?\n\n{code}", options, correct, "Factorial recursion.")


def _t_sql(rng):
    rows = [(i + 1, rng.choice(range(50, 400, 10))) for i in range(rng.choice([4, 5]))]
    threshold = rng.choice(range(80, 300, 20))
    values = [amount for _id, amount in rows]
    kind = rng.choice(["SUM", "COUNT", "MAX"])
    matching = [v for v in values if v > threshold]
    if not matching:
        return _t_sql(rng)
    answer = {"SUM": sum(matching), "COUNT": len(matching), "MAX": max(matching)}[kind]
    table = ", ".join(f"({i}, {a})" for i, a in rows)
    column = "*" if kind == "COUNT" else "amount"
    options, correct = _numeric_options(rng, answer, max(1, answer // 10 if answer > 20 else 1))
    return _q("technical_logic", "intermediate", f"Table orders(id, amount) contains rows {table}. What does SELECT {kind}({column}) FROM orders WHERE amount > {threshold}; return?", options, correct, "Filter first, then aggregate.")


COMPLEXITY = [
    ("for i in range(n):\n    for j in range(n):\n        pass", "O(n^2)"),
    ("for i in range(n):\n    pass", "O(n)"),
    ("i = 1\nwhile i < n:\n    i *= 2", "O(log n)"),
    ("x = items[0]", "O(1)"),
    ("for i in range(n):\n    for j in range(i):\n        pass", "O(n^2)"),
    ("items.sort()", "O(n log n)"),
]

HTTP_FACTS = [
    ("Which HTTP status code means the requested resource was not found?", "404", ["200", "301", "500"]),
    ("Which HTTP status code means a request was created successfully?", "201", ["204", "302", "400"]),
    ("Which HTTP status code means the client is not authenticated?", "401", ["403", "404", "409"]),
    ("Which HTTP method is safe and idempotent for reading data?", "GET", ["POST", "PATCH", "DELETE"]),
    ("Which HTTP status code means a server error?", "500", ["404", "302", "201"]),
    ("Which HTTP status code means the request conflicts with the current state of the resource?", "409", ["401", "404", "500"]),
]

STRUCTURES = [
    ("You need first-in, first-out processing of tasks. Which structure fits best?", "queue", ["stack", "set", "tree"]),
    ("You need fast lookup of a value by key. Which Python structure fits best?", "dict", ["list", "tuple", "string"]),
    ("You need to remove duplicates from a list quickly. Which structure fits best?", "set", ["list", "tuple", "queue"]),
    ("You need last-in, first-out undo behaviour. Which structure fits best?", "stack", ["queue", "set", "heap"]),
]


def _t_complexity(rng):
    code, answer = rng.choice(COMPLEXITY)
    options, correct = _text_options(rng, answer, ["O(1)", "O(n)", "O(n^2)", "O(log n)", "O(n log n)", "O(2^n)"])
    return _q("technical_logic", "advanced", f"What is the time complexity of this code?\n\n{code}", options, correct, "Count how many times the work repeats.")


def _t_http(rng):
    question, answer, wrong = rng.choice(HTTP_FACTS)
    options, correct = _text_options(rng, answer, wrong)
    return _q("technical_logic", "basic", question, options, correct, "Standard HTTP semantics.")


def _t_structure(rng):
    question, answer, wrong = rng.choice(STRUCTURES)
    options, correct = _text_options(rng, answer, wrong)
    return _q("technical_logic", "basic", question, options, correct, "Choose the structure that matches the access pattern.")


def _t_boolean(rng):
    a, b, c = rng.choice([True, False]), rng.choice([True, False]), rng.choice([True, False])
    expression = rng.choice(["(a and b) or c", "a and (b or c)", "not a or (b and c)", "(a or b) and not c"])
    code = f"a, b, c = {a}, {b}, {c}\nprint({expression})"
    answer = _run(code)
    options = ["True", "False", "None", "Error"]
    return _q("technical_logic", "intermediate", f"What does this code print?\n\n{code}", options, answer, "Evaluate with Python precedence.")


TECHNICAL = [_t_loop_sum, _t_modulo_count, _t_slice, _t_halving, _t_default_arg, _t_off_by_one, _t_recursion,
             _t_sql, _t_complexity, _t_http, _t_structure, _t_boolean]

TEMPLATES = {
    "quantitative_aptitude": QUANT,
    "logical_reasoning": LOGICAL,
    "verbal_reasoning": VERBAL,
    "technical_logic": TECHNICAL,
}

# What difficulty each experience band prefers per category (falls back to any template)
BAND_DIFFICULTY = {
    "fresher": {"quantitative_aptitude": {"intermediate", "advanced"}, "logical_reasoning": {"intermediate", "advanced"},
                "verbal_reasoning": {"intermediate", "basic"}},
    "junior": {"quantitative_aptitude": {"basic", "intermediate"}, "logical_reasoning": {"basic", "intermediate"},
               "verbal_reasoning": {"basic", "intermediate"}, "technical_logic": {"intermediate", "basic"}},
    "senior": {"quantitative_aptitude": {"basic"}, "logical_reasoning": {"basic"}, "verbal_reasoning": {"basic"},
               "technical_logic": {"advanced", "intermediate"}},
}


def generate_parametric_questions(band: str, plan: dict, rng: random.Random | None = None) -> list[dict]:
    """
    A complete, per-candidate question set (ids 1..N) with COMPUTED answer keys.
    `plan` is {category: count}; `band` is fresher | junior | senior.
    """

    rng = rng or random.Random()
    wanted = BAND_DIFFICULTY.get(band, {})

    slots = [category for category, count in plan.items() for _ in range(count)]
    rng.shuffle(slots)

    questions, seen, used_templates = [], set(), set()

    for category in slots:

        preferred = wanted.get(category)
        built = None

        for attempt in range(60):

            template = rng.choice(TEMPLATES[category])

            try:
                candidate = template(rng)
            except (ValueError, RecursionError):
                continue

            if candidate["question"] in seen:
                continue

            # prefer a template not used yet in this set (no two questions worded alike)
            if template in used_templates and attempt < 40:
                continue

            # first ~30 tries insist on the band's difficulty, then accept any valid question
            if preferred and attempt < 30 and candidate["difficulty"] not in preferred:
                continue

            built = candidate
            break

        if built is None:
            raise RuntimeError(f"Could not build a {category} question.")

        seen.add(built["question"])
        used_templates.add(template)
        questions.append(built)

    for number, item in enumerate(questions, start=1):
        item["id"] = number

    return questions
