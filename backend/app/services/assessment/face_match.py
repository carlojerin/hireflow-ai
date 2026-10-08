"""
Face match: is the person in the later camera photos the same person as in the reference photo (taken before the self introduction)?

This is a RECRUITER AID, not an identity verification. A vision model compares two small webcam photos and answers "same", "different" or "unclear" with a
confidence and one sentence of reason. Nothing biometric is stored (no embedding), only that answer. A person always makes the decision; the report says so.
Different lighting, glasses, a hand on the chin or a different angle can make the model say "unclear": that is why "unclear" is a review flag, not an accusation.
"""

import json
import logging
import os
import time

from sqlalchemy.orm import Session

from backend.app.models.assessment_snapshot import AssessmentSnapshot
from backend.app.models.face_match_result import FaceMatchResult

logger = logging.getLogger("jobpilot.face_match")

SNAPSHOT_STORAGE_ROOT = os.path.join("data", "assessment_snapshots")
MAX_PHOTOS_COMPARED = 7                    # stage-start photos first, then a couple of periodic ones: bounded cost per candidate
SAME_CONFIDENCE = 0.6                      # "same" below this is treated as unclear
DIFFERENT_CONFIDENCE = 0.7                 # "different" at or above this is a mismatch worth the recruiter's attention
VISION_TIMEOUT_MS = 20000

_SYSTEM = """You help a recruiter review webcam photos taken during an online assessment. You are shown TWO photos: the first is the candidate's reference photo, the second is a \
later photo from the same assessment. Decide whether both show the same person. Compare only stable facial features (face shape, eyes, nose, mouth, eyebrows, hairline, ears, facial \
hair). Ignore clothing, background, lighting, image quality, glasses on or off, a hand near the face, and small changes of angle or expression. Do NOT say who anyone is. If either photo \
has no clear face, is too dark, blurred, covered or turned away, answer "unclear". If you cannot tell, answer "unclear": never guess.
Return ONLY this JSON: {"verdict": "same" | "different" | "unclear", "confidence": 0.0-1.0, "reason": "one short sentence"}"""


def _models() -> list[str]:
    from backend.app.services.sjt.practical_design import vision_models

    return vision_models()


def _read_snapshot(row: AssessmentSnapshot) -> bytes | None:

    root = os.path.realpath(SNAPSHOT_STORAGE_ROOT)
    path = os.path.realpath(os.path.join(SNAPSHOT_STORAGE_ROOT, row.file_path))

    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        return None

    with open(path, "rb") as handle:
        return handle.read()


def compare_pair(reference_jpeg: bytes, other_jpeg: bytes) -> dict:
    """{"verdict", "confidence", "reason", "model"} for two JPEGs. Raises when no vision model could answer (the caller stores nothing and tries again later)."""

    models = _models()

    if not models or not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("no vision model is available for the face check")

    from google import genai
    from google.genai import types

    last_error = None

    for model in models:

        started = time.perf_counter()
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"), http_options=types.HttpOptions(timeout=VISION_TIMEOUT_MS))     # kept in a variable: a collected client closes mid-call

        try:
            response = client.models.generate_content(
                model=model,
                contents=[
                    "Photo 1, the reference:", types.Part.from_bytes(data=reference_jpeg, mime_type="image/jpeg"),
                    "Photo 2, the later photo:", types.Part.from_bytes(data=other_jpeg, mime_type="image/jpeg"),
                ],
                config=types.GenerateContentConfig(system_instruction=_SYSTEM, response_mime_type="application/json", temperature=0.0, max_output_tokens=2000),
            )
            parsed = json.loads(response.text)

            verdict = str(parsed.get("verdict", "")).strip().lower()
            if verdict not in ("same", "different", "unclear"):
                raise ValueError("unreadable verdict")

            try:
                from backend.app.services.llm_usage import record_usage
                record_usage("gemini", model, ok=True, seconds=time.perf_counter() - started)
            except Exception:  # noqa: BLE001
                pass

            return {
                "verdict": verdict,
                "confidence": max(0.0, min(1.0, float(parsed.get("confidence", 0.0) or 0.0))),
                "reason": str(parsed.get("reason", ""))[:300],
                "model": model,
            }

        except Exception as exc:  # noqa: BLE001 - try the next model
            last_error = exc
            try:
                from backend.app.services.llm_usage import note_gemini_error, record_usage
                note_gemini_error(exc, model)
                record_usage("gemini", model, ok=False, rate_limited="429" in str(exc) or "quota" in str(exc).lower())
            except Exception:  # noqa: BLE001
                pass

        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    raise RuntimeError(f"the face check could not be completed ({type(last_error).__name__ if last_error else 'no model'})")


def pick_photos(rows: list[AssessmentSnapshot]) -> tuple[AssessmentSnapshot | None, list[AssessmentSnapshot]]:
    """(the reference photo, the later photos to compare): the face at the start of each later stage first, then a few periodic photos."""

    starts = [r for r in rows if r.reason == "identity_reference"]
    reference = next((r for r in starts if r.assessment_type == "self_intro"), starts[0] if starts else None)

    if reference is None:
        return None, []

    chosen, seen = [], set()

    for row in starts:
        if row.id != reference.id and row.assessment_type not in seen:
            seen.add(row.assessment_type)
            chosen.append(row)

    periodic = [r for r in rows if r.reason == "periodic" and r.id != reference.id]

    if periodic:
        step = max(1, len(periodic) // 2)
        chosen.extend(periodic[::step][:2])

    return reference, chosen[:MAX_PHOTOS_COMPARED]


def summarise(results: list[dict]) -> dict:
    """consistent | review | mismatch | not_run, from the stored per-photo answers."""

    if not results:
        return {"status": "not_run", "same": 0, "different": 0, "unclear": 0, "headline": "The face check has not run yet."}

    counts = {"same": 0, "different": 0, "unclear": 0}
    mismatch = False

    for r in results:
        verdict = r["verdict"]
        if verdict == "same" and r["confidence"] < SAME_CONFIDENCE:
            verdict = "unclear"
        counts[verdict] += 1
        if verdict == "different" and r["confidence"] >= DIFFERENT_CONFIDENCE:
            mismatch = True

    if mismatch:
        status, headline = "mismatch", "A later photo looks like a different person. Please look at the photos yourself."
    elif counts["different"] or counts["unclear"]:
        status, headline = "review", "Some photos could not be matched with confidence (lighting, angle or a covered face are common reasons). Please check them."
    else:
        status, headline = "consistent", "All compared photos look like the same person."

    return {"status": status, "headline": headline, **counts}


def stored_results(db: Session, application_id: int) -> list[dict]:

    rows = db.query(FaceMatchResult).filter(FaceMatchResult.application_id == application_id).order_by(FaceMatchResult.id.asc()).all()

    return [
        {"snapshot_id": r.snapshot_id, "stage": r.stage, "verdict": r.verdict, "confidence": round(r.confidence, 2), "reason": r.reason, "model": r.model}
        for r in rows
    ]


def run_for_application(db: Session, application_id: int, compare=None) -> dict:
    """Compare every not-yet-compared photo with the reference and store the answers. Photos already compared are never sent again.

    `compare` is the pair comparer (a fake in tests). A photo whose comparison fails is skipped and will be tried again next time."""

    compare = compare or compare_pair

    rows = (
        db.query(AssessmentSnapshot)
        .filter(AssessmentSnapshot.application_id == application_id)
        .order_by(AssessmentSnapshot.created_at.asc(), AssessmentSnapshot.id.asc())
        .all()
    )

    reference, later = pick_photos(rows)

    if reference is None or not later:
        return {"photos_available": len(later) + (1 if reference else 0), "ran": 0, "failed": 0}

    already = {r.snapshot_id for r in db.query(FaceMatchResult).filter(FaceMatchResult.application_id == application_id).all()}
    reference_bytes = _read_snapshot(reference)

    ran = failed = 0

    if reference_bytes is None:
        return {"photos_available": len(later) + 1, "ran": 0, "failed": len(later)}

    todo = []

    for row in later:

        if row.id in already:
            continue

        other = _read_snapshot(row)

        if other is None:
            failed += 1
            continue

        todo.append((row, other))

    def _one(item):
        row, other = item
        try:
            return row, compare(reference_bytes, other), None
        except Exception as exc:  # noqa: BLE001
            return row, None, exc

    # a few photos at a time: each answer takes several seconds, so one after another would make the recruiter wait for the whole list
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(_one, todo))

    for row, answer, error in outcomes:

        if answer is None:
            logger.warning("face check for snapshot %s failed: %s", row.id, error)
            failed += 1
            continue

        db.add(FaceMatchResult(
            application_id=application_id, snapshot_id=row.id, reference_snapshot_id=reference.id, stage=row.assessment_type,
            verdict=answer["verdict"], confidence=answer["confidence"], reason=answer.get("reason"), model=answer.get("model"),
        ))
        db.commit()
        ran += 1

    return {"photos_available": len(later) + 1, "ran": ran, "failed": failed}
