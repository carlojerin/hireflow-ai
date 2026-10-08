"""
Task 5O-A: a hosted provider (Groq) is the primary Interview Brain provider, Gemini by default.
The HTTP layer (requests.post inside interview_llm_provider) is faked,
so these prove ROUTING and VALIDATION only -- not model quality; the real
model is exercised separately by the live scenario run.
"""

import contextlib
import json
import os

from backend.app.schemas.interview_blueprint_v2 import InterviewBlueprintV2, InterviewCompetencyV2
from backend.app.schemas.interview_context_v2 import (
    AptitudeEvidenceV2, CandidateContextV2, InterviewContextV2, JobContextV2, ProgrammingEvidenceSummaryV2,
)
from backend.app.services.assessment import interview_brain_v2 as brain
from backend.app.services.assessment import interview_llm_provider as prov


def _context():
    return InterviewContextV2(
        job=JobContextV2(title="Java Backend Developer", company="Acme", required_skills="Java, Spring Boot, REST APIs, SQL"),
        candidate=CandidateContextV2(full_name="Jane Doe", skills=["Java", "Spring Boot", "REST"]),
        aptitude=AptitudeEvidenceV2(),
        programming=ProgrammingEvidenceSummaryV2(),
    )


def _blueprint():
    return InterviewBlueprintV2(competencies=[
        InterviewCompetencyV2(name=n, priority="high", evidence_status="UNKNOWN")
        for n in ("Java", "Spring Boot", "REST APIs", "SQL")
    ])


def _decision(action="FOLLOW_UP", strength="moderate", evidence=("built REST endpoints",),
              question="Which HTTP status codes did you return for validation errors, and why?", competency="Spring Boot"):
    return {
        "answer_analysis": {"competency": "Spring Boot", "strengths": [], "weaknesses": [], "evidence": list(evidence),
                            "claims": ["created REST APIs"], "missing_depth": ["no design detail"], "strength_level": strength},
        "next_action": action, "reason": "r", "next_competency": competency, "next_difficulty": "intermediate",
        "next_question": question, "next_question_type": "TECHNICAL",
    }


class FakeHosted:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.urls = []
        self.payloads = []

    def __call__(self, url, json=None, headers=None, timeout=None, **kw):
        self.calls.append({"url": url, "json": json, "headers": headers})
        self.urls.append(url)
        self.payloads.append(json)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        status = item if isinstance(item, int) else 200
        text = "" if isinstance(item, int) else (item if isinstance(item, str) else __import__("json").dumps(item))

        class R:
            status_code = status

            def json(self_inner):
                return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 900, "completion_tokens": 120}}

        return R()


@contextlib.contextmanager
def _env(**kv):
    """Sets (str) or removes (None) env vars for the duration of one test, and resets call counts."""
    old = {k: os.environ.get(k) for k in ("AI_INTERVIEW_PROVIDER", "AI_INTERVIEW_MODEL", "AI_INTERVIEW_REASONING_EFFORT", "GEMINI_API_KEY", "GROQ_API_KEY", *kv)}
    os.environ.pop("AI_INTERVIEW_MODEL", None)                  # hermetic: never inherit the deployment's model name from .env
    os.environ.pop("AI_INTERVIEW_REASONING_EFFORT", None)
    os.environ["AI_INTERVIEW_PROVIDER"] = "groq"      # these tests fake Groq's HTTP endpoint: select it explicitly (default is Gemini)
    os.environ["GROQ_API_KEY"] = "test-key"
    os.environ.pop("GEMINI_API_KEY", None)
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    prov.reset_call_counts()
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _isolated(fn):
    import functools, inspect

    @functools.wraps(fn)
    def wrapper(*a, **k):
        with _env():
            return fn(*a, **k)

    wrapper.__signature__ = inspect.signature(fn)
    return wrapper


def _turn(answer="I created REST APIs using Spring Boot.", turn=2, last="How do you build APIs?"):
    return brain.analyze_and_decide_next_v2(
        context=_context(), blueprint=_blueprint(), conversation_summary="", current_competency="Spring Boot",
        current_difficulty="intermediate", last_question=last, answer_text=answer, turn_number=turn,
        min_turns=4, max_turns=8, covered_competencies=set(), follow_ups_on_current=0,
    )


@_isolated
def test_default_provider_is_gemini_and_groq_only_when_explicit():
    os.environ.pop("AI_INTERVIEW_PROVIDER", None)
    p = prov.get_interview_llm_provider()
    assert p.name == "gemini" and p.model == "gemini-3.5-flash-lite" and len(p.models) >= 3
    os.environ["AI_INTERVIEW_GEMINI_MODEL"] = "gemini-flash-lite-latest"
    assert prov.get_interview_llm_provider().model == "gemini-flash-lite-latest"
    os.environ.pop("AI_INTERVIEW_GEMINI_MODEL", None)
    os.environ["INTERVIEW_REALTIME_PROVIDER"] = "gemini_pipeline"      # the explicit pin selects the Gemini brain
    os.environ["AI_INTERVIEW_PROVIDER"] = "groq"
    assert prov.get_interview_llm_provider().name == "gemini"
    os.environ.pop("INTERVIEW_REALTIME_PROVIDER", None)
    os.environ.pop("AI_INTERVIEW_PROVIDER", None)
    os.environ["AI_INTERVIEW_PROVIDER"] = "gemini"
    assert prov.get_interview_llm_provider().name == "gemini"
    os.environ["AI_INTERVIEW_PROVIDER"] = "groq"
    p = prov.get_interview_llm_provider()
    assert p.name == "groq" and p.model == "openai/gpt-oss-120b" and "api.groq.com" in p.base_url


@_isolated
def test_gemini_quota_on_one_model_moves_the_same_turn_to_the_next_gemini_model(monkeypatch):
    from backend.app.services.assessment import gemini_client
    os.environ.pop("AI_INTERVIEW_PROVIDER", None)
    os.environ["AI_INTERVIEW_GEMINI_MODELS"] = "model-a,model-b"
    prov._gemini_cooldown_until.clear(); prov._gemini_latency.clear()
    used = []

    def fake(prompt, model=None, **_kw):
        used.append(model)
        if model == "model-a":
            raise gemini_client.GeminiUnavailableError("Gemini API request failed: 429 RESOURCE_EXHAUSTED quota")
        return json.dumps(_decision())

    monkeypatch.setattr(gemini_client, "generate_json_content", fake)
    p = prov.get_interview_llm_provider()
    assert json.loads(p.generate_json("hi")) and used == ["model-a", "model-b"]
    used.clear()
    p.generate_json("again")
    assert used == ["model-b"]                      # model-a is on cooldown: no wasted call
    os.environ.pop("AI_INTERVIEW_GEMINI_MODELS", None)
    prov._gemini_cooldown_until.clear(); prov._gemini_latency.clear()


@_isolated
def test_gemini_default_turn_uses_only_gemini_and_never_groq(monkeypatch):
    from backend.app.services.assessment import gemini_client
    os.environ.pop("AI_INTERVIEW_PROVIDER", None)
    seen = []
    monkeypatch.setattr(gemini_client, "generate_json_content", lambda prompt, model=gemini_client.GEMINI_MODEL, **_kw: (seen.append(prompt), json.dumps(_decision()))[1])
    def boom(*a, **k):
        raise AssertionError("Groq must not be called")
    monkeypatch.setattr(prov.requests, "post", boom)
    decision, by = _turn()
    assert by == "gemini" and len(seen) == 1 and prov.get_call_counts() == {"gemini": 1}


@_isolated
def test_gemini_failure_falls_back_deterministically_and_never_to_groq(monkeypatch):
    from backend.app.services.assessment import gemini_client
    os.environ.pop("AI_INTERVIEW_PROVIDER", None)
    def down(prompt, model=gemini_client.GEMINI_MODEL, **_kw):
        raise gemini_client.GeminiUnavailableError("down")
    monkeypatch.setattr(gemini_client, "generate_json_content", down)
    def boom(*a, **k):
        raise AssertionError("Groq must not be called")
    monkeypatch.setattr(prov.requests, "post", boom)
    decision, by = _turn()
    assert by == "fallback" and prov.get_call_counts() == {"gemini": 1}


@_isolated
def test_interview_turn_goes_to_groq_and_never_gemini(monkeypatch):
    fake = FakeHosted([_decision()])
    monkeypatch.setattr(prov.requests, "post", fake)
    decision, by = _turn()
    assert by == "groq"
    assert fake.urls == ["https://api.groq.com/openai/v1/chat/completions"]
    assert fake.payloads[0]["model"] == "openai/gpt-oss-120b"
    assert prov.get_call_counts() == {"groq": 1}


@_isolated
def test_prompt_contains_no_credentials(monkeypatch):
    os.environ["LIVEKIT_API_SECRET"] = "SECRETVALUE1"
    os.environ["TAVUS_API_KEY"] = "SECRETVALUE2"
    fake = FakeHosted([_decision()])
    monkeypatch.setattr(prov.requests, "post", fake)
    _turn()
    assert "SECRETVALUE" not in json.dumps(fake.payloads)


@_isolated
def test_groq_down_falls_back_deterministically_and_never_to_gemini(monkeypatch):
    import requests
    monkeypatch.setattr(prov.requests, "post", FakeHosted([requests.ConnectionError("down")]))
    decision, by = _turn()
    assert by == "fallback" and decision.next_action in {"FOLLOW_UP", "MOVE_ON", "END_INTERVIEW"}
    assert prov.get_call_counts() == {"groq": 1}


@_isolated
def test_hosted_provider_still_gets_one_repair_retry(monkeypatch):
    """A malformed reply gets ONE repair-note retry -- it demonstrably helps a capable model that made a
    one-off mistake."""

    os.environ["AI_INTERVIEW_PROVIDER"] = "groq"
    os.environ["GROQ_API_KEY"] = "gsk_test_secret_value"
    os.environ.pop("AI_INTERVIEW_MODEL", None)
    fake = FakeHosted(["{ this is not json", json.dumps(_decision())])
    monkeypatch.setattr(prov.requests, "post", fake)
    decision, by = _turn()
    assert by == "groq" and len(fake.calls) == 2
    assert "rejected" in fake.calls[1]["json"]["messages"][-1]["content"]
    os.environ.pop("GROQ_API_KEY", None)


@_isolated
def test_two_malformed_replies_fall_back(monkeypatch):
    monkeypatch.setattr(prov.requests, "post", FakeHosted(["nope", "still nope"]))
    assert _turn()[1] == "fallback"


@_isolated
def test_think_block_and_fences_are_stripped(monkeypatch):
    wrapped = "<think>hmm</think>\n```json\n" + json.dumps(_decision()) + "\n```"
    monkeypatch.setattr(prov.requests, "post", FakeHosted([wrapped]))
    assert _turn()[1] == "groq"


@_isolated
def test_unknown_competency_is_rejected(monkeypatch):
    bad = _decision(competency="Quantum Computing")
    monkeypatch.setattr(prov.requests, "post", FakeHosted([bad, bad]))
    assert _turn()[1] == "fallback"


@_isolated
def test_repeated_question_is_rejected(monkeypatch):
    bad = _decision(question="How do you build APIs?")
    monkeypatch.setattr(prov.requests, "post", FakeHosted([bad, bad]))
    assert _turn()[1] == "fallback"


@_isolated
def test_model_cannot_end_interview_before_minimum_turns(monkeypatch):
    end = _decision(action="END_INTERVIEW", question=None)
    monkeypatch.setattr(prov.requests, "post", FakeHosted([end, end]))
    assert _turn(turn=2)[1] == "fallback"


@_isolated
def test_scenario1_short_claim_cannot_be_verified_as_strong(monkeypatch):
    monkeypatch.setattr(prov.requests, "post", FakeHosted([_decision(action="MOVE_ON", strength="strong")]))
    decision, by = _turn("I created REST APIs using Spring Boot.")
    assert by == "groq" and decision.answer_analysis.strength_level == "moderate"


@_isolated
def test_scenario2_detailed_answer_may_move_to_another_competency(monkeypatch):
    long = ("I designed versioned REST endpoints in Spring Boot with DTO validation, a global exception handler "
            "mapping domain errors to 4xx codes, pagination, and integration tests using MockMvc and Testcontainers.")
    reply = _decision(action="MOVE_ON", strength="strong", competency="SQL",
                      question="How would you index a table queried by customer and date?")
    monkeypatch.setattr(prov.requests, "post", FakeHosted([reply]))
    decision, _ = _turn(long)
    assert decision.next_action == "MOVE_ON" and decision.next_competency == "SQL"
    assert decision.answer_analysis.strength_level == "strong"


@_isolated
def test_scenarios_3_4_5_follow_up_style_actions_are_accepted(monkeypatch):
    for action in ("CLARIFICATION", "CHALLENGE", "RESUME_VERIFICATION", "FOLLOW_UP"):
        monkeypatch.setattr(prov.requests, "post", FakeHosted([_decision(action=action, strength="weak")]))
        decision, by = _turn("Yes I know it, it is fine.")
        assert by == "groq" and decision.next_action == action


@_isolated
def test_opening_uses_groq(monkeypatch):
    opening = {"opening_message": "Hello.", "first_question": "Tell me about a Spring Boot service you built.",
               "competency": "Spring Boot", "difficulty": "basic", "question_type": "EXPERIENCE"}
    monkeypatch.setattr(prov.requests, "post", FakeHosted([opening]))
    _, by = brain.generate_opening_v2(_context(), _blueprint())
    assert by == "groq" and prov.get_call_counts() == {"groq": 1}


def _turn_on(follow_ups):
    return brain.analyze_and_decide_next_v2(
        context=_context(), blueprint=_blueprint(), conversation_summary="", current_competency="Spring Boot",
        current_difficulty="intermediate", last_question="How do you build APIs?",
        answer_text="I built and load tested a versioned Spring Boot service with optimistic locking and retries.",
        turn_number=5, min_turns=4, max_turns=8, covered_competencies={"spring boot"}, follow_ups_on_current=follow_ups,
    )


@_isolated
def test_competency_coverage_guard_forces_a_move_after_three_turns(monkeypatch):
    # This test's premise is a first reply that violates the coverage guard, corrected on the repair-note retry.
    os.environ["AI_INTERVIEW_PROVIDER"] = "groq"
    os.environ["GROQ_API_KEY"] = "gsk_test_secret_value"
    os.environ.pop("AI_INTERVIEW_MODEL", None)
    stay = _decision(action="DEEPER_PROBE", competency="Spring Boot", question="Go deeper on transactions?")
    move = _decision(action="DIRECT_NEXT_TOPIC", competency="SQL", question="How would you index the orders table?")
    fake = FakeHosted([json.dumps(stay), json.dumps(move)])
    monkeypatch.setattr(prov.requests, "post", fake)
    decision, by = _turn_on(follow_ups=2)            # this is the 3rd turn on Spring Boot; Java/REST APIs/SQL uncovered
    assert by == "groq" and decision.next_competency == "SQL"
    assert "not-yet-covered" in fake.calls[1]["json"]["messages"][-1]["content"]
    os.environ.pop("GROQ_API_KEY", None)


@_isolated
def test_coverage_guard_allows_staying_when_under_the_limit(monkeypatch):
    stay = _decision(action="DEEPER_PROBE", competency="Spring Boot", question="Go deeper on transactions?")
    monkeypatch.setattr(prov.requests, "post", FakeHosted([stay]))
    decision, by = _turn_on(follow_ups=1)             # only the 2nd turn on this competency
    assert by == "groq" and decision.next_competency == "Spring Boot"


@_isolated
def test_coverage_guard_does_not_apply_when_nothing_else_is_uncovered(monkeypatch):
    stay = _decision(action="DEEPER_PROBE", competency="Spring Boot", question="Go deeper on transactions?")
    monkeypatch.setattr(prov.requests, "post", FakeHosted([stay]))
    decision, by = brain.analyze_and_decide_next_v2(
        context=_context(), blueprint=_blueprint(), conversation_summary="", current_competency="Spring Boot",
        current_difficulty="intermediate", last_question="How do you build APIs?",
        answer_text="I built and load tested a versioned Spring Boot service with optimistic locking and retries.",
        turn_number=5, min_turns=4, max_turns=8, covered_competencies={"java", "spring boot", "rest apis", "sql"}, follow_ups_on_current=4,
    )
    assert by == "groq"


@_isolated
def test_harmless_enum_drift_is_normalised_but_bad_actions_stay_rejected(monkeypatch):
    drift = _decision(action="follow_up")
    drift["next_difficulty"] = "medium"
    drift["next_question_type"] = "Follow Up Question"
    monkeypatch.setattr(prov.requests, "post", FakeHosted([drift]))
    decision, by = _turn()
    assert by == "groq" and decision.next_difficulty == "intermediate" and decision.next_question_type == "TECHNICAL"
    assert decision.next_action == "FOLLOW_UP"
    bad = _decision(action="HIRE_CANDIDATE")
    monkeypatch.setattr(prov.requests, "post", FakeHosted([bad, bad]))
    assert _turn()[1] == "fallback"


@_isolated
def test_opening_enum_drift_is_normalised(monkeypatch):
    opening = {"opening_message": "Hello.", "first_question": "Tell me about a Spring Boot service you built.",
               "competency": "Spring Boot", "difficulty": "Easy", "question_type": "warm-up"}
    monkeypatch.setattr(prov.requests, "post", FakeHosted([opening]))
    op, by = brain.generate_opening_v2(_context(), _blueprint())
    assert by == "groq" and op.difficulty == "basic" and op.question_type == "TECHNICAL"


@_isolated
def test_gemini_transient_503_is_retried_on_gemini_only(monkeypatch):
    from backend.app.services.assessment import gemini_client
    os.environ.pop("AI_INTERVIEW_PROVIDER", None)
    monkeypatch.setattr(prov, "GEMINI_RETRY_BACKOFF_SECONDS", 0)
    seq = [gemini_client.GeminiUnavailableError("Gemini API request failed: 503 UNAVAILABLE. high demand"), json.dumps(_decision())]

    def flaky(prompt, model=gemini_client.GEMINI_MODEL, **_kw):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(gemini_client, "generate_json_content", flaky)

    def boom(*a, **k):
        raise AssertionError("Groq must not be called")

    monkeypatch.setattr(prov.requests, "post", boom)
    decision, by = _turn()
    assert by == "gemini" and prov.get_call_counts() == {"gemini": 2}


@_isolated
def test_gemini_non_transient_failure_is_not_retried(monkeypatch):
    from backend.app.services.assessment import gemini_client
    os.environ.pop("AI_INTERVIEW_PROVIDER", None)
    n = {"c": 0}

    def bad(prompt, model=gemini_client.GEMINI_MODEL, **_kw):
        n["c"] += 1
        raise gemini_client.GeminiUnavailableError("GEMINI_API_KEY is not configured.")

    monkeypatch.setattr(gemini_client, "generate_json_content", bad)
    assert _turn()[1] == "fallback" and n["c"] == 1


@_isolated
def test_gemini_daily_quota_429_is_not_retried(monkeypatch):
    from backend.app.services.assessment import gemini_client
    os.environ.pop("AI_INTERVIEW_PROVIDER", None)
    n = {"c": 0}

    def quota(prompt, model=gemini_client.GEMINI_MODEL, **_kw):
        n["c"] += 1
        raise gemini_client.GeminiUnavailableError("Gemini API request failed: 429 RESOURCE_EXHAUSTED. GenerateRequestsPerDayPerProjectPerModel-FreeTier")

    monkeypatch.setattr(gemini_client, "generate_json_content", quota)
    prov._gemini_cooldown_until.clear(); prov._gemini_latency.clear()
    assert _turn()[1] == "fallback"
    assert n["c"] == len(prov._gemini_model_list())        # every Gemini model tried ONCE, never re-tried in rounds
    prov._gemini_cooldown_until.clear(); prov._gemini_latency.clear()


@_isolated
def test_groq_provider_is_selected_explicitly_and_calls_only_groq(monkeypatch):
    os.environ["AI_INTERVIEW_PROVIDER"] = "groq"
    os.environ["GROQ_API_KEY"] = "gsk_test_secret_value"
    os.environ.pop("AI_INTERVIEW_MODEL", None)
    fake = FakeHosted([json.dumps(_decision())])
    monkeypatch.setattr(prov.requests, "post", fake)
    from backend.app.services.assessment import gemini_client

    def gem_boom(*a, **k):
        raise AssertionError("Gemini must not be called")

    monkeypatch.setattr(gemini_client, "generate_json_content", gem_boom)
    decision, by = _turn()
    assert by == "groq" and prov.get_call_counts() == {"groq": 1}
    call = fake.calls[0]
    assert call["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert call["json"]["model"] == "openai/gpt-oss-120b" and call["json"]["response_format"] == {"type": "json_object"}
    assert call["headers"]["Authorization"] == "Bearer gsk_test_secret_value"
    assert "gsk_test_secret_value" not in json.dumps(call["json"])                    # the key is only ever a header
    os.environ.pop("GROQ_API_KEY", None)


@_isolated
def test_hosted_provider_failure_falls_back_deterministically_and_never_to_gemini(monkeypatch):
    os.environ["AI_INTERVIEW_PROVIDER"] = "groq"
    os.environ["GROQ_API_KEY"] = "gsk_test_secret_value"
    fake = FakeHosted([429])
    monkeypatch.setattr(prov.requests, "post", fake)
    from backend.app.services.assessment import gemini_client

    def gem_boom(*a, **k):
        raise AssertionError("Gemini must not be called")

    monkeypatch.setattr(gemini_client, "generate_json_content", gem_boom)
    monkeypatch.setattr(brain.time, "sleep", lambda s: None)
    decision, by = _turn()
    # a rate limit is waited out ONCE (a per-minute limit clears in seconds), never in a storm, and never on another provider
    assert by == "fallback" and prov.get_call_counts() == {"groq": 2} and len(fake.calls) == 2
    os.environ.pop("GROQ_API_KEY", None)


@_isolated
def test_hosted_provider_without_key_or_model_fails_safely_before_any_request(monkeypatch):
    fake = FakeHosted([])
    monkeypatch.setattr(prov.requests, "post", fake)
    os.environ["AI_INTERVIEW_PROVIDER"] = "groq"
    os.environ.pop("GROQ_API_KEY", None)
    assert _turn()[1] == "fallback"
    os.environ["AI_INTERVIEW_PROVIDER"] = "cerebras"
    os.environ["CEREBRAS_API_KEY"] = "csk_x"
    os.environ.pop("AI_INTERVIEW_MODEL", None)
    assert _turn()[1] == "fallback"                                                     # no invented model name
    assert fake.calls == []
    os.environ.pop("CEREBRAS_API_KEY", None)


@_isolated
def test_hosted_provider_errors_never_contain_the_api_key(monkeypatch):
    os.environ["AI_INTERVIEW_PROVIDER"] = "openrouter"
    os.environ["OPENROUTER_API_KEY"] = "sk-or-secret-123"
    os.environ["AI_INTERVIEW_MODEL"] = "some/model:free"
    p = prov.get_interview_llm_provider()

    def boom(*a, **k):
        raise RuntimeError("connection failed with Authorization: Bearer sk-or-secret-123")

    monkeypatch.setattr(prov.requests, "post", boom)
    try:
        p.generate_json("x")
    except prov.InterviewLLMUnavailableError as exc:
        assert "sk-or-secret-123" not in str(exc) and "RuntimeError" in str(exc)
    else:
        raise AssertionError("expected an error")
    os.environ.pop("OPENROUTER_API_KEY", None); os.environ.pop("AI_INTERVIEW_MODEL", None)


# ---- interviewer intelligence guards (short spoken questions, no looping) -------------------------------------------

def _decision_with_question(question):
    d = _decision()
    d["next_question"] = question
    return d


@_isolated
def test_overlong_or_looping_questions_are_rejected_then_retried(monkeypatch):
    os.environ["AI_INTERVIEW_PROVIDER"] = "gemini"
    from backend.app.services.assessment import gemini_client
    replies = iter([
        json.dumps(_decision_with_question("Could you describe a specific feature you implemented with Spring Boot, including the problem it solved, " * 3)),
        json.dumps(_decision_with_question("You mentioned Redis caching; how did you invalidate the cache?")),
    ])
    monkeypatch.setattr(gemini_client, "generate_json_content", lambda prompt, model=None, **_kw: next(replies))
    decision, by = _turn()
    assert by == "gemini" and decision.next_question.startswith("You mentioned Redis")


@_isolated
def test_reworded_copy_of_an_earlier_question_is_rejected(monkeypatch):
    os.environ["AI_INTERVIEW_PROVIDER"] = "gemini"
    from backend.app.services.assessment import gemini_client
    summary = 'Turn 1 [Spring Boot, weak]: you asked "Can you describe a feature you built with Spring Boot at Nimbus?"; candidate said "Hello" -> none.'
    assert brain._asked_questions(summary) == ["Can you describe a feature you built with Spring Boot at Nimbus?"]
    assert brain._too_similar("Can you describe a feature you built using Spring Boot at Nimbus?", brain._asked_questions(summary)[0])
    assert not brain._too_similar("How did you invalidate the Redis cache?", brain._asked_questions(summary)[0])


@_isolated
def test_a_slow_gemini_model_times_out_and_the_next_model_answers(monkeypatch):
    from backend.app.services.assessment import gemini_client
    os.environ.pop("AI_INTERVIEW_PROVIDER", None)
    os.environ["AI_INTERVIEW_GEMINI_MODELS"] = "slow-a,fast-b"
    os.environ["AI_INTERVIEW_GEMINI_TIMEOUT_SECONDS"] = "7"
    prov._gemini_cooldown_until.clear(); prov._gemini_latency.clear()
    seen = []

    def fake(prompt, model=None, timeout_ms=None, **_kw):
        seen.append((model, timeout_ms))
        if model == "slow-a":
            raise gemini_client.GeminiUnavailableError("Gemini API request failed: ReadTimeout: The read operation timed out")
        return json.dumps(_decision())

    monkeypatch.setattr(gemini_client, "generate_json_content", fake)
    decision, by = _turn()
    assert by == "gemini" and [m for m, _ in seen] == ["slow-a", "fast-b"]
    assert all(t == 7000 for _, t in seen)                          # every model call carries the short timeout
    os.environ.pop("AI_INTERVIEW_GEMINI_MODELS", None)
    os.environ.pop("AI_INTERVIEW_GEMINI_TIMEOUT_SECONDS", None)
    prov._gemini_cooldown_until.clear(); prov._gemini_latency.clear()


@_isolated
def test_groq_brain_waits_out_a_short_rate_limit_once_and_asks_for_a_small_reply(monkeypatch):
    os.environ["AI_INTERVIEW_PROVIDER"] = "groq"
    os.environ["AI_INTERVIEW_MODEL"] = "openai/gpt-oss-120b"
    os.environ["GROQ_API_KEY"] = "test-key-not-real"
    calls, sleeps = [], []

    class FakeResp:
        def __init__(self, status, body):
            self.status_code, self._b = status, body
        def json(self):
            return self._b

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(json["max_tokens"])
        if len(calls) == 1:
            return FakeResp(429, {"error": {"code": "rate_limit_exceeded", "message": "Rate limit reached. Please try again in 2.5s."}})
        return FakeResp(200, {"choices": [{"message": {"content": __import__("json").dumps(_decision())}}], "usage": {}})

    monkeypatch.setattr(prov.requests, "post", fake_post)
    monkeypatch.setattr(brain.time, "sleep", lambda s: sleeps.append(s))
    decision, by = _turn()
    assert by == "groq" and len(calls) == 2                      # one retry, not the deterministic fallback
    assert calls == [700, 700]                                   # a turn reserves a small reply, not the 1500 default
    assert sleeps and 2.5 <= sleeps[0] <= 9
    os.environ.pop("GROQ_API_KEY", None)


@_isolated
def test_i_dont_know_gets_a_kind_line_and_a_move_on_never_more_pressure(monkeypatch):
    os.environ["AI_INTERVIEW_PROVIDER"] = "gemini"
    from backend.app.services.assessment import gemini_client
    pressing = _decision(action="FOLLOW_UP", question="Could you give one example of an endpoint you built?", competency="Spring Boot")
    moving = _decision(action="DIRECT_NEXT_TOPIC", question="Let's look at your database work: how do you design a table for orders?", competency="SQL")
    replies = iter([json.dumps(pressing), json.dumps(moving)])
    monkeypatch.setattr(gemini_client, "generate_json_content", lambda prompt, model=None, **_kw: next(replies))
    decision, by = _turn(answer="I don't know.", turn=2)
    assert by == "gemini" and decision.next_action == "DIRECT_NEXT_TOPIC" and decision.next_competency == "SQL"
    assert any(marker in decision.next_question.lower() for marker in ("okay", "no worries", "no problem", "fair enough", "alright", "fine"))
    assert "Let's look at your database work" in decision.next_question


def test_the_kind_line_is_added_once_and_only_after_a_dont_know():
    from types import SimpleNamespace as N
    d = N(next_question="Tell me about your Java project.", model_copy=lambda update: N(**{**{"next_question": "Tell me about your Java project."}, **update}))
    assert brain._with_kind_line(d, "I built a Spring Boot service", 2).next_question == "Tell me about your Java project."
    said = brain._with_kind_line(d, "I don't know", 2).next_question
    assert said.endswith("Tell me about your Java project.") and said != "Tell me about your Java project."
    d2 = N(next_question="No worries, tell me about your Java project.", model_copy=lambda update: N(**update))
    assert brain._with_kind_line(d2, "I don't know", 2).next_question == "No worries, tell me about your Java project."      # already kind: unchanged


# --- evaluator provider override (the final evaluator's ONE harder call can go to a different, more capable
# provider than the fast per-turn brain -- see interview_llm_provider.get_evaluator_llm_provider()) ---------------

@_isolated
def test_evaluator_provider_defaults_to_the_same_as_the_turn_brain_when_unset():
    os.environ.pop("AI_INTERVIEW_EVALUATOR_PROVIDER", None)
    os.environ["AI_INTERVIEW_PROVIDER"] = "groq"
    os.environ.pop("INTERVIEW_REALTIME_PROVIDER", None)
    assert prov.get_evaluator_llm_provider().name == "groq"


@_isolated
def test_evaluator_provider_can_be_pinned_independently_of_the_turn_brain():
    os.environ["AI_INTERVIEW_PROVIDER"] = "gemini"
    os.environ.pop("INTERVIEW_REALTIME_PROVIDER", None)
    os.environ["AI_INTERVIEW_EVALUATOR_PROVIDER"] = "groq"
    assert prov.get_interview_llm_provider().name == "gemini"          # per-turn brain: unaffected by the override
    assert prov.get_evaluator_llm_provider().name == "groq"            # the one harder call: a more capable provider


@_isolated
def test_evaluator_provider_ignores_the_realtime_pipeline_pin():
    """INTERVIEW_REALTIME_PROVIDER pins the per-turn brain only -- an explicit evaluator override must not be
    silently re-pinned back to whatever the realtime pipeline chose."""

    os.environ["INTERVIEW_REALTIME_PROVIDER"] = "groq_pipeline"       # pins the turn brain to groq
    os.environ["AI_INTERVIEW_EVALUATOR_PROVIDER"] = "gemini"
    assert prov.get_interview_llm_provider().name == "groq"
    assert prov.get_evaluator_llm_provider().name == "gemini"
