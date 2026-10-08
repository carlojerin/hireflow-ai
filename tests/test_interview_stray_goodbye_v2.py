"""
Two interview bugs seen live:
- the model's own working notes (headings, private-note echoes) reached the transcript panel: only the spoken words may be shown or recorded;
- the model said goodbye / "the interview is complete" by itself in the middle of the interview and nothing closed: such a goodbye is corrected, not recorded,
  and the interview is NOT closed. The real closing (after the final question) still works.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_realtime_interview_v2 as base
from backend.app.services.assessment import realtime_interview_v2 as rt


def _run(coro):
    return asyncio.run(coro)


def test_only_the_spoken_words_survive_the_cleaning():
    assert rt.clean_spoken("**Planning the next question**\n\nI am thinking about the candidate.\n\nThat is a good point. How did you decide on the layout?") == \
        "That is a good point. How did you decide on the layout?"
    assert rt.clean_spoken("Sure. PRIVATE NOTE: ask the salary question\nHow do you test a login page? [pauses]") == "Sure.\nHow do you test a login page?"
    assert rt.clean_spoken("Next, steer toward competency 'Web design'\nWhat did you test?") == "What did you test?"
    assert rt.clean_spoken("A normal spoken question with no markup at all?") == "A normal spoken question with no markup at all?"
    assert rt.clean_spoken("") == ""


def test_a_goodbye_is_recognised_and_an_ordinary_question_is_not():
    for text in ("Thanks for taking the time today, Arjun. The interview is complete, and we'll be in touch.", "That concludes our interview.", "We will be in touch soon.",
                 "This is the end of the interview."):
        assert rt.is_stray_goodbye(text), text
    for text in ("How would you test a login page?", "Tell me about a time you had to finish a project on time.", "What salary package are you expecting?", ""):
        assert not rt.is_stray_goodbye(text), text


class _CapturingPublisher(base.FakePublisher):
    def __init__(self):
        super().__init__()
        self.captions = []

    async def send_state(self, state, **extra):
        await super().send_state(state, **extra)
        if state == "speaking_text":
            self.captions.append(extra.get("text", ""))


def test_the_live_caption_never_shows_the_models_thinking():
    async def go():
        session, conn, _pub, *_ = await base._make()
        pub = _CapturingPublisher()
        session.publisher = pub
        await session.start()
        await asyncio.sleep(0.3)

        conn.feed("audio", b"AAAA")
        conn.feed("output_text", "**Choosing the next question**\n\nI should probe the layout decision.\n\n")
        await asyncio.sleep(0.25)                                                   # the live caption is throttled: words arrive over time
        conn.feed("output_text", "Great. How did you decide on the layout?")
        await asyncio.sleep(0.4)

        shown = " ".join(c for c in pub.captions if c)
        assert "Choosing" not in shown and "probe" not in shown and "**" not in shown
        assert "How did you decide on the layout?" in shown
        await session.close()
    _run(go())


def test_a_goodbye_the_model_says_by_itself_is_corrected_and_not_recorded_and_nothing_closes():
    async def go():
        session, conn, pub, *_ = await base._make()
        await session.start()
        await asyncio.sleep(0.3)                                                    # the opening note goes out first
        session._opening_spoken = True

        conn.feed("audio", b"AAAA")
        conn.feed("output_text", "Thanks for taking the time today, Arjun. The interview is complete, and we'll be in touch.")
        conn.feed("turn_complete")
        await asyncio.sleep(0.5)

        assert session.work.spoken == []                                            # not stored as one of the interview's questions
        assert session.stats["stray_goodbyes"] == 1 and session.state == "ready"    # the interview carries on, the screen is not closed
        text, turn_complete = conn.notes[-1]
        assert turn_complete and "NOT the end" in text and "must not say goodbye" in text
        assert ("state", "complete") not in pub.log
        await session.close()
    _run(go())


def test_the_real_closing_after_the_final_question_still_ends_the_interview():
    async def go():
        original = rt.CLOSING_DEADLINE_SECONDS
        rt.CLOSING_DEADLINE_SECONDS = 5
        try:
            session, conn, pub, *_ = await base._make(work=base.FakeWork(complete_at=1))
            await session.start()
            await session._record_answer("That is everything I wanted to add about my background and why I am a good fit")

            conn.feed("audio", b"AAAA")
            conn.feed("output_text", "Thank you so much for your time today, the interview is complete and we will be in touch.")
            conn.feed("turn_complete")
            await asyncio.sleep(1.8)
            assert ("state", "complete") in pub.log and session.stats.get("stray_goodbyes", 0) == 0
        finally:
            rt.CLOSING_DEADLINE_SECONDS = original
    _run(go())


def test_the_candidate_page_no_longer_draws_the_evaluation_categories():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = open(os.path.join(root, "frontend", "js", "ai-interview-v2.js"), encoding="utf-8").read()
    page = open(os.path.join(root, "frontend", "ai-interview-v2.html"), encoding="utf-8").read()
    assert "if (true) return;" in script and 'class="iv2-progress-card" hidden' in page
