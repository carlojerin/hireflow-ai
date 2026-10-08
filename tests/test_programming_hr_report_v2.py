import json

from backend.app.services.assessment.programming_fallback_bank_v2 import (
    build_fallback_question_set_v2,
)
from backend.app.services.assessment.programming_hr_report_v2 import build_hr_report_v2
from backend.app.services.assessment.programming_submission_v2 import (
    CandidateSubmission,
    submit_question,
)
from backend.app.services.assessment.programming_scoring_v2 import score_assessment

_SOLUTIONS = {
    1: "a, b = map(int, input().split())\nprint(a + b)",
    2: "words = input().split()\ncounts = {}\nfor w in words:\n    counts[w] = counts.get(w, 0) + 1\nbest = words[0]\nfor w in words:\n    if counts[w] > counts[best]:\n        best = w\nprint(best)",
    3: "s = input()\nstack = []\npairs = {')':'(', ']':'[', '}':'{'}\nok = True\nfor c in s:\n    if c in '([{':\n        stack.append(c)\n    elif c in ')]}':\n        if not stack or stack.pop() != pairs[c]:\n            ok = False\n            break\nif stack:\n    ok = False\nprint('YES' if ok else 'NO')",
    4: "nums = list(map(int, input().split()))\nseen = set()\nresult = -1\nfor n in nums:\n    if n in seen:\n        result = n\n        break\n    seen.add(n)\nprint(result)",
    5: "s = input()\nprint('YES' if s == s[::-1] else 'NO')",
}


def _full_assessment_result():
    qs = build_fallback_question_set_v2(["python"])
    question_results = [
        submit_question(qs, CandidateSubmission(question_id=q.question_id, language="python", source_code=_SOLUTIONS[q.question_id]))
        for q in qs.main_questions
    ]
    return qs, score_assessment(qs, question_results)


# X: HR result structure
def test_hr_report_contains_required_fields():
    qs, score_result = _full_assessment_result()
    report = build_hr_report_v2(qs, score_result)

    assert report["overall_score"] == 100.0
    assert report["overall_max"] == 100.0
    assert report["percentage"] == 100.0
    assert report["passed"] is True
    assert report["languages_used"] == ["python"]
    assert len(report["questions"]) == 5

    for q in report["questions"]:
        assert "earned_points" in q
        assert "max_points" in q
        assert "language_used" in q
        assert "passed_test_count" in q
        assert "failed_test_count" in q
        assert "compile_error_count" in q
        assert "runtime_error_count" in q
        assert "timeout_count" in q

    assert len(report["skill_breakdown"]) >= 1


# Hidden test data must never leak into the HR report either --
# structural guarantee: the report's own key set never includes
# any field that could carry hidden stdin/expected stdout.
def test_hr_report_never_contains_hidden_test_case_keys():
    qs, score_result = _full_assessment_result()
    report = build_hr_report_v2(qs, score_result)
    serialized = json.dumps(report)
    assert '"stdin"' not in serialized
    assert '"expected_stdout"' not in serialized
