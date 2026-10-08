import copy


# ============================================================
# JOBPILOT AI - APTITUDE QUESTION BANK
#
# A fixed, deterministic set of aptitude questions. No AI
# generation — this is intentionally a static bank for a
# reliable, predictable first implementation.
#
# Each question has a stable integer id, a category, the
# question text, a list of options, and correct_answer (the
# exact string of the correct option). correct_answer must
# never be sent to the candidate before they submit the test.
# ============================================================

APTITUDE_QUESTION_BANK = [

    {
        "id": 1,
        "category": "quantitative_aptitude",
        "question": "What is 15% of 200?",
        "options": ["20", "25", "30", "35"],
        "correct_answer": "30",
    },

    {
        "id": 2,
        "category": "quantitative_aptitude",
        "question": (
            "A train travels 60 km in 1.5 hours. What is its "
            "average speed in km/h?"
        ),
        "options": ["30", "40", "45", "50"],
        "correct_answer": "40",
    },

    {
        "id": 3,
        "category": "quantitative_aptitude",
        "question": (
            "A shopkeeper sells an item for $120 after making "
            "a 20% profit. What was the cost price?"
        ),
        "options": ["$96", "$100", "$104", "$110"],
        "correct_answer": "$100",
    },

    {
        "id": 4,
        "category": "logical_reasoning",
        "question": (
            "Look at this number series: 2, 4, 8, 16, ... "
            "What number should come next?"
        ),
        "options": ["18", "24", "30", "32"],
        "correct_answer": "32",
    },

    {
        "id": 5,
        "category": "logical_reasoning",
        "question": (
            "All engineers are logical thinkers. Some logical "
            "thinkers are managers. Which conclusion is valid?"
        ),
        "options": [
            "All engineers are managers",
            "Some engineers may be managers",
            "No engineers are managers",
            "All managers are engineers",
        ],
        "correct_answer": "Some engineers may be managers",
    },

    {
        "id": 6,
        "category": "logical_reasoning",
        "question": (
            "Find the odd one out: Apple, Banana, Carrot, Mango"
        ),
        "options": ["Apple", "Banana", "Carrot", "Mango"],
        "correct_answer": "Carrot",
    },

    {
        "id": 7,
        "category": "verbal_reasoning",
        "question": (
            "Choose the word that is opposite in meaning to "
            "'ABUNDANT'."
        ),
        "options": ["Plentiful", "Scarce", "Generous", "Massive"],
        "correct_answer": "Scarce",
    },

    {
        "id": 8,
        "category": "verbal_reasoning",
        "question": (
            "Choose the word most similar in meaning to "
            "'METICULOUS'."
        ),
        "options": ["Careless", "Careful", "Quick", "Lazy"],
        "correct_answer": "Careful",
    },

    {
        "id": 9,
        "category": "analytical_ability",
        "question": (
            "A is taller than B. C is shorter than B. Who is "
            "the shortest?"
        ),
        "options": [
            "A",
            "B",
            "C",
            "Cannot be determined",
        ],
        "correct_answer": "C",
    },

    {
        "id": 10,
        "category": "analytical_ability",
        "question": (
            "If it takes 5 machines 5 minutes to make 5 "
            "widgets, how long would it take 100 machines to "
            "make 100 widgets?"
        ),
        "options": ["5 minutes", "20 minutes", "100 minutes", "500 minutes"],
        "correct_answer": "5 minutes",
    },

]


def get_aptitude_questions() -> list[dict]:
    """
    Return a fresh deep copy of the fixed aptitude question
    bank, so callers can safely mutate/snapshot it (e.g. into
    an AptitudeTest.questions JSON column) without risk of
    mutating the shared module-level list.
    """

    return copy.deepcopy(
        APTITUDE_QUESTION_BANK
    )
