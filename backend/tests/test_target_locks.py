"""
Tests for DistributedLockRepository (target_locks) — the per-target
remediation lease that prevents two incidents from running mutating steps
against the same resource concurrently.
"""

import pytest
from datetime import datetime, timedelta
from uuid import uuid4
from sqlalchemy import text

from agentic_os.db.repositories import DistributedLockRepository
from agentic_os.db.models import TargetLockModel


@pytest.fixture(autouse=True)
def clean_target_locks(db):
    """Truncate target_locks before each test — it has no FK to workflow_states
    so clean_workflow_states' CASCADE truncate (conftest.py) doesn't reach it."""
    db.execute(text("DELETE FROM target_locks"))
    db.commit()
    yield


def test_acquire_succeeds_when_unlocked(db):
    repo = DistributedLockRepository(db)
    incident_id = uuid4()

    acquired = repo.acquire("host-1", incident_id, ttl_seconds=900)

    assert acquired is True
    rows = db.query(TargetLockModel).filter(TargetLockModel.target_id == "host-1").all()
    assert len(rows) == 1
    assert rows[0].incident_id == incident_id


def test_acquire_fails_when_already_locked(db):
    repo = DistributedLockRepository(db)
    incident_a = uuid4()
    incident_b = uuid4()

    first = repo.acquire("host-2", incident_a, ttl_seconds=900)
    second = repo.acquire("host-2", incident_b, ttl_seconds=900)

    assert first is True
    assert second is False
    rows = db.query(TargetLockModel).filter(TargetLockModel.target_id == "host-2").all()
    assert len(rows) == 1
    assert rows[0].incident_id == incident_a


def test_release_only_removes_own_lease(db):
    repo = DistributedLockRepository(db)
    incident_a = uuid4()
    incident_b = uuid4()

    repo.acquire("host-3", incident_a, ttl_seconds=900)

    # Wrong incident — must be a no-op, not allowed to release someone else's lease.
    repo.release("host-3", incident_b)
    rows = db.query(TargetLockModel).filter(TargetLockModel.target_id == "host-3").all()
    assert len(rows) == 1

    # Correct incident — releases it.
    repo.release("host-3", incident_a)
    rows = db.query(TargetLockModel).filter(TargetLockModel.target_id == "host-3").all()
    assert len(rows) == 0


def test_delete_expired_removes_only_expired(db):
    repo = DistributedLockRepository(db)
    now = datetime.utcnow()

    db.add(TargetLockModel(
        target_id="host-expired", incident_id=uuid4(),
        acquired_at=now - timedelta(minutes=30), expires_at=now - timedelta(minutes=15),
    ))
    db.add(TargetLockModel(
        target_id="host-active", incident_id=uuid4(),
        acquired_at=now, expires_at=now + timedelta(minutes=15),
    ))
    db.commit()

    deleted = repo.delete_expired()

    assert deleted == 1
    remaining = {row.target_id for row in db.query(TargetLockModel).all()}
    assert remaining == {"host-active"}


def test_renew_extends_expiry_for_current_holder(db):
    repo = DistributedLockRepository(db)
    incident_id = uuid4()

    repo.acquire("host-5", incident_id, ttl_seconds=900)
    original_expiry = db.query(TargetLockModel).filter(
        TargetLockModel.target_id == "host-5"
    ).one().expires_at

    renewed = repo.renew("host-5", incident_id, ttl_seconds=900)

    assert renewed is True
    new_expiry = db.query(TargetLockModel).filter(
        TargetLockModel.target_id == "host-5"
    ).one().expires_at
    assert new_expiry > original_expiry


def test_renew_fails_for_wrong_incident(db):
    repo = DistributedLockRepository(db)
    incident_a = uuid4()
    incident_b = uuid4()

    repo.acquire("host-6", incident_a, ttl_seconds=900)

    renewed = repo.renew("host-6", incident_b, ttl_seconds=900)

    assert renewed is False


def test_renew_fails_when_lease_does_not_exist(db):
    repo = DistributedLockRepository(db)

    renewed = repo.renew("host-not-locked", uuid4(), ttl_seconds=900)

    assert renewed is False


def test_acquire_succeeds_again_after_release(db):
    """A target that was locked and released can be locked again by a new incident."""
    repo = DistributedLockRepository(db)
    incident_a = uuid4()
    incident_b = uuid4()

    repo.acquire("host-4", incident_a, ttl_seconds=900)
    repo.release("host-4", incident_a)

    acquired = repo.acquire("host-4", incident_b, ttl_seconds=900)
    assert acquired is True
