import random

from backend.app.services.sjt.question_bank import get_sjt_question_bank

SOURCE_FALLBACK = "fallback"  # curated bank — role-agnostic scenarios
SOURCE_AI = "ai"              # written for THIS job by the AI assessment planner's plan (see assessment_planner.py)

DEFAULT_TOTAL_QUESTIONS = 10


def build_practical_test(job, spec: dict, seed) -> dict:
    """
    A practical (work-sample) test for this job, from its plan's practical stage.

      environment "data"    -> a table + five questions whose answers are computed by code (practical_data_templates)
      environment "written" -> two written tasks for this job (AI-written, curated bank as the fallback) graded against a rubric

    Returns {"questions": [...], "payload": {...for the page...}, "source": "procedural" | "ai" | "fallback"}.
    """

    environment = spec.get("environment")

    if environment == "data":

        from backend.app.services.sjt.practical_data_templates import build_data_task

        task = build_data_task(spec.get("template") or "sales_report", seed)

        questions = [
            {
                "id": index + 1, "type": "data", "category": task["title"], "question": q["question"], "options": [],
                "answer_kind": q["answer_kind"], "unit": q["unit"], "hint": q["hint"],
                "correct_answer": q["correct_answer"], "tolerance": q["tolerance"],
            }
            for index, q in enumerate(task["questions"])
        ]

        return {
            "questions": questions, "source": "procedural",
            "payload": {
                "environment": "data", "title": spec.get("title") or task["title"], "intro": spec.get("intro") or "",
                "template": task["template"],
                "dataset": {"title": task["title"], "note": task["note"], "columns": task["columns"], "rows": task["rows"]},
                "minutes": 25,
            },
        }

    from backend.app.services.assessment.assessment_planner import WRITTEN_FOR_ROLEPLAY, practical_family

    family = spec.get("family") or practical_family(job)

    if environment == "lab":

        from backend.app.services.sjt.practical_lab_templates import build_lab_task

        task = build_lab_task(spec.get("lab") or "log_analysis", seed)

        questions = [
            {
                "id": index + 1, "type": "lab", "category": task["title"], "question": q["question"], "options": [],
                "answer_kind": q["answer_kind"], "unit": q["unit"], "hint": q["hint"],
                "correct_answer": q["correct_answer"], "tolerance": q["tolerance"],
            }
            for index, q in enumerate(task["questions"])
        ]

        return {
            "questions": questions, "source": "procedural",
            "payload": {
                "environment": "lab", "title": spec.get("title") or task["title"], "intro": spec.get("intro") or "", "lab": task["template"],
                "artifact": {"title": task["title"], "note": task["note"], "kind": task["artifact"]["kind"], "name": task["artifact"]["name"], "lines": task["artifact"]["lines"]},
                "minutes": 30,
            },
        }

    if environment == "design":

        from backend.app.services.sjt.practical_design import candidate_view_of, pick_brief

        brief = pick_brief(job, seed)

        return {
            "questions": [{"id": 1, "type": "design", "category": "Web design", "question": brief["title"], "options": [], "brief": brief}],
            "source": "procedural",
            "payload": {"environment": "design", "title": spec.get("title") or "Web Design Practical", "intro": spec.get("intro") or "",
                        "design": candidate_view_of(brief), "minutes": 40},
        }

    if environment == "roleplay":

        from backend.app.services.sjt.practical_roleplay import generate_roleplay_scenario, roleplay_enabled, voice_available

        if roleplay_enabled() and voice_available():

            scenario, source = generate_roleplay_scenario(job, family=family, focus=spec.get("focus"))

            return {
                "questions": [{"id": 1, "type": "roleplay", "category": "Call simulation", "question": scenario["title"], "options": []}],
                "source": source, "roleplay_scenario": scenario,
                "payload": {
                    "environment": "roleplay", "title": spec.get("title") or "Call Simulation", "intro": spec.get("intro") or "",
                    "brief": scenario["candidate_brief"], "caller": {"name": scenario["persona"]["name"], "role": scenario["persona"]["role"]},
                    "minutes": 25,
                },
            }

        # the live voice stack is not available here: a written task tests the same skill, so the candidate still gets a real practical test
        spec = {**spec, "environment": "written"}
        family = WRITTEN_FOR_ROLEPLAY.get(family, family)

    from backend.app.services.sjt.practical_written_tasks import generate_written_tasks

    tasks, source = generate_written_tasks(job, focus=spec.get("focus"), family=family)

    questions = [{**t, "options": []} for t in tasks]

    return {
        "questions": questions, "source": source,
        "payload": {"environment": "written", "title": spec.get("title") or "Practical Work Sample", "intro": spec.get("intro") or "", "minutes": 25},
    }


def build_sjt_questions(total_questions: int = DEFAULT_TOTAL_QUESTIONS, job=None) -> tuple[list[dict], str]:
    """
    The ONE place SJT questions are produced for the non-technical
    pipeline stage (sjt_tests.py), mirroring aptitude_question_
    service.py's build_aptitude_questions() role exactly.

    Deliberately bank-only for now, unlike aptitude's Groq-first
    3-tier approach — a curated, validated SJT scenario bank is
    what real SJT providers (SHL, TestGorilla) actually ship with;
    AI-generating fresh workplace scenarios per candidate is a real
    future enhancement (mirroring ai_question_generator.py's
    pattern) but a materially bigger lift than the aptitude case,
    since a bad SJT scenario (an ambiguous "best" answer) is a worse
    failure mode than a bad math question. Each candidate still gets
    a genuinely randomized SUBSET and ORDER from the bank, so two
    candidates for the same job essentially never see an identical
    test even without per-candidate generation.
    """

    # When the job's plan gives this stage its own areas (role-specific scenarios, or a knowledge test for the job), the AI writes
    # them for this job. Any failure falls back to the curated bank below, so the candidate always gets a test.
    if job is not None:
        try:
            from backend.app.services.assessment.assessment_planner import SLOT_STAGE3, get_job_plan, stage_for_slot
            get_job_plan(job)
            custom = stage_for_slot(job, SLOT_STAGE3)
            if custom:
                from backend.app.services.assessment.skills_question_generator import generate_skills_questions
                return generate_skills_questions(job, custom), SOURCE_AI
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger("jobpilot.sjt").warning("Planned assessment could not be generated (%s): using the standard scenario bank.", exc)

    bank = get_sjt_question_bank()
    count = min(total_questions, len(bank))

    selected = random.sample(bank, count)
    random.shuffle(selected)

    return selected, SOURCE_FALLBACK
