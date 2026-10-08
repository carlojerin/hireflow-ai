import logging
import random

from backend.app.services.aptitude import question_pool
from backend.app.services.aptitude.ai_question_generator import (
    generate_ai_aptitude_questions,
    plan_for_candidate,
)
from backend.app.services.aptitude.parametric_bank import generate_parametric_questions

logger = logging.getLogger("jobpilot.aptitude")

# question_source values stored on AptitudeTest
SOURCE_AI = "ai"                  # generated for this candidate by Groq just now
SOURCE_POOL = "pool"              # drawn from questions Groq generated earlier (Groq was unavailable)
SOURCE_PARAMETRIC = "parametric"  # built from random numbers with computed answers (no AI available at all)


def build_aptitude_questions(job, user, profile, resume_analysis, blueprint=None) -> tuple[list[dict], str]:
    """
    The ONE place aptitude questions are produced, for both the HR "Send Assessment" and the
    candidate's own start after the self introduction.

        1. Groq (openai/gpt-oss-120b), a fresh set for this candidate
        2. the pool of earlier Groq questions (least used first)
        3. parametric questions (random numbers, computed answers)

    Every step gives a different set per candidate. Returns (questions, question_source).
    There is no fallback to any other AI provider, and never the old identical fixed bank.
    """

    band, plan = plan_for_candidate(user, profile, resume_analysis, blueprint, job=job)

    # The AI assessment planner may have decided this job needs a different second assessment than general aptitude
    # (for example Language & Grammar for a copywriter). Its content is written for the job; if that cannot be produced, the
    # standard aptitude path below still gives the candidate a test.
    try:
        from backend.app.services.assessment.assessment_planner import SLOT_APTITUDE, get_job_plan, stage_for_slot
        get_job_plan(job)                                              # first time for this job: plan it (AI, else rules) and store it
        custom = stage_for_slot(job, SLOT_APTITUDE)
        if custom:
            from backend.app.services.assessment.skills_question_generator import generate_skills_questions
            return generate_skills_questions(job, custom, user=user, profile=profile, resume_analysis=resume_analysis), SOURCE_AI
    except Exception as exc:  # noqa: BLE001
        logger.warning("Planned assessment for job %s could not be generated (%s): using the standard aptitude test.", getattr(job, "id", None), exc)

    try:

        questions = generate_ai_aptitude_questions(job, user, profile, resume_analysis, blueprint=blueprint)

        question_pool.add(band, questions)

        unverified_count = sum(1 for item in questions if not item.get("_verified"))

        if unverified_count:
            logger.warning(
                "%s/%s aptitude question(s) shipped without an independent answer-key "
                "check (verification call unavailable) for job_id=%s user_id=%s.",
                unverified_count, len(questions),
                getattr(job, "id", None), getattr(user, "id", None),
            )

        for item in questions:
            item.pop("_verified", None)

        return questions, SOURCE_AI

    except Exception as exc:

        logger.warning("Groq aptitude generation unavailable (%s); trying the question pool.", exc)

    drawn = question_pool.draw(band, plan)

    if drawn:
        logger.info("Aptitude questions served from the pool (band=%s).", band)
        return drawn, SOURCE_POOL

    logger.info("Aptitude questions built by the parametric generator (band=%s).", band)

    return generate_parametric_questions(band, plan, random.Random()), SOURCE_PARAMETRIC
