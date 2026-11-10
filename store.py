"""SQLite storage. Amounts are integer cents; positive means money left the account (Plaid's sign)."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

PLUGIN_ID = "pocketbook"

_SCHEMA = [
    # v1
    """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE items (
        item_id TEXT PRIMARY KEY, institution TEXT, token_env TEXT, cursor TEXT,
        status TEXT NOT NULL DEFAULT 'ok', error TEXT, created_at TEXT, last_sync TEXT
    );
    CREATE TABLE accounts (
        account_id TEXT PRIMARY KEY, item_id TEXT, name TEXT, mask TEXT, type TEXT, subtype TEXT,
        current_cents INTEGER, available_cents INTEGER, limit_cents INTEGER, currency TEXT,
        updated_at TEXT
    );
    CREATE TABLE transactions (
        id TEXT PRIMARY KEY, account_id TEXT, item_id TEXT, date TEXT NOT NULL,
        amount_cents INTEGER NOT NULL, name TEXT, merchant TEXT, category TEXT, detailed TEXT,
        pending INTEGER NOT NULL DEFAULT 0, pending_id TEXT, channel TEXT, user_category TEXT,
        first_seen TEXT
    );
    CREATE INDEX tx_date ON transactions(date);
    CREATE INDEX tx_merchant ON transactions(merchant);
    CREATE TABLE budgets (
        category TEXT PRIMARY KEY, amount_cents INTEGER, proposed_cents INTEGER, basis TEXT,
        status TEXT NOT NULL DEFAULT 'proposed', updated_at TEXT
    );
    CREATE TABLE alerts (
        key TEXT PRIMARY KEY, kind TEXT NOT NULL, severity TEXT NOT NULL, title TEXT NOT NULL,
        detail TEXT NOT NULL, txn_ids TEXT, created_at TEXT NOT NULL, notified_at TEXT,
        dismissed_at TEXT
    );
    """,
    # v2: net worth, investments, goals, categorization rules
    """
    ALTER TABLE transactions ADD COLUMN rule_category TEXT;
    CREATE TABLE rules (
        id INTEGER PRIMARY KEY, pattern TEXT NOT NULL, category TEXT NOT NULL,
        min_cents INTEGER, max_cents INTEGER, created_at TEXT NOT NULL
    );
    CREATE TABLE goals (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, target_cents INTEGER NOT NULL, target_date TEXT,
        account_id TEXT, saved_cents INTEGER NOT NULL DEFAULT 0, start_cents INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, done_at TEXT
    );
    CREATE TABLE securities (
        security_id TEXT PRIMARY KEY, name TEXT, ticker TEXT, type TEXT, close_cents INTEGER, close_date TEXT
    );
    CREATE TABLE holdings (
        account_id TEXT NOT NULL, security_id TEXT NOT NULL, quantity REAL, value_cents INTEGER,
        cost_cents INTEGER, updated_at TEXT, PRIMARY KEY (account_id, security_id)
    );
    CREATE TABLE networth (
        date TEXT PRIMARY KEY, assets_cents INTEGER NOT NULL, liabilities_cents INTEGER NOT NULL,
        cash_cents INTEGER, investments_cents INTEGER, credit_cents INTEGER, loans_cents INTEGER
    );
    """,
    # v3: item-level purchases (Amazon orders) and how long things last
    """
    CREATE TABLE purchases (
        id TEXT PRIMARY KEY, source TEXT NOT NULL, order_id TEXT, order_date TEXT NOT NULL,
        product TEXT NOT NULL, product_key TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1,
        cents INTEGER NOT NULL, txn_id TEXT, created_at TEXT
    );
    CREATE INDEX purchases_key ON purchases(product_key);
    CREATE INDEX purchases_txn ON purchases(txn_id);
    CREATE TABLE products (
        key TEXT PRIMARY KEY, lifespan_days INTEGER, lifespan_source TEXT, hidden INTEGER NOT NULL DEFAULT 0
    );
    """,
    # v4: reversible account controls and environment-scoped views/history
    """
    ALTER TABLE items ADD COLUMN environment TEXT;
    ALTER TABLE items ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE accounts ADD COLUMN custom_name TEXT;
    ALTER TABLE accounts ADD COLUMN include_spending INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE accounts ADD COLUMN include_networth INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE accounts ADD COLUMN closed INTEGER NOT NULL DEFAULT 0;
    CREATE VIEW active_items AS SELECT * FROM items WHERE enabled = 1 AND
      (token_env IS NULL OR environment IS NULL OR environment =
       COALESCE((SELECT value FROM meta WHERE key='active_plaid_env'), environment));
    CREATE VIEW active_accounts AS SELECT a.account_id, a.item_id,
      COALESCE(NULLIF(a.custom_name, ''), a.name) AS name, a.name AS source_name,
      a.custom_name, a.mask, a.type, a.subtype, a.current_cents, a.available_cents,
      a.limit_cents, a.currency, a.updated_at, a.include_spending, a.include_networth, a.closed
      FROM accounts a LEFT JOIN items i ON i.item_id = a.item_id
      WHERE (i.item_id IS NULL OR i.item_id IN (SELECT item_id FROM active_items))
        AND COALESCE(a.currency, 'USD') = 'USD';
    CREATE VIEW spending_accounts AS SELECT * FROM active_accounts WHERE include_spending = 1;
    CREATE VIEW balance_accounts AS SELECT * FROM active_accounts WHERE include_networth = 1 AND closed = 0;
    ALTER TABLE networth RENAME TO networth_legacy;
    CREATE TABLE networth (
      date TEXT NOT NULL, assets_cents INTEGER NOT NULL, liabilities_cents INTEGER NOT NULL,
      cash_cents INTEGER, investments_cents INTEGER, credit_cents INTEGER, loans_cents INTEGER,
      scope TEXT NOT NULL DEFAULT '', PRIMARY KEY(date, scope)
    );
    INSERT INTO networth SELECT date, assets_cents, liabilities_cents, cash_cents,
      investments_cents, credit_cents, loans_cents, '' FROM networth_legacy;
    DROP TABLE networth_legacy;
    """,

]

_lock = threading.RLock()
_conns: dict[str, sqlite3.Connection] = {}


def migrate(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Each schema step commits with its version bump or not at all, so a crash cannot leave half a step."""
    conn.row_factory = sqlite3.Row
    while (version := conn.execute("PRAGMA user_version").fetchone()[0]) < len(_SCHEMA):
        try:
            conn.executescript(f"BEGIN IMMEDIATE; {_SCHEMA[version]}; PRAGMA user_version = {version + 1}; COMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.rollback()
            if conn.execute("PRAGMA user_version").fetchone()[0] == version:
                raise  # a real failure, not another process finishing this step first
    return conn


def secure(folder: Path) -> None:
    """Owner-only access to the data folder and database: it holds balances and every transaction."""
    try:
        os.chmod(folder, 0o700)
        for f in folder.glob("data.db*"):
            os.chmod(f, 0o600)
    except OSError:
        pass  # filesystems without POSIX modes


def connect() -> sqlite3.Connection:
    """One connection per Hermes profile, opened through Hermes's plugin storage."""
    from hermes_constants import get_hermes_home
    from plugins.plugin_storage import plugin_data_dir, plugin_db

    key = str(get_hermes_home())
    with _lock:
        if key not in _conns:
            _conns[key] = migrate(plugin_db(PLUGIN_ID))
            secure(plugin_data_dir(PLUGIN_ID))
        return _conns[key]


def memory() -> sqlite3.Connection:
    return migrate(sqlite3.connect(":memory:", check_same_thread=False))


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today() -> date:
    return date.today()


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn, key, value):
    with _lock:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))
        conn.commit()


def cents(dollars) -> int:
    """Dollars to whole cents, rounded half up on the decimal value: 19.99 is 1999, never 1998."""
    try:
        d = Decimal(str(dollars).strip())
    except InvalidOperation:
        raise ValueError(f"not an amount: {dollars!r}") from None
    if not d.is_finite() or abs(d) > 10 ** 12:
        raise ValueError(f"not an amount: {dollars!r}")
    return int((d * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def require_no_demo(conn) -> None:
    """Sample and real data never share a database: clearing samples could not tell them apart."""
    if conn.execute("SELECT 1 FROM items WHERE item_id = 'demo'").fetchone():
        raise ValueError("Sample data is loaded. Remove it first (`hermes pocketbook demo --clear`, or "
                         "Remove sample data in the Finance tab) so it never mixes with real accounts.")


def dollars(c) -> float:
    return round((c or 0) / 100, 2)


def fmt(c) -> str:
    sign = "-" if c and c < 0 else ""
    return f"{sign}${abs(c or 0) / 100:,.2f}"
