from typing import Literal

from pydantic import BaseModel, Field


# ============================================================
# JOBPILOT AI — FRONTEND CODING QUESTION CONTRACT (Programming
# Engine V2, AI generation task, Step 11)
#
# A REAL SCHEMA for a future frontend-coding assessment type —
# not a fake execution engine. See frontend_execution.py's own
# module docstring for exactly why no execution engine exists yet
# (no headless-browser harness, no DOM-assertion runner, and this
# task confirmed no playwright/selenium/pyppeteer package is even
# installed in this venv). This schema exists so a future
# execution engine has a concrete, already-designed contract to
# implement against, and so today's generator can produce a
# STRUCTURALLY VALID frontend question definition while being
# explicit — via get_frontend_execution_capability() — that
# nothing can execute it yet.
#
# DOMAssertion is deliberately NOT an ExecutableTestCase: a
# frontend test asserts on rendered DOM state / computed styles /
# emitted events, never a single stdout string — forcing it into
# the stdin/stdout shape would be exactly the kind of category
# error Step 12 (SQL) explicitly warned against for a different
# capability.
# ============================================================

FrontendStack = Literal["html_css_js", "react", "vue", "angular", "typescript"]


class DOMAssertion(BaseModel):

    assertion_id: str = Field(min_length=1, max_length=50)

    description: str = Field(min_length=1, max_length=300)

    # What kind of check this is — deliberately a closed set
    # rather than free text, since a real engine's assertion
    # runner will need to dispatch on this.
    assertion_type: Literal[
        "element_exists", "element_text", "element_attribute",
        "computed_style", "event_emitted", "element_count",
    ]

    # A CSS selector or component path the assertion targets.
    target_selector: str = Field(min_length=1, max_length=300)

    expected_value: str = Field(min_length=0, max_length=1000)

    weight: float = Field(gt=0, le=1)

    is_hidden: bool = Field(default=True)


class FrontendCodingQuestion(BaseModel):

    question_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)

    stack: FrontendStack

    starter_files: dict[str, str] = Field(
        default_factory=dict,
        description="Filename -> starter file content, e.g. {'index.html': '...'}",
    )

    dom_assertions: list[DOMAssertion] = Field(min_length=1)

    points: float = Field(gt=0, le=100)
