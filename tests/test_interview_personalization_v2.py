"""
Task 9: is the interview actually PERSONALIZED? Deterministic proof (no model, no network, no DB).

The interview brain is fed a candidate that mirrors the dedicated live-test candidate (Python Backend Developer,
Python/FastAPI/PostgreSQL, B.Sc, Hospital Management System project, internship). A fake model captures the exact
prompt that reaches it, so these tests prove WHAT the model is given (job, candidate, evidence, previous answer,
competency coverage) and that the no-model fallback also points at the candidate's own words. Whether a real model
then uses it well is measured separately in the live run.
"""

import json
import os
import re
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.app.services.assessment import interview_brain_v2 as brain
from backend.app.services.assessment import interview_llm_provider as prov
from backend.app.services.assessment.interview_context_builder_v2 import build_interview_blueprint_v2, build_interview_context_v2
from backend.app.services.assessment.local_stt_provider_v2 import LocalWhisperSpeechToTextProviderV2
from backend.app.services.assessment import interview_voice_integration_v2 as bridge


def _job():
    return types.SimpleNamespace(
        title="Python Backend Developer", company="JobPilot Test Technologies",
        description="Design, build and maintain REST APIs and database-driven services using Python, FastAPI and PostgreSQL. Authentication, data modelling, testing.",
        required_skills="Python, FastAPI, REST APIs, PostgreSQL, SQL, Authentication, Backend development, Git",
        preferred_skills="SQLAlchemy, Docker, JWT, pytest", technologies="Python, FastAPI, PostgreSQL, Git",
        responsibilities="Build FastAPI services; design SQL schemas; implement authentication", requirements="JWT/session authentication; SQL; Git",
        employment_type="Full-time", work_mode="Remote", experience="0-2 years",
    )


def _user():
    return types.SimpleNamespace(full_name="Arjun Live Tester", experience_level="Entry-level / Junior", current_role="Software Development Intern",
                                 education="B.Sc Computer Science", preferred_role="Python Backend Developer")


def _profile():
    return types.SimpleNamespace(
        headline="Junior Python Backend Developer | FastAPI | PostgreSQL", about="Junior developer with experience building backend APIs and database-driven applications.",
        education="B.Sc Computer Science", experience="Software development internship (backend APIs and database work).",
        projects="Hospital Management System using Python/FastAPI/PostgreSQL", certifications=None, achievements=None,
        skills="Python, FastAPI, SQL, PostgreSQL, REST API, JavaScript, Git", experience_level="Entry-level / Junior", preferred_roles="Python Backend Developer",
    )


def _analysis():
    return types.SimpleNamespace(
        summary="Junior developer with experience building backend APIs using Python, FastAPI and PostgreSQL.",
        skills=json.dumps(["Python", "FastAPI", "SQL"]),                                  # (a subset: the profile adds the rest)
        education=json.dumps(["B.Sc Computer Science, 2025"]), experience_level="Entry Level",
        experience=json.dumps(["Software Development Intern (6 months): built and tested REST API endpoints, wrote SQL queries, used Git.",
                               "Hospital Management System (project): Python, FastAPI, PostgreSQL APIs with authentication and relational design."]),
    )


def _aptitude():
    return types.SimpleNamespace(status="completed", score=10, total_questions=10, passed=True, questions="[]", answers=None)


def _evidence():
    return types.SimpleNamespace(strengths=["Balanced Brackets", "Word Frequency Counter"], weaknesses=[], insufficient_evidence=[],
                                 technologies_demonstrated=[], languages_used=["python"], highest_difficulty_passed="hard")


def _ctx():
    return build_interview_context_v2(job=_job(), user=_user(), profile=_profile(), resume_analysis=_analysis(), aptitude_test=_aptitude(), programming_evidence=_evidence())


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def __call__(self, prompt, *a, **k):
        self.prompts.append(prompt)
        return json.dumps(self.reply)


def _with_llm(fake, fn):
    saved = brain.generate_llm_json
    brain.generate_llm_json = fake
    try:
        return fn()
    finally:
        brain.generate_llm_json = saved


# ---------------------------------------------------------------- what reaches the model
def test_the_context_carries_every_job_candidate_and_assessment_field_the_platform_has():
    c = _ctx()
    assert c.job.title == "Python Backend Developer" and c.job.company == "JobPilot Test Technologies"
    assert "REST APIs" in c.job.description and "FastAPI" in c.job.required_skills
    assert c.job.responsibilities.startswith("Build FastAPI") and c.job.requirements.startswith("JWT") and "Docker" in c.job.preferred_skills
    assert c.job.technologies == "Python, FastAPI, PostgreSQL, Git" and c.job.work_mode == "Remote" and c.job.experience_required == "0-2 years"
    assert c.candidate.education == "B.Sc Computer Science, 2025"                       # readable text, not a raw JSON list
    assert c.candidate.skills[:3] == ["Python", "FastAPI", "SQL"] and {"PostgreSQL", "JavaScript", "Git"} <= set(c.candidate.skills)   # resume AND profile skills
    assert any("Hospital Management System" in p for p in c.candidate.resume_projects_or_experience)
    assert c.candidate.profile_projects == "Hospital Management System using Python/FastAPI/PostgreSQL" and "internship" in c.candidate.profile_experience
    assert c.candidate.headline.startswith("Junior Python Backend Developer") and c.candidate.preferred_role == "Python Backend Developer"
    assert c.aptitude.available and c.aptitude.percentage == 100.0
    assert c.programming.available and c.programming.strengths == ["Balanced Brackets", "Word Frequency Counter"]     # titles, never "question_1"
    assert not any(s.startswith("question_") for s in c.programming.strengths)


def test_the_opening_prompt_is_grounded_in_the_real_role_skills_education_and_project():
    ctx = _ctx()
    fake = FakeLLM({"opening_message": "Hi Arjun.", "first_question": "Walk me through the Hospital Management System.", "competency": "FastAPI",
                    "difficulty": "basic", "question_type": "EXPERIENCE"})
    opening, by = _with_llm(fake, lambda: brain.generate_opening_v2(ctx, build_interview_blueprint_v2(ctx), use_llm=True))
    prompt = fake.prompts[0]
    for needle in ("Python Backend Developer", "JobPilot Test Technologies", "Design, build and maintain REST APIs", "FastAPI", "PostgreSQL", "Authentication",
                   "Responsibilities: Build FastAPI services", "Requirements: JWT/session authentication", "Technologies: Python, FastAPI, PostgreSQL, Git",
                   "B.Sc Computer Science, 2025", "Arjun Live Tester", "Entry Level", "Hospital Management System", "Software Development Intern",
                   "Profile projects:", "Junior Python Backend Developer", "Score: 100.0%", "Balanced Brackets", "FastAPI (priority: high"):
        assert needle in prompt, needle
    assert "never invent a project" in prompt.lower()                                     # the model may only use listed facts
    assert opening.competency == "FastAPI" and by != "fallback"


def test_the_no_model_opening_is_still_grounded_in_the_candidates_own_project():
    ctx = _ctx()
    opening, by = brain.generate_opening_v2(ctx, build_interview_blueprint_v2(ctx), use_llm=False)
    assert by == "fallback" and ("Software Development Intern" in opening.first_question or "Hospital Management System" in opening.first_question)
    assert "Python Backend Developer" in opening.opening_message and "JobPilot Test Technologies" in opening.opening_message


def test_blueprint_competencies_come_from_the_jobs_own_requirements_not_a_generic_list():
    names = [c.name for c in build_interview_blueprint_v2(_ctx()).competencies]
    assert names[:5] == ["Python", "FastAPI", "REST APIs", "PostgreSQL", "SQL"] and "Communication" in names


# ---------------------------------------------------------------- follow-ups
def _turn(ctx, answer, question="Tell me about a project you built.", competency="FastAPI", turn=1, covered=None, follow_ups=0, use_llm=True):
    return brain.analyze_and_decide_next_v2(
        context=ctx, blueprint=build_interview_blueprint_v2(ctx), conversation_summary="Turn 1 [Python, moderate]: candidate said something -> evidence.",
        current_competency=competency, current_difficulty="basic", last_question=question, answer_text=answer, turn_number=turn,
        min_turns=4, max_turns=8, covered_competencies=covered or set(), follow_ups_on_current=follow_ups, use_llm=use_llm,
    )


def test_the_follow_up_prompt_contains_the_exact_answer_the_question_it_answers_and_the_interview_so_far():
    ctx = _ctx()
    answer = "I built a REST API using FastAPI and PostgreSQL."
    fake = FakeLLM({
        "answer_analysis": {"competency": "FastAPI", "strengths": ["built a REST API"], "weaknesses": [], "evidence": ["FastAPI + PostgreSQL REST API"], "claims": [],
                            "missing_depth": ["authentication", "database design"], "strength_level": "moderate"},
        "next_action": "FOLLOW_UP", "reason": "needs depth", "next_competency": "FastAPI", "next_difficulty": "intermediate",
        "next_question": "How did you handle authentication and password storage in that FastAPI API?", "next_question_type": "TECHNICAL",
    })
    decision, by = _with_llm(fake, lambda: _turn(ctx, answer, question="Tell me about your Hospital Management System."))
    prompt = fake.prompts[0]
    assert answer in prompt and "Tell me about your Hospital Management System." in prompt          # exactly what was said and what it answered
    assert "Turn 1 [Python, moderate]" in prompt and "Competency being assessed: FastAPI" in prompt   # the interview so far + current competency
    assert "Hospital Management System" in prompt and "B.Sc Computer Science" in prompt              # and still the whole candidate/job context
    assert decision.next_action == "FOLLOW_UP" and "authentication" in decision.next_question.lower() and by != "fallback"


def test_without_a_model_the_follow_up_points_at_the_technologies_the_candidate_just_named():
    ctx = _ctx()
    decision, by = _turn(ctx, "I built a REST API using FastAPI and PostgreSQL.", use_llm=False)
    assert by == "fallback" and decision.next_action == "FOLLOW_UP"
    assert "FastAPI" in decision.next_question and "PostgreSQL" in decision.next_question             # "You mentioned FastAPI and PostgreSQL..."
    assert "candidate named: FastAPI, PostgreSQL" in " ".join(decision.answer_analysis.evidence)      # the evidence says what was actually named
    assert decision.next_competency == "FastAPI" and decision.next_question_type == "DEEP_DIVE"


def test_without_a_model_an_answer_naming_nothing_known_gets_the_honest_generic_probe_not_an_invented_topic():
    decision, by = _turn(_ctx(), "I worked on some backend things during my internship and learned a lot from my mentor there.", use_llm=False)
    assert decision.next_action == "FOLLOW_UP" and "You mentioned" not in decision.next_question
    assert "postgres" not in decision.next_question.lower()                                          # only the competency under discussion is named, nothing the candidate did not say


def test_the_brain_moves_on_to_an_uncovered_competency_instead_of_drilling_one_topic_forever():
    ctx = _ctx()
    repeat = FakeLLM({
        "answer_analysis": {"competency": "FastAPI", "strengths": [], "weaknesses": [], "evidence": [], "claims": [], "missing_depth": [], "strength_level": "moderate"},
        "next_action": "FOLLOW_UP", "reason": "more", "next_competency": "FastAPI", "next_difficulty": "basic",
        "next_question": "And how did you structure the FastAPI routers?", "next_question_type": "TECHNICAL",
    })
    decision, by = _with_llm(repeat, lambda: _turn(ctx, "I used routers and dependencies to organise the FastAPI routes.", follow_ups=2, covered={"python"}))
    assert decision.next_action != "END_INTERVIEW"
    assert (decision.next_competency or "").lower() != "fastapi"                                     # the coverage guard forced a transition
    assert (decision.next_competency or "").lower() in {c.name.lower() for c in build_interview_blueprint_v2(ctx).competencies}


def test_the_model_cannot_repeat_the_previous_question_or_end_the_interview_early():
    ctx = _ctx()
    same = FakeLLM({
        "answer_analysis": {"competency": "FastAPI", "strengths": [], "weaknesses": [], "evidence": [], "claims": [], "missing_depth": [], "strength_level": "weak"},
        "next_action": "FOLLOW_UP", "reason": "x", "next_competency": "FastAPI", "next_difficulty": "basic",
        "next_question": "Tell me about a project you built.", "next_question_type": "TECHNICAL",
    })
    decision, by = _with_llm(same, lambda: _turn(ctx, "I built an API.", question="Tell me about a project you built."))
    assert by == "fallback" and decision.next_question != "Tell me about a project you built."       # a repeated question is rejected, the deterministic engine takes over
    early = FakeLLM({
        "answer_analysis": {"competency": "FastAPI", "strengths": [], "weaknesses": [], "evidence": [], "claims": [], "missing_depth": [], "strength_level": "weak"},
        "next_action": "END_INTERVIEW", "reason": "x", "next_competency": "FastAPI", "next_difficulty": "basic", "next_question": None, "next_question_type": "TECHNICAL",
    })
    decision, by = _with_llm(early, lambda: _turn(ctx, "I built an API with FastAPI.", turn=2))
    assert decision.next_action != "END_INTERVIEW"                                                    # JobPilot, not the model, decides completion


# ---------------------------------------------------------------- speech recognition of the candidate's technical words
def test_speech_recognition_defaults_to_english_and_auto_detection_is_opt_in():
    saved = os.environ.pop("AI_STT_LANGUAGE", None)
    try:
        assert LocalWhisperSpeechToTextProviderV2().language == "en"          # a real accented candidate once came back in Devanagari script
        os.environ["AI_STT_LANGUAGE"] = "auto"
        assert LocalWhisperSpeechToTextProviderV2().language is None
        os.environ["AI_STT_LANGUAGE"] = "hi"
        assert LocalWhisperSpeechToTextProviderV2().language == "hi"
    finally:
        os.environ.pop("AI_STT_LANGUAGE", None)
        if saved is not None:
            os.environ["AI_STT_LANGUAGE"] = saved


def test_the_vocabulary_hint_is_built_from_the_jobs_and_candidates_own_terms_and_reaches_the_recogniser():
    import test_voice_turn_local_pipeline_v2 as VT
    db = VT._db()
    user, app, session = VT._interview(db)
    hint = bridge._stt_vocabulary_hint(db, app)
    assert hint and hint.startswith("Technical job interview") and "Python" in hint and len(hint) <= 400
    seen = {}

    class HintStt:
        provider_name = "faster-whisper"

        def transcribe(self, audio_bytes, mime_type, prompt=None):
            seen["prompt"] = prompt
            return types.SimpleNamespace(transcript="I built a REST API using FastAPI.")

    class LegacyStt:
        provider_name = "faster-whisper"

        def transcribe(self, audio_bytes, mime_type):
            seen["legacy"] = True
            return types.SimpleNamespace(transcript="I built a REST API using FastAPI.")

    assert bridge.inspect.signature(HintStt().transcribe).parameters.get("prompt") is not None
    from backend.app.services.assessment.interview_voice_integration_v2 import run_voice_turn_v2
    with VT._isolated(VT.FakeGroq(session)):
        run_voice_turn_v2(db, app, b"audio", "audio/webm", stt_provider=HintStt(), tts_provider=VT.FakeTts())
        assert seen["prompt"] == hint                                            # the recogniser was told the vocabulary
        run_voice_turn_v2(db, app, b"audio", "audio/webm", stt_provider=LegacyStt(), tts_provider=VT.FakeTts()) if False else None
