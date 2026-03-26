"""
tests/test_repository.py
─────────────────────────
Tests for Repository pattern.
Uses InMemoryUserRepository — zero DB, instant.
"""

import pytest
from datetime import datetime
from fraudshield_core.models import User, RiskTier, KYCStatus
from fraud_api.repository.inmemory_repo import InMemoryUserRepository


def make_user(user_id: str = "u_001",
              risk_tier: str = "low") -> User:
    return User(
        id=user_id, email=f"{user_id}@test.com", phone=None,
        risk_tier=RiskTier(risk_tier), kyc_status=KYCStatus.VERIFIED,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow()
    )


class TestInMemoryUserRepository:

    def test_save_and_get_by_id(self):
        repo = InMemoryUserRepository()
        user = make_user("u_001")
        repo.save(user)
        fetched = repo.get_by_id("u_001")
        assert fetched is not None
        assert fetched.id == "u_001"
        assert fetched.email == "u_001@test.com"

    def test_get_by_id_returns_none_for_missing(self):
        repo = InMemoryUserRepository()
        assert repo.get_by_id("nonexistent") is None

    def test_get_by_email(self):
        repo = InMemoryUserRepository()
        user = make_user("u_002")
        repo.save(user)
        fetched = repo.get_by_email("u_002@test.com")
        assert fetched is not None
        assert fetched.id == "u_002"

    def test_update_risk_tier(self):
        repo = InMemoryUserRepository()
        user = make_user("u_003", risk_tier="low")
        repo.save(user)

        repo.update_risk_tier("u_003", "high")
        updated = repo.get_by_id("u_003")
        assert updated.risk_tier == RiskTier.HIGH

    def test_seed_users(self):
        users = [make_user(f"u_{i:03d}") for i in range(5)]
        repo  = InMemoryUserRepository(seed=users)
        for u in users:
            assert repo.get_by_id(u.id) is not None

    def test_is_blocked_check(self):
        user = make_user("u_004", risk_tier="blocked")
        assert user.is_blocked() is True

        user2 = make_user("u_005", risk_tier="low")
        assert user2.is_blocked() is False
