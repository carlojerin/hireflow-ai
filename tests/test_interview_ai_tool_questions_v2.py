"""Every interview ends with the same four questions, after the model's own six resume / job questions:
how they would use AI on a real task of the role (turn 7), where AI falls short and how they check it (turn 8), a standard closing question: anything not covered + why they fit (turn 9), then the expected salary package (turn 10). The model's own turn choice cannot skip, reorder or end before them."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace

from backend.app.schemas.interview_blueprint_v2 import InterviewBlueprintV2, InterviewCompetencyV2
from backend.app.schemas.interview_turn_v2 import AnswerAnalysisV2, InterviewTurnDecisionV2
from backend.app.services.assessment.interview_integration_v2 import (
    AI_TOOL_COMPETENCY, FREE_TURNS, INTERVIEW_V2_MAX_TURNS, INTERVIEW_V2_MIN_TURNS, PACKAGE_COMPETENCY,
    WORK_FIT_COMPETENCY, _enforce_closing_sequence, named_ai_tool,
)


def _blueprint(role_shape="technical"):
    return InterviewBlueprintV2(
        role_shape=role_shape,
        competencies=[InterviewCompetencyV2(name="Python", priority="high", target_evidence="x")],
    )


def _decision(action="FOLLOW_UP"):
    return InterviewTurnDecisionV2(
        answer_analysis=AnswerAnalysisV2(competency="Python"), next_action=action,
        next_competency="Python", next_question="Tell me more about decorators?",
    )


def _turn(n, competency):
    return SimpleNamespace(turn_number=n, competency=competency)


def _history(*competencies):
    return [_turn(i + 1, c) for i, c in enumerate(competencies)]


def test_the_interview_is_ten_questions_long():
    assert INTERVIEW_V2_MIN_TURNS == INTERVIEW_V2_MAX_TURNS == 10 and FREE_TURNS == 6


def test_the_model_decides_the_first_six_questions():
    turns = _history("Python", "Python", "SQL", "SQL", "SQL")
    free = _decision()
    assert _enforce_closing_sequence(free, _blueprint(), turns, turns[-1]) is free


def test_the_seventh_question_is_how_the_candidate_would_use_ai_on_a_real_task_of_this_role_even_if_the_model_wants_to_end():
    turns = _history("Python", "Python", "SQL", "SQL", "SQL", "SQL")
    out = _enforce_closing_sequence(_decision("END_INTERVIEW"), _blueprint(), turns, turns[-1], job_title="MIS Executive")
    assert out.next_competency == AI_TOOL_COMPETENCY
    assert "as a MIS Executive role" in out.next_question and "use AI" in out.next_question and "still check yourself" in out.next_question


def test_without_a_job_title_the_ai_question_still_reads_naturally():
    turns = _history("Python", "Python", "SQL", "SQL", "SQL", "SQL")
    out = _enforce_closing_sequence(_decision(), _blueprint("non_technical"), turns, turns[-1])
    assert "in this role" in out.next_question and "Canva" not in out.next_question


def test_after_the_first_ai_question_comes_the_second_then_the_closing_question_then_the_salary_then_it_is_over():
    turns = _history("Python", "Python", "SQL", "SQL", "SQL", "SQL", AI_TOOL_COMPETENCY)
    second_ai = _enforce_closing_sequence(_decision(), _blueprint(), turns, turns[-1])
    assert second_ai.next_competency == AI_TOOL_COMPETENCY and "fall short" in second_ai.next_question and "correct" in second_ai.next_question

    turns.append(_turn(8, AI_TOOL_COMPETENCY))
    work = _enforce_closing_sequence(_decision(), _blueprint(), turns, turns[-1], company="Acme Ltd")
    assert work.next_competency == WORK_FIT_COMPETENCY
    assert "anything we haven't covered" in work.next_question and "right fit" in work.next_question

    turns.append(_turn(9, WORK_FIT_COMPETENCY))
    package = _enforce_closing_sequence(_decision(), _blueprint(), turns, turns[-1])
    assert package.next_competency == PACKAGE_COMPETENCY and "package" in package.next_question

    turns.append(_turn(10, PACKAGE_COMPETENCY))
    last = _decision()
    assert _enforce_closing_sequence(last, _blueprint(), turns, turns[-1]) is last        # question 10 answered: the interview ends


def test_named_ai_tool_matching():
    assert named_ai_tool("I use GitHub Copilot daily") == "GitHub Copilot"
    assert named_ai_tool("mostly canva for posters") == "Canva"
    assert named_ai_tool("I use none of them") is None
    assert named_ai_tool("the scoopilot of ironcursor") is None                              # no partial-word matches


def test_the_model_cannot_end_the_interview_before_the_fixed_questions_even_in_the_first_turns():
    for count in (1, 2, 3, 4, 5):
        turns = _history(*["Python"] * count)
        out = _enforce_closing_sequence(_decision("END_INTERVIEW"), _blueprint(), turns, turns[-1])
        assert out.next_action != "END_INTERVIEW" and out.next_question, count


def test_the_interviewer_is_told_the_interview_is_not_over_after_the_role_questions_and_to_ask_the_fixed_ones_as_given():
    import inspect
    from backend.app.services.assessment import realtime_interview_v2 as rt

    prompt = inspect.getsource(rt.build_realtime_instruction)
    assert "NEVER say or hint that the interview is ending" in prompt and "CONTINUES with the four fixed questions" in prompt

    source = inspect.getsource(rt.DbRealtimeWork.record_answer)
    assert "Required question" in source and "NOT ending" in source and "AI_TOOL_COMPETENCY" in source

    assert "tell them the interview is now complete" in rt._CLOSING_REQUIREMENTS            # the closing speech itself is unchanged
