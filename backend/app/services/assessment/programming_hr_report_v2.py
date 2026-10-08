from backend.app.schemas.programming_question_v2 import ProgrammingQuestionSetV2
from backend.app.services.assessment.programming_scoring_v2 import AssessmentScoreResult


# ============================================================
# JOBPILOT AI — PROGRAMMING V2 HR REPORT (Programming Engine V2,
# AI generation task, Step 15)
#
# Assembles an AssessmentScoreResult (programming_scoring_v2.py)
# into the exact fields HR needs to understand technical
# performance. Built entirely from data already present on the
# scoring dataclasses — which are candidate-safe by construction
# (see programming_scoring_v2.py's module header) — so this
# report is, by the same construction, ALSO safe: it can never
# expose a hidden test's stdin/expected_stdout, because those
# values were never carried into TestCaseScoreResult in the first
# place. There is no separate "redact hidden fields" step here
# because there is nothing hidden left to redact by this point.
# ============================================================


def build_hr_report_v2(
    question_set: ProgrammingQuestionSetV2,
    score_result: AssessmentScoreResult,
) -> dict:

    percentage = (
        round(100.0 * score_result.total_earned / score_result.total_possible, 2)
        if score_result.total_possible
        else 0.0
    )

    questions_by_id = {q.question_id: q for q in question_set.main_questions}

    question_reports = []

    for question_result in score_result.question_results:

        question = questions_by_id.get(question_result.question_id)

        subpart_reports = []
        total_passed = 0
        total_failed = 0
        compile_errors = 0
        runtime_errors = 0
        timeouts = 0

        # HR needs the public/hidden split reported SEPARATELY (e.g.
        # "Hidden tests: 8/8 passed") without ever exposing which
        # specific test case is which — is_hidden is already a safe,
        # non-identifying boolean on every TestCaseScoreResult (see
        # programming_scoring_v2.py), so counting by it here adds no
        # new exposure.
        public_passed = 0
        public_total = 0
        hidden_passed = 0
        hidden_total = 0

        for subpart_result in question_result.subpart_results:

            test_case_reports = []

            for tc in subpart_result.test_case_results:

                if tc.passed:
                    total_passed += 1
                else:
                    total_failed += 1

                if tc.is_hidden:
                    hidden_total += 1
                    if tc.passed:
                        hidden_passed += 1
                else:
                    public_total += 1
                    if tc.passed:
                        public_passed += 1

                if tc.error_type == "compile_error":
                    compile_errors += 1
                elif tc.error_type == "runtime_error":
                    runtime_errors += 1
                elif tc.error_type == "timeout":
                    timeouts += 1

                test_case_reports.append({
                    "test_case_id": tc.test_case_id,
                    "is_hidden": tc.is_hidden,
                    "passed": tc.passed,
                    "weight": tc.weight,
                    "execution_time_ms": tc.execution_time_ms,
                    "error_type": tc.error_type,
                })

            subpart_reports.append({
                "subpart_id": subpart_result.subpart_id,
                "earned_fraction": subpart_result.earned_fraction,
                "platform_failure": subpart_result.platform_failure,
                "test_cases": test_case_reports,
            })

        question_reports.append({
            "question_id": question_result.question_id,
            "title": question.title if question else None,
            "scenario": question.scenario if question else None,
            "requirements": question.requirements if question else [],
            "skills_assessed": question.skills_assessed if question else [],
            "expected_complexity": question.expected_complexity if question else None,
            "notes": question.notes if question else None,
            "description": question.description if question else None,
            "constraints": question.constraints if question else None,
            "technology_assessed": question.technology if question else None,
            "earned_points": question_result.earned_points,
            "max_points": question_result.max_points,
            "language_used": question_result.language_used,
            "platform_failure": question_result.platform_failure,
            "passed_test_count": total_passed,
            "failed_test_count": total_failed,
            "public_test_summary": {"passed": public_passed, "total": public_total},
            "hidden_test_summary": {"passed": hidden_passed, "total": hidden_total},
            "compile_error_count": compile_errors,
            "runtime_error_count": runtime_errors,
            "timeout_count": timeouts,
            "subparts": subpart_reports,
        })

    languages_used = sorted({
        r.language_used for r in score_result.question_results if r.language_used
    })

    skill_breakdown: dict[str, dict[str, float]] = {}

    for question_result in score_result.question_results:

        question = questions_by_id.get(question_result.question_id)
        skill = (question.technology if question else None) or "General Problem Solving"

        bucket = skill_breakdown.setdefault(skill, {"earned": 0.0, "max": 0.0})
        bucket["earned"] += question_result.earned_points
        bucket["max"] += question_result.max_points

    return {
        "overall_score": score_result.total_earned,
        "overall_max": score_result.total_possible,
        "percentage": percentage,
        "passed": percentage >= 70.0,
        "languages_used": languages_used,
        "questions": question_reports,
        "skill_breakdown": [
            {"skill": skill, "earned": round(v["earned"], 2), "max": round(v["max"], 2)}
            for skill, v in skill_breakdown.items()
        ],
    }
