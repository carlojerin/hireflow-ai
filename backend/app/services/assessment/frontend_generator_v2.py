from dataclasses import dataclass
from typing import Literal, Optional

from backend.app.schemas.assessment_plan import AssessmentPlan
from backend.app.schemas.frontend_question_v2 import FrontendCodingQuestion
from backend.app.services.assessment.frontend_execution import (
    get_frontend_execution_capability,
)


# ============================================================
# JOBPILOT AI — FRONTEND ASSESSMENT V2 (Programming Engine V2,
# AI generation task)
#
# Deliberately produces NO question set. frontend_execution.py's
# capability check is always False (no execution engine exists —
# see its own module header), so generating a FrontendCodingQuestion
# here would create an assessment with no way to ever score it —
# exactly the "fake/broken assessment" outcome this entire project
# has repeatedly refused to produce (see assessment_orchestrator.py's
# classify_generation_outcome() and this task's own Step 11
# instruction: "Do not fake frontend execution"). This function's
# only job is to report that status HONESTLY and IMMEDIATELY, the
# same way generate_programming_assessment_v2() reports
# "capability_unavailable" for an unsupported programming language.
# ============================================================

GenerationStatus = Literal["capability_unavailable", "not_applicable"]


@dataclass(frozen=True)
class FrontendAssessmentGenerationResult:

    status: GenerationStatus
    question: Optional[FrontendCodingQuestion]
    message: str


def _plan_requires_frontend(plan: AssessmentPlan) -> bool:

    return any(item.assessment_type == "frontend" for item in plan.assessment_types)


def generate_frontend_assessment_v2(plan: AssessmentPlan) -> FrontendAssessmentGenerationResult:

    if not _plan_requires_frontend(plan):

        return FrontendAssessmentGenerationResult(
            status="not_applicable",
            question=None,
            message="This role's assessment plan does not include a frontend assessment.",
        )

    capability = get_frontend_execution_capability()

    return FrontendAssessmentGenerationResult(
        status="capability_unavailable",
        question=None,
        message=(
            "This job's assessment plan calls for a frontend coding "
            f"assessment, which JobPilot cannot yet execute: {capability.reason} "
            "No question was generated, since one with no way to "
            "ever be scored would be a broken assessment."
        ),
    )
