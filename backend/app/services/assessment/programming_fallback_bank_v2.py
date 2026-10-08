from backend.app.schemas.programming_question_v2 import (
    ExecutableTestCase,
    ProgrammingMainQuestionV2,
    ProgrammingQuestionSetV2,
    ProgrammingSubpartV2,
)


# ============================================================
# JOBPILOT AI — PROGRAMMING V2 DETERMINISTIC FALLBACK BANK
# (Programming Engine V2, AI generation task, Step 7)
#
# Guaranteed-available, Gemini-independent, and — critically —
# genuinely EXECUTABLE: every stdin/expected_stdout pair below
# was hand-verified against the stated logic, not invented. This
# is what actually runs when Gemini is unavailable/misconfigured/
# returns invalid output, so it must be real, correct data, not
# a plausible-looking placeholder.
#
# Language-neutral by construction (stdin/stdout only) — the
# SAME 5 questions are handed to a Python, JavaScript, or C#
# candidate; only allowed_languages (passed in by the caller,
# never hardcoded here) changes.
# ============================================================


def _sum_of_two_numbers(allowed_languages: list[str]) -> ProgrammingMainQuestionV2:

    return ProgrammingMainQuestionV2(
        question_id=1,
        title="Sum of Two Numbers",
        description=(
            "Read two integers from standard input, given on a "
            "single line separated by a space, and print their sum."
        ),
        constraints="-1,000,000 <= a, b <= 1,000,000",
        examples=[{"input": "2 3", "output": "5"}],
        input_specification="A single line: two space-separated integers a and b.",
        output_specification="A single line containing the integer a + b.",
        difficulty="easy",
        allowed_languages=allowed_languages,
        points=20.0,
        subparts=[
            ProgrammingSubpartV2(
                subpart_id="sp1",
                description="Basic correctness on typical positive inputs.",
                weight=0.5,
                test_cases=[
                    ExecutableTestCase(test_case_id="tc1", stdin="2 3", expected_stdout="5", weight=0.5, is_hidden=False, description="Basic example"),
                    ExecutableTestCase(test_case_id="tc2", stdin="10 20", expected_stdout="30", weight=0.5, is_hidden=True),
                ],
            ),
            ProgrammingSubpartV2(
                subpart_id="sp2",
                description="Negative numbers and boundary values.",
                weight=0.5,
                test_cases=[
                    ExecutableTestCase(test_case_id="tc3", stdin="-4 10", expected_stdout="6", weight=0.5, is_hidden=True),
                    ExecutableTestCase(test_case_id="tc4", stdin="1000000 -1000000", expected_stdout="0", weight=0.5, is_hidden=True),
                ],
            ),
        ],
    )


def _word_frequency_counter(allowed_languages: list[str]) -> ProgrammingMainQuestionV2:

    return ProgrammingMainQuestionV2(
        question_id=2,
        title="Word Frequency Counter",
        description=(
            "Read a single line of whitespace-separated words and "
            "print the word that occurs most frequently. On a tie, "
            "print whichever tied word appears first in the input."
        ),
        constraints="1 <= number of words <= 1000",
        examples=[{"input": "the cat sat on the mat", "output": "the"}],
        input_specification="A single line of space-separated words.",
        output_specification="The most frequent word, on its own line.",
        difficulty="medium",
        allowed_languages=allowed_languages,
        points=20.0,
        subparts=[
            ProgrammingSubpartV2(
                subpart_id="sp1",
                description="A clear single most-frequent word.",
                weight=0.5,
                test_cases=[
                    ExecutableTestCase(test_case_id="tc1", stdin="the cat sat on the mat", expected_stdout="the", weight=1.0, is_hidden=False, description="Basic example"),
                ],
            ),
            ProgrammingSubpartV2(
                subpart_id="sp2",
                description="Tie-breaking by first occurrence.",
                weight=0.5,
                test_cases=[
                    ExecutableTestCase(test_case_id="tc2", stdin="a a b b c", expected_stdout="a", weight=0.5, is_hidden=True),
                    ExecutableTestCase(test_case_id="tc3", stdin="single", expected_stdout="single", weight=0.5, is_hidden=True),
                ],
            ),
        ],
    )


def _balanced_brackets(allowed_languages: list[str]) -> ProgrammingMainQuestionV2:

    return ProgrammingMainQuestionV2(
        question_id=3,
        title="Balanced Brackets",
        description=(
            "Read a single line containing only the characters "
            "(){}[] and print YES if every opening bracket has a "
            "matching closing bracket of the same type in the "
            "correct order, otherwise print NO."
        ),
        constraints="0 <= length of string <= 10,000",
        examples=[{"input": "({[]})", "output": "YES"}],
        input_specification="A single line containing only the characters (){}[].",
        output_specification="YES if balanced, otherwise NO.",
        difficulty="hard",
        allowed_languages=allowed_languages,
        points=20.0,
        subparts=[
            ProgrammingSubpartV2(
                subpart_id="sp1",
                description="Correctly nested and correctly interleaved cases.",
                weight=0.5,
                test_cases=[
                    ExecutableTestCase(test_case_id="tc1", stdin="({[]})", expected_stdout="YES", weight=0.5, is_hidden=False, description="Basic example"),
                    ExecutableTestCase(test_case_id="tc2", stdin="()[]{}", expected_stdout="YES", weight=0.5, is_hidden=True),
                ],
            ),
            ProgrammingSubpartV2(
                subpart_id="sp2",
                description="Unbalanced/interleaved edge cases.",
                weight=0.5,
                test_cases=[
                    ExecutableTestCase(test_case_id="tc3", stdin="([)]", expected_stdout="NO", weight=0.34, is_hidden=True),
                    ExecutableTestCase(test_case_id="tc4", stdin="(((", expected_stdout="NO", weight=0.33, is_hidden=True),
                    ExecutableTestCase(test_case_id="tc5", stdin="\n", expected_stdout="YES", weight=0.33, is_hidden=True, description="Empty string is trivially balanced"),
                ],
            ),
        ],
    )


def _first_duplicate(allowed_languages: list[str]) -> ProgrammingMainQuestionV2:

    return ProgrammingMainQuestionV2(
        question_id=4,
        title="First Duplicate Value",
        description=(
            "Read a single line of space-separated integers and "
            "print the FIRST value that is seen a second time while "
            "scanning left to right. If no value repeats, print -1."
        ),
        constraints="1 <= number of integers <= 10,000",
        examples=[{"input": "2 1 3 5 3 2", "output": "3"}],
        input_specification="A single line of space-separated integers.",
        output_specification="The first repeated value, or -1 if none.",
        difficulty="medium",
        allowed_languages=allowed_languages,
        points=20.0,
        subparts=[
            ProgrammingSubpartV2(
                subpart_id="sp1",
                description="A list with a clear first duplicate.",
                weight=0.5,
                test_cases=[
                    ExecutableTestCase(test_case_id="tc1", stdin="2 1 3 5 3 2", expected_stdout="3", weight=1.0, is_hidden=False, description="Basic example"),
                ],
            ),
            ProgrammingSubpartV2(
                subpart_id="sp2",
                description="No duplicates at all.",
                weight=0.5,
                test_cases=[
                    ExecutableTestCase(test_case_id="tc2", stdin="1 2 3 4", expected_stdout="-1", weight=0.5, is_hidden=True),
                    ExecutableTestCase(test_case_id="tc3", stdin="7", expected_stdout="-1", weight=0.5, is_hidden=True),
                ],
            ),
        ],
    )


def _palindrome_check(allowed_languages: list[str]) -> ProgrammingMainQuestionV2:

    return ProgrammingMainQuestionV2(
        question_id=5,
        title="Palindrome Check",
        description=(
            "Read a single line containing a string of lowercase "
            "letters and print YES if it reads the same forwards "
            "and backwards, otherwise print NO."
        ),
        constraints="1 <= length of string <= 10,000",
        examples=[{"input": "racecar", "output": "YES"}],
        input_specification="A single line of lowercase letters.",
        output_specification="YES if the string is a palindrome, otherwise NO.",
        difficulty="easy",
        allowed_languages=allowed_languages,
        points=20.0,
        subparts=[
            ProgrammingSubpartV2(
                subpart_id="sp1",
                description="A true palindrome.",
                weight=0.5,
                test_cases=[
                    ExecutableTestCase(test_case_id="tc1", stdin="racecar", expected_stdout="YES", weight=1.0, is_hidden=False, description="Basic example"),
                ],
            ),
            ProgrammingSubpartV2(
                subpart_id="sp2",
                description="A non-palindrome and a single-character edge case.",
                weight=0.5,
                test_cases=[
                    ExecutableTestCase(test_case_id="tc2", stdin="hello", expected_stdout="NO", weight=0.5, is_hidden=True),
                    ExecutableTestCase(test_case_id="tc3", stdin="a", expected_stdout="YES", weight=0.5, is_hidden=True),
                ],
            ),
        ],
    )


def build_fallback_question_set_v2(allowed_languages: list[str]) -> ProgrammingQuestionSetV2:

    return ProgrammingQuestionSetV2(
        main_questions=[
            _sum_of_two_numbers(allowed_languages),
            _word_frequency_counter(allowed_languages),
            _balanced_brackets(allowed_languages),
            _first_duplicate(allowed_languages),
            _palindrome_check(allowed_languages),
        ],
        generated_by="fallback",
    )
