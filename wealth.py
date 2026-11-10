"""Net worth, investments, savings goals and categorization rules."""

from __future__ import annotations

from datetime import date, timedelta

from .store import _lock, now_iso

ASSET_TYPES = ("depository", "investment", "brokerage", "other")
LIABILITY_TYPES = ("credit", "loan")


# ---------- categorization rules ----------

def apply_rules(conn) -> None:
    """Recompute rule categories for every transaction. Newer rules win over older ones."""
    rules = conn.execute("SELECT * FROM rules ORDER BY id").fetchall()
    with _lock, conn:
        conn.execute("UPDATE transactions SET rule_category = NULL WHERE rule_category IS NOT NULL")
        for r in rules:
            sql = ("UPDATE transactions SET rule_category = ? WHERE (LOWER(COALESCE(merchant, '')) LIKE ?"
                   " OR LOWER(COALESCE(name, '')) LIKE ?)")
            args: list = [r["category"], f"%{r['pattern'].lower()}%", f"%{r['pattern'].lower()}%"]
            if r["min_cents"] is not None:
                sql += " AND amount_cents >= ?"
                args.append(r["min_cents"])
            if r["max_cents"] is not None:
                sql += " AND amount_cents <= ?"
                args.append(r["max_cents"])
            conn.execute(sql, args)


def add_rule(conn, pattern: str, category: str, min_cents=None, max_cents=None) -> dict:
    pattern = pattern.strip()
    if not pattern:
        raise ValueError("a rule needs text to match")
    with _lock, conn:
        cur = conn.execute("INSERT INTO rules(pattern, category, min_cents, max_cents, created_at) VALUES(?,?,?,?,?)",
                           (pattern, category, min_cents, max_cents, now_iso()))
    apply_rules(conn)
    matched = conn.execute("SELECT COUNT(*) FROM transactions WHERE rule_category = ?", (category,)).fetchone()[0]
    return {"id": cur.lastrowid, "pattern": pattern, "category": category, "matched": matched}


def remove_rule(conn, rule_id: int) -> bool:
    with _lock, conn:
        n = conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,)).rowcount
    apply_rules(conn)
    return bool(n)


def list_rules(conn) -> list[dict]:
    from .insights import label

    out = []
    for r in conn.execute("SELECT * FROM rules ORDER BY id DESC").fetchall():
        n = conn.execute("SELECT COUNT(*) FROM transactions WHERE LOWER(COALESCE(merchant, '')) LIKE ?"
                         " OR LOWER(COALESCE(name, '')) LIKE ?",
                         (f"%{r['pattern'].lower()}%",) * 2).fetchone()[0]
        out.append({"id": r["id"], "pattern": r["pattern"], "category": r["category"],
                    "category_label": label(r["category"]), "min_cents": r["min_cents"],
                    "max_cents": r["max_cents"], "matches": n})
    return out


# ---------- net worth ----------

def balances(conn) -> dict:
    rows = conn.execute("SELECT type, COALESCE(SUM(current_cents), 0) FROM accounts GROUP BY type").fetchall()
    by = {r[0] or "other": r[1] for r in rows}
    cash = by.get("depository", 0)
    invest = by.get("investment", 0) + by.get("brokerage", 0)
    other = by.get("other", 0)
    credit, loans = by.get("credit", 0), by.get("loan", 0)
    return {"assets_cents": cash + invest + other, "liabilities_cents": credit + loans, "cash_cents": cash,
            "investments_cents": invest, "other_assets_cents": other, "credit_cents": credit, "loans_cents": loans}


def snapshot(conn, day: date) -> None:
    """Record today's net worth. One row per day; later syncs the same day overwrite it."""
    b = balances(conn)
    if not conn.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
        return
    with _lock, conn:
        conn.execute(
            "INSERT OR REPLACE INTO networth(date, assets_cents, liabilities_cents, cash_cents, investments_cents,"
            " credit_cents, loans_cents) VALUES(?,?,?,?,?,?,?)",
            (day.isoformat(), b["assets_cents"], b["liabilities_cents"], b["cash_cents"], b["investments_cents"],
             b["credit_cents"], b["loans_cents"]))


def networth(conn, today: date, days: int = 365) -> dict:
    b = balances(conn)
    net = b["assets_cents"] - b["liabilities_cents"]
    hist = [dict(r) for r in conn.execute(
        "SELECT date, assets_cents, liabilities_cents FROM networth WHERE date >= ? ORDER BY date",
        ((today - timedelta(days=days)).isoformat(),)).fetchall()]
    for h in hist:
        h["net_cents"] = h["assets_cents"] - h["liabilities_cents"]

    def change_since(d: date):
        past = conn.execute("SELECT assets_cents - liabilities_cents FROM networth WHERE date <= ?"
                            " ORDER BY date DESC LIMIT 1", (d.isoformat(),)).fetchone()
        return None if past is None else net - past[0]

    accts = [dict(r) for r in conn.execute(
        "SELECT account_id, name, mask, type, subtype, current_cents FROM accounts ORDER BY type, current_cents DESC")]
    return {
        "net_cents": net, **b,
        "change_30d_cents": change_since(today - timedelta(days=30)),
        "change_ytd_cents": change_since(date(today.year, 1, 1)),
        "history": hist,
        "accounts": [{**a, "side": "liability" if a["type"] in LIABILITY_TYPES else "asset"} for a in accts],
    }


# ---------- investments ----------

TYPE_LABELS = {"equity": "Stocks", "etf": "ETFs", "mutual fund": "Mutual funds", "fixed income": "Bonds",
               "cash": "Cash", "cryptocurrency": "Crypto", "derivative": "Options", "loan": "Loans", "other": "Other"}


def save_holdings(conn, holdings: list[dict], securities: list[dict], account_ids=None) -> None:
    from .store import cents

    stamp = now_iso()
    with _lock, conn:
        for s in securities:
            conn.execute(
                "INSERT OR REPLACE INTO securities(security_id, name, ticker, type, close_cents, close_date)"
                " VALUES(?,?,?,?,?,?)",
                (s["security_id"], s.get("name"), s.get("ticker_symbol"), s.get("type"),
                 None if s.get("close_price") is None else cents(s["close_price"]), s.get("close_price_as_of")))
        for acct in {h["account_id"] for h in holdings} | set(account_ids or []):
            conn.execute("DELETE FROM holdings WHERE account_id = ?", (acct,))
        for h in holdings:
            value = h.get("institution_value")
            if value is None and h.get("institution_price") is not None:
                value = h["institution_price"] * (h.get("quantity") or 0)
            conn.execute(
                "INSERT OR REPLACE INTO holdings(account_id, security_id, quantity, value_cents, cost_cents, updated_at)"
                " VALUES(?,?,?,?,?,?)",
                (h["account_id"], h["security_id"], h.get("quantity"), None if value is None else cents(value),
                 None if h.get("cost_basis") is None else cents(h["cost_basis"]), stamp))


def investments(conn) -> dict:
    rows = [dict(r) for r in conn.execute(
        "SELECT h.*, s.name, s.ticker, s.type AS sec_type, a.name AS account FROM holdings h"
        " LEFT JOIN securities s ON s.security_id = h.security_id"
        " LEFT JOIN accounts a ON a.account_id = h.account_id ORDER BY h.value_cents DESC")]
    total = sum(r["value_cents"] or 0 for r in rows)
    with_cost = [r for r in rows if r["cost_cents"] is not None and r["value_cents"] is not None]
    cost = sum(r["cost_cents"] for r in with_cost)
    gain = sum(r["value_cents"] for r in with_cost) - cost
    alloc: dict[str, int] = {}
    for r in rows:
        key = TYPE_LABELS.get(r["sec_type"] or "other", (r["sec_type"] or "Other").title())
        alloc[key] = alloc.get(key, 0) + (r["value_cents"] or 0)
    accounts_without = [dict(r) for r in conn.execute(
        "SELECT name, current_cents FROM accounts WHERE type IN ('investment', 'brokerage')"
        " AND account_id NOT IN (SELECT account_id FROM holdings)")]
    return {
        "total_cents": total,
        "cost_basis_cents": cost if with_cost else None,
        "gain_cents": gain if with_cost else None,
        "gain_pct": round(100 * gain / cost, 1) if with_cost and cost else None,
        "allocation": sorted(({"type": k, "value_cents": v, "pct": round(100 * v / total, 1) if total else 0}
                              for k, v in alloc.items()), key=lambda a: -a["value_cents"]),
        "holdings": [{
            "ticker": r["ticker"], "name": r["name"], "type": TYPE_LABELS.get(r["sec_type"] or "other", r["sec_type"]),
            "account": r["account"], "quantity": r["quantity"], "value_cents": r["value_cents"],
            "cost_cents": r["cost_cents"],
            "gain_cents": None if r["cost_cents"] is None or r["value_cents"] is None else r["value_cents"] - r["cost_cents"],
            "pct_of_portfolio": round(100 * (r["value_cents"] or 0) / total, 1) if total else 0,
        } for r in rows],
        "accounts_without_holdings": accounts_without,
    }


# ---------- goals ----------

def _months_between(a: date, b: date) -> float:
    return (b.year - a.year) * 12 + (b.month - a.month) + (b.day - a.day) / 30.4


def add_goal(conn, name: str, target_cents: int, target_date: str | None = None, account_id: str | None = None,
             saved_cents: int = 0) -> dict:
    if target_cents <= 0:
        raise ValueError("target must be positive")
    if target_date:
        target_date = date.fromisoformat(target_date).isoformat()
    start = saved_cents
    if account_id:
        row = conn.execute("SELECT current_cents FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
        if not row:
            raise ValueError(f"unknown account {account_id}")
        start = row[0] or 0
    with _lock, conn:
        cur = conn.execute(
            "INSERT INTO goals(name, target_cents, target_date, account_id, saved_cents, start_cents, created_at)"
            " VALUES(?,?,?,?,?,?,?)", (name, target_cents, target_date, account_id, saved_cents, start, now_iso()))
    return {"id": cur.lastrowid}


def update_goal(conn, goal_id: int, **fields) -> None:
    allowed = {"name", "target_cents", "target_date", "account_id", "saved_cents"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return
    # goals() divides by the target and parses the date on every overview; a bad value would break them all.
    if "target_cents" in sets and sets["target_cents"] <= 0:
        raise ValueError("target must be positive")
    if "target_date" in sets:
        sets["target_date"] = date.fromisoformat(sets["target_date"]).isoformat() if sets["target_date"] else None
    if sets.get("account_id") and not conn.execute(
            "SELECT 1 FROM accounts WHERE account_id = ?", (sets["account_id"],)).fetchone():
        raise ValueError(f"unknown account {sets['account_id']}")
    with _lock, conn:
        conn.execute(f"UPDATE goals SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", [*sets.values(), goal_id])


def remove_goal(conn, goal_id: int) -> bool:
    with _lock, conn:
        return bool(conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,)).rowcount)


def goals(conn, today: date) -> list[dict]:
    out = []
    for g in conn.execute(
            "SELECT g.*, a.current_cents, a.name AS account FROM goals g"
            " LEFT JOIN accounts a ON a.account_id = g.account_id ORDER BY g.target_date IS NULL, g.target_date, g.id"):
        current = (g["current_cents"] or 0) if g["account_id"] else g["saved_cents"]
        target = g["target_cents"]
        item = {"id": g["id"], "name": g["name"], "target_cents": target, "current_cents": current,
                "remaining_cents": max(0, target - current), "pct": min(100, round(100 * current / target)),
                "target_date": g["target_date"], "account": g["account"], "account_id": g["account_id"],
                "tracking": "account balance" if g["account_id"] else "manual"}
        if current >= target:
            item["status"] = "done"
        elif g["target_date"]:
            end = date.fromisoformat(g["target_date"])
            start = date.fromisoformat(g["created_at"][:10])
            months_left = _months_between(today, end)
            item["months_left"] = round(max(0.0, months_left), 1)
            item["needed_per_month_cents"] = int(item["remaining_cents"] / months_left) if months_left > 0.5 else item["remaining_cents"]
            span = max(1, (end - start).days)
            expected = g["start_cents"] + (target - g["start_cents"]) * min(1, max(0, (today - start).days) / span)
            item["expected_cents"] = int(expected)
            item["status"] = "overdue" if today > end else "behind" if current < expected * 0.97 else "on_track"
        else:
            item["status"] = "no_date"
        out.append(item)
    return out

