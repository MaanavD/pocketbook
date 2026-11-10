"""Reversible bank/account preferences and environment boundaries. Never deletes financial history."""
from __future__ import annotations
import hashlib
import json
from .store import _lock, get_meta


def token_environment(token):
    for env in ("sandbox", "production"):
        if token and any(token.startswith(f"{kind}-{env}-") for kind in ("access", "link", "public")):
            return env
    return None


def prepare(conn, environment, secret_getter):
    with _lock, conn:
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('active_plaid_env',?)", (environment,))
        for item in conn.execute("SELECT item_id,token_env FROM items WHERE token_env IS NOT NULL AND environment IS NULL").fetchall():
            env = token_environment(secret_getter(item["token_env"]))
            if env:
                conn.execute("UPDATE items SET environment=? WHERE item_id=?", (env, item["item_id"]))


def scope_key(conn, kind="networth"):
    flag = "include_spending" if kind == "spending" else "include_networth"
    # Closed accounts retain their history; closing one changes balances, not the selected scope.
    selected = [r[0] for r in conn.execute(f"SELECT account_id FROM active_accounts WHERE {flag}=1 ORDER BY account_id")]
    state = [get_meta(conn, "active_plaid_env", "all"), selected]
    return hashlib.sha256(json.dumps(state).encode()).hexdigest()[:20]


def _changed(conn):
    from .wealth import snapshot
    from .store import today
    snapshot(conn, today())


def set_bank(conn, item_id, enabled):
    with _lock, conn:
        if not conn.execute("SELECT 1 FROM items WHERE item_id=?", (item_id,)).fetchone():
            raise ValueError("Bank connection not found. Refresh the account list.")
        conn.execute("UPDATE items SET enabled=? WHERE item_id=?", (int(bool(enabled)), item_id))
    _changed(conn)


def set_account(conn, account_id, *, name=None, include_spending=None, include_networth=None):
    values = {}
    if name is not None:
        values["custom_name"] = str(name).strip()[:120] or None
    for key, value in (("include_spending", include_spending), ("include_networth", include_networth)):
        if value is not None:
            values[key] = int(bool(value))
    with _lock, conn:
        if not conn.execute("SELECT 1 FROM accounts WHERE account_id=?", (account_id,)).fetchone():
            raise ValueError("Account not found. Refresh the account list.")
        if values:
            conn.execute(f"UPDATE accounts SET {', '.join(k+'=?' for k in values)} WHERE account_id=?", [*values.values(), account_id])
    _changed(conn)


def catalog(conn, environment):
    active_ids = {r[0] for r in conn.execute("SELECT account_id FROM active_accounts")}
    banks = []
    for row in conn.execute("SELECT item_id,institution,status,error,last_sync,environment,enabled FROM items ORDER BY institution,item_id"):
        bank = dict(row)
        bank["enabled"] = bool(bank["enabled"])
        bank["environment_match"] = not bank["environment"] or bank["environment"] == environment
        bank["accounts"] = []
        for row in conn.execute("SELECT * FROM accounts WHERE item_id=? ORDER BY type,name", (bank["item_id"],)):
            a = dict(row)
            a["name"] = a["custom_name"] or a["name"]
            a["active"] = a["account_id"] in active_ids
            a["include_spending"], a["include_networth"] = bool(a["include_spending"]), bool(a["include_networth"])
            bank["accounts"].append(a)
        banks.append(bank)
    return sorted(banks, key=lambda bank: (not bank["environment_match"], not bank["enabled"], (bank["institution"] or "").lower()))
