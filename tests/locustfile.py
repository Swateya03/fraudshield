"""
tests/locustfile.py
────────────────────
Locust load test scenarios for FraudShield API.

Run:
  locust -f tests/locustfile.py --host http://localhost:8000 \
         --users 50 --spawn-rate 5 --run-time 60s --headless
"""

import random
import uuid
from locust import HttpUser, task, between

_TOKEN = "dev_token_fraudshield_local_only"
_HEADERS = {"X-API-Token": _TOKEN}

_CHANNELS   = ["online", "upi", "pos", "nfc", "atm"]
_CURRENCIES = ["INR", "USD", "EUR"]
_MERCHANTS  = ["m_grocery", "m_gas", "m_retail", "m_food", "m_crypto", "m_giftcard"]


def _txn_payload() -> dict:
    return {
        "user_id":     f"u_{random.randint(1, 200):04d}",
        "merchant_id": random.choice(_MERCHANTS),
        "amount":      round(random.uniform(10, 50_000), 2),
        "currency":    random.choice(_CURRENCIES),
        "channel":     random.choice(_CHANNELS),
        "ip_address":  f"192.168.{random.randint(0,255)}.{random.randint(1,254)}",
        "device_id":   f"dev_{random.randint(1, 500):04d}",
    }


class ScoringUser(HttpUser):
    """Hammers the scoring endpoint — the hot path under real load."""

    wait_time = between(0.1, 0.5)

    @task(8)
    def score_transaction(self):
        payload = _txn_payload()
        self.client.post(
            "/v1/score",
            json=payload,
            headers=_HEADERS,
            name="/v1/score",
        )

    @task(2)
    def score_with_idempotency_key(self):
        payload = _txn_payload()
        idem_key = str(uuid.uuid4())
        for _ in range(2):
            self.client.post(
                "/v1/score",
                json=payload,
                headers={**_HEADERS, "Idempotency-Key": idem_key},
                name="/v1/score [idempotent]",
            )

    @task(1)
    def score_dry_run(self):
        payload = {**_txn_payload(), "dry_run": True}
        self.client.post(
            "/v1/score",
            json=payload,
            headers=_HEADERS,
            name="/v1/score [dry_run]",
        )


class AnalystUser(HttpUser):
    """Simulates an analyst browsing the transaction list and user profiles."""

    wait_time = between(1, 3)

    @task(5)
    def list_transactions_recent(self):
        self.client.get(
            "/v1/transactions?limit=50&order=desc",
            headers=_HEADERS,
            name="/v1/transactions",
        )

    @task(3)
    def list_transactions_filtered(self):
        decision = random.choice(["block", "review", "allow"])
        self.client.get(
            f"/v1/transactions?limit=50&decision={decision}",
            headers=_HEADERS,
            name="/v1/transactions?decision=",
        )

    @task(2)
    def get_user_profile(self):
        uid = f"u_{random.randint(1, 200):04d}"
        self.client.get(
            f"/v1/users/{uid}",
            headers=_HEADERS,
            name="/v1/users/{id}",
        )

    @task(1)
    def get_stats(self):
        self.client.get("/v1/stats", headers=_HEADERS, name="/v1/stats")

    @task(1)
    def health_check(self):
        self.client.get("/health", name="/health")
