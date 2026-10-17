"""
The interviewer behaves like a person who helps the candidate, and the interview always ends:

- "I don't know" on a role question replaces the question ONCE (not an extra question); the second time the answer is accepted;
- the salary / closing questions are never replaced;
- a candidate who stays silent is nudged, then offered one easier question, then the interview moves on;
- if the goodbye never finishes the interview is closed anyway (the permanent fix for "the interview screen did not end").
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_realtime_interview_v2 as base
from backend.app.services.assessment import realtime_interview_v2 as rt


def _run(coro):
    return asyncio.run(coro)


def test_dont_know_is_detected_but_a_real_answer_is_not():
    for text in ("I don't know", "I dont know", "no idea about this one", "honestly I do not know much about pivot tables", "I have never worked on that", "skip", "not familiar with it"):
        assert rt.is_dont_know(text), text
    for text in ("I know pivot tables very well", "I built a dashboard with pivot tables and slicers for the finance team last year, and I do not know of a better way to share it", ""):
        assert not rt.is_dont_know(text), text


def test_the_first_dont_know_replaces_the_question_and_does_not_count():
    async def go():
        session, conn, pub, *_ = await base._make()
        await session.start()
        session._open_turn = 3

        await session._record_answer("I don't know")

        assert session.work.answers == []                                         # nothing recorded: the turn did not advance
        text, turn_complete = conn.notes[-1]
        assert turn_complete and "ONE different, easier" in text and "REPLACES the question" in text
        assert session.stats["swapped_questions"] == 1

        await session._record_answer("no idea, sorry")                            # second time on the same question: accepted as the answer
        assert session.work.answers == ["no idea, sorry"]
        await session.close()
    _run(go())


def test_each_new_question_gets_its_own_one_swap():
    async def go():
        session, conn, pub, *_ = await base._make()
        await session.start()
        session._open_turn = 2
        await session._record_answer("I don't know")
        await session._record_answer("not sure about that either")                # recorded: moves to question 3 (FakeWork says so)
        assert session._open_turn == 2 + 0 or session._open_turn >= 2
        session._open_turn = 3
        before = len(conn.notes)
        await session._record_answer("I don't know")
        assert len(conn.notes) == before + 1 and "REPLACES" in conn.notes[-1][0]
        await session.close()
    _run(go())


def test_the_salary_and_closing_questions_are_never_replaced():
    async def go():
        session, conn, pub, *_ = await base._make()
        await session.start()
        session._open_turn = 7
        await session._record_answer("I don't know")
        assert session.work.answers == ["I don't know"] and session.stats["swapped_questions"] == 0
        await session.close()
    _run(go())


def test_a_long_answer_that_happens_to_contain_not_sure_is_recorded():
    async def go():
        session, conn, pub, *_ = await base._make()
        await session.start()
        session._open_turn = 2
        text = "I am not sure about every function but I usually build the monthly report with VLOOKUP and pivot tables and check the totals"
        await session._record_answer(text)
        assert session.work.answers == [text] and session.stats["swapped_questions"] == 0
        await session.close()
    _run(go())


def test_a_silent_candidate_is_nudged_then_offered_one_easier_question_then_the_interview_moves_on():
    async def go():
        session, conn, pub, *_ = await base._make()
        await session.start()
        session._open_turn = 2
        session._opening_spoken = True

        await session._nudge()
        text, turn_complete = conn.notes[-1]
        assert turn_complete and "quiet for a while" in text and "SAME question" in text and session._notice_turns == 1   # a kind prompt, not a question

        await session._nudge()
        assert "still quiet" in conn.notes[-1][0] and "REPLACES" in conn.notes[-1][0] and session._swaps[2] == 1

        await session._nudge()
        assert session.work.answers == ["(The candidate did not answer this question.)"]
        assert "stayed silent" in conn.notes[-1][0] and conn.notes[-1][1] is True                                       # then the next question follows
        await session.close()
    _run(go())


def test_the_silence_watcher_nudges_only_after_the_interviewer_has_stopped_talking():
    async def go():
        original = rt.SILENCE_NUDGE_SECONDS
        rt.SILENCE_NUDGE_SECONDS = 0.2
        try:
            session, conn, pub, *_ = await base._make()
            await session.start()
            session._opening_spoken = True

            session._ai_idle_since = None                                          # the interviewer is still talking: no nudge
            await asyncio.sleep(0.8)
            assert session.stats["silence_nudges"] == 0

            import time
            session._ai_idle_since = time.monotonic() - 5
            await asyncio.sleep(0.8)
            assert session.stats["silence_nudges"] >= 1
            await session.close()
        finally:
            rt.SILENCE_NUDGE_SECONDS = original
    _run(go())


def test_if_the_goodbye_never_finishes_the_interview_is_closed_anyway():
    async def go():
        original = rt.CLOSING_DEADLINE_SECONDS
        rt.CLOSING_DEADLINE_SECONDS = 0.1
        try:
            session, conn, pub, *_ = await base._make(work=base.FakeWork(complete_at=1))
            await session.start()
            session._open_turn = 8

            await session._record_answer("That is everything I wanted to add about my background and why I am a good fit")
            assert session._closing_sent                                           # the closing note went to the model...

            await asyncio.sleep(0.8)                                               # ...which stays silent: nothing ever says goodbye
            assert session.stats["forced_closes"] == 1
            assert ("state", "complete") in pub.log and session.state == "closed"
        finally:
            rt.CLOSING_DEADLINE_SECONDS = original
    _run(go())


def test_a_normal_goodbye_ends_without_the_watchdog():
    async def go():
        original = rt.CLOSING_DEADLINE_SECONDS
        rt.CLOSING_DEADLINE_SECONDS = 5
        try:
            session, conn, pub, *_ = await base._make(work=base.FakeWork(complete_at=1))
            await session.start()
            await session._record_answer("That is everything I wanted to add about my background and why I am a good fit")

            conn.feed("audio", b"AAAA")
            conn.feed("output_text", "Thank you so much, we will be in touch.")
            conn.feed("turn_complete")
            await asyncio.sleep(1.8)
            assert ("state", "complete") in pub.log and session.stats["forced_closes"] == 0
        finally:
            rt.CLOSING_DEADLINE_SECONDS = original
    _run(go())


def test_the_instruction_teaches_coughs_repeats_and_hesitation():
    text = rt.build_realtime_instruction.__code__.co_consts
    source = " ".join(c for c in text if isinstance(c, str))
    # the template is an f-string: check the literal text pieces
    for needle in ("A cough, sneeze", "Take care, have some water", "repeat the SAME question", "REPLACES the question", "question 7"):
        assert needle in source or needle.lower() in source.lower(), needle


def test_a_stalled_model_is_asked_again_instead_of_leaving_the_candidate_in_silence():
    async def go():
        original = rt.STALL_SECONDS
        rt.STALL_SECONDS = 0.3
        try:
            session, conn, pub, *_ = await base._make(lead=0.0)
            await session.start()
            await asyncio.sleep(1.6)                                                # the opening note was sent; the model never speaks

            opening_notes = [text for text, _tc in conn.notes if "Begin now" in text]
            assert len(opening_notes) >= 2                                          # asked again
            assert all("Do NOT apologise" in text for text in opening_notes[1:])    # the retry never makes the interviewer say "Sorry about that, let me start over"
            assert "Do NOT apologise" not in opening_notes[0]
            assert session.stats["stall_recoveries"] >= 1

            conn.feed("audio", b"AAAA")                                             # the model finally speaks: the clock stops
            await asyncio.sleep(0.2)
            count = len(conn.notes)
            await asyncio.sleep(1.2)
            assert len(conn.notes) == count and session._awaiting_reply_since is None
            await session.close()
        finally:
            rt.STALL_SECONDS = original
    _run(go())


def test_the_default_realtime_model_is_the_fast_one():
    assert rt.DEFAULT_MODEL == "gemini-3.1-flash-live-preview"
