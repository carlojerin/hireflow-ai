import copy


# ============================================================
# JOBPILOT AI - PROGRAMMING QUESTION BANK (fallback)
#
# A fixed, deterministic set of 3 Python programming problems
# (easy / medium / hard), used whenever Gemini generation fails
# or produces invalid output. Every problem already validates
# against schemas/programming_question.ProgrammingQuestion —
# this is the same "reliable, predictable" role
# services/aptitude/question_bank.py plays for the aptitude
# stage.
#
# hidden_tests are never returned to a candidate — see
# sanitize_programming_question() in
# api/routes/programming_tests.py.
# ============================================================

PROGRAMMING_QUESTION_BANK = [

    {
        "question_id": 1,
        "title": "Sum of Two Numbers",
        "description": (
            "Read two integers from standard input (on a single "
            "line, separated by a space) and print their sum."
        ),
        "difficulty": "easy",
        "category": "Fundamentals",
        "primary_skill": "Python",
        "input_description": (
            "A single line containing two space-separated "
            "integers, a and b."
        ),
        "output_description": (
            "A single line containing the integer sum a + b."
        ),
        "constraints": "-10^6 <= a, b <= 10^6",
        "examples": [
            {"input": "2 3", "output": "5", "explanation": "2 + 3 = 5"},
            {"input": "-4 10", "output": "6", "explanation": "-4 + 10 = 6"},
        ],
        "starter_code": (
            "a, b = map(int, input().split())\n"
            "# TODO: print the sum of a and b\n"
        ),
        "expected_language": "python",
        "time_limit_seconds": 5,
        "memory_limit_mb": 128,
        "evaluation_notes": "Basic input parsing and arithmetic.",
        "hidden_tests": [
            {"input": "2 3", "expected_output": "5"},
            {"input": "-4 10", "expected_output": "6"},
            {"input": "0 0", "expected_output": "0"},
            {"input": "1000000 1000000", "expected_output": "2000000"},
            {"input": "-1000000 -1000000", "expected_output": "-2000000"},
        ],
    },

    {
        "question_id": 2,
        "title": "Word Frequency Counter",
        "description": (
            "Read a single line of text from standard input. "
            "Split it into whitespace-separated words (case-"
            "sensitive, punctuation kept as-is) and print the "
            "word that occurs most frequently. If there is a "
            "tie, print whichever of the tied words appears "
            "first in the input."
        ),
        "difficulty": "medium",
        "category": "Data Structures",
        "primary_skill": "Python",
        "input_description": "A single line of space-separated words.",
        "output_description": "The most frequent word, on its own line.",
        "constraints": "1 <= number of words <= 1000",
        "examples": [
            {
                "input": "the cat sat on the mat",
                "output": "the",
                "explanation": "'the' appears twice, every other word once.",
            },
        ],
        "starter_code": (
            "line = input()\n"
            "words = line.split()\n"
            "# TODO: find and print the most frequent word\n"
        ),
        "expected_language": "python",
        "time_limit_seconds": 5,
        "memory_limit_mb": 128,
        "evaluation_notes": "Tests dictionary/counter usage and tie-breaking by first occurrence.",
        "hidden_tests": [
            {"input": "the cat sat on the mat", "expected_output": "the"},
            {"input": "a a b b c", "expected_output": "a"},
            {"input": "single", "expected_output": "single"},
            {"input": "x y z x y x", "expected_output": "x"},
            {"input": "one two two one three three", "expected_output": "one"},
        ],
    },

    {
        "question_id": 3,
        "title": "Balanced Brackets",
        "description": (
            "Read a single line containing a string made only "
            "of the characters (){}[]. Print 'YES' if every "
            "opening bracket has a matching closing bracket of "
            "the same type in the correct order, otherwise "
            "print 'NO'."
        ),
        "difficulty": "hard",
        "category": "Algorithms",
        "primary_skill": "Python",
        "input_description": "A single line containing only the characters (){}[].",
        "output_description": "'YES' if the brackets are balanced, otherwise 'NO'.",
        "constraints": "0 <= length of string <= 10^4",
        "examples": [
            {"input": "({[]})", "output": "YES", "explanation": "Every bracket is correctly matched and nested."},
            {"input": "([)]", "output": "NO", "explanation": "Brackets are interleaved incorrectly."},
        ],
        "starter_code": (
            "s = input()\n"
            "# TODO: determine whether the brackets in s are balanced\n"
        ),
        "expected_language": "python",
        "time_limit_seconds": 5,
        "memory_limit_mb": 128,
        "evaluation_notes": "Classic stack-based bracket-matching problem.",
        "hidden_tests": [
            {"input": "({[]})", "expected_output": "YES"},
            {"input": "([)]", "expected_output": "NO"},
            {"input": "", "expected_output": "YES"},
            {"input": "(((", "expected_output": "NO"},
            {"input": "()[]{}", "expected_output": "YES"},
            {"input": "{[()]}", "expected_output": "YES"},
            {"input": "{[(])}", "expected_output": "NO"},
        ],
    },

]


def get_programming_questions() -> list[dict]:
    """
    Return a fresh deep copy of the fixed programming question
    bank, so callers can safely mutate/snapshot it (e.g. into a
    ProgrammingTest.questions JSON column) without risk of
    mutating the shared module-level list.
    """

    return copy.deepcopy(
        PROGRAMMING_QUESTION_BANK
    )
