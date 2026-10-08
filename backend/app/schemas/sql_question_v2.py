from typing import Literal

from pydantic import BaseModel, Field, model_validator


# ============================================================
# JOBPILOT AI — SQL QUESTION V2 SCHEMA (Programming Engine V2,
# AI generation task)
#
# SQL is explicitly NOT shoehorned into ProgrammingQuestionSetV2
# (whose ExecutableTestCase is a stdin/expected_stdout contract —
# a category error for SQL, which compares RESULT ROWS against
# an isolated fixture, not a text diff). This is its own small,
# distinct schema, evaluated by sql_execution.py's real SQLite
# fixture engine — never routed through a LanguageRunner, and
# never through Python.
# ============================================================


class SQLQuestionV2(BaseModel):

    question_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=3000)

    # DDL + seed INSERT statements creating this question's
    # isolated fixture — executed fresh, in-memory, per
    # evaluation (see sql_execution.SQLFixture).
    setup_sql: str = Field(min_length=1, max_length=5000)

    # The visible table schema description shown to the candidate
    # (human-readable, e.g. "customers(id, name)") — deliberately
    # separate from setup_sql, which may include row data the
    # candidate should not be shown verbatim if it overlaps with
    # a hidden test's answer.
    schema_description: str = Field(min_length=1, max_length=1000)

    # The exact expected result, as a list of rows (each row a
    # list of scalar values) — the deterministic ground truth
    # sql_execution.run_sql_submission() compares against.
    expected_rows: list[list] = Field(min_length=0)

    order_sensitive: bool = Field(default=False)

    is_hidden: bool = Field(default=True)

    points: float = Field(gt=0, le=100)


class SQLQuestionSetV2(BaseModel):

    questions: list[SQLQuestionV2] = Field(min_length=1, max_length=10)

    generated_by: Literal["gemini", "fallback"] = "fallback"

    @model_validator(mode="after")
    def _points_sum_to_100(self) -> "SQLQuestionSetV2":

        total = sum(q.points for q in self.questions)

        if abs(total - 100.0) > 1e-6:
            raise ValueError(f"SQL question points must sum to 100, got {total!r}.")

        return self

    @model_validator(mode="after")
    def _question_ids_unique(self) -> "SQLQuestionSetV2":

        ids = [q.question_id for q in self.questions]

        if len(set(ids)) != len(ids):
            raise ValueError(f"Duplicate question_id values in {ids!r}.")

        return self
