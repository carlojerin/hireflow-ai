import copy


# ============================================================
# JOBPILOT AI - AI INTERVIEW QUESTION BANK (fallback)
#
# A fixed, deterministic set of 5 interview questions covering
# the 5 recommended categories, used whenever Gemini generation
# fails or produces invalid output. Mirrors
# services/assessment/programming_question_bank.py's role.
#
# expected_answer_points/follow_up_focus are never returned to
# a candidate — see sanitize_interview_question() in
# api/routes/interview_tests.py.
# ============================================================

INTERVIEW_QUESTION_BANK = [

    {
        "question_id": 1,
        "question": (
            "Walk me through a recent project you worked on. What "
            "was your specific role, what technical decisions did "
            "you make, and what was the outcome?"
        ),
        "category": "experience",
        "difficulty": "easy",
        "primary_skill": "Communication",
        "what_it_assesses": (
            "Ability to clearly explain your own contributions and "
            "technical decisions on real work."
        ),
        "expected_answer_points": [
            "Names a specific, real project (not a vague generality)",
            "Describes a concrete personal contribution",
            "Mentions at least one real technical decision or trade-off",
            "States a clear outcome or result",
        ],
        "follow_up_focus": (
            "If the answer is vague or generic, the next technical "
            "question should stay close to fundamentals rather than "
            "assuming deep hands-on experience."
        ),
    },

    {
        "question_id": 2,
        "question": (
            "Explain how you would design a simple REST API endpoint "
            "that accepts a list of items and returns only the ones "
            "matching a given filter. What would the request and "
            "response look like, and what could go wrong?"
        ),
        "category": "technical_skill",
        "difficulty": "medium",
        "primary_skill": "API design",
        "what_it_assesses": (
            "Practical understanding of REST API request/response "
            "design and basic error handling."
        ),
        "expected_answer_points": [
            "Describes a sensible HTTP method and URL/query shape",
            "Describes the response shape (status code, body)",
            "Mentions at least one failure/edge case (empty result, invalid filter, etc.)",
            "Shows awareness of validating input",
        ],
        "follow_up_focus": (
            "A strong answer here justifies a deeper architecture "
            "question later; a weak one justifies a more fundamentals"
            "-focused later question instead."
        ),
    },

    {
        "question_id": 3,
        "question": (
            "Suppose a feature you built is required by this role's "
            "job description works correctly for most users but is "
            "very slow for a small number of them. How would you "
            "investigate and address this?"
        ),
        "category": "job_scenario",
        "difficulty": "medium",
        "primary_skill": "Problem solving",
        "what_it_assesses": (
            "Practical debugging and performance-reasoning approach "
            "relevant to this job's responsibilities."
        ),
        "expected_answer_points": [
            "Proposes a concrete way to investigate (logging, profiling, reproducing)",
            "Considers data-dependent causes (large inputs, N+1 queries, etc.)",
            "Proposes a validation/measurement step before declaring it fixed",
        ],
        "follow_up_focus": (
            "Depth of reasoning here should inform whether the "
            "behavioral question probes collaboration under pressure "
            "or individual ownership."
        ),
    },

    {
        "question_id": 4,
        "question": (
            "Pick a skill from this job's requirements that you have "
            "the least hands-on experience with. What do you "
            "currently understand about it, and how would you "
            "approach learning it well enough to be productive "
            "quickly?"
        ),
        "category": "skill_gap",
        "difficulty": "medium",
        "primary_skill": "Self-awareness / learning approach",
        "what_it_assesses": (
            "Honesty about current skill gaps and a realistic plan "
            "for closing them."
        ),
        "expected_answer_points": [
            "Honestly identifies a real gap rather than deflecting",
            "Shows some existing conceptual understanding, even if shallow",
            "Describes a credible, specific learning approach (not just 'I'd read docs')",
        ],
        "follow_up_focus": (
            "None — this question is intentionally self-contained."
        ),
    },

    {
        "question_id": 5,
        "question": (
            "Tell me about a time you disagreed with a teammate or "
            "reviewer about a technical approach. How did you handle "
            "it, and what was the result?"
        ),
        "category": "behavioral",
        "difficulty": "easy",
        "primary_skill": "Collaboration",
        "what_it_assesses": (
            "Communication and collaboration under professional "
            "disagreement."
        ),
        "expected_answer_points": [
            "Describes a real, specific disagreement",
            "Explains their own reasoning, not just the outcome",
            "Shows respect for the other person's perspective",
            "States a concrete resolution",
        ],
        "follow_up_focus": (
            "None — this is the final question of the interview."
        ),
    },

]


def get_interview_questions() -> list[dict]:
    """
    Return a fresh deep copy of the fixed interview question
    bank, so callers can safely mutate/snapshot it (e.g. into an
    InterviewTest.questions JSON column) without risk of
    mutating the shared module-level list.
    """

    return copy.deepcopy(
        INTERVIEW_QUESTION_BANK
    )
