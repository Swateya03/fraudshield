"""
fraud_api/repository/base.py
─────────────────────────────
The Repository interface (ABC).

This is the ONLY thing FraudAPI knows about storage.
No SQL, no SQLite, no PostgreSQL anywhere in business logic.

Scaling rule: to switch databases, write a new class that
implements this interface. FraudAPI never changes.
"""

from abc import ABC, abstractmethod
from typing import Optional, List
from fraudshield_core.models import User, Transaction, FraudScore, FraudLabel, UserRiskHistory
from datetime import datetime


class UserRepository(ABC):

    @abstractmethod
    def get_by_id(self, user_id: str) -> Optional[User]:
        """Return User or None if not found."""
        ...

    @abstractmethod
    def get_by_email(self, email: str) -> Optional[User]:
        ...

    @abstractmethod
    def save(self, user: User) -> None:
        """Insert or update."""
        ...

    @abstractmethod
    def update_risk_tier(self, user_id: str, risk_tier: str,
                         changed_by: str = "system",
                         reason: str = None) -> None:
        """
        Updates risk_tier AND writes to user_risk_history (SCD Type 2).
        Never just UPDATE users SET risk_tier — always preserve history.
        """
        ...

    @abstractmethod
    def get_risk_tier_at(self, user_id: str,
                         at_time: datetime) -> Optional[str]:
        """
        Point-in-time correct risk tier query.
        Used in ML training to avoid temporal leakage (slide 55).
        """
        ...


class TransactionRepository(ABC):

    @abstractmethod
    def save(self, transaction: Transaction) -> None:
        ...

    @abstractmethod
    def get_by_id(self, transaction_id: str) -> Optional[Transaction]:
        ...

    @abstractmethod
    def get_recent_by_user(self, user_id: str,
                           limit: int = 20) -> List[Transaction]:
        ...

    @abstractmethod
    def get_velocity(self, user_id: str,
                     window_hours: int) -> int:
        """
        Count transactions for user in last N hours.
        This is the velocity signal — critical fraud feature.
        Uses idx_txns_user_time index (slide 53).
        """
        ...


class FraudScoreRepository(ABC):

    @abstractmethod
    def save(self, score: FraudScore) -> None:
        ...

    @abstractmethod
    def get_by_transaction_id(self, transaction_id: str) -> Optional[FraudScore]:
        ...


class FraudLabelRepository(ABC):

    @abstractmethod
    def save(self, label: FraudLabel) -> None:
        ...

    @abstractmethod
    def get_by_transaction_id(self, transaction_id: str) -> Optional[FraudLabel]:
        ...

    @abstractmethod
    def get_unlabeled_reviews(self, limit: int = 50) -> List[dict]:
        """
        Returns transactions with decision=review that have no label yet.
        This is the analyst review queue.
        """
        ...

    @abstractmethod
    def get_labeled_training_data(self,
                                   from_date: datetime,
                                   to_date: datetime) -> List[dict]:
        """
        Returns labeled transactions for model training.
        Joins: transactions + fraud_scores + fraud_labels
        """
        ...
