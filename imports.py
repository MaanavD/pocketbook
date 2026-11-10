"""CSV imports: transactions from Monarch, Mint, YNAB, Rocket Money, Copilot or a bank, and Amazon order history.
The format is recognized from the header row; nothing is uploaded anywhere."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime
from pathlib import Path

from . import purchases
from .insights import category_key
from .plaid import save_sync
from .store import _lock, now_iso

DATE = ("date", "transaction date", "posted date", "post date", "posting date", "original date")
DESC = ("merchant", "payee", "custom name", "name", "description", "original statement", "original description", "memo")
CATEGORY = ("category", "category name", "category group/category")
ACCOUNT = ("account", "account name")
NOTES = ("notes", "note", "memo")

# Common category names in other apps -> Plaid primary categories used here.
CATEGORY_MAP = {
    "groceries": "GROCERIES", "restaurants & bars": "FOOD_AND_DRINK", "restaurants": "FOOD_AND_DRINK",
    "coffee shops": "FOOD_AND_DRINK", "fast food": "FOOD_AND_DRINK", "food & dining": "FOOD_AND_DRINK",
    "food & drink": "FOOD_AND_DRINK", "dining": "FOOD_AND_DRINK", "dining & drinks": "FOOD_AND_DRINK",
    "alcohol & bars": "FOOD_AND_DRINK", "shopping": "GENERAL_MERCHANDISE", "clothing": "GENERAL_MERCHANDISE",
    "electronics": "GENERAL_MERCHANDISE", "general merchandise": "GENERAL_MERCHANDISE",
    "rent": "RENT_AND_UTILITIES", "mortgage": "RENT_AND_UTILITIES", "utilities": "RENT_AND_UTILITIES",
    "gas & electric": "RENT_AND_UTILITIES", "internet & cable": "RENT_AND_UTILITIES", "phone": "RENT_AND_UTILITIES",
    "bills & utilities": "RENT_AND_UTILITIES", "housing": "RENT_AND_UTILITIES",
    "gas": "TRANSPORTATION", "auto & transport": "TRANSPORTATION", "parking & tolls": "TRANSPORTATION",
    "taxi & ride shares": "TRANSPORTATION", "public transit": "TRANSPORTATION", "auto payment": "TRANSPORTATION",
    "car": "TRANSPORTATION", "transportation": "TRANSPORTATION", "travel": "TRAVEL", "travel & vacation": "TRAVEL",
    "entertainment": "ENTERTAINMENT", "entertainment & recreation": "ENTERTAINMENT", "subscriptions": "ENTERTAINMENT",
    "medical": "MEDICAL", "healthcare": "MEDICAL", "health & wellness": "MEDICAL", "dentist": "MEDICAL",
    "fitness": "PERSONAL_CARE", "personal care": "PERSONAL_CARE", "gym": "PERSONAL_CARE",
    "home improvement": "HOME_IMPROVEMENT", "home": "HOME_IMPROVEMENT", "furniture & housewares": "HOME_IMPROVEMENT",
    "paychecks": "INCOME", "paycheck": "INCOME", "income": "INCOME", "interest": "INCOME", "salary": "INCOME",
    "other income": "INCOME", "dividends & capital gains": "INCOME",
    "ready to assign": "INCOME", "to be budgeted": "INCOME", "inflow": "INCOME", "inflow: ready to assign": "INCOME",
    "transfer": "TRANSFER", "transfers": "TRANSFER", "internal transfers": "TRANSFER",
    "credit card payment": "CARD_PAYMENT", "credit card payments": "CARD_PAYMENT",
    "loan repayment": "LOAN_PAYMENTS", "student loans": "LOAN_PAYMENTS", "loan payment": "LOAN_PAYMENTS",
    "taxes": "GOVERNMENT_AND_NON_PROFIT", "charity": "GOVERNMENT_AND_NON_PROFIT",
    "gifts & donations": "GOVERNMENT_AND_NON_PROFIT", "financial fees": "BANK_FEES", "bank fees": "BANK_FEES",
    "fees & charges": "BANK_FEES", "service charges": "BANK_FEES",
    "uncategorized": "OTHER", "miscellaneous": "OTHER", "other": "OTHER",
}
CREDIT_HINT = re.compile(r"card|visa|amex|american express|mastercard|discover|credit|sapphire|freedom", re.I)


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").replace("﻿", "").strip().lower())


def _pick(headers: dict[str, str], names) -> str | None:
    return next((headers[n] for n in names if n in headers), None)


def _money(v) -> float | None:
    s = str(v or "").strip().strip("'\"").replace("$", "").replace(",", "").replace(" ", "")
    if not s or s.lower() in ("na", "n/a", "-", "notavailable"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    try:
        x = float(s.strip("()"))
    except ValueError:
        return None
    return -x if neg else x


def _date(v) -> str | None:
    s = str(v or "").strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    for f in ("%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d %b %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            continue
    return None


def detect(header: list[str]) -> str:
    h = {_norm(x) for x in header}
    if "order id" in h and ("product name" in h or "asin" in h):
        return "amazon"
    if "original statement" in h and "merchant" in h:
        return "monarch"
    if "transaction type" in h and "original description" in h:
        return "mint"
    if "outflow" in h and "inflow" in h:
        return "ynab"
    if "custom name" in h and "institution name" in h:
        return "rocket money"
    if "parent category" in h or ("excluded" in h and "recurring" in h):
        return "copilot"
    if h & set(DATE) and ("amount" in h or {"debit", "credit"} <= h):
        return "bank"
    raise ValueError("Could not recognize this CSV. It needs a date column and an amount (or debit and credit) column.")


def read(text: str) -> tuple[str, list[dict], dict[str, str]]:
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        raise ValueError("The file is empty.")
    header = rows[0]
    fmt = detect(header)
    headers = {_norm(x): x for x in header}
    return fmt, [dict(zip(header, r)) for r in rows[1:] if any(c.strip() for c in r)], headers


def import_text(conn, text: str, name: str = "import.csv", fmt: str | None = None) -> dict:
    detected, rows, headers = read(text)
    fmt = fmt or detected
    if fmt == "amazon":
        return {"format": "amazon", **_amazon(conn, rows, headers)}
    return {"format": fmt, **_transactions(conn, rows, headers, fmt, name)}


def import_path(conn, path: str, fmt: str | None = None) -> dict:
    p = Path(path).expanduser()
    return import_text(conn, p.read_text(encoding="utf-8-sig", errors="replace"), p.name, fmt)


def _amazon(conn, rows, headers) -> dict:
    col = lambda *names: _pick(headers, names)
    oid, odate, prod = col("order id"), col("order date"), col("product name", "title")
    qty, asin = col("quantity", "original quantity"), col("asin", "asin/isbn")
    total = col("total owed", "total amount", "item total", "total charged")
    unit = col("unit price", "purchase price per unit", "list price per unit")
    status = col("order status", "shipment status")
    orders: dict[str, dict] = {}
    skipped = 0
    for r in rows:
        if status and "cancel" in (r.get(status) or "").lower():
            skipped += 1
            continue
        d = _date(r.get(odate))
        price = _money(r.get(total)) if total else None
        if price is None and unit:
            u = _money(r.get(unit))
            price = None if u is None else u * int(_money(r.get(qty)) or 1)
        if not d or price is None or not (r.get(prod) or "").strip():
            skipped += 1
            continue
        o = orders.setdefault(r.get(oid) or f"{d}-{len(orders)}", {"order_id": r.get(oid), "date": d, "items": []})
        o["items"].append({"name": r[prod], "price": price, "quantity": int(_money(r.get(qty)) or 1) if qty else 1,
                           "asin": r.get(asin) if asin else None})
    res = purchases.add_orders(conn, list(orders.values()))
    return {**res, "orders": len(orders), "skipped_rows": skipped}


def _transactions(conn, rows, headers, fmt, name) -> dict:
    col = lambda names: _pick(headers, names)
    c_date, c_desc, c_cat, c_acct = col(DATE), col(DESC), col(CATEGORY), col(ACCOUNT)
    c_amt, c_debit, c_credit = col(("amount",)), col(("debit", "outflow")), col(("credit", "inflow"))
    c_type = col(("transaction type",))
    c_orig = col(("original statement", "original description", "description"))
    parsed, skipped = [], 0
    for r in rows:
        d = _date(r.get(c_date))
        if c_debit and c_credit and not c_amt:
            amt = (_money(r.get(c_debit)) or 0) - (_money(r.get(c_credit)) or 0)
        else:
            amt = _money(r.get(c_amt))
            if amt is not None and c_type:
                amt = abs(amt) if (r.get(c_type) or "").strip().lower() == "debit" else -abs(amt)
        if not d or amt is None:
            skipped += 1
            continue
        parsed.append((d, amt, r))
    if not parsed:
        raise ValueError("No rows with a date and an amount.")
    # Most rows are purchases. If most amounts are negative, this app writes spending as negative; flip to ours.
    flip = not (c_type or (c_debit and c_credit and not c_amt)) and sum(a < 0 for _, a, _ in parsed) > len(parsed) / 2
    source = re.sub(r"[^a-z]+", "-", fmt.lower()).strip("-")
    item_id = f"import-{source}"
    accounts, txs, seen = {}, [], {}
    for d, amt, r in parsed:
        amt = -amt if flip else amt
        acct_name = (r.get(c_acct) or "").strip() or f"{fmt.title()} import"
        acct_id = "imp-" + hashlib.sha1(f"{source}|{acct_name}".encode()).hexdigest()[:10]
        accounts.setdefault(acct_id, {"account_id": acct_id, "name": acct_name,
                                      "type": "credit" if CREDIT_HINT.search(acct_name) else "depository",
                                      "subtype": None, "balances": {}})
        desc = (r.get(c_desc) or r.get(c_orig) or "").strip() or "Unknown"
        primary, detailed = _category(r.get(c_cat) if c_cat else None, amt)
        base = f"{source}|{d}|{amt:.2f}|{desc}|{acct_id}"
        seen[base] = seen.get(base, 0) + 1  # identical rows on one day stay distinct; re-imports stay idempotent
        txs.append({"transaction_id": "imp-" + hashlib.sha1(f"{base}|{seen[base]}".encode()).hexdigest()[:16],
                    "account_id": acct_id, "date": d, "amount": round(amt, 2),
                    "name": (r.get(c_orig) or desc).strip(), "merchant_name": desc,
                    "personal_finance_category": {"primary": primary, "detailed": detailed},
                    "pending": False, "payment_channel": None})
    existing = {r[0] for r in conn.execute("SELECT id FROM transactions WHERE item_id=?", (item_id,))}
    with _lock, conn:
        conn.execute("INSERT INTO items(item_id, institution, token_env, status, created_at, last_sync)"
                     " VALUES(?,?,NULL,'ok',?,?) ON CONFLICT(item_id) DO UPDATE SET last_sync=excluded.last_sync",
                     (item_id, f"Imported from {fmt.title()}", now_iso(), now_iso()))
    save_sync(conn, item_id, list(accounts.values()), [t for t in txs if t["transaction_id"] not in existing],
              [], [], None)
    purchases.match(conn)
    dates = sorted(d for d, _, _ in parsed)
    return {"added": sum(t["transaction_id"] not in existing for t in txs), "already_had": len(existing & {t["transaction_id"] for t in txs}),
            "skipped_rows": skipped, "accounts": [a["name"] for a in accounts.values()],
            "from": dates[0], "to": dates[-1], "spending_was_negative": flip}


def _category(name, amt) -> tuple[str, str | None]:
    n = _norm(name or "")
    key = CATEGORY_MAP.get(n)
    if key is None and ":" in n:  # YNAB "Group: Category"
        key = CATEGORY_MAP.get(n.split(":")[-1].strip())
    if key is None and amt < 0 and re.search(r"income|inflow|payroll|salary|deposit", n):
        key = "INCOME"
    if key == "TRANSFER":
        return ("TRANSFER_IN" if amt < 0 else "TRANSFER_OUT"), None
    if key == "CARD_PAYMENT":
        return "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"
    if key:
        return key, None
    if not n:
        return ("INCOME" if amt < 0 else "OTHER"), None
    return category_key(name), None


def remove(conn, source: str) -> int:
    item_id = f"import-{source}"
    with _lock, conn:
        n = conn.execute("DELETE FROM transactions WHERE item_id=?", (item_id,)).rowcount
        conn.execute("DELETE FROM accounts WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM items WHERE item_id=?", (item_id,))
    return n


def sources(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT i.item_id, i.institution, i.last_sync, COUNT(t.id) AS transactions, MIN(t.date) AS first, MAX(t.date) AS last"
        " FROM items i LEFT JOIN transactions t ON t.item_id = i.item_id WHERE i.item_id LIKE 'import-%' GROUP BY i.item_id")]
