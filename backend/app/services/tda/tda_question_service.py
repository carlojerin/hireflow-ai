import random

from backend.app.services.tda.question_bank import get_tda_question_bank

SOURCE_FALLBACK = "fallback"  # curated bank — see question_bank.py's own note on why there's no AI tier yet

DEFAULT_TOTAL_QUESTIONS = 10


def build_tda_questions(domain: str, total_questions: int = DEFAULT_TOTAL_QUESTIONS) -> tuple[list[dict], str]:
    """
    The ONE place Technical Domain Assessment questions are produced,
    mirroring build_sjt_questions()'s role exactly -- except filtered
    down to the candidate's own domain first (a Salesforce candidate
    must never see SAP or embedded-firmware questions).

    Bank-only for now, same reasoning as SJT: a curated, validated
    domain bank is the real industry pattern (TestGorilla/iMocha/
    Vervoe/Mettl all ship fixed domain question libraries); AI-
    generating fresh domain questions per candidate is a future
    enhancement, not a day-one requirement. Each candidate still
    gets a genuinely randomized SUBSET and ORDER.
    """

    bank = [q for q in get_tda_question_bank() if q["category"] == domain]
    count = min(total_questions, len(bank))

    if count == 0:
        return [], SOURCE_FALLBACK

    selected = random.sample(bank, count)
    random.shuffle(selected)

    return selected, SOURCE_FALLBACK
