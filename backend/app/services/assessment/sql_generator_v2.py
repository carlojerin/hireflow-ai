import json
from dataclasses import dataclass
from typing import Literal, Optional

from backend.app.models.job import Job
from backend.app.schemas.assessment_blueprint import AssessmentBlueprint
from backend.app.schemas.assessment_plan import AssessmentPlan
from backend.app.schemas.sql_question_v2 import SQLQuestionSetV2, SQLQuestionV2
from backend.app.services.assessment.gemini_client import (
    GeminiUnavailableError,
    generate_json_content,
)
from backend.app.services.assessment.programming_question_validator_v2 import (
    validate_sql_question_set_v2,
)


# ============================================================
# JOBPILOT AI — SQL ASSESSMENT V2 GENERATOR (Programming Engine
# V2, AI generation task)
#
# STANDALONE. SQL kept as its OWN distinct generation path (see
# sql_question_v2.py's module header) — this never produces a
# ProgrammingQuestionSetV2 and never calls a LanguageRunner. The
# fallback below is hand-verified against sql_execution.py's real
# in-memory SQLite engine (see this task's tests), exactly like
# programming_fallback_bank_v2.py is verified against real
# language runners.
# ============================================================

GenerationStatus = Literal["generated", "not_applicable"]


@dataclass(frozen=True)
class SQLAssessmentGenerationResult:

    status: GenerationStatus
    question_set: Optional[SQLQuestionSetV2]
    message: str
    generated_by: Optional[Literal["gemini", "fallback"]] = None


def _plan_requires_sql(plan: AssessmentPlan) -> bool:

    return any(item.assessment_type == "sql" for item in plan.assessment_types)


def _build_gemini_prompt(job: Job, blueprint: AssessmentBlueprint, plan: AssessmentPlan) -> str:

    return f"""
Generate a SQL assessment as a JSON object with EXACTLY this shape
(no extra text, no markdown fences):

{{
  "questions": [
    {{
      "question_id": 1,
      "title": "...",
      "description": "...",
      "setup_sql": "CREATE TABLE ...; INSERT INTO ... VALUES ...;",
      "schema_description": "table_name(col1, col2, ...)",
      "expected_rows": [["value1"], ["value2"]],
      "order_sensitive": false,
      "is_hidden": true,
      "points": 50.0
    }}
  ],
  "generated_by": "gemini"
}}

RULES (validated server-side, enforced regardless of what you return):
- 2 to 10 questions, "points" summing to EXACTLY 100.
- "setup_sql" must be plain SQLite DDL/DML only (CREATE TABLE /
  INSERT INTO) — never ATTACH, PRAGMA, or DETACH.
- "expected_rows" must be the ACTUAL correct result of running
  the intended solution query against setup_sql's fixture — you
  are defining ground truth for automated grading.
- Never include a DROP/DELETE/UPDATE statement anywhere.
- question_id values unique, starting at 1.

JOB CONTEXT
Title: {job.title}
Required skills: {getattr(job, "required_skills", "") or "(not specified)"}

CANDIDATE CONTEXT
Experience level: {blueprint.candidate_experience_level}

ASSESSMENT PLAN
Role type: {plan.role_type}
""".strip()


def _fallback_question_set() -> SQLQuestionSetV2:
    """
    Hand-verified against sql_execution.py's real SQLite engine —
    see tests/test_sql_generator_v2.py, which re-runs these exact
    setup_sql/expected_rows pairs through run_sql_submission().
    """

    customers_fixture = (
        "CREATE TABLE customers (id INTEGER, name TEXT);"
        "CREATE TABLE transactions (id INTEGER, customer_id INTEGER, amount INTEGER);"
        "INSERT INTO customers VALUES (1, 'Alice'), (2, 'Bob'), (3, 'Carol');"
        "INSERT INTO transactions VALUES "
        "(1, 1, 100), (2, 1, 50), (3, 2, 30), (4, 3, 200), (5, 3, 10);"
    )

    return SQLQuestionSetV2(
        questions=[
            SQLQuestionV2(
                question_id=1,
                title="Total Spend Per Customer",
                description=(
                    "Write a query that returns each customer's name "
                    "and their total transaction amount, ordered by "
                    "name."
                ),
                setup_sql=customers_fixture,
                schema_description="customers(id, name); transactions(id, customer_id, amount)",
                expected_rows=[["Alice", 150], ["Bob", 30], ["Carol", 210]],
                order_sensitive=True,
                is_hidden=False,
                points=50.0,
            ),
            SQLQuestionV2(
                question_id=2,
                title="Highest-Spending Customer",
                description=(
                    "Write a query that returns the name of the "
                    "customer with the highest total transaction "
                    "amount."
                ),
                setup_sql=customers_fixture,
                schema_description="customers(id, name); transactions(id, customer_id, amount)",
                expected_rows=[["Carol"]],
                order_sensitive=False,
                is_hidden=True,
                points=50.0,
            ),
        ],
        generated_by="fallback",
    )


def generate_sql_assessment_v2(
    job: Job,
    blueprint: AssessmentBlueprint,
    plan: AssessmentPlan,
    use_gemini: bool = True,
) -> SQLAssessmentGenerationResult:

    if not _plan_requires_sql(plan):

        return SQLAssessmentGenerationResult(
            status="not_applicable",
            question_set=None,
            message="This role's assessment plan does not include a SQL assessment.",
        )

    if use_gemini:

        try:

            prompt = _build_gemini_prompt(job, blueprint, plan)
            raw_json_text = generate_json_content(prompt)
            raw = json.loads(raw_json_text)

            validation = validate_sql_question_set_v2(raw)

            if validation.valid and validation.question_set is not None:

                return SQLAssessmentGenerationResult(
                    status="generated",
                    question_set=validation.question_set,
                    message="Generated via Gemini and passed deterministic validation.",
                    generated_by="gemini",
                )

        except (GeminiUnavailableError, json.JSONDecodeError, ValueError) as exc:

            _ = exc

    return SQLAssessmentGenerationResult(
        status="generated",
        question_set=_fallback_question_set(),
        message="Generated via the deterministic fallback bank.",
        generated_by="fallback",
    )
