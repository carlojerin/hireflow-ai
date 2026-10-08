"""
Safe cleanup utilities for ad hoc test/verification scripts that
create temporary rows against a real (non-production, but shared)
database and must guarantee two things:

1. Rows that existed before the test are NEVER deleted.
2. Only rows this specific test actually created are ever deleted.

Background: a Task 5C Step 6 live-integration test used inline
before/after snapshot cleanup logic equivalent to

    before_ids = snapshot_ids(Model) - snapshot_ids(Model)   # always empty set
    after_ids = snapshot_ids(Model)
    to_delete = after_ids - before_ids                       # == after_ids

which silently treated "no snapshot was ever actually captured
before the test ran" the same as "the table was empty before the
test ran" -- and deleted all 6 pre-existing rows of
programming_submissions, including data belonging to a real
candidate application.

This module exists so no future script has to hand-roll that logic
again. CreatedRowTracker (explicit ID tracking) is the preferred,
safest mechanism -- it can never delete a pre-existing row, because
it only ever knows about rows it itself was told to track after
creating them. safe_diff_cleanup() is provided for the rarer case
where explicit tracking genuinely isn't practical, and is
deliberately fail-closed: it refuses (raises UnsafeCleanupError)
rather than guessing whenever the snapshot evidence is incomplete
or ambiguous.
"""

from __future__ import annotations


class UnsafeCleanupError(Exception):
    """
    Raised whenever a cleanup helper cannot PROVE which rows are
    safe to delete. The correct response to this exception is to
    fix the calling test, never to silently proceed.
    """


class CreatedRowTracker:
    """
    Tracks the exact (model, primary_key) pairs a test creates, and
    deletes ONLY those exact rows on cleanup -- never anything
    inferred from a table-wide snapshot.

    Usage:
        tracker = CreatedRowTracker(db)
        row = Model(...)
        db.add(row)
        db.flush()
        tracker.track(Model, row.id)
        ...
        tracker.cleanup()

    Safe by construction:
      - An empty tracker (no track() calls happened, e.g. because
        the test failed before creating anything) deletes nothing.
      - A partially-populated tracker (the test failed halfway
        through creating several rows) deletes only the rows that
        were actually tracked before the failure -- never more.
      - track() refuses a None id outright, so a not-yet-flushed
        row can never be tracked and then accidentally resolved
        against the wrong (later-reused) primary key.
    """

    def __init__(self, db):
        self._db = db
        self._created: list[tuple[type, object]] = []

    def track(self, model: type, row_id) -> None:
        if row_id is None:
            raise UnsafeCleanupError(
                f"Cannot track a None id for {model.__name__} -- "
                "flush/commit the row first so its real primary "
                "key is known before tracking it."
            )
        self._created.append((model, row_id))

    @property
    def tracked_count(self) -> int:
        return len(self._created)

    def cleanup(self) -> list[tuple[str, object]]:
        """
        Deletes exactly the tracked (model, id) rows, in reverse
        creation order (so children are removed before parents when
        the caller tracked them in creation order). Idempotent: a
        second call is a no-op since the tracked list is cleared
        after the first cleanup. Returns the list of (model_name,
        id) pairs actually deleted, for the caller to log/assert on.
        """

        deleted: list[tuple[str, object]] = []

        for model, row_id in reversed(self._created):
            row = self._db.query(model).filter(model.id == row_id).first()
            if row is not None:
                self._db.delete(row)
                deleted.append((model.__name__, row_id))

        self._db.commit()
        self._created.clear()

        return deleted


def snapshot_ids(db, model) -> frozenset:
    """
    Read-only helper: the current set of primary-key ids for a
    model. Callers must capture this BEFORE any test mutation and
    hold onto the returned value -- never recompute it against
    itself (that is exactly the bug this module exists to prevent).
    """

    return frozenset(row.id for row in db.query(model.id).all())


def safe_diff_cleanup(
    db,
    model: type,
    ids_before: frozenset,
    expected_before_count: int,
    label: str = "",
) -> list[object]:
    """
    Fail-closed snapshot-based cleanup, for use ONLY when explicit
    ID tracking (CreatedRowTracker, preferred) genuinely is not
    practical. Deletes rows present now but ABSENT from ids_before
    -- i.e. genuinely created since the snapshot was taken.

    Refuses to delete anything and raises UnsafeCleanupError
    instead when the evidence is ambiguous:

      - ids_before is not a frozenset/set (guards against passing
        a live expression like `a - a` by mistake -- such an
        expression evaluates to a plain, unfrozen set literal only
        if the caller wrote it as one; this is a best-effort type
        guard, not a substitute for rule 9's "prefer explicit ID
        tracking").
      - expected_before_count does not match len(ids_before) --
        this is the load-bearing check: the caller must state, as
        an independent fact, how many rows they expect to have
        found before the test ran. If a snapshot bug makes
        ids_before wrongly empty, the caller's independently-stated
        expected_before_count will not match an actually-nonempty
        table, and cleanup refuses rather than deleting everything.
      - the computed to_delete set equals the entire current table
        (i.e. every single existing row would be deleted) while
        ids_before is empty -- refused unless the caller explicitly
        confirms the table really was empty via
        confirm_table_was_empty=True.
    """

    if not isinstance(ids_before, (frozenset, set)):
        raise UnsafeCleanupError(
            f"[{label}] ids_before must be a frozenset/set captured "
            "before the test ran, not a value computed now."
        )

    if len(ids_before) != expected_before_count:
        raise UnsafeCleanupError(
            f"[{label}] Refusing to clean up {model.__name__}: "
            f"ids_before has {len(ids_before)} entries but the "
            f"caller expected {expected_before_count}. This "
            "mismatch means the snapshot is not trustworthy -- "
            "fix the test instead of cleaning up."
        )

    ids_after = snapshot_ids(db, model)
    to_delete = ids_after - ids_before

    if not to_delete:
        return []

    if len(ids_before) == 0 and to_delete == ids_after and len(ids_after) > 0:
        raise UnsafeCleanupError(
            f"[{label}] Refusing to clean up {model.__name__}: "
            f"ids_before is empty and to_delete equals the ENTIRE "
            f"current table ({len(ids_after)} rows). This is "
            "exactly the failure mode that previously deleted all "
            "pre-existing programming_submissions rows. If the "
            "table genuinely was empty before this test, use "
            "CreatedRowTracker instead so deletion is based on "
            "positive proof of creation, not a table-wide diff."
        )

    deleted_ids = sorted(to_delete)

    for row_id in deleted_ids:
        row = db.query(model).filter(model.id == row_id).first()
        if row is not None:
            db.delete(row)

    db.commit()

    return deleted_ids
