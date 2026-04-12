"""
ml_pipeline/data/simulator.py
──────────────────────────────
Synthetic users, devices, merchants, and labeled transactions for seeding.

v3 archetype system:
  Fraud transactions are drawn from 7 distinct archetypes, each activating a
  DIFFERENT subset of fraud signals.  This prevents the model from learning a
  trivial "all-5-signals-on → fraud" shortcut and produces realistic AUC/KS
  values suitable for evaluating the pipeline end-to-end.

  Archetypes:
    classic_cnp    (30%) — all 5 signals (new device, Tor, spike, late-night, risky merchant)
    ato_takeover   (22%) — victim's device & IP, high amount, risky merchant
    card_testing   (12%) — tiny probe amount, new device + Tor, normal merchant
    friendly_fraud (12%) — user's own device/IP/merchant, moderate amount
    sophisticated  (10%) — new device, clean IP, normal hours, high amount, risky merchant
    gradual_drain   (8%) — victim's device, small incremental amounts, no signals
    refund_abuse    (6%) — victim's device, moderate amount, online, normal merchant

  Each archetype has a signal_dropout probability that randomly disables
  individual signals, and legitimate transactions include trait-driven noise
  plus compound multi-signal bursts for realistic false-positive pressure.
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

FRAUD_IPS = ("185.220.101.5", "185.220.101.6", "192.42.116.16",
             "199.87.154.255", "23.129.64.131")

HIGH_RISK_MERCHANTS = frozenset({"m_crypto", "m_giftcard", "m_jewelry", "m_luxury"})

MERCHANTS: list[dict[str, Any]] = [
    {"id": "m_grocery",  "name": "FreshMart Daily",  "category": "grocery",  "mcc": "5411", "risk": "low",      "city": "Delhi",     "state": "DL"},
    {"id": "m_gas",      "name": "QuickFuel",        "category": "gas",      "mcc": "5541", "risk": "standard", "city": "Bengaluru", "state": "KA"},
    {"id": "m_retail",   "name": "ShopAll Mega",     "category": "retail",   "mcc": "5311", "risk": "standard", "city": "Mumbai",    "state": "MH"},
    {"id": "m_food",     "name": "FoodCourt Express","category": "food",     "mcc": "5812", "risk": "standard", "city": "Hyderabad", "state": "TS"},
    {"id": "m_pharmacy", "name": "HealthPlus",       "category": "pharmacy", "mcc": "5912", "risk": "standard", "city": "Chennai",   "state": "TN"},
    {"id": "m_crypto",   "name": "CoinTrade",        "category": "crypto",   "mcc": "6012", "risk": "high",     "city": "Mumbai",    "state": "MH"},
    {"id": "m_giftcard", "name": "GiftCard Hub",     "category": "gift",     "mcc": "5999", "risk": "high",     "city": "Mumbai",    "state": "MH"},
    {"id": "m_jewelry",  "name": "GoldWorks",        "category": "jewelry",  "mcc": "5944", "risk": "high",     "city": "Mumbai",    "state": "MH"},
    {"id": "m_luxury",   "name": "Luxe Boutique",    "category": "apparel",  "mcc": "5691", "risk": "high",     "city": "Mumbai",    "state": "MH"},
]

CHANNELS = ("online", "upi", "pos", "nfc")

_CHANNEL_AMOUNT_PARAMS = {
    #          (mu_scale, sigma, clip_min, clip_max_mult)
    "upi":    (0.7, 1.2,   10.0, 12.0),
    "nfc":    (0.6, 1.1,   10.0,  8.0),
    "pos":    (1.2, 1.3,   30.0, 18.0),
    "online": (1.8, 1.5,  100.0, 25.0),
}

_DEVICE_PROFILES = (
    ("mobile",  "Android 14",    "Chrome"),
    ("mobile",  "Android 13",    "Chrome"),
    ("mobile",  "Android 12",    "Samsung Browser"),
    ("mobile",  "iOS 17",        "Safari"),
    ("mobile",  "iOS 16",        "Safari"),
    ("desktop", "Windows 11",    "Chrome"),
    ("desktop", "Windows 10",    "Chrome"),
    ("desktop", "Windows 10",    "Edge"),
    ("tablet",  "iPadOS 17",     "Safari"),
    ("desktop", "macOS Sonoma",  "Safari"),
)
_DEVICE_WEIGHTS = (22, 18, 12, 12, 8, 8, 7, 5, 4, 4)

_LEGIT_MERCHANT_IDS  = [m["id"] for m in MERCHANTS if m["id"] not in HIGH_RISK_MERCHANTS]
_FRAUD_MERCHANT_IDS  = sorted(HIGH_RISK_MERCHANTS)

# ── Fraud archetypes ─────────────────────────────────────────────────────────

_FRAUD_ARCHETYPES = {
    "classic_cnp": {
        "weight": 0.30,
        "new_device": True, "tor_ip": True,
        "amount_mult": (6.0, 22.0),
        "late_night": True, "high_risk_merchant": True,
        "channel": "online",
        "signal_dropout": 0.20,
    },
    "ato_takeover": {
        "weight": 0.22,
        "new_device": False, "tor_ip": False,
        "amount_mult": (3.0, 10.0),
        "late_night": False, "high_risk_merchant": True,
        "channel": "online",
        "signal_dropout": 0.18,
    },
    "card_testing": {
        "weight": 0.12,
        "new_device": True, "tor_ip": True,
        "amount_mult": (0.01, 0.10),
        "late_night": False, "high_risk_merchant": False,
        "channel": "online",
        "signal_dropout": 0.15,
    },
    "friendly_fraud": {
        "weight": 0.12,
        "new_device": False, "tor_ip": False,
        "amount_mult": (0.8, 2.5),
        "late_night": False, "high_risk_merchant": False,
        "channel": None,
        "signal_dropout": 0.0,
    },
    "sophisticated": {
        "weight": 0.10,
        "new_device": True, "tor_ip": False,
        "amount_mult": (5.0, 15.0),
        "late_night": False, "high_risk_merchant": True,
        "channel": "online",
        "signal_dropout": 0.22,
    },
    "gradual_drain": {
        "weight": 0.08,
        "new_device": False, "tor_ip": False,
        "amount_mult": (1.2, 2.0),
        "late_night": False, "high_risk_merchant": False,
        "channel": None,
        "signal_dropout": 0.0,
    },
    "refund_abuse": {
        "weight": 0.06,
        "new_device": False, "tor_ip": False,
        "amount_mult": (1.5, 4.0),
        "late_night": False, "high_risk_merchant": False,
        "channel": "online",
        "signal_dropout": 0.10,
    },
}
_ARCHETYPE_NAMES   = list(_FRAUD_ARCHETYPES.keys())
_ARCHETYPE_WEIGHTS = [a["weight"] for a in _FRAUD_ARCHETYPES.values()]


_INDIAN_IP_PREFIXES = (
    (49,  "JIO"),    (117, "BSNL"),
    (182, "Airtel"), (110, "Idea"),
    (59,  "ACT"),    (103, "Hathway"),
)


def _legit_ip(rng: random.Random) -> str:
    prefix, _ = rng.choice(_INDIAN_IP_PREFIXES)
    return (f"{prefix}.{rng.randint(0, 255)}"
            f".{rng.randint(0, 255)}.{rng.randint(1, 254)}")


# ── User generation ───────────────────────────────────────────────────────────

_INDIAN_CITIES = [
    ("Mumbai",    "MH", 0.28),
    ("Delhi",     "DL", 0.24),
    ("Bengaluru", "KA", 0.18),
    ("Hyderabad", "TS", 0.12),
    ("Chennai",   "TN", 0.10),
    ("Pune",      "MH", 0.08),
]
_CITY_WEIGHTS = [w for _, _, w in _INDIAN_CITIES]


def generate_users(n: int, seed: int = 42) -> list[dict[str, Any]]:
    """
    Generate n user profiles, each tagged with behavioural traits.

    _avg_amount: drawn from log-normal (median ~650 INR).
    _traits:     set of behavioural tags that make legit transactions noisier.
    """
    rng  = random.Random(seed)
    fake = Faker("en_IN")
    fake.seed_instance(seed)
    users: list[dict[str, Any]] = []
    now = datetime.utcnow().replace(microsecond=0)

    _TRAIT_PROBS = [
        ("night_owl",     0.08),
        ("traveler",      0.12),
        ("high_roller",   0.05),
        ("device_hopper", 0.06),
        ("crypto_user",   0.04),
    ]

    for _ in range(n):
        uid         = f"u_{uuid.uuid4().hex[:10]}"
        created     = now - timedelta(days=rng.randint(30, 420))
        risk        = rng.choices(
            ["low", "medium", "high", "blocked"],
            weights=[0.72, 0.18, 0.08, 0.02],
        )[0]
        kyc         = "verified" if rng.random() < 0.92 else "pending"
        avg_amt     = round(
            max(100.0, min(abs(rng.lognormvariate(math.log(2500), 1.0)), 120_000.0)), 2
        )
        city_choice = rng.choices(_INDIAN_CITIES, weights=_CITY_WEIGHTS)[0]
        is_ato = rng.random() < 0.06
        updated = (
            (now - timedelta(days=rng.randint(1, 14))).replace(microsecond=0)
            if is_ato else created
        )

        traits: set[str] = set()
        for trait_name, trait_prob in _TRAIT_PROBS:
            if rng.random() < trait_prob:
                traits.add(trait_name)

        users.append({
            "id":          uid,
            "email":       fake.unique.email(),
            "phone":       f"+91{rng.randint(7000000000, 9999999999)}" if rng.random() > 0.12 else None,
            "risk_tier":   risk,
            "kyc_status":  kyc,
            "city":        city_choice[0],
            "state":       city_choice[1],
            "created_at":  created.isoformat(),
            "updated_at":  updated.isoformat(),
            "_avg_amount": avg_amt,
            "_traits":     traits,
        })
    return users


# ── Device generation ─────────────────────────────────────────────────────────

def generate_devices(users: list[dict[str, Any]], seed: int = 43) -> list[dict[str, Any]]:
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

            dtype, os_name, browser = rng.choices(_DEVICE_PROFILES, weights=_DEVICE_WEIGHTS)[0]
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

_DAYTIME_WEIGHTS = [1,1,1,1,1,2,3,6,9,10,10,10,9,10,10,9,8,7,6,5,4,3,2,1]


def _make_fraudulent_transaction(
    user: dict[str, Any],
    at: datetime,
    *,
    devices: list[dict[str, Any]] | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """
    Generate a fraud transaction drawn from one of 7 archetypes.
    A per-archetype signal_dropout probability further degrades signals.
    """
    r   = rng or random.Random()
    avg = float(user.get("_avg_amount", 500.0))

    arch_name = r.choices(_ARCHETYPE_NAMES, weights=_ARCHETYPE_WEIGHTS)[0]
    arch      = _FRAUD_ARCHETYPES[arch_name]
    dropout   = arch["signal_dropout"]

    use_new_device      = arch["new_device"]      and r.random() >= dropout
    use_tor_ip          = arch["tor_ip"]           and r.random() >= dropout
    use_late_night      = arch["late_night"]       and r.random() >= dropout
    use_high_risk_merch = arch["high_risk_merchant"] and r.random() >= dropout

    lo, hi = arch["amount_mult"]
    raw_mult = r.uniform(lo, hi)
    if dropout > 0 and r.random() < dropout:
        raw_mult = max(lo, raw_mult * r.uniform(0.3, 0.7))
    amount = round(min(max(avg * raw_mult, 1.0), 200_000.0), 2)

    if use_new_device:
        device_id = f"d_fraud_{uuid.uuid4().hex[:12]}"
    elif devices:
        device_id = r.choice(devices)["id"]
    else:
        device_id = f"d_fraud_{uuid.uuid4().hex[:12]}"

    ip_address = r.choice(FRAUD_IPS) if use_tor_ip else _legit_ip(r)

    if use_late_night:
        hour = r.choice([0, 1, 2, 3, 4])
    else:
        hour = r.choices(range(24), weights=_DAYTIME_WEIGHTS)[0]

    merchant_id = (r.choice(_FRAUD_MERCHANT_IDS) if use_high_risk_merch
                   else r.choice(_LEGIT_MERCHANT_IDS))

    channel = arch["channel"] or r.choice(CHANNELS)

    ts = at.replace(hour=hour, minute=r.randint(0, 59),
                    second=r.randint(0, 59), microsecond=0)

    return {
        "id":          str(uuid.uuid4()),
        "user_id":     user["id"],
        "merchant_id": merchant_id,
        "device_id":   device_id,
        "amount":      amount,
        "currency":    "INR",
        "channel":     channel,
        "ip_address":  ip_address,
        "created_at":  ts.isoformat(),
        "is_fraud":    True,
        "_archetype":  arch_name,
    }


def _make_legitimate_transaction(
    user: dict[str, Any],
    devices: list[dict[str, Any]],
    at: datetime,
    *,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """
    Legitimate transaction with three layers of realistic noise:
    Layer 1 — Trait-driven (persistent per user)
    Layer 2 — Random single-signal (~6 %)
    Layer 3 — Compound burst (~3 %) — two signals at once
    """
    r       = rng or random.Random()
    avg     = float(user.get("_avg_amount", 500.0))
    traits  = user.get("_traits", set())
    channel = r.choice(CHANNELS)
    scale, sigma, clip_min, clip_max_mult = _CHANNEL_AMOUNT_PARAMS[channel]
    mu      = math.log(max(avg * scale, clip_min))
    raw     = r.lognormvariate(mu, sigma)
    amount  = round(max(clip_min, min(raw, avg * clip_max_mult)), 2)
    mid     = r.choice(_LEGIT_MERCHANT_IDS)
    hour    = r.choices(range(24), weights=_DAYTIME_WEIGHTS)[0]
    device_id  = r.choice(devices)["id"] if devices else f"d_legit_{uuid.uuid4().hex[:12]}"
    ip_address = _legit_ip(r)

    # Layer 1: Trait-driven persistent noise
    if "night_owl" in traits and r.random() < 0.40:
        hour = r.choice([0, 1, 2, 3, 4])
    if "traveler" in traits and r.random() < 0.35:
        mid = r.choice([m["id"] for m in MERCHANTS])
    if "high_roller" in traits and r.random() < 0.30:
        amount = round(min(avg * r.uniform(3.0, 10.0), 200_000.0), 2)
    if "device_hopper" in traits and r.random() < 0.25:
        device_id = f"d_new_{uuid.uuid4().hex[:12]}"
    if "crypto_user" in traits and r.random() < 0.30:
        mid = r.choice(_FRAUD_MERCHANT_IDS)

    # Layer 2: Random single-signal noise (~6 %)
    roll = r.random()
    if roll < 0.015:
        device_id = f"d_new_{uuid.uuid4().hex[:12]}"
    elif roll < 0.03:
        hour = r.choice([0, 1, 2, 3, 4])
    elif roll < 0.045:
        mid = r.choice(_FRAUD_MERCHANT_IDS)
    elif roll < 0.06:
        amount = round(min(avg * r.uniform(3.5, 8.0), 200_000.0), 2)

    # Layer 3: Compound burst (~3 %) — two signals at once
    if r.random() < 0.03:
        combo = r.choice(["dev_amount", "night_merchant", "dev_night", "amount_merchant"])
        if combo == "dev_amount":
            device_id = f"d_new_{uuid.uuid4().hex[:12]}"
            amount = round(min(avg * r.uniform(4.0, 9.0), 200_000.0), 2)
        elif combo == "night_merchant":
            hour = r.choice([0, 1, 2, 3, 4])
            mid = r.choice(_FRAUD_MERCHANT_IDS)
        elif combo == "dev_night":
            device_id = f"d_new_{uuid.uuid4().hex[:12]}"
            hour = r.choice([0, 1, 2, 3, 4])
        elif combo == "amount_merchant":
            amount = round(min(avg * r.uniform(4.0, 12.0), 200_000.0), 2)
            mid = r.choice(_FRAUD_MERCHANT_IDS)

    ts = at.replace(hour=hour, minute=r.randint(0, 59),
                    second=r.randint(0, 59), microsecond=0)
    return {
        "id":          str(uuid.uuid4()),
        "user_id":     user["id"],
        "merchant_id": mid,
        "device_id":   device_id,
        "amount":      amount,
        "currency":    "INR",
        "channel":     channel,
        "ip_address":  ip_address,
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
) -> list[dict[str, Any]]:
    """
    Generate n_days of transaction history.  Returns transactions only —
    labels are created separately by generate_labels().
    """
    rng = random.Random(seed)

    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in devices:
        by_user[d["user_id"]].append(d)

    end   = datetime.utcnow().replace(microsecond=0)
    start = end - timedelta(days=n_days)

    transactions: list[dict[str, Any]] = []

    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        for u in users:
            devs = by_user.get(u["id"])
            if not devs:
                continue

            user_created = datetime.fromisoformat(u["created_at"])
            if day < user_created:
                continue

            n_txns = rng.randint(0, 3)
            if rng.random() < 0.04:
                n_txns = rng.randint(6, 10)
            for _ in range(n_txns):
                is_fraud = (rng.random() < fraud_rate)

                if is_fraud:
                    txn = _make_fraudulent_transaction(
                        u, day, devices=devs, rng=rng,
                    )
                else:
                    txn = _make_legitimate_transaction(u, devs, day, rng=rng)

                ts = datetime.fromisoformat(txn["created_at"])
                if ts >= end:
                    ts = end - timedelta(minutes=rng.randint(1, 120))
                    txn["created_at"] = ts.replace(microsecond=0).isoformat()

                transactions.append(txn)

        day += timedelta(days=1)

    return transactions


# ── Sparse label generator ───────────────────────────────────────────────────

def generate_labels(
    transactions: list[dict[str, Any]],
    user_avg_amounts: dict[str, float],
    review_rate: float = 0.13,
    seed: int = 45,
) -> list[dict[str, Any]]:
    """
    Create labels for a realistic subset of transactions (10-15% coverage).
    Includes label noise: 3% of true fraud labeled legit, 2% of true legit labeled fraud.
    """
    rng = random.Random(seed)
    labels: list[dict[str, Any]] = []

    for txn in transactions:
        user_avg = user_avg_amounts.get(txn["user_id"], 500.0)
        triggered = (
            txn["amount"] > user_avg * 2.5
            or txn.get("device_id", "").startswith("d_fraud_")
            or txn.get("device_id", "").startswith("d_new_")
            or txn["ip_address"] in FRAUD_IPS
            or txn["merchant_id"] in HIGH_RISK_MERCHANTS
            or rng.random() < 0.05
        )
        if not triggered or rng.random() > review_rate * 3:
            continue

        is_fraud = txn.get("is_fraud", False)

        if is_fraud and rng.random() < 0.03:
            is_fraud = False
        elif not is_fraud and rng.random() < 0.02:
            is_fraud = True

        delay = rng.randint(5, 45) if is_fraud else rng.randint(1, 7)
        ts = datetime.fromisoformat(txn["created_at"])
        labels.append({
            "id":             str(uuid.uuid4()),
            "transaction_id": txn["id"],
            "is_fraud":       is_fraud,
            "label_source":   "chargeback" if is_fraud else "manual_review",
            "labeled_at":     (ts + timedelta(days=delay)).isoformat(),
            "labeled_by":     None,
            "notes":          None,
        })

    return labels


# ── Built-in data quality validator ──────────────────────────────────────────

def validate(
    transactions: list[dict[str, Any]],
    labels:       list[dict[str, Any]],
) -> dict[str, Any]:
    import statistics

    label_map      = {lb["transaction_id"]: lb["is_fraud"] for lb in labels}
    labeled_ids    = set(label_map.keys())
    fraud_txns     = [t for t in transactions if label_map.get(t["id"]) is True]
    unlabeled_txns = [t for t in transactions if t["id"] not in labeled_ids]

    fraud_amounts  = [t["amount"]     for t in fraud_txns]
    legit_amounts  = [t["amount"]     for t in unlabeled_txns]
    fraud_ips      = [t["ip_address"] for t in fraud_txns]
    fraud_devices  = [t["device_id"] or "" for t in fraud_txns]
    fraud_hours    = [int(t["created_at"][11:13]) for t in fraud_txns]
    fraud_merch    = [t["merchant_id"] for t in fraud_txns]

    total      = len(transactions)
    n_labels   = len(labels)
    n_fraud    = len(fraud_txns)
    avg_fraud  = statistics.mean(fraud_amounts) if fraud_amounts else 0
    avg_legit  = statistics.mean(legit_amounts) if legit_amounts else 1
    ratio      = avg_fraud / avg_legit
    fraud_rate_in_labels = n_fraud / max(n_labels, 1) * 100

    ip_rate    = sum(1 for ip in fraud_ips    if ip in FRAUD_IPS)          / max(n_fraud, 1)
    dev_rate   = sum(1 for d  in fraud_devices if d.startswith("d_fraud_")) / max(n_fraud, 1)
    night_rate = sum(1 for h  in fraud_hours   if h < 5)                   / max(n_fraud, 1)
    merch_rate = sum(1 for m  in fraud_merch   if m in HIGH_RISK_MERCHANTS) / max(n_fraud, 1)

    coverage = n_labels / max(total, 1)

    results: dict[str, Any] = {
        "total_transactions":  total,
        "n_labels":            n_labels,
        "label_coverage_pct":  round(coverage * 100, 1),
        "n_fraud":             n_fraud,
        "fraud_rate_in_labels_pct": round(fraud_rate_in_labels, 1),
        "fraud_avg_amount":    round(avg_fraud),
        "legit_avg_amount":    round(avg_legit),
        "amount_ratio":        round(ratio, 1),
        "fraud_ip_rate_pct":   round(ip_rate   * 100, 1),
        "fraud_dev_rate_pct":  round(dev_rate  * 100, 1),
        "fraud_night_rate_pct":round(night_rate * 100, 1),
        "fraud_merch_rate_pct":round(merch_rate * 100, 1),
        "PASS_label_coverage": 0.08 <= coverage <= 0.22,
        "PASS_fraud_rate":    5.0 <= fraud_rate_in_labels <= 55.0,
        "PASS_amount_ratio":  ratio      >= 1.0,
        "PASS_ip_signal":     ip_rate    >= 0.20,
        "PASS_device_signal": dev_rate   >= 0.25,
        "PASS_night_signal":  night_rate >= 0.10,
        "PASS_merch_signal":  merch_rate >= 0.30,
    }
    results["ALL_PASS"] = all(v for k, v in results.items() if k.startswith("PASS_"))
    return results


# ── Public aliases ────────────────────────────────────────────────────────────
make_fraudulent_transaction = _make_fraudulent_transaction
make_legitimate_transaction = _make_legitimate_transaction


# ── Self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Simulator self-test (seed=42/43/44, 20 users, 10 days)...\n")
    users   = generate_users(20,  seed=42)
    devs    = generate_devices(users, seed=43)
    txns    = generate_transactions(users, devs, n_days=10, seed=44)
    avg_map = {u["id"]: u.get("_avg_amount", 500.0) for u in users}
    labels  = generate_labels(txns, avg_map, seed=45)
    r = validate(txns, labels)

    print(f"  Transactions:      {r['total_transactions']}")
    print(f"  Labels:            {r['n_labels']} ({r['label_coverage_pct']}%)")
    print(f"  Label coverage:    {'PASS' if r['PASS_label_coverage'] else 'FAIL'}")
    print(f"  Fraud count:       {r['n_fraud']} ({r['fraud_rate_in_labels_pct']}% of labels)")
    print(f"  Fraud avg:         Rs {r['fraud_avg_amount']:,}")
    print(f"  Legit avg:         Rs {r['legit_avg_amount']:,}")
    print(f"  Amount ratio:      {r['amount_ratio']}x   {'PASS' if r['PASS_amount_ratio'] else 'FAIL'}")
    print(f"  Fraud IP rate:     {r['fraud_ip_rate_pct']}%  {'PASS' if r['PASS_ip_signal'] else 'FAIL'}")
    print(f"  New device rate:   {r['fraud_dev_rate_pct']}%  {'PASS' if r['PASS_device_signal'] else 'FAIL'}")
    print(f"  Late night rate:   {r['fraud_night_rate_pct']}%  {'PASS' if r['PASS_night_signal'] else 'FAIL'}")
    print(f"  Bad merchant rate: {r['fraud_merch_rate_pct']}%  {'PASS' if r['PASS_merch_signal'] else 'FAIL'}")
    print(f"\n  {'ALL CHECKS PASSED' if r['ALL_PASS'] else 'SOME CHECKS FAILED'}")
