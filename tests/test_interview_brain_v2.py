"""
Tests for the Task 5H adaptive interview brain
(interview_brain_v2.py). Covers:
  C. Question generation (opening) -- valid JSON, fallback
  D. Answer analysis -- fallback classification
  E. Adaptive next action -- follow-up / move-on / end
  F. Conversation memory -- no duplicate/repeated questions,
     compact summary carries forward real prior evidence

Gemini-backed paths are isolated behind monkeypatched
generate_llm_json, exactly like this project's existing
generator tests (test_programming_generator_v2.py) -- the
regression suite never depends on a live Gemini call succeeding
except in the one explicitly-marked real-Gemini test.
"""

import json

from backend.app.schemas.interview_blueprint_v2 import (
    InterviewBlueprintV2,
    InterviewCompetencyV2,
)
from backend.app.schemas.interview_context_v2 import (
    AptitudeEvidenceV2,
    CandidateContextV2,
    InterviewContextV2,
    JobContextV2,
    ProgrammingEvidenceSummaryV2,
)
from backend.app.schemas.interview_turn_v2 import AnswerAnalysisV2
from backend.app.services.assessment import interview_brain_v2 as brain


def _context():
    return InterviewContextV2(
        job=JobContextV2(title="Backend Developer", company="Acme", required_skills="Java, Debugging"),
        candidate=CandidateContextV2(full_name="Jane Doe", skills=["Java"]),
        aptitude=AptitudeEvidenceV2(),
        programming=ProgrammingEvidenceSummaryV2(),
    )


def _blueprint():
    return InterviewBlueprintV2(competencies=[
        InterviewCompetencyV2(name="Java", priority="high", evidence_status="UNKNOWN"),
        InterviewCompetencyV2(name="Debugging", priority="high", evidence_status="NEEDS_VERIFICATION"),
        InterviewCompetencyV2(name="Communication", priority="medium", evidence_status="UNKNOWN"),
    ])


# --- opening: fallback path (no Gemini) ---------------------------------

def test_fallback_opening_uses_real_resume_detail_when_available():

    context = _context()
    context.candidate.resume_projects_or_experience = ["Built a payments API in Java."]

    opening, generated_by = brain.generate_opening_v2(context, _blueprint(), use_llm=False)

    assert generated_by == "fallback"
    assert "payments API in Java" in opening.first_question
    assert opening.competency in {c.name for c in _blueprint().competencies}


def test_fallback_opening_never_invents_a_project_when_none_exists():

    context = _context()  # no resume_projects_or_experience

    opening, generated_by = brain.generate_opening_v2(context, _blueprint(), use_llm=False)

    assert generated_by == "fallback"
    assert "Backend Developer" in opening.first_question or context.job.title in opening.first_question


# --- opening: Gemini path, validated + malformed fallback --------------

def test_opening_falls_back_when_gemini_returns_malformed_json(monkeypatch):

    def _bad(*a, **k):
        return "not json at all"

    monkeypatch.setattr(brain, "generate_llm_json", _bad)

    opening, generated_by = brain.generate_opening_v2(_context(), _blueprint(), use_llm=True)

    assert generated_by == "fallback"
    assert opening.first_question


def test_opening_falls_back_when_gemini_names_unknown_competency(monkeypatch):

    def _bad_competency(*a, **k):
        return json.dumps({
            "opening_message": "Hi there.",
            "first_question": "Tell me about yourself.",
            "competency": "Quantum Computing",  # not in blueprint
            "difficulty": "basic",
            "question_type": "INTRODUCTION",
        })

    monkeypatch.setattr(brain, "generate_llm_json", _bad_competency)

    opening, generated_by = brain.generate_opening_v2(_context(), _blueprint(), use_llm=True)

    assert generated_by == "fallback"


def test_opening_accepts_valid_gemini_response(monkeypatch):

    def _good(*a, **k):
        return json.dumps({
            "opening_message": "Hi, thanks for joining.",
            "first_question": "Tell me about a Java project you worked on.",
            "competency": "Java",
            "difficulty": "basic",
            "question_type": "INTRODUCTION",
        })

    monkeypatch.setattr(brain, "generate_llm_json", _good)

    opening, generated_by = brain.generate_opening_v2(_context(), _blueprint(), use_llm=True)

    assert generated_by == brain._llm_label()          # whichever provider is configured
    assert opening.competency == "Java"


# --- answer analysis: fallback classification (no invented confidence) --

def test_fallback_classifies_short_answer_as_none_not_weak_or_strong():

    level, evidence, missing = brain._classify_answer_fallback("ok")

    assert level == "none"
    assert evidence == []
    assert "too short" in missing[0]


def test_fallback_classifies_real_answer_as_moderate_never_strong():
    """
    The fallback path must never CLAIM strong evidence -- it has no
    real basis to judge quality, only presence of content.
    """

    level, evidence, missing = brain._classify_answer_fallback(
        "I used Redis to cache frequently-read data because the API "
        "was reading the same rows repeatedly and it reduced load."
    )

    assert level == "moderate"
    assert evidence
    assert missing == []


# --- adaptive next action: fallback decision engine ----------------------

def test_fallback_follow_up_on_strong_first_answer_same_competency():

    decision, generated_by = brain.analyze_and_decide_next_v2(
        context=_context(), blueprint=_blueprint(), conversation_summary="",
        current_competency="Java", current_difficulty="basic",
        last_question="Tell me about your Java experience.",
        answer_text="I built a payments processing service using Spring Boot and JPA.",
        turn_number=1, min_turns=4, max_turns=8,
        covered_competencies=set(), follow_ups_on_current=0,
        use_llm=False,
    )

    assert generated_by == "fallback"
    assert decision.next_action == "FOLLOW_UP"
    assert decision.next_competency == "Java"
    assert decision.next_question


def test_fallback_moves_on_after_a_follow_up_already_happened():

    decision, _ = brain.analyze_and_decide_next_v2(
        context=_context(), blueprint=_blueprint(), conversation_summary="turn 1...",
        current_competency="Java", current_difficulty="intermediate",
        last_question="How did you handle token expiry?",
        answer_text="We used refresh tokens with a sliding expiration window.",
        turn_number=2, min_turns=4, max_turns=8,
        covered_competencies=set(), follow_ups_on_current=1,
        use_llm=False,
    )

    assert decision.next_action == "MOVE_ON"
    assert decision.next_competency != "Java"


def test_fallback_ends_interview_at_max_turns():

    decision, _ = brain.analyze_and_decide_next_v2(
        context=_context(), blueprint=_blueprint(), conversation_summary="...",
        current_competency="Communication", current_difficulty="basic",
        last_question="Describe a time you resolved a disagreement.",
        answer_text="I listened to both sides and proposed a compromise.",
        turn_number=8, min_turns=4, max_turns=8,
        covered_competencies={"java", "debugging"}, follow_ups_on_current=0,
        use_llm=False,
    )

    assert decision.next_action == "END_INTERVIEW"
    assert decision.next_question is None


def test_fallback_ends_when_all_competencies_covered():

    decision, _ = brain.analyze_and_decide_next_v2(
        context=_context(), blueprint=_blueprint(), conversation_summary="...",
        current_competency="Communication", current_difficulty="basic",
        last_question="Describe a time you resolved a disagreement.",
        answer_text="I listened to both sides and proposed a compromise.",
        turn_number=5, min_turns=4, max_turns=8,
        covered_competencies={"java", "debugging"}, follow_ups_on_current=0,
        use_llm=False,
    )

    assert decision.next_action == "END_INTERVIEW"


# --- Gemini turn decision: validated + malformed fallback ---------------

def test_turn_decision_falls_back_on_malformed_gemini_output(monkeypatch):

    monkeypatch.setattr(brain, "generate_llm_json", lambda *a, **k: "{broken")

    decision, generated_by = brain.analyze_and_decide_next_v2(
        context=_context(), blueprint=_blueprint(), conversation_summary="",
        current_competency="Java", current_difficulty="basic",
        last_question="Tell me about Java.", answer_text="I use it daily.",
        turn_number=1, min_turns=4, max_turns=8,
        covered_competencies=set(), follow_ups_on_current=0,
        use_llm=True,
    )

    assert generated_by == "fallback"


def test_turn_decision_falls_back_when_question_missing_for_non_end_action(monkeypatch):

    def _missing_question(*a, **k):
        return json.dumps({
            "answer_analysis": {
                "competency": "Java", "strengths": [], "weaknesses": [], "evidence": [],
                "claims": [], "missing_depth": [], "strength_level": "moderate",
            },
            "next_action": "FOLLOW_UP",
            "next_competency": "Java",
            "next_difficulty": "intermediate",
            "next_question": None,  # invalid: FOLLOW_UP requires a question
        })

    monkeypatch.setattr(brain, "generate_llm_json", _missing_question)

    decision, generated_by = brain.analyze_and_decide_next_v2(
        context=_context(), blueprint=_blueprint(), conversation_summary="",
        current_competency="Java", current_difficulty="basic",
        last_question="Tell me about Java.", answer_text="I use it daily.",
        turn_number=1, min_turns=4, max_turns=8,
        covered_competencies=set(), follow_ups_on_current=0,
        use_llm=True,
    )

    assert generated_by == "fallback"


def test_turn_decision_accepts_valid_gemini_response(monkeypatch):

    def _good(*a, **k):
        return json.dumps({
            "answer_analysis": {
                "competency": "Java",
                "strengths": ["understands connection pooling"],
                "weaknesses": [],
                "evidence": ["mentioned HikariCP explicitly"],
                "claims": ["used HikariCP in production"],
                "missing_depth": [],
                "strength_level": "strong",
            },
            "next_action": "DEEPER_PROBE",
            "reason": "candidate showed strong understanding",
            "next_competency": "Java",
            "next_difficulty": "advanced",
            "next_question": "How did you tune the pool size for production load?",
            "next_question_type": "DEEP_DIVE",
        })

    monkeypatch.setattr(brain, "generate_llm_json", _good)

    decision, generated_by = brain.analyze_and_decide_next_v2(
        context=_context(), blueprint=_blueprint(), conversation_summary="",
        current_competency="Java", current_difficulty="intermediate",
        last_question="How do you manage DB connections?",
        answer_text="I used HikariCP in production and tuned the pool size after load testing showed connection wait times spiking under peak traffic.",
        turn_number=2, min_turns=4, max_turns=8,
        covered_competencies=set(), follow_ups_on_current=0,
        use_llm=True,
    )

    assert generated_by == brain._llm_label()          # whichever provider is configured
    assert decision.next_action == "DEEPER_PROBE"
    assert decision.answer_analysis.strength_level == "strong"


# --- conversation memory -------------------------------------------------

def test_conversation_summary_includes_real_evidence_not_full_transcript():

    from backend.app.schemas.interview_turn_v2 import AnswerAnalysisV2

    analysis = AnswerAnalysisV2(
        competency="Java", evidence=["used HikariCP for pooling"], strength_level="strong",
    )

    summary = brain.build_conversation_summary(
        "", 1, "How do you manage DB connections?",
        "I used HikariCP with a tuned pool size for production load.",
        analysis,
    )

    assert "HikariCP" in summary
    assert "Turn 1" in summary
    assert "Java" in summary


def test_conversation_summary_caps_length_to_recent_entries():

    from backend.app.schemas.interview_turn_v2 import AnswerAnalysisV2

    summary = ""

    for i in range(1, 20):

        analysis = AnswerAnalysisV2(competency=f"Skill{i}", evidence=[f"evidence {i}"], strength_level="moderate")
        summary = brain.build_conversation_summary(summary, i, f"Q{i}", f"A{i}", analysis)

    lines = [ln for ln in summary.split("\n") if ln.strip()]

    assert len(lines) <= brain._MAX_SUMMARY_ENTRIES
    # the OLDEST entries are dropped, not the newest
    assert "Turn 19" in summary
    assert "Turn 1 " not in summary


# --- probe ladder ---------------------------------------------------------
# Real bug from application 54's transcript: the interviewer asked for an
# "example" of backend work on turn 4, then re-asked for an "example" again in
# reworded form on turn 5 -- a genuine repeated question that plain
# text-similarity (_too_similar) could not catch (real duplicate pairs scored
# 0.34-0.58, far below any usable threshold). Enforced structurally instead:
# reusing a probe-ladder rung already targeted for the SAME competency is
# rejected, forcing either a new rung or a move to an uncovered competency.

def _reworded_example_response(*a, **k):
    return json.dumps({
        "answer_analysis": {
            "competency": "Java", "strengths": [], "weaknesses": [], "evidence": [],
            "claims": [], "missing_depth": [], "strength_level": "moderate",
        },
        "next_action": "FOLLOW_UP",
        "next_competency": "Java",
        "next_difficulty": "intermediate",
        "next_question": "Could you walk me through a specific instance where you used Java in a project?",
        "next_question_type": "DEEP_DIVE",
        "next_question_focus": "example",
    })


def test_turn_decision_falls_back_when_probe_rung_already_used_for_competency(monkeypatch):

    monkeypatch.setattr(brain, "generate_llm_json", _reworded_example_response)

    decision, generated_by = brain.analyze_and_decide_next_v2(
        context=_context(), blueprint=_blueprint(), conversation_summary="",
        current_competency="Java", current_difficulty="intermediate",
        last_question="Can you give me an example of Java code you wrote?",
        answer_text="I built a REST API with Spring Boot for order processing.",
        turn_number=2, min_turns=4, max_turns=8,
        covered_competencies=set(), follow_ups_on_current=1,
        use_llm=True,
        used_probe_rungs=frozenset({"example"}),
    )

    # both attempts (initial + one constrained retry) return the same reused
    # "example" rung, so validation rejects it every time and JobPilot falls
    # back to the deterministic engine rather than showing a repeated question.
    assert generated_by == "fallback"


def test_turn_decision_accepts_a_new_unused_probe_rung_for_same_competency(monkeypatch):

    def _next_rung(*a, **k):
        return json.dumps({
            "answer_analysis": {
                "competency": "Java", "strengths": ["gave a concrete example"], "weaknesses": [],
                "evidence": ["built a REST API with Spring Boot"], "claims": [], "missing_depth": [],
                "strength_level": "moderate",
            },
            "next_action": "DEEPER_PROBE",
            "next_competency": "Java",
            "next_difficulty": "intermediate",
            "next_question": "What was your specific role in designing that API, and why did you choose that approach?",
            "next_question_type": "DEEP_DIVE",
            "next_question_focus": "role_or_decision",
        })

    monkeypatch.setattr(brain, "generate_llm_json", _next_rung)

    decision, generated_by = brain.analyze_and_decide_next_v2(
        context=_context(), blueprint=_blueprint(), conversation_summary="",
        current_competency="Java", current_difficulty="intermediate",
        last_question="Can you give me an example of Java code you wrote?",
        answer_text="I built a REST API with Spring Boot for order processing.",
        turn_number=2, min_turns=4, max_turns=8,
        covered_competencies=set(), follow_ups_on_current=1,
        use_llm=True,
        used_probe_rungs=frozenset({"example"}),
    )

    assert generated_by == brain._llm_label()
    assert decision.next_question_focus == "role_or_decision"
    assert decision.next_action == "DEEPER_PROBE"


def test_turn_decision_allows_reusing_clarification_rung(monkeypatch):
    """"clarification" is explicitly exempt: a genuinely new unclear point can legitimately need it more than once."""

    def _clarify_again(*a, **k):
        return json.dumps({
            "answer_analysis": {
                "competency": "Java", "strengths": [], "weaknesses": ["answer was vague"], "evidence": [],
                "claims": [], "missing_depth": ["no concrete detail"], "strength_level": "weak",
            },
            "next_action": "CLARIFICATION",
            "next_competency": "Java",
            "next_difficulty": "intermediate",
            "next_question": "When you say you 'used Spring', which specific module or annotation did you rely on?",
            "next_question_type": "CLARIFICATION",
            "next_question_focus": "clarification",
        })

    monkeypatch.setattr(brain, "generate_llm_json", _clarify_again)

    decision, generated_by = brain.analyze_and_decide_next_v2(
        context=_context(), blueprint=_blueprint(), conversation_summary="",
        current_competency="Java", current_difficulty="intermediate",
        last_question="Can you tell me more about how you used Spring?",
        answer_text="I used Spring for the backend, it was fine.",
        turn_number=3, min_turns=4, max_turns=8,
        covered_competencies=set(), follow_ups_on_current=1,
        use_llm=True,
        used_probe_rungs=frozenset({"example", "clarification"}),
    )

    assert generated_by == brain._llm_label()
    assert decision.next_question_focus == "clarification"


# --- representative code snippet (grounding follow-ups in the candidate's own submitted code) --------------------

def _context_with_snippet(language="java", competency_hint="Java"):
    return InterviewContextV2(
        job=JobContextV2(title="Backend Developer", company="Acme", required_skills="Java, Debugging"),
        candidate=CandidateContextV2(full_name="Jane Doe", skills=["Java"]),
        aptitude=AptitudeEvidenceV2(),
        programming=ProgrammingEvidenceSummaryV2(
            available=True,
            representative_snippet={
                "question_title": "Order log summarizer",
                "language": language,
                "code_excerpt": "Map<String, Integer> count = new TreeMap<>();",
            },
        ),
    )


def test_representative_code_note_appears_when_language_matches_current_competency():

    note = brain._representative_code_note(_context_with_snippet(language="java"), "Java")

    assert "Order log summarizer" in note
    assert "TreeMap" in note


def test_representative_code_note_is_empty_when_competency_does_not_match_language():

    note = brain._representative_code_note(_context_with_snippet(language="java"), "Communication")

    assert note == ""


def test_representative_code_note_is_empty_when_no_snippet_available():

    note = brain._representative_code_note(_context(), "Java")

    assert note == ""


def test_turn_prompt_includes_the_snippet_only_for_the_matching_competency():

    prompt_matching = brain._turn_prompt(
        _context_with_snippet(language="java"), _blueprint(), "", "Java", "intermediate",
        "Tell me about your Java experience.", "I used Spring Boot.", 2, 4, 8,
    )
    prompt_other = brain._turn_prompt(
        _context_with_snippet(language="java"), _blueprint(), "", "Communication", "intermediate",
        "Tell me about a time you communicated a tradeoff.", "I explained it in a doc.", 2, 4, 8,
    )

    assert "Order log summarizer" in prompt_matching
    assert "Order log summarizer" not in prompt_other


# --- structured candidate memory (real evidence/claims/gaps carried between turns, not just prose) ---------------

def test_structured_memory_is_empty_placeholder_with_no_prior_turns():

    assert brain.build_structured_memory_v2((), "Java") == "(no prior turns yet)"


def test_structured_memory_surfaces_unresolved_gaps_for_current_competency():

    analysis = AnswerAnalysisV2(
        competency="Java", evidence=["used HikariCP for pooling"], claims=["tuned pool size"],
        missing_depth=["did not explain how the tuned number was chosen"], strength_level="moderate",
    )

    memory = brain.build_structured_memory_v2((("Java", analysis),), "Java")

    assert "did not explain how the tuned number was chosen" in memory
    assert "used HikariCP for pooling" in memory
    assert "UNRESOLVED GAPS" in memory


def test_structured_memory_caps_items_per_competency():

    analyses = tuple(
        ("Java", AnswerAnalysisV2(competency="Java", evidence=[f"evidence {i}"], strength_level="moderate"))
        for i in range(10)
    )

    memory = brain.build_structured_memory_v2(analyses, "Java")

    # only the first _MAX_STRUCTURED_ITEMS_PER_COMPETENCY evidence items are kept, not all 10
    assert "evidence 0" in memory
    assert f"evidence {brain._MAX_STRUCTURED_ITEMS_PER_COMPETENCY}" not in memory


def test_structured_memory_gives_other_competencies_a_compact_one_liner():

    java_analysis = AnswerAnalysisV2(competency="Java", evidence=["e1"], strength_level="weak")
    sql_analysis = AnswerAnalysisV2(competency="SQL", evidence=["wrote a JOIN query"], strength_level="strong")

    memory = brain.build_structured_memory_v2((("Java", java_analysis), ("SQL", sql_analysis)), "Java")

    assert "Other competencies already covered this interview" in memory
    assert "SQL [strong]" in memory
    assert "wrote a JOIN query" in memory


def test_turn_prompt_includes_structured_memory_section():

    analysis = AnswerAnalysisV2(
        competency="Java", evidence=[], claims=[],
        missing_depth=["never named the specific data structure used"], strength_level="weak",
    )

    prompt = brain._turn_prompt(
        _context(), _blueprint(), "", "Java", "intermediate",
        "Tell me about your Java experience.", "I used some collections.", 2, 4, 8,
        prior_analyses=(("Java", analysis),),
    )

    assert "never named the specific data structure used" in prompt
    assert "STRUCTURED EVIDENCE SO FAR" in prompt


# --- neutral acknowledgment for a genuinely weak/wrong (but attempted) answer -------------------------------------

def _decision(strength_level, next_question="Let's move to the next topic."):
    from backend.app.schemas.interview_turn_v2 import InterviewTurnDecisionV2

    return InterviewTurnDecisionV2(
        answer_analysis=AnswerAnalysisV2(competency="Java", strength_level=strength_level),
        next_action="MOVE_ON",
        next_question=next_question,
    )


def test_acknowledgment_line_added_for_weak_attempted_answer():

    decision = brain._with_acknowledgment_line(_decision("weak"), "I think it's something with loops maybe?", 0)

    assert decision.next_question.startswith(brain._ACKNOWLEDGMENT_LINES[0])
    assert "Let's move to the next topic." in decision.next_question


def test_acknowledgment_line_not_added_for_strong_answer():

    decision = brain._with_acknowledgment_line(_decision("strong"), "I used a TreeMap for sorted iteration.", 0)

    assert decision.next_question == "Let's move to the next topic."


def test_acknowledgment_line_not_added_when_candidate_said_dont_know():
    """"I don't know" already gets its own warmer reassurance via _with_kind_line -- never double up."""

    decision = brain._with_acknowledgment_line(_decision("none"), "I don't know, sorry.", 0)

    assert decision.next_question == "Let's move to the next topic."


def test_acknowledgment_line_never_judgmental_or_negative():

    for phrase in brain._ACKNOWLEDGMENT_LINES:
        lowered = phrase.lower()
        assert "wrong" not in lowered and "incorrect" not in lowered and "not quite" not in lowered and "actually" not in lowered


def test_acknowledgment_line_varies_by_turn_number():

    lines = {
        brain._with_acknowledgment_line(_decision("weak"), "not sure honestly", n).next_question.split(".")[0]
        for n in range(len(brain._ACKNOWLEDGMENT_LINES))
    }

    assert len(lines) == len(brain._ACKNOWLEDGMENT_LINES)


def test_questions_are_focused_on_resume_projects_tied_to_the_job_and_non_technical_roles_get_portfolio_work():
    from backend.app.schemas.interview_blueprint_v2 import InterviewBlueprintV2, InterviewCompetencyV2
    from backend.app.services.assessment.interview_brain_v2 import _competency_lines

    competencies = [InterviewCompetencyV2(name="X", priority="high", target_evidence="x")]

    technical = _competency_lines(InterviewBlueprintV2(role_shape="technical", competencies=competencies))
    non_technical = _competency_lines(InterviewBlueprintV2(role_shape="non_technical", competencies=competencies))

    assert "QUESTION FOCUS" in technical and "specific projects on the candidate's resume" in technical
    assert "tie EVERY question to a requirement of THIS job" in technical
    assert "portfolio" in non_technical and "campaigns" in non_technical and "specific projects on the candidate's resume" not in non_technical
