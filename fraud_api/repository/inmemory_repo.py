"""
fraud_api/repository/inmemory_repo.py
──────────────────────────────────────
In-memory implementation — for unit tests only.
Zero database, zero setup, instant.

Usage in tests:
    repo = InMemoryUserRepository(seed=[user1, user2])
    service = FraudAPI(user_repo=repo, ...)
    # test without any DB
"""

from typing import Optional, List, Dict
from datetime import datetime
from fraudshield_core.models import (
    User, Transaction, FraudScore, FraudLabel, RiskTier
)
from fraud_api.repository.base import (
    UserRepository, TransactionRepository,
    FraudScoreRepository, FraudLabelRepository
)


class InMemoryUserRepository(UserRepository):

    def __init__(self, seed: List[User] = None):
        self._users: Dict[str, User] = {u.id: u for u in (seed or [])}
        self._history: List[dict] = []

    def get_by_id(self, user_id: str) -> Optional[User]:
        return self._users.get(user_id)

    def get_by_email(self, email: str) -> Optional[User]:
        return next((u for u in self._users.values() if u.email == email), None)

    def save(self, user: User) -> None:
        self._users[user.id] = user

    def update_risk_tier(self, user_id: str, risk_tier: str,
                          changed_by: str = "system", reason: str = None) -> None:
        if user_id in self._users:
            u = self._users[user_id]
            self._users[user_id] = User(
                id=u.id, email=u.email, phone=u.phone,
                risk_tier=RiskTier(risk_tier),
                kyc_status=u.kyc_status,
                created_at=u.created_at,
                updated_at=datetime.utcnow()
            )

    def get_risk_tier_at(self, user_id: str,
                          at_time: datetime) -> Optional[str]:
        # In-memory version: just return current (good enough for tests)
        user = self._users.get(user_id)
        return user.risk_tier.value if user else None


class InMemoryTransactionRepository(TransactionRepository):

    def __init__(self):
        self._txns: Dict[str, Transaction] = {}

    def save(self, transaction: Transaction) -> None:
        self._txns[transaction.id] = transaction

    def get_by_id(self, transaction_id: str) -> Optional[Transaction]:
        return self._txns.get(transaction_id)

    def get_recent_by_user(self, user_id: str, limit: int = 20):
        txns = [t for t in self._txns.values() if t.user_id == user_id]
        return sorted(txns, key=lambda t: t.created_at, reverse=True)[:limit]

    def get_velocity(self, user_id: str, window_hours: int) -> int:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=window_hours)
        return sum(
            1 for t in self._txns.values()
            if t.user_id == user_id and t.created_at >= cutoff
        )


class InMemoryFraudScoreRepository(FraudScoreRepository):

    def __init__(self):
        self._scores: Dict[str, FraudScore] = {}

    def save(self, score: FraudScore) -> None:
        self._scores[score.transaction_id] = score

    def get_by_transaction_id(self, tid: str) -> Optional[FraudScore]:
        return self._scores.get(tid)


class InMemoryFraudLabelRepository(FraudLabelRepository):

    def __init__(self):
        self._labels: Dict[str, FraudLabel] = {}

    def save(self, label: FraudLabel) -> None:
        self._labels[label.transaction_id] = label

    def get_by_transaction_id(self, tid: str) -> Optional[FraudLabel]:
        return self._labels.get(tid)

    def get_unlabeled_reviews(self, limit: int = 50) -> List[dict]:
        return []

    def get_labeled_training_data(self, from_date, to_date) -> List[dict]:
        return []
