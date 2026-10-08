"""
Regression tests proving tests/db_safety.py cannot reproduce the
Task 5C Step 6 incident (all 6 pre-existing programming_submissions
rows deleted by a self-subtracting snapshot).

These tests run ENTIRELY against a throwaway, isolated, in-memory
SQLite database created fresh in-process (sqlite:///:memory:) --
never D:\\jobpilot-ai\\jobpilot.db, and never any file on disk.
"""

import pytest
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from tests.db_safety import (
    CreatedRowTracker,
    UnsafeCleanupError,
    safe_diff_cleanup,
    snapshot_ids,
)

Base = declarative_base()


class Widget(Base):
    __tablename__ = "widgets"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _seed_existing_rows(db, count):
    for i in range(count):
        db.add(Widget(name=f"existing-{i}"))
    db.commit()


# ------------------------------------------------------------
# A. Existing rows survive cleanup (CreatedRowTracker)
# ------------------------------------------------------------

def test_existing_rows_survive_tracker_cleanup(db):
    _seed_existing_rows(db, 6)
    existing_ids_before = snapshot_ids(db, Widget)
    assert len(existing_ids_before) == 6

    tracker = CreatedRowTracker(db)
    # deliberately create nothing new, cleanup should be a no-op
    tracker.cleanup()

    assert snapshot_ids(db, Widget) == existing_ids_before


def test_existing_rows_survive_safe_diff_cleanup(db):
    _seed_existing_rows(db, 6)
    ids_before = snapshot_ids(db, Widget)

    deleted = safe_diff_cleanup(
        db, Widget, ids_before, expected_before_count=6, label="test",
    )

    assert deleted == []
    assert snapshot_ids(db, Widget) == ids_before
    assert db.query(Widget).count() == 6


# ------------------------------------------------------------
# B. Newly-created rows are cleaned
# ------------------------------------------------------------

def test_created_rows_are_cleaned_by_tracker(db):
    _seed_existing_rows(db, 3)
    existing_ids = snapshot_ids(db, Widget)

    tracker = CreatedRowTracker(db)
    new_row = Widget(name="new-one")
    db.add(new_row)
    db.flush()
    tracker.track(Widget, new_row.id)

    deleted = tracker.cleanup()

    assert deleted == [("Widget", new_row.id)]
    assert snapshot_ids(db, Widget) == existing_ids
    assert db.query(Widget).count() == 3


def test_created_rows_are_cleaned_by_safe_diff(db):
    _seed_existing_rows(db, 3)
    ids_before = snapshot_ids(db, Widget)

    new_row = Widget(name="new-one")
    db.add(new_row)
    db.commit()

    deleted = safe_diff_cleanup(
        db, Widget, ids_before, expected_before_count=3, label="test",
    )

    assert deleted == [new_row.id]
    assert snapshot_ids(db, Widget) == ids_before
    assert db.query(Widget).count() == 3


# ------------------------------------------------------------
# C. Empty created-ID set deletes nothing
# ------------------------------------------------------------

def test_empty_tracker_deletes_nothing_even_with_existing_rows(db):
    _seed_existing_rows(db, 6)
    ids_before = snapshot_ids(db, Widget)

    tracker = CreatedRowTracker(db)
    assert tracker.tracked_count == 0

    deleted = tracker.cleanup()

    assert deleted == []
    assert snapshot_ids(db, Widget) == ids_before


# ------------------------------------------------------------
# D. Snapshot mismatch fails closed
# ------------------------------------------------------------

def test_safe_diff_cleanup_refuses_on_count_mismatch(db):
    _seed_existing_rows(db, 6)
    ids_before = snapshot_ids(db, Widget)

    with pytest.raises(UnsafeCleanupError):
        # caller claims they expected 0 existing rows, but the
        # snapshot actually has 6 -- must refuse, not "trust" either
        # value blindly.
        safe_diff_cleanup(
            db, Widget, ids_before, expected_before_count=0, label="test",
        )

    # nothing was deleted
    assert db.query(Widget).count() == 6


def test_safe_diff_cleanup_refuses_non_set_before(db):
    _seed_existing_rows(db, 2)

    with pytest.raises(UnsafeCleanupError):
        safe_diff_cleanup(
            db, Widget, [1, 2], expected_before_count=2, label="test",
        )

    assert db.query(Widget).count() == 2


# ------------------------------------------------------------
# E. Existing application assessment records cannot be deleted by
#    test teardown -- this is the EXACT incident reproduced safely
#    against a throwaway table, proving the new helper refuses it.
# ------------------------------------------------------------

def test_self_subtracting_snapshot_bug_is_refused_not_executed(db):
    _seed_existing_rows(db, 6)  # stand-in for the 6 real programming_submissions rows

    # Reproduce the ORIGINAL bug's exact shape: a snapshot that is
    # wrongly empty because it was computed as `X - X` instead of
    # being captured before the test ran.
    buggy_before_ids = snapshot_ids(db, Widget) - snapshot_ids(db, Widget)
    assert buggy_before_ids == frozenset()  # confirms the bug shape reproduces

    with pytest.raises(UnsafeCleanupError):
        safe_diff_cleanup(
            db,
            Widget,
            buggy_before_ids,
            expected_before_count=0,  # a test that (wrongly) believes the table started empty
            label="reproduces-original-incident",
        )

    # The critical assertion: all 6 pre-existing rows are still there.
    assert db.query(Widget).count() == 6


def test_tracker_cannot_touch_untracked_existing_rows(db):
    _seed_existing_rows(db, 6)
    ids_before = snapshot_ids(db, Widget)

    tracker = CreatedRowTracker(db)
    new_row = Widget(name="genuinely-new")
    db.add(new_row)
    db.flush()
    tracker.track(Widget, new_row.id)

    tracker.cleanup()

    # only the tracked row is gone; all 6 pre-existing rows remain
    assert snapshot_ids(db, Widget) == ids_before
    assert db.query(Widget).count() == 6


# ------------------------------------------------------------
# F. A test failure midway through creation does not cause
#    pre-existing rows to be deleted
# ------------------------------------------------------------

def test_partial_failure_only_cleans_up_what_was_tracked(db):
    _seed_existing_rows(db, 4)
    ids_before = snapshot_ids(db, Widget)

    tracker = CreatedRowTracker(db)

    row1 = Widget(name="step-1")
    db.add(row1)
    db.flush()
    tracker.track(Widget, row1.id)

    row2 = Widget(name="step-2")
    db.add(row2)
    db.flush()
    tracker.track(Widget, row2.id)

    # simulate the test crashing before creating a 3rd row / before
    # tracking anything further
    try:
        raise RuntimeError("simulated mid-test failure")
    except RuntimeError:
        pass
    finally:
        deleted = tracker.cleanup()

    assert sorted(deleted) == sorted([("Widget", row1.id), ("Widget", row2.id)])
    assert snapshot_ids(db, Widget) == ids_before
    assert db.query(Widget).count() == 4


def test_track_refuses_none_id(db):
    tracker = CreatedRowTracker(db)
    with pytest.raises(UnsafeCleanupError):
        tracker.track(Widget, None)
