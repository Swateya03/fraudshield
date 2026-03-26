"""
ml_pipeline/data/simulator.py
──────────────────────────────
Synthetic users, devices, merchants, and labeled transactions for seeding.

Merges the best of both versions:
  FROM attached file: math/lognormvariate, random.Random instances, Faker seed,
                      HIGH_RISK_MERCHANTS frozenset, CHANNELS, _DEVICE_PROFILES,
                      _legit_ip helper, defaultdict, public aliases
  FROM v2 fix:        fraud signals ALWAYS enforced in generate_transactions
                      (amount 8-40x, Tor IP, new device, late-night, high-risk merchant)
                      validate() quality gate, fixed seed for reproducibility

Root cause of old bug:
  generate_transactions computed amount BEFORE checking is_fraud, using the same
  lognormal distribution for both — so fraud and legit amounts were identical.
  Fix: when is_fraud=True, call _make_fraudulent_transaction (which enforces all signals).
       when is_fraud=False, call _make_legitimate_transaction (normal pattern).
"""

from __future__ import annotations

import math
import random
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from faker import Faker

# ── Constants — shared with dataset.py and rule_based.py ─────────────────────

# IPs treated as fraudulent in dataset.py / rule-based scoring
FRAUD_IPS = ("185.220.101.5", "185.220.101.6", "192.42.116.16",
             "199.87.154.255", "23.129.64.131")

# Merchant IDs that map to high merchant_risk_score in FeatureBuilder
HIGH_RISK_MERCHANTS = frozenset({"m_crypto", "m_giftcard", "m_jewelry", "m_luxury"})

MERCHANTS: list[dict[str, Any]] = [
    {"id": "m_grocery",  "name": "FreshMart Daily",    "category": "grocery",   "mcc": "5411", "risk": "low"},
    {"id": "m_gas",      "name": "QuickFuel",           "category": "gas",       "mcc": "5541", "risk": "standard"},
    {"id": "m_retail",   "name": "ShopAll Mega",        "category": "retail",    "mcc": "5311", "risk": "standard"},
    {"id": "m_food",     "name": "FoodCourt Express",   "category": "food",      "mcc": "5812", "risk": "standard"},
    {"id": "m_pharmacy", "name": "HealthPlus",          "category": "pharmacy",  "mcc": "5912", "risk": "standard"},
    {"id": "m_crypto",   "name": "CoinTrade",           "category": "crypto",    "mcc": "6012", "risk": "high"},
    {"id": "m_giftcard", "name": "GiftCard Hub",        "category": "gift",      "mcc": "5999", "risk": "high"},
    {"id": "m_jewelry",  "name": "GoldWorks",           "category": "jewelry",   "mcc": "5944", "risk": "high"},
    {"id": "m_luxury",   "name": "Luxe Boutique",       "category": "apparel",   "mcc": "5691", "risk": "high"},
]

CHANNELS = ("online", "upi", "pos", "nfc")

_DEVICE_PROFILES = (
    ("mobile",  "Android 14", "Chrome"),
    ("mobile",  "iOS 17",     "Safari"),
    ("desktop", "Windows 11", "Edge"),
    ("tablet",  "iPadOS 17",  "Safari"),
)

_LEGIT_MERCHANT_IDS  = [m["id"] for m in MERCHANTS if m["id"] not in HIGH_RISK_MERCHANTS]
_FRAUD_MERCHANT_IDS  = sorted(HIGH_RISK_MERCHANTS)


def _legit_ip(rng: random.Random) -> str:
    """Returns a random-looking legitimate ISP IP."""
    return (f"{rng.randint(10, 223)}.{rng.randint(0, 255)}"
            f".{rng.randint(0, 255)}.{rng.randint(1, 254)}")


# ── User generation ───────────────────────────────────────────────────────────

def generate_users(n: int, seed: int = 42) -> list[dict[str, Any]]:
    """
    Generate n user profiles.

    _avg_amount: drawn from log-normal (median ~650 INR) — realistic spend distribution.
                 NOT stored in DB — used only by the simulator.
    """
    rng  = random.Random(seed)
    fake = Faker()
    fake.seed_instance(seed)
    users: list[dict[str, Any]] = []
    now = datetime.utcnow().replace(microsecond=0)

    for _ in range(n):
        uid         = f"u_{uuid.uuid4().hex[:10]}"
        created     = now - timedelta(days=rng.randint(30, 420))
        risk        = rng.choices(
            ["low", "medium", "high", "blocked"],
            weights=[0.72, 0.18, 0.08, 0.02],
        )[0]
        kyc         = "verified" if rng.random() < 0.92 else "pending"
        # Log-normal spend: most users spend 200-2000 INR per txn, a few spend much more
        avg_amt     = round(
            max(45.0, min(abs(rng.lognormvariate(math.log(650), 0.55)), 22_000.0)), 2
        )
        users.append({
            "id":          uid,
            "email":       fake.unique.email(),
            "phone":       fake.phone_number()[:24] if rng.random() > 0.12 else None,
            "risk_tier":   risk,
            "kyc_status":  kyc,
            "created_at":  created.isoformat(),
            "updated_at":  created.isoformat(),
            # Private simulation fields — NOT written to DB
            "_avg_amount": avg_amt,
        })
    return users


# ── Device generation ─────────────────────────────────────────────────────────

def generate_devices(users: list[dict[str, Any]], seed: int = 43) -> list[dict[str, Any]]:
    """
    Each user gets 1-2 trusted devices.
    Timestamps are relative to user creation — realistic account lifecycle.
    """
    rng     = random.Random(seed)
    devices: list[dict[str, Any]] = []

    for u in users:
        n_dev       = rng.randint(1, 2)
        created_user = datetime.fromisoformat(u["created_at"])

        for j in range(n_dev):
            first = created_user + timedelta(
                days=rng.randint(0, 7), hours=rng.randint(0, 20)
            )
            last  = datetime.utcnow().replace(microsecond=0) - timedelta(
                days=rng.randint(0, 14), hours=rng.randint(0, 12)
            )
            if last < first:
                last = first + timedelta(hours=1)

            dtype, os_name, browser = rng.choice(_DEVICE_PROFILES)
            devices.append({
                "id":            f"d_{uuid.uuid4().hex[:12]}",
                "user_id":       u["id"],
                "device_type":   dtype,
                "os":            os_name,
                "browser":       browser,
                "first_seen_at": first.isoformat(),
                "last_seen_at":  last.isoformat(),
                "is_trusted":    bool(j == 0 and rng.random() < 0.55),
            })
    return devices


# ── Single-transaction generators ────────────────────────────────────────────

def _make_fraudulent_transaction(
    user: dict[str, Any],
    at: datetime,
    *,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """
    CNP fraud transaction — all 5 signals enforced simultaneously:
      amount    : 6–22× user's normal spend  (maximising stolen card value)
      device_id : always a new unknown device (attacker's machine)
      ip_address: always a Tor exit node      (hiding real location)
      hour      : 0–4am                       (victim is asleep)
      merchant  : always high-risk category   (crypto/jewelry for resale)

    Used for: notebooks, tests, drift injection.
    Also called by generate_transactions when is_fraud=True.
    """
    r      = rng or random.Random()
    avg    = float(user.get("_avg_amount", 500.0))
    amount = round(min(max(avg * r.uniform(6.0, 22.0), avg + 250.0), 200_000.0), 2)
    mid    = r.choice(_FRAUD_MERCHANT_IDS)
    hour   = r.choice([0, 1, 2, 3, 4])            # always late-night
    ts     = at.replace(hour=hour, minute=r.randint(0, 59),
                        second=r.randint(0, 59), microsecond=0)
    return {
        "id":          str(uuid.uuid4()),
        "user_id":     user["id"],
        "merchant_id": mid,
        "device_id":   f"d_fraud_{uuid.uuid4().hex[:12]}",   # always new device
        "amount":      amount,
        "currency":    "INR",
        "channel":     "online",
        "ip_address":  r.choice(FRAUD_IPS),                  # always Tor IP
        "created_at":  ts.isoformat(),
        "is_fraud":    True,
    }


def _make_legitimate_transaction(
    user: dict[str, Any],
    devices: list[dict[str, Any]],
    at: datetime,
    *,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """
    Normal in-pattern transaction:
      amount    : log-normal around user's average (realistic variation)
      device_id : known trusted device from user's device list
      ip_address: clean ISP IP (not Tor)
      hour      : daytime weighted (9am–9pm peaks)
      merchant  : low or standard risk only
    """
    r      = rng or random.Random()
    avg    = float(user.get("_avg_amount", 500.0))
    # Log-normal variation: most txns near avg, occasional larger purchases
    raw    = r.lognormvariate(math.log(max(avg, 50.0)), 0.38)
    amount = round(max(12.0, min(raw, avg * 2.8)), 2)
    mid    = r.choice(_LEGIT_MERCHANT_IDS)
    # Daytime weighted: 9am–9pm peaks, near-zero at 1-5am
    hour   = r.choices(
        range(24),
        weights=[1,1,1,1,1,2,3,6,9,10,10,10,9,10,10,9,8,7,6,5,4,3,2,1]
    )[0]
    device_id = r.choice(devices)["id"] if devices else f"d_legit_{uuid.uuid4().hex[:12]}"
    ts         = at.replace(hour=hour, minute=r.randint(0, 59),
                            second=r.randint(0, 59), microsecond=0)
    return {
        "id":          str(uuid.uuid4()),
        "user_id":     user["id"],
        "merchant_id": mid,
        "device_id":   device_id,
        "amount":      amount,
        "currency":    "INR",
        "channel":     r.choice(CHANNELS),
        "ip_address":  _legit_ip(r),
        "created_at":  ts.isoformat(),
        "is_fraud":    False,
    }


# ── Main generation loop ──────────────────────────────────────────────────────

def generate_transactions(
    users:      list[dict[str, Any]],
    devices:    list[dict[str, Any]],
    n_days:     int   = 90,
    fraud_rate: float = 0.08,
    seed:       int   = 44,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Generate n_days of transaction history.

    seed=44  → identical data every run (reproducible, default).
    seed=None → new random data every run.

    Key fix over old version:
      When is_fraud=True, calls _make_fraudulent_transaction() which enforces
      ALL 5 fraud signals (amount 6-22x, Tor IP, new device, late night, bad merchant).
      The old version computed amount INDEPENDENTLY of is_fraud — same distribution
      for both fraud and legit — making amount_ratio ≈ 1.0 (useless feature).
    """
    rng = random.Random(seed)

    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in devices:
        by_user[d["user_id"]].append(d)

    end   = datetime.utcnow().replace(microsecond=0)
    start = end - timedelta(days=n_days)

    transactions: list[dict[str, Any]] = []
    labels:        list[dict[str, Any]] = []

    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        for u in users:
            devs = by_user.get(u["id"])
            if not devs:
                continue

            # 0–3 transactions per user per day (realistic volume)
            for _ in range(rng.randint(0, 3)):
                is_fraud = (rng.random() < fraud_rate)

                if is_fraud:
                    # ALL fraud signals enforced inside _make_fraudulent_transaction
                    txn = _make_fraudulent_transaction(u, day, rng=rng)
                else:
                    # Normal pattern inside _make_legitimate_transaction
                    txn = _make_legitimate_transaction(u, devs, day, rng=rng)

                # Cap timestamp at end to avoid future dates
                ts = datetime.fromisoformat(txn["created_at"])
                if ts >= end:
                    ts = end - timedelta(minutes=rng.randint(1, 120))
                    txn["created_at"] = ts.replace(microsecond=0).isoformat()

                transactions.append(txn)

                # Chargeback delay: fraud labels arrive 5–45 days after txn
                # Legit review: 1–7 days
                label_delay = rng.randint(5, 45) if is_fraud else rng.randint(1, 7)
                labeled_at  = ts + timedelta(days=label_delay)

                labels.append({
                    "id":             str(uuid.uuid4()),
                    "transaction_id": txn["id"],
                    "is_fraud":       is_fraud,
                    "label_source":   "chargeback" if is_fraud else "manual_review",
                    "labeled_at":     labeled_at.isoformat(),
                    "labeled_by":     None,
                    "notes":          None,
                })

        day += timedelta(days=1)

    return transactions, labels


# ── Built-in data quality validator ──────────────────────────────────────────

def validate(
    transactions: list[dict[str, Any]],
    labels:       list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Quality gate — call BEFORE inserting to DB.
    Returns pass/fail for each signal that the ML model depends on.

    ALL_PASS must be True before running train_model.py.
    If any check fails, the model will train on bad data and look good in
    eval but fail silently in production.
    """
    import statistics

    label_map      = {lb["transaction_id"]: lb["is_fraud"] for lb in labels}
    fraud_txns     = [t for t in transactions if label_map.get(t["id"])]
    legit_txns     = [t for t in transactions if not label_map.get(t["id"])]

    fraud_amounts  = [t["amount"]     for t in fraud_txns]
    legit_amounts  = [t["amount"]     for t in legit_txns]
    fraud_ips      = [t["ip_address"] for t in fraud_txns]
    fraud_devices  = [t["device_id"] or "" for t in fraud_txns]
    fraud_hours    = [int(t["created_at"][11:13]) for t in fraud_txns]
    fraud_merch    = [t["merchant_id"] for t in fraud_txns]

    total      = len(transactions)
    n_fraud    = len(fraud_txns)
    avg_fraud  = statistics.mean(fraud_amounts) if fraud_amounts else 0
    avg_legit  = statistics.mean(legit_amounts) if legit_amounts else 1
    ratio      = avg_fraud / avg_legit

    ip_rate    = sum(1 for ip in fraud_ips    if ip in FRAUD_IPS)          / max(n_fraud, 1)
    dev_rate   = sum(1 for d  in fraud_devices if "fraud" in d)            / max(n_fraud, 1)
    night_rate = sum(1 for h  in fraud_hours   if h < 5)                   / max(n_fraud, 1)
    merch_rate = sum(1 for m  in fraud_merch   if m in HIGH_RISK_MERCHANTS) / max(n_fraud, 1)

    results: dict[str, Any] = {
        # Raw metrics
        "total_transactions":  total,
        "n_fraud":             n_fraud,
        "fraud_rate_pct":      round(n_fraud / total * 100, 1) if total else 0,
        "fraud_avg_amount":    round(avg_fraud),
        "legit_avg_amount":    round(avg_legit),
        "amount_ratio":        round(ratio, 1),
        "fraud_ip_rate_pct":   round(ip_rate   * 100, 1),
        "fraud_dev_rate_pct":  round(dev_rate  * 100, 1),
        "fraud_night_rate_pct":round(night_rate * 100, 1),
        "fraud_merch_rate_pct":round(merch_rate * 100, 1),
        # Pass/fail gates (thresholds are conservative — easy to achieve)
        "PASS_fraud_rate":    5.0 <= (n_fraud / total * 100 if total else 0) <= 12.0,
        "PASS_amount_ratio":  ratio      >= 5.0,   # fraud should be 5x+ legit
        "PASS_ip_signal":     ip_rate    >= 0.70,  # 70%+ of fraud uses Tor IPs
        "PASS_device_signal": dev_rate   >= 0.70,  # 70%+ of fraud uses new device
        "PASS_night_signal":  night_rate >= 0.50,  # 50%+ of fraud is late-night
        "PASS_merch_signal":  merch_rate >= 0.70,  # 70%+ of fraud uses bad merchant
    }
    results["ALL_PASS"] = all(v for k, v in results.items() if k.startswith("PASS_"))
    return results


# ── Public aliases (backward-compatible imports) ──────────────────────────────
# Some notebooks / tests import these directly.
make_fraudulent_transaction = _make_fraudulent_transaction
make_legitimate_transaction = _make_legitimate_transaction


# ── Self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Simulator self-test (seed=42/43/44, 20 users, 10 days)...\n")
    users   = generate_users(20,  seed=42)
    devs    = generate_devices(users, seed=43)
    txns, labels = generate_transactions(users, devs, n_days=10, seed=44)
    r = validate(txns, labels)

    print(f"  Transactions:      {r['total_transactions']}")
    print(f"  Fraud count:       {r['n_fraud']} ({r['fraud_rate_pct']}%)")
    print(f"  Fraud avg:         Rs {r['fraud_avg_amount']:,}")
    print(f"  Legit avg:         Rs {r['legit_avg_amount']:,}")
    print(f"  Amount ratio:      {r['amount_ratio']}x   {'PASS' if r['PASS_amount_ratio'] else 'FAIL'}")
    print(f"  Fraud IP rate:     {r['fraud_ip_rate_pct']}%  {'PASS' if r['PASS_ip_signal'] else 'FAIL'}")
    print(f"  New device rate:   {r['fraud_dev_rate_pct']}%  {'PASS' if r['PASS_device_signal'] else 'FAIL'}")
    print(f"  Late night rate:   {r['fraud_night_rate_pct']}%  {'PASS' if r['PASS_night_signal'] else 'FAIL'}")
    print(f"  Bad merchant rate: {r['fraud_merch_rate_pct']}%  {'PASS' if r['PASS_merch_signal'] else 'FAIL'}")
    print(f"\n  {'ALL CHECKS PASSED' if r['ALL_PASS'] else 'SOME CHECKS FAILED — do not train on this data'}")
