"""Sample data so the plugin can be tried (and recorded) without a bank. Everything is labeled as demo."""

from __future__ import annotations

import random
from datetime import date, timedelta

from . import wealth
from .plaid import save_sync
from .store import _lock, now_iso, set_meta

ACCOUNTS = [
    {"account_id": "demo-checking", "name": "Everyday Checking", "mask": "4021", "type": "depository",
     "subtype": "checking", "balances": {"current": 4820.37, "available": 4820.37}},
    {"account_id": "demo-savings", "name": "High-Yield Savings", "mask": "8830", "type": "depository",
     "subtype": "savings", "balances": {"current": 12400.00, "available": 12400.00}},
    {"account_id": "demo-credit", "name": "Rewards Visa", "mask": "1177", "type": "credit",
     "subtype": "credit card", "balances": {"current": 1236.18, "limit": 9000}},
    {"account_id": "demo-brokerage", "name": "Brokerage", "mask": "5512", "type": "investment",
     "subtype": "brokerage", "balances": {"current": 50532.08}},  # = sum of HOLDINGS
    {"account_id": "demo-401k", "name": "401(k)", "mask": "0907", "type": "investment",
     "subtype": "401k", "balances": {"current": 61831.04}},
    {"account_id": "demo-student-loan", "name": "Student Loan", "mask": "3301", "type": "loan",
     "subtype": "student", "balances": {"current": 18420.77}},
]

# ticker, name, type, account, quantity, price, cost basis
HOLDINGS = [
    ("VTI", "Vanguard Total Stock Market ETF", "etf", "demo-brokerage", 82, 291.40, 19120.00),
    ("VXUS", "Vanguard Total International Stock ETF", "etf", "demo-brokerage", 140, 66.35, 8410.00),
    ("AAPL", "Apple Inc.", "equity", "demo-brokerage", 25, 228.10, 4380.00),
    ("NVDA", "NVIDIA Corp.", "equity", "demo-brokerage", 30, 140.25, 2950.00),
    ("BND", "Vanguard Total Bond Market ETF", "etf", "demo-brokerage", 70, 72.80, 5260.00),
    ("CASH", "Cash sweep", "cash", "demo-brokerage", 2342.28, 1.00, 2342.28),
    ("FXAIX", "Fidelity 500 Index Fund", "mutual fund", "demo-401k", 228.4, 205.60, 39800.00),
    ("FXNAX", "Fidelity U.S. Bond Index Fund", "mutual fund", "demo-401k", 1430, 10.40, 15200.00),
]

# merchant, primary category, detailed, low, high
EVERYDAY = [
    ("Trader Joe's", "FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES", 38, 95, 0.22),
    ("Whole Foods Market", "FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES", 45, 140, 0.10),
    ("Blue Bottle Coffee", "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE", 5.5, 9, 0.35),
    ("Sweetgreen", "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT", 14, 19, 0.12),
    ("Chipotle", "FOOD_AND_DRINK", "FOOD_AND_DRINK_FAST_FOOD", 11, 16, 0.08),
    ("Nopa", "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT", 45, 110, 0.04),
    ("Uber", "TRANSPORTATION", "TRANSPORTATION_TAXIS_AND_RIDE_SHARES", 14, 38, 0.12),
    ("Shell", "TRANSPORTATION", "TRANSPORTATION_GAS", 38, 62, 0.06),
    ("Amazon", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES", 12, 85, 0.14),
    ("Target", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_SUPERSTORES", 20, 90, 0.05),
    ("CVS Pharmacy", "MEDICAL", "MEDICAL_PHARMACIES_AND_SUPPLEMENTS", 8, 35, 0.04),
    ("AMC Theatres", "ENTERTAINMENT", "ENTERTAINMENT_TV_AND_MOVIES", 18, 32, 0.03),
]


def _day_in_month(d: date, day: int) -> date:
    for back in range(4):
        try:
            return d.replace(day=day - back)
        except ValueError:
            continue
    return d


def build(today: date, months: int = 6, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    start = today - timedelta(days=months * 31)
    tx: list[dict] = []

    def add(d, merchant, amount, cat, detailed, account="demo-credit", name=None, pending=False):
        tx.append({
            "transaction_id": f"demo-{len(tx):05d}", "account_id": account, "date": d.isoformat(),
            "amount": round(amount, 2), "name": name or merchant.upper(), "merchant_name": merchant,
            "personal_finance_category": {"primary": cat, "detailed": detailed},
            "pending": pending, "payment_channel": "in store",
        })

    netflix_day = (today - timedelta(days=2)).day
    internet_day = (today + timedelta(days=2)).day
    d = start
    while d <= today:
        if d.day == 1:
            add(d, "Mission Street Apartments", 2150, "RENT_AND_UTILITIES", "RENT_AND_UTILITIES_RENT", "demo-checking")
        if d.day == 12:
            add(d, "PG&E", rng.uniform(78, 132), "RENT_AND_UTILITIES", "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY", "demo-checking")
        if d == _day_in_month(d, internet_day):
            add(d, "Sonic Internet", 65, "RENT_AND_UTILITIES", "RENT_AND_UTILITIES_INTERNET_AND_CABLE")
        if d == _day_in_month(d, netflix_day):
            raised = (today - d).days < 20
            add(d, "Netflix", 22.99 if raised else 17.99, "ENTERTAINMENT", "ENTERTAINMENT_TV_AND_MOVIES")
        if d.day == 21:
            add(d, "Spotify", 11.99, "ENTERTAINMENT", "ENTERTAINMENT_MUSIC_AND_AUDIO")
        if d.day == 3:
            add(d, "Equinox", 49, "PERSONAL_CARE", "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS")
        if d.day == 15:
            add(d, "Rewards Visa Payment", 1450, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", "demo-checking")
            add(d, "Transfer to Savings", 400, "TRANSFER_OUT", "TRANSFER_OUT_SAVINGS", "demo-checking")
        if d.weekday() == 4 and (d - date(2024, 1, 5)).days % 14 == 0:
            add(d, "Acme Corp Payroll", -3150, "INCOME", "INCOME_WAGES", "demo-checking", "ACME CORP DIRECT DEP")
        for merchant, cat, detailed, lo, hi, p in EVERYDAY:
            if rng.random() < p:
                add(d, merchant, rng.uniform(lo, hi), cat, detailed, pending=(today - d).days < 1)
        d += timedelta(days=1)

    # The moments worth a ping, all inside the last week.
    add(today - timedelta(days=2), "Apple Store", 1299, "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_ELECTRONICS")
    add(today - timedelta(days=1), "Uber", 186.40, "TRANSPORTATION", "TRANSPORTATION_TAXIS_AND_RIDE_SHARES",
        name="UBER *TRIP SFO")
    dup_day = today - timedelta(days=3)
    add(dup_day, "Sweetgreen", 16.42, "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT")
    add(dup_day, "Sweetgreen", 16.42, "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT")
    return tx


def load(conn, today: date) -> dict:
    clear(conn)
    with _lock:
        conn.execute(
            "INSERT OR REPLACE INTO items(item_id, institution, token_env, status, created_at, last_sync)"
            " VALUES('demo', 'Demo Bank (sample data)', NULL, 'ok', ?, ?)", (now_iso(), now_iso()))
        conn.commit()
    tx = build(today)
    save_sync(conn, "demo", ACCOUNTS, tx, [], [], "demo")
    wealth.save_holdings(
        conn,
        [{"account_id": acct, "security_id": f"demo-{t}", "quantity": q, "institution_value": round(q * p, 2),
          "cost_basis": cost} for t, _, _, acct, q, p, cost in HOLDINGS],
        [{"security_id": f"demo-{t}", "name": n, "ticker_symbol": t, "type": ty, "close_price": p}
         for t, n, ty, _, _, p, _ in HOLDINGS])
    _networth_history(conn, today)
    wealth.add_goal(conn, "Emergency fund", 2000000, (today + timedelta(days=210)).isoformat(), "demo-savings")
    with _lock, conn:  # pretend the goal started in spring so its pace is meaningful
        conn.execute("UPDATE goals SET start_cents = 900000, created_at = ? WHERE name = 'Emergency fund'",
                     ((today - timedelta(days=150)).isoformat() + "T00:00:00+00:00",))
    wealth.add_goal(conn, "Japan trip", 600000, (today + timedelta(days=150)).isoformat(), saved_cents=210000)
    wealth.add_rule(conn, "Trader Joe", "GROCERIES")
    wealth.add_rule(conn, "Whole Foods", "GROCERIES")
    set_meta(conn, "demo", "1")
    return {"transactions": len(tx), "accounts": len(ACCOUNTS)}


def _networth_history(conn, today: date) -> None:
    """Six months of daily net worth: markets drift up with noise, savings grow, the loan shrinks."""
    rng = random.Random(11)
    end = wealth.balances(conn)
    days = 186
    invest = end["investments_cents"]
    with _lock, conn:
        for i in range(days, -1, -1):
            d = today - timedelta(days=i)
            f = 1 - i / days
            market = invest * (0.86 + 0.14 * f + rng.uniform(-0.012, 0.012) if i else 1)
            cash = end["cash_cents"] - int((1 - f) * 650000) + rng.randint(-60000, 60000) if i else end["cash_cents"]
            loans = end["loans_cents"] + int((1 - f) * 210000)
            credit = end["credit_cents"] + (rng.randint(-40000, 40000) if i else 0)
            conn.execute(
                "INSERT OR REPLACE INTO networth(date, assets_cents, liabilities_cents, cash_cents, investments_cents,"
                " credit_cents, loans_cents) VALUES(?,?,?,?,?,?,?)",
                (d.isoformat(), int(market) + cash, loans + credit, cash, int(market), credit, loans))


def charge(conn, merchant: str, amount: float, category: str, today: date) -> dict:
    """Add one demo card charge dated today, as if a sync had just brought it in."""
    n = conn.execute("SELECT COUNT(*) FROM transactions WHERE item_id='demo'").fetchone()[0]
    t = {
        "transaction_id": f"demo-live-{n:05d}", "account_id": "demo-credit", "date": today.isoformat(),
        "amount": amount, "name": merchant.upper(), "merchant_name": merchant,
        "personal_finance_category": {"primary": category, "detailed": None},
        "pending": True, "payment_channel": "online",
    }
    save_sync(conn, "demo", [], [t], [], [], "demo")
    return {"id": t["transaction_id"]}


def clear(conn) -> None:
    with _lock:
        conn.execute("DELETE FROM transactions WHERE item_id='demo'")
        conn.execute("DELETE FROM accounts WHERE item_id='demo'")
        conn.execute("DELETE FROM items WHERE item_id='demo'")
        conn.execute("DELETE FROM holdings WHERE account_id LIKE 'demo-%'")
        conn.execute("DELETE FROM securities WHERE security_id LIKE 'demo-%'")
        conn.execute("DELETE FROM goals WHERE account_id LIKE 'demo-%' OR name IN ('Emergency fund', 'Japan trip')")
        if not conn.execute("SELECT 1 FROM items WHERE item_id != 'demo' LIMIT 1").fetchone():
            # sample-only state; with a real bank connected these stay
            for table in ("alerts", "budgets", "networth", "rules"):
                conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM meta WHERE key='demo'")
        conn.commit()
