"""Deterministic money math. The model explains these numbers; it never computes them."""

from __future__ import annotations

import calendar
import re
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from .store import fmt, get_meta, now_iso, _lock

LABELS = {
    "FOOD_AND_DRINK": "Food & drink",
    "GENERAL_MERCHANDISE": "Shopping",
    "RENT_AND_UTILITIES": "Rent & utilities",
    "TRANSPORTATION": "Transportation",
    "TRAVEL": "Travel",
    "ENTERTAINMENT": "Entertainment",
    "PERSONAL_CARE": "Personal care",
    "MEDICAL": "Medical",
    "GENERAL_SERVICES": "Services",
    "HOME_IMPROVEMENT": "Home",
    "GOVERNMENT_AND_NON_PROFIT": "Taxes & giving",
    "BANK_FEES": "Bank fees",
    "LOAN_PAYMENTS": "Loan payments",
    "INCOME": "Income",
    "TRANSFER_IN": "Transfers in",
    "TRANSFER_OUT": "Transfers out",
    "OTHER": "Other",
}

# Money moving between your own accounts, or arriving, is not spending.
NOT_SPEND = ("INCOME", "TRANSFER_IN", "TRANSFER_OUT")
CARD_PAYMENT = "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"

# Shared WHERE clause for "this row is spending". Refunds (negative amounts) net against spend.
SPEND_SQL = (
    "COALESCE(t.user_category, t.category, 'OTHER') NOT IN ('INCOME','TRANSFER_IN','TRANSFER_OUT')"
    " AND COALESCE(t.detailed, '') != 'LOAN_PAYMENTS_CREDIT_CARD_PAYMENT'"
    " AND COALESCE(a.type, 'depository') IN ('depository', 'credit')"
)
CAT_SQL = "COALESCE(t.user_category, t.category, 'OTHER')"
FROM_SQL = "FROM transactions t LEFT JOIN accounts a ON a.account_id = t.account_id"


def label(category: str) -> str:
    return LABELS.get(category, category.replace("_", " ").title())


def category_key(text: str) -> str:
    """Accept 'Food & drink', 'food', or 'FOOD_AND_DRINK'."""
    t = (text or "").strip()
    up = re.sub(r"[^A-Z]+", "_", t.upper().replace("&", "AND")).strip("_")
    if up in LABELS:
        return up
    for key, lab in LABELS.items():
        if lab.lower() == t.lower() or key.startswith(up) or lab.lower().startswith(t.lower()):
            return key
    return up or "OTHER"


def month_of(d: date) -> str:
    return d.strftime("%Y-%m")


def month_range(month: str) -> tuple[date, date]:
    y, m = (int(x) for x in month.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def shift_month(month: str, delta: int) -> str:
    y, m = (int(x) for x in month.split("-"))
    idx = y * 12 + (m - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def spending_by_category(conn, start: date, end: date) -> list[dict]:
    rows = conn.execute(
        f"SELECT {CAT_SQL} AS cat, SUM(t.amount_cents) AS c, COUNT(*) AS n {FROM_SQL}"
        f" WHERE {SPEND_SQL} AND t.date BETWEEN ? AND ? GROUP BY cat ORDER BY c DESC",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return [
        {"category": r["cat"], "label": label(r["cat"]), "cents": r["c"], "count": r["n"]}
        for r in rows if r["c"] > 0
    ]


def spending_by_merchant(conn, start: date, end: date, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        f"SELECT COALESCE(t.merchant, t.name) AS m, SUM(t.amount_cents) AS c, COUNT(*) AS n {FROM_SQL}"
        f" WHERE {SPEND_SQL} AND t.date BETWEEN ? AND ? GROUP BY m ORDER BY c DESC LIMIT ?",
        (start.isoformat(), end.isoformat(), limit),
    ).fetchall()
    return [{"merchant": r["m"], "cents": r["c"], "count": r["n"]} for r in rows if r["c"] > 0]


def total_spend(conn, start: date, end: date, category: str | None = None) -> int:
    sql = f"SELECT COALESCE(SUM(t.amount_cents), 0) {FROM_SQL} WHERE {SPEND_SQL} AND t.date BETWEEN ? AND ?"
    args: list = [start.isoformat(), end.isoformat()]
    if category:
        sql += f" AND {CAT_SQL} = ?"
        args.append(category)
    return conn.execute(sql, args).fetchone()[0]


def total_income(conn, start: date, end: date) -> int:
    return -conn.execute(
        f"SELECT COALESCE(SUM(t.amount_cents), 0) {FROM_SQL}"
        f" WHERE {CAT_SQL} = 'INCOME' AND t.date BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    ).fetchone()[0]


def monthly_totals(conn, today: date, months: int = 6) -> list[dict]:
    out = []
    current = month_of(today)
    for i in range(months - 1, -1, -1):
        m = shift_month(current, -i)
        start, end = month_range(m)
        out.append({
            "month": m,
            "spend_cents": total_spend(conn, start, end),
            "income_cents": total_income(conn, start, end),
            "partial": m == current,
        })
    return out


# ---------- budgets ----------

def propose_budgets(conn, today: date, months: int = 3) -> list[dict]:
    """Median of the last `months` complete months per category, rounded up to $10.
    Categories under $20/month are skipped. Active budgets keep their amount; only the proposal updates."""
    current = month_of(today)
    span = [shift_month(current, -i) for i in range(months, 0, -1)]
    per_month = []
    for m in span:
        start, end = month_range(m)
        per_month.append({r["category"]: r["cents"] for r in spending_by_category(conn, start, end)})
    proposals = []
    stamp = now_iso()
    for cat in set().union(*per_month):
        values = [totals.get(cat, 0) for totals in per_month]
        med = median(values)
        if med < 2000:
            continue
        amount = int(-(-med // 1000) * 1000)  # ceil to $10
        names = ", ".join(f"{calendar.month_abbr[int(m[5:])]} {fmt(v)}" for m, v in zip(span, values))
        basis = f"Median of {names}"
        proposals.append({"category": cat, "label": label(cat), "proposed_cents": amount, "basis": basis})
        with _lock:
            conn.execute(
                "INSERT INTO budgets(category, proposed_cents, basis, status, updated_at) VALUES(?,?,?,'proposed',?)"
                " ON CONFLICT(category) DO UPDATE SET proposed_cents=excluded.proposed_cents,"
                " basis=excluded.basis, updated_at=excluded.updated_at",
                (cat, amount, basis, stamp),
            )
    conn.commit()
    proposals.sort(key=lambda p: -p["proposed_cents"])
    return proposals


def approve_budgets(conn, categories: list[str] | None = None) -> list[str]:
    rows = conn.execute("SELECT category FROM budgets WHERE proposed_cents IS NOT NULL").fetchall()
    chosen = [r[0] for r in rows if not categories or r[0] in categories]
    with _lock:
        for cat in chosen:
            conn.execute(
                "UPDATE budgets SET amount_cents=proposed_cents, status='active', updated_at=? WHERE category=?",
                (now_iso(), cat),
            )
        conn.commit()
    return chosen


def set_budget(conn, category: str, amount_cents: int | None) -> None:
    with _lock:
        if amount_cents is None or amount_cents <= 0:
            conn.execute("DELETE FROM budgets WHERE category=?", (category,))
        else:
            conn.execute(
                "INSERT INTO budgets(category, amount_cents, status, updated_at) VALUES(?,?,'active',?)"
                " ON CONFLICT(category) DO UPDATE SET amount_cents=excluded.amount_cents, status='active',"
                " updated_at=excluded.updated_at",
                (category, amount_cents, now_iso()),
            )
        conn.commit()


def budget_status(conn, today: date, month: str | None = None) -> dict:
    month = month or month_of(today)
    start, end = month_range(month)
    days = end.day
    elapsed = days if month < month_of(today) else max(1, min(today.day, days))
    spent = {r["category"]: r["cents"] for r in spending_by_category(conn, start, end)}
    fixed_spent, fixed_due = _fixed_charges(conn, today, start, end)
    biggest = {r[0]: r[1] for r in conn.execute(
        f"SELECT {CAT_SQL}, MAX(t.amount_cents) {FROM_SQL} WHERE {SPEND_SQL} AND t.date BETWEEN ? AND ? GROUP BY 1",
        (start.isoformat(), end.isoformat()))}
    rows = conn.execute("SELECT * FROM budgets ORDER BY category").fetchall()
    budgets, proposed = [], []
    for r in rows:
        cat = r["category"]
        if r["status"] == "active" and r["amount_cents"]:
            s = spent.get(cat, 0)
            # Fixed bills land once; only flexible spending continues at its daily rate.
            # The biggest single charge is treated as a one-off, so one laptop does not project a month of laptops.
            flexible = max(0, s - fixed_spent.get(cat, 0) - biggest.get(cat, 0))
            projected = s + int(flexible * (days - elapsed) / elapsed) + fixed_due.get(cat, 0)
            budgets.append({
                "category": cat, "label": label(cat), "budget_cents": r["amount_cents"], "spent_cents": s,
                "remaining_cents": r["amount_cents"] - s, "pct": round(100 * s / r["amount_cents"]),
                "projected_cents": projected,
                "status": "over" if s > r["amount_cents"] else "warn" if projected > r["amount_cents"] else "ok",
            })
        elif r["proposed_cents"]:
            proposed.append({
                "category": cat, "label": label(cat), "proposed_cents": r["proposed_cents"],
                "basis": r["basis"], "spent_cents": spent.get(cat, 0),
            })
    total_budget = sum(b["budget_cents"] for b in budgets)
    budgeted_spend = sum(b["spent_cents"] for b in budgets)
    budgets.sort(key=lambda b: -b["pct"])
    return {
        "month": month, "days_in_month": days, "days_elapsed": elapsed,
        "budgets": budgets, "proposed": proposed,
        "total_budget_cents": total_budget, "total_spent_cents": budgeted_spend,
        "unbudgeted_spend_cents": sum(v for k, v in spent.items() if k not in {b["category"] for b in budgets}),
    }


def _fixed_charges(conn, today: date, start: date, end: date) -> tuple[dict, dict]:
    """Per category: recurring charges already posted this month, and those still due before month end."""
    spent: dict[str, int] = defaultdict(int)
    due: dict[str, int] = defaultdict(int)
    if not (start <= today <= end):
        return spent, due
    for r in recurring(conn, today):
        if start.isoformat() <= r["last_date"] <= end.isoformat():
            spent[r["category"]] += r["last_cents"]
        if today.isoformat() < r["next_date"] <= end.isoformat():
            due[r["category"]] += r["last_cents"]
    return spent, due


# ---------- recurring charges ----------

CADENCES = [("weekly", 7, 2), ("biweekly", 14, 3), ("monthly", 30.4, 5), ("quarterly", 91, 10), ("yearly", 365, 20)]


def _norm(merchant: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (merchant or "").lower()).strip()


def recurring(conn, today: date) -> list[dict]:
    """Merchants charged on a steady cadence with steady amounts. Needs three or more posted charges."""
    since = (today - timedelta(days=400)).isoformat()
    rows = conn.execute(
        f"SELECT t.id, t.date, t.amount_cents, COALESCE(t.merchant, t.name) AS m, {CAT_SQL} AS cat {FROM_SQL}"
        f" WHERE {SPEND_SQL} AND t.pending = 0 AND t.amount_cents > 0 AND t.date >= ? ORDER BY t.date",
        (since,),
    ).fetchall()
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[_norm(r["m"])].append(r)
    found = []
    for key, txs in groups.items():
        if len(txs) < 3 or not key:
            continue
        dates = [date.fromisoformat(t["date"]) for t in txs]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        if not gaps or min(gaps) == 0:
            continue
        gap = median(gaps)
        cadence = next((c for c in CADENCES if abs(gap - c[1]) <= c[2]), None)
        if not cadence:
            continue
        on_time = sum(abs(g - cadence[1]) <= cadence[2] for g in gaps)
        amounts = [t["amount_cents"] for t in txs]
        typical = median(amounts)
        steady = sum(abs(a - typical) <= 0.25 * typical for a in amounts)
        if on_time < 0.7 * len(gaps) or steady < 0.7 * len(amounts):
            continue
        last = dates[-1]
        nxt = add_months(last, 1) if cadence[0] == "monthly" else add_months(last, 3) if cadence[0] == "quarterly" \
            else last + timedelta(days=round(cadence[1]))
        if (today - last).days > 2.5 * cadence[1]:
            continue  # stopped
        monthly = int(amounts[-1] * 30.4 / cadence[1])
        found.append({
            "merchant": txs[-1]["m"], "category": txs[-1]["cat"], "cadence": cadence[0],
            "last_date": last.isoformat(), "next_date": nxt.isoformat(),
            "last_cents": amounts[-1], "previous_cents": amounts[-2], "typical_cents": int(typical),
            "monthly_cents": monthly, "count": len(txs), "last_id": txs[-1]["id"],
        })
    found.sort(key=lambda r: r["next_date"])
    return found


# ---------- accounts and overview ----------

def accounts(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT a.*, i.institution, i.status AS item_status, i.last_sync FROM accounts a"
        " LEFT JOIN items i ON i.item_id = a.item_id ORDER BY a.type, a.name"
    ).fetchall()
    return [dict(r) for r in rows]


def overview(conn, today: date) -> dict:
    month = month_of(today)
    start, end = month_range(month)
    prev_start, prev_end = month_range(shift_month(month, -1))
    same_day_prev = min(prev_end, prev_start + timedelta(days=today.day - 1))
    accts = accounts(conn)
    cash = sum(a["current_cents"] or 0 for a in accts if a["type"] == "depository")
    owed = sum(a["current_cents"] or 0 for a in accts if a["type"] == "credit")
    bills = [r for r in recurring(conn, today) if r["next_date"] <= (today + timedelta(days=14)).isoformat()]
    status = budget_status(conn, today)
    open_alerts = conn.execute("SELECT COUNT(*) FROM alerts WHERE dismissed_at IS NULL").fetchone()[0]
    last_sync = conn.execute("SELECT MAX(last_sync) FROM items").fetchone()[0]
    return {
        "today": today.isoformat(),
        "month": month,
        "spent_cents": total_spend(conn, start, end),
        "spent_same_point_last_month_cents": total_spend(conn, prev_start, same_day_prev),
        "income_cents": total_income(conn, start, end),
        "cash_cents": cash,
        "credit_owed_cents": owed,
        "budget": {k: status[k] for k in ("total_budget_cents", "total_spent_cents", "budgets", "proposed")},
        "top_categories": spending_by_category(conn, start, end)[:6],
        "upcoming_bills": bills,
        "open_alerts": open_alerts,
        "accounts": len(accts),
        "last_sync": last_sync,
        "demo": get_meta(conn, "demo") == "1",
    }


def find_transactions(conn, *, query=None, category=None, merchant=None, start=None, end=None,
                      min_cents=None, ids=None, limit=50) -> list[dict]:
    sql = (
        f"SELECT t.*, {CAT_SQL} AS cat, a.name AS account, a.mask {FROM_SQL} WHERE 1=1"
    )
    args: list = []
    if ids:
        sql += f" AND t.id IN ({','.join('?' * len(ids))})"
        args += list(ids)
    if query:
        sql += " AND (t.name LIKE ? OR t.merchant LIKE ?)"
        args += [f"%{query}%"] * 2
    if merchant:
        sql += " AND COALESCE(t.merchant, t.name) LIKE ?"
        args.append(f"%{merchant}%")
    if category:
        sql += f" AND {CAT_SQL} = ?"
        args.append(category)
    if start:
        sql += " AND t.date >= ?"
        args.append(str(start))
    if end:
        sql += " AND t.date <= ?"
        args.append(str(end))
    if min_cents is not None:
        sql += " AND ABS(t.amount_cents) >= ?"
        args.append(min_cents)
    sql += " ORDER BY t.date DESC, t.amount_cents DESC LIMIT ?"
    args.append(int(limit))
    return [
        {
            "id": r["id"], "date": r["date"], "amount_cents": r["amount_cents"],
            "merchant": r["merchant"] or r["name"], "description": r["name"],
            "category": r["cat"], "category_label": label(r["cat"]), "pending": bool(r["pending"]),
            "account": f"{r['account'] or ''} {('••' + r['mask']) if r['mask'] else ''}".strip(),
        }
        for r in conn.execute(sql, args).fetchall()
    ]


def recategorize(conn, txn_id: str, category: str, all_from_merchant: bool = False) -> int:
    with _lock:
        if all_from_merchant:
            row = conn.execute("SELECT COALESCE(merchant, name) FROM transactions WHERE id=?", (txn_id,)).fetchone()
            if not row:
                return 0
            n = conn.execute(
                "UPDATE transactions SET user_category=? WHERE COALESCE(merchant, name)=?", (category, row[0])
            ).rowcount
        else:
            n = conn.execute("UPDATE transactions SET user_category=? WHERE id=?", (category, txn_id)).rowcount
        conn.commit()
    return n
