"""
scripts/simulate_live.py
─────────────────────────
Continuously submits realistic transactions to the API so the Live Feed
looks like a real fraud analyst dashboard.

Usage:
    python scripts/simulate_live.py             # default ~1 txn / 2-3s
    python scripts/simulate_live.py --rate 1.0  # faster (1 txn/s)
    python scripts/simulate_live.py --rate 5.0  # slower (1 txn/5s)

Mix:  ~80% legit   ·   ~20% fraud / suspicious
"""

import argparse
import random
import time
import uuid

import requests

# ── Config ────────────────────────────────────────────────────────────────────
API_URL = "http://127.0.0.1:8000/v1/transactions/score"
TOKEN   = "dev_token_fraudshield_local_only"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# ── Realistic user population ─────────────────────────────────────────────────
USERS = [
    "u_alice", "u_bob", "u_carol", "u_dave", "u_eve",
    "u_frank", "u_grace", "u_henry", "u_irene", "u_jack",
    "u_kavya", "u_liam",  "u_maya",  "u_neel",  "u_priya",
]

# ── Merchants with weighted selection ─────────────────────────────────────────
MERCHANTS = [
    ("m_grocery",     0.25),
    ("m_amazon",      0.20),
    ("m_zomato",      0.12),
    ("m_netflix",     0.08),
    ("m_uber",        0.08),
    ("m_electronics", 0.10),
    ("m_pharmacy",    0.07),
    ("m_giftcard",    0.05),
    ("m_crypto",      0.03),
    ("m_jewelry",     0.02),
]
_merch_names, _merch_weights = zip(*MERCHANTS)

# ── Channel mix (realistic India payments) ────────────────────────────────────
CHANNELS         = ["online", "upi",  "pos",  "atm",  "nfc"]
CHANNEL_WEIGHTS  = [0.30,     0.35,   0.22,   0.08,   0.05]

# ── IP pools ──────────────────────────────────────────────────────────────────
CLEAN_IPS = [
    "203.112.45.67", "117.196.67.89", "49.207.45.23",
    "103.21.58.100", "106.51.68.220", "182.68.12.34",
    "59.180.22.91",  "122.163.54.77", "115.99.234.5",
]
FRAUD_IPS = [
    "185.220.101.5", "185.220.101.6", "23.129.64.131",
    "192.42.116.16", "199.87.154.255",
]

# ── Currency mix ──────────────────────────────────────────────────────────────
CURRENCIES = ["INR"] * 14 + ["USD", "EUR"]  # mostly INR, occasional foreign


# ── Transaction generators ────────────────────────────────────────────────────

def _legit_txn() -> dict:
    merchant = random.choices(_merch_names, weights=_merch_weights)[0]
    channel  = random.choices(CHANNELS, weights=CHANNEL_WEIGHTS)[0]
    # Amounts calibrated per merchant type
    amount_ranges = {
        "m_grocery": (200, 3500), "m_amazon": (500, 8000),
        "m_zomato":  (150, 900),  "m_netflix": (199, 799),
        "m_uber":    (80, 600),   "m_electronics": (2000, 25000),
        "m_pharmacy":(50, 1200),  "m_giftcard": (500, 5000),
        "m_crypto":  (1000, 15000), "m_jewelry": (3000, 40000),
    }
    lo, hi = amount_ranges.get(merchant, (200, 5000))
    return {
        "user_id":     random.choice(USERS),
        "merchant_id": merchant,
        "amount":      round(random.uniform(lo, hi), 2),
        "currency":    random.choice(CURRENCIES),
        "channel":     channel,
        "ip_address":  random.choice(CLEAN_IPS),
    }


def _fraud_txn() -> dict:
    """High-risk pattern: fraud IP + risky merchant + large amount + online."""
    patterns = [
        # Classic card-not-present fraud
        {"merchant_id": "m_crypto",   "channel": "online", "lo": 20000, "hi": 90000},
        {"merchant_id": "m_giftcard", "channel": "online", "lo": 10000, "hi": 50000},
        {"merchant_id": "m_jewelry",  "channel": "online", "lo": 15000, "hi": 70000},
        # Account takeover — unusual channel for user
        {"merchant_id": "m_amazon",   "channel": "online", "lo": 8000,  "hi": 45000},
        {"merchant_id": "m_electronics", "channel": "online", "lo": 12000, "hi": 60000},
    ]
    p = random.choice(patterns)
    return {
        "user_id":     random.choice(USERS),
        "merchant_id": p["merchant_id"],
        "amount":      round(random.uniform(p["lo"], p["hi"]), 2),
        "currency":    random.choices(["INR", "USD", "EUR"], weights=[0.6, 0.25, 0.15])[0],
        "channel":     p["channel"],
        "ip_address":  random.choice(FRAUD_IPS),
    }


def _suspicious_txn() -> dict:
    """Mid-risk: clean IP but unusual amount/merchant combo."""
    return {
        "user_id":     random.choice(USERS),
        "merchant_id": random.choice(["m_crypto", "m_giftcard", "m_jewelry"]),
        "amount":      round(random.uniform(5000, 20000), 2),
        "currency":    "INR",
        "channel":     random.choice(["online", "upi"]),
        "ip_address":  random.choice(CLEAN_IPS),
    }


# ── Decision colour for terminal output ───────────────────────────────────────
_COLORS = {"allow": "\033[92m", "review": "\033[93m", "block": "\033[91m"}
_RESET  = "\033[0m"


def _print_result(txn: dict, result: dict) -> None:
    decision = result["decision"]
    color    = _COLORS.get(decision, "")
    score    = result["fraud_probability"]
    print(
        f"  {color}[{decision.upper():6}]{_RESET} "
        f"{txn['user_id']:8} -> {txn['merchant_id']:15} "
        f"{txn['currency']:3} {txn['amount']:>9,.0f}  "
        f"score={score:.3f}  "
        f"via {txn['channel']:6}  ip={txn['ip_address']}"
    )


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="FraudShield live transaction simulator")
    parser.add_argument("--rate", type=float, default=2.5,
                        help="Average seconds between transactions (default: 2.5)")
    args = parser.parse_args()

    print(f"\n  FraudShield Live Simulator")
    print(f"  Target: {API_URL}")
    print(f"  Rate:   ~1 transaction every {args.rate}s")
    print(f"  Mix:    80% legit / 12% suspicious / 8% fraud")
    print(f"  Press Ctrl+C to stop\n")

    total = blocked = 0

    while True:
        # Weighted pattern selection
        roll = random.random()
        if roll < 0.08:
            txn = _fraud_txn()
        elif roll < 0.20:
            txn = _suspicious_txn()
        else:
            txn = _legit_txn()

        txn["transaction_id"] = f"sim_{uuid.uuid4().hex[:12]}"

        try:
            r = requests.post(API_URL, json=txn, headers=HEADERS, timeout=5)
            if r.status_code == 200:
                result = r.json()
                _print_result(txn, result)
                total  += 1
                if result["decision"] == "block":
                    blocked += 1
                if total % 20 == 0:
                    print(f"\n  -- {total} transactions - {blocked} blocked "
                          f"({blocked/total*100:.1f}% block rate) --\n")
            else:
                print(f"  [ERROR {r.status_code}] {r.text[:120]}")
        except requests.ConnectionError:
            print("  [ERROR] Cannot reach API — is fraud_api running?")
        except Exception as e:
            print(f"  [ERROR] {e}")

        # Jitter ±40% so the feed doesn't look mechanical
        sleep = args.rate * random.uniform(0.6, 1.4)
        time.sleep(sleep)


if __name__ == "__main__":
    main()
