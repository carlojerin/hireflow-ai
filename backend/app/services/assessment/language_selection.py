from dataclasses import dataclass

from backend.app.schemas.programming_question_v2 import ProgrammingMainQuestionV2
from backend.app.services.assessment.language_capability_registry import (
    get_language_capability,
    normalize_language_name,
)


# ============================================================
# JOBPILOT AI — PROGRAMMING ENGINE V2 — CANDIDATE LANGUAGE
# SELECTION CONTRACT
#
# STANDALONE. Not called from any live route. The frontend's own
# language dropdown is NEVER trusted — this is the one function a
# future wiring task must call server-side, on every submission,
# before any code is executed. It never silently substitutes a
# language: a rejected selection returns a rejection, and it is
# the caller's job to surface that to the candidate, never to
# fall back to running the code as a different language.
# ============================================================


@dataclass(frozen=True)
class LanguageSelectionResult:

    accepted: bool
    canonical_language: str | None
    reason: str | None = None


def validate_language_selection(
    requested_language: str,
    question: ProgrammingMainQuestionV2,
) -> LanguageSelectionResult:
    """
    Server-side enforcement of a candidate's language choice for
    ONE main question. Every check below is independently
    sufficient to reject — this function never "tries its best" to
    salvage an invalid selection.
    """

    canonical_name = normalize_language_name(requested_language)

    if canonical_name is None:
        return LanguageSelectionResult(
            accepted=False,
            canonical_language=None,
            reason=f"'{requested_language}' is not a recognized language.",
        )

    allowed_canonical = {
        normalize_language_name(lang) for lang in question.allowed_languages
    }

    if canonical_name not in allowed_canonical:
        return LanguageSelectionResult(
            accepted=False,
            canonical_language=canonical_name,
            reason=(
                f"'{canonical_name}' is not permitted for this "
                f"question. Allowed languages: "
                f"{sorted(l for l in allowed_canonical if l)}."
            ),
        )

    capability = get_language_capability(canonical_name)

    if not capability.known:
        return LanguageSelectionResult(
            accepted=False,
            canonical_language=canonical_name,
            reason=f"'{canonical_name}' is not a known language to JobPilot.",
        )

    if not capability.execution_supported:
        return LanguageSelectionResult(
            accepted=False,
            canonical_language=canonical_name,
            reason=(
                f"JobPilot does not yet have a working execution "
                f"runner for '{canonical_name}' "
                f"(status: {capability.implementation_status})."
            ),
        )

    return LanguageSelectionResult(
        accepted=True,
        canonical_language=canonical_name,
        reason=None,
    )
