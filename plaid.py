"""Plaid client and sync. Uses the user's own Plaid keys; access tokens live in Hermes's secret store."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from datetime import date

from .store import _lock, cents, get_meta, now_iso, require_no_demo, set_meta
from .wealth import apply_rules, save_holdings, snapshot

HOSTS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}
CLIENT_ID_ENV = "POCKETBOOK_PLAID_CLIENT_ID"
SECRET_ENV = "POCKETBOOK_PLAID_SECRET"
# Reads that are safe to repeat. Token exchange, link creation and item removal are sent once.
RETRY_SAFE = ("/transactions/sync", "/accounts/get", "/investments/holdings/get", "/link/token/get")
RETRY_HTTP = (429, 500, 502, 503, 504)
GONE = ("ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN")  # Plaid no longer has the item


class PlaidError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------- secrets (Hermes routes only) ----------

def get_secret(name: str) -> str | None:
    from agent.secret_scope import get_secret as _get
    return _get(name)


def save_secret(name: str, value: str) -> None:
    from hermes_cli.config import save_env_value
    save_env_value(name, value)


def remove_secret(name: str) -> None:
    from hermes_cli.config import remove_env_value
    remove_env_value(name)


class Plaid:
    def __init__(self, env: str = "sandbox", client_id: str | None = None, secret: str | None = None,
                 secrets=None):
        env = str(env or "").strip().lower()
        self.env = env if env in HOSTS else "sandbox"
        self.secrets = secrets or {"get": get_secret, "save": save_secret, "remove": remove_secret}
        self.client_id = client_id or self.secrets["get"](CLIENT_ID_ENV)
        self.secret = secret or self.secrets["get"](SECRET_ENV)

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.secret)

    def call(self, path: str, body: dict) -> dict:
        if not self.configured:
            raise PlaidError("NOT_CONFIGURED", "Add your Plaid client ID and secret in Settings → Plugins → pocketbook.")
        payload = json.dumps({"client_id": self.client_id, "secret": self.secret, **body}).encode()
        req = urllib.request.Request(HOSTS[self.env] + path, data=payload,
                                     headers={"Content-Type": "application/json"})
        tries = 3 if path in RETRY_SAFE else 1
        for attempt in range(tries):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                retry = e.code in RETRY_HTTP
                try:
                    body = json.loads(e.read())
                    err = PlaidError(str(body.get("error_code") or "ERROR"), str(body.get("error_message") or ""))
                except Exception:
                    err = PlaidError(f"HTTP_{e.code}", str(e.reason))
            except (OSError, ValueError) as e:  # refused, DNS, timeout mid-read, or a body that is not JSON
                retry, err = True, PlaidError("NETWORK", str(getattr(e, "reason", e)))
            if not retry or attempt == tries - 1:
                raise err from None
            time.sleep(1 + 2 * attempt)

    # ---------- linking ----------

    def start_link(self, conn) -> dict:
        """Hosted Link: Plaid hosts the bank sign-in page, so no web server is needed."""
        require_no_demo(conn)
        user = hashlib.sha256((self.client_id or "").encode()).hexdigest()[:16]
        res = self.call("/link/token/create", {
            "client_name": "Pocketbook for Hermes",
            "user": {"client_user_id": f"pocketbook-{user}"},
            "products": ["transactions"],
            "optional_products": ["investments"],
            "transactions": {"days_requested": 730},
            "country_codes": ["US"],
            "language": "en",
            "hosted_link": {},
        })
        url = str(res.get("hosted_link_url") or "")
        host = urllib.parse.urlsplit(url).hostname or ""
        if not url.startswith("https://") or not (host == "plaid.com" or host.endswith(".plaid.com")):
            raise PlaidError("BAD_LINK", "Plaid did not return a sign-in link on plaid.com.")  # we open this URL
        set_meta(conn, "pending_link_token", res["link_token"])
        return {"url": url, "expires": res.get("expiration")}

    def finish_link(self, conn) -> list[dict]:
        token = get_meta(conn, "pending_link_token")
        if not token:
            raise PlaidError("NO_LINK", "No bank connection is in progress. Start one first.")
        res = self.call("/link/token/get", {"link_token": token})
        publics = []
        for session in res.get("link_sessions") or []:
            results = session.get("results") or {}
            for add in results.get("item_add_results") or []:
                publics.append((add.get("public_token"), (add.get("institution") or {}).get("name")))
            done = session.get("on_success") or {}
            if done.get("public_token"):
                publics.append((done["public_token"], (done.get("metadata") or {}).get("institution", {}).get("name")))
        if not publics:
            raise PlaidError("LINK_PENDING", "Plaid has not reported a finished connection yet. Finish signing in, then try again.")
        # A public token is spent once exchanged. If one of several fails, keep the banks that worked
        # and close the session; retrying it would fail on the tokens already used.
        added, failed = [], []
        for public, institution in dict(publics).items():
            try:
                added.append(self.add_item(conn, public, institution))
            except PlaidError as e:
                failed.append(e)
        if not added:
            raise failed[0]
        set_meta(conn, "pending_link_token", "")
        return added

    def sandbox_link(self, conn, institution_id: str = "ins_109508") -> dict:
        """Sandbox only: create a test bank connection without a browser."""
        if self.env != "sandbox":
            raise PlaidError("NOT_SANDBOX", "Test connections only work with plaid_env = sandbox.")
        require_no_demo(conn)
        body = {
            "institution_id": institution_id,
            "initial_products": ["transactions", "investments"],
            "options": {"override_username": "user_transactions_dynamic", "override_password": "pass_good",
                        "transactions": {"days_requested": 730}},
        }
        try:
            res = self.call("/sandbox/public_token/create", body)
        except PlaidError:
            body["initial_products"] = ["transactions"]  # test user without investment accounts
            res = self.call("/sandbox/public_token/create", body)
        return self.add_item(conn, res["public_token"], "Plaid Sandbox Bank")

    def add_item(self, conn, public_token: str, institution: str | None) -> dict:
        require_no_demo(conn)
        res = self.call("/item/public_token/exchange", {"public_token": public_token})
        item_id = res["item_id"]
        env_name = "POCKETBOOK_PLAID_TOKEN_" + hashlib.sha256(item_id.encode()).hexdigest()[:12].upper()
        self.secrets["save"](env_name, res["access_token"])
        if self.secrets["get"](env_name) != res["access_token"]:
            # Without the token the connection can never sync or be removed; do not leave it open at Plaid.
            try:
                self.call("/item/remove", {"access_token": res["access_token"]})
            except PlaidError:
                pass
            raise PlaidError("TOKEN_NOT_SAVED", "Hermes could not store the bank's access token, so the "
                                                "connection was not kept. Check that Hermes can write its .env.")
        with _lock:
            conn.execute(
                "INSERT OR REPLACE INTO items(item_id, institution, token_env, status, created_at) VALUES(?,?,?,'ok',?)",
                (item_id, institution or "Bank", env_name, now_iso()),
            )
            conn.commit()
        return {"item_id": item_id, "institution": institution or "Bank"}

    def remove_item(self, conn, item_id: str) -> None:
        row = conn.execute("SELECT token_env FROM items WHERE item_id=?", (item_id,)).fetchone()
        if not row:
            raise PlaidError("NOT_FOUND", f"No connection {item_id}.")
        token = self.secrets["get"](row[0]) if row[0] else None  # imports and sample data have no token
        if token:
            try:
                self.call("/item/remove", {"access_token": token})
            except PlaidError as e:
                if e.code not in GONE:
                    # Dropping the token now would leave a live connection at Plaid that nothing can remove.
                    raise PlaidError(e.code, f"Plaid did not confirm the removal, so the connection was kept. "
                                             f"Try again. ({e})") from None
            self.secrets["remove"](row[0])
        with _lock, conn:
            conn.execute("DELETE FROM holdings WHERE account_id IN (SELECT account_id FROM accounts WHERE item_id=?)",
                         (item_id,))
            conn.execute("DELETE FROM transactions WHERE item_id=?", (item_id,))
            conn.execute("DELETE FROM accounts WHERE item_id=?", (item_id,))
            conn.execute("DELETE FROM items WHERE item_id=?", (item_id,))
            conn.execute("UPDATE purchases SET txn_id = NULL WHERE txn_id NOT IN (SELECT id FROM transactions)")

    # ---------- sync ----------

    def sync(self, conn) -> dict:
        summary = {"items": 0, "added": 0, "modified": 0, "removed": 0, "errors": []}
        for item in conn.execute("SELECT * FROM items WHERE token_env IS NOT NULL").fetchall():
            token = self.secrets["get"](item["token_env"])
            if not token:
                _item_status(conn, item["item_id"], "error", "ACCESS_TOKEN_MISSING")
                summary["errors"].append(f"{item['institution']}: access token missing; reconnect it")
                continue
            try:
                counts = self._sync_item(conn, item, token)
                for k in ("added", "modified", "removed"):
                    summary[k] += counts[k]
                if counts.get("investments_error"):
                    summary.setdefault("notes", []).append(
                        f"{item['institution']}: holdings unavailable ({counts['investments_error']})")
                summary["items"] += 1
                _item_status(conn, item["item_id"], "ok", None, synced=True)
            except PlaidError as e:
                _item_status(conn, item["item_id"], "error", e.code)
                summary["errors"].append(f"{item['institution']}: {e}")
            except (KeyError, TypeError, ValueError) as e:  # a response we cannot read must not stop other banks
                _item_status(conn, item["item_id"], "error", "BAD_RESPONSE")
                summary["errors"].append(f"{item['institution']}: unexpected response from Plaid ({type(e).__name__})")
        set_meta(conn, "last_sync_attempt", now_iso())
        snapshot(conn, date.today())
        return summary

    def _sync_item(self, conn, item, token: str) -> dict:
        cursor = item["cursor"] or ""
        counts = {"added": 0, "modified": 0, "removed": 0}
        for _ in range(3):  # Plaid asks callers to restart pagination if data changes mid-sync
            try:
                start = cursor
                added, modified, removed, accts = [], [], [], []
                while True:
                    body = {"access_token": token, "count": 500}
                    if cursor:
                        body["cursor"] = cursor
                    res = self.call("/transactions/sync", body)
                    added += res.get("added", [])
                    modified += res.get("modified", [])
                    removed += res.get("removed", [])
                    accts = res.get("accounts") or accts
                    cursor = res["next_cursor"]
                    if not res.get("has_more"):
                        break
                break
            except PlaidError as e:
                if e.code != "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                    raise
                cursor = start
        else:
            raise PlaidError("TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION",
                             "the bank's data kept changing during the sync; nothing was saved")
        if not accts:
            accts = self.call("/accounts/get", {"access_token": token}).get("accounts", [])
        save_sync(conn, item["item_id"], accts, added, modified, removed, cursor)
        counts.update(added=len(added), modified=len(modified), removed=len(removed))
        if any(a.get("type") in ("investment", "brokerage") for a in accts):
            try:
                inv = self.call("/investments/holdings/get", {"access_token": token})
                save_holdings(conn, inv.get("holdings", []), inv.get("securities", []))
            except PlaidError as e:
                counts["investments_error"] = e.code  # not fatal: balances still count toward net worth
        return counts


def _item_status(conn, item_id, status, error, synced=False):
    with _lock:
        if synced:
            conn.execute("UPDATE items SET status=?, error=?, last_sync=? WHERE item_id=?",
                         (status, error, now_iso(), item_id))
        else:
            conn.execute("UPDATE items SET status=?, error=? WHERE item_id=?", (status, error, item_id))
        conn.commit()


def save_sync(conn, item_id, accts, added, modified, removed, cursor) -> None:
    """Apply one /transactions/sync result atomically, so a crash never leaves a half-applied page."""
    stamp = now_iso()
    with _lock, conn:
        for a in accts:
            bal = a.get("balances") or {}
            conn.execute(
                "INSERT OR REPLACE INTO accounts(account_id, item_id, name, mask, type, subtype, current_cents,"
                " available_cents, limit_cents, currency, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (a["account_id"], item_id, a.get("name"), a.get("mask"), a.get("type"), a.get("subtype"),
                 _c(bal.get("current")), _c(bal.get("available")), _c(bal.get("limit")),
                 bal.get("iso_currency_code") or "USD", stamp),
            )
        for t in added + modified:
            pfc = t.get("personal_finance_category") or {}
            conn.execute(
                "INSERT INTO transactions(id, account_id, item_id, date, amount_cents, name, merchant, category,"
                " detailed, pending, pending_id, channel, first_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET account_id=excluded.account_id, date=excluded.date,"
                " amount_cents=excluded.amount_cents, name=excluded.name, merchant=excluded.merchant,"
                " category=excluded.category, detailed=excluded.detailed, pending=excluded.pending,"
                " pending_id=excluded.pending_id, channel=excluded.channel",
                (t["transaction_id"], t["account_id"], item_id, t["date"], cents(t["amount"]), t.get("name"),
                 t.get("merchant_name"), pfc.get("primary") or "OTHER", pfc.get("detailed"),
                 int(bool(t.get("pending"))), t.get("pending_transaction_id"), t.get("payment_channel"), stamp),
            )
        for r in removed:
            conn.execute("DELETE FROM transactions WHERE id=?", (r["transaction_id"],))
        conn.execute("UPDATE items SET cursor=? WHERE item_id=?", (cursor, item_id))
    if added or modified:
        apply_rules(conn)


def _c(v):
    return None if v is None else cents(v)
