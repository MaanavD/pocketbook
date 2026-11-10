"""Plaid client and sync. Uses the user's own Plaid keys; access tokens live in Hermes's secret store."""

from __future__ import annotations

import hashlib
import json
import time
import threading
import urllib.error
import urllib.parse
import urllib.request

from datetime import date, datetime, timezone

from .store import _lock, cents, get_meta, now_iso, require_no_demo, set_meta
from .wealth import apply_rules, save_holdings, snapshot
from . import connections

_link_lock = threading.RLock()

HOSTS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}
CLIENT_ID_ENV = "POCKETBOOK_PLAID_CLIENT_ID"
SECRET_ENV = "POCKETBOOK_PLAID_SECRET"
# Reads that are safe to repeat. Token exchange, link creation and item removal are sent once.
RETRY_SAFE = ("/transactions/sync", "/accounts/get", "/investments/holdings/get", "/link/token/get")
RETRY_HTTP = (429, 500, 502, 503, 504)
GONE = ("ITEM_NOT_FOUND",)  # an invalid token can also mean the wrong keys/environment


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

    def start_link(self, conn, item_id=None) -> dict:
        with _link_lock:
            return self._start_link(conn, item_id)

    def _start_link(self, conn, item_id=None) -> dict:
        """Hosted Link: Plaid hosts the bank sign-in page, so no web server is needed."""
        require_no_demo(conn)
        if get_meta(conn, "pending_link_token"):
            raise PlaidError("LINK_IN_PROGRESS", "A bank sign-in is already in progress. Finish it or cancel it in Finance before starting another.")
        user = hashlib.sha256((self.client_id or "").encode()).hexdigest()[:16]
        body = {
            "client_name": "Pocketbook for Hermes",
            "user": {"client_user_id": f"pocketbook-{user}"},
            "products": ["transactions"],
            "optional_products": ["investments"],
            "transactions": {"days_requested": 730},
            "country_codes": ["US"],
            "language": "en",
            "hosted_link": {},
        }
        if item_id:
            connections.prepare(conn, self.env, self.secrets["get"])
            item = conn.execute("SELECT * FROM items WHERE item_id=?", (item_id,)).fetchone()
            if not item or not item["token_env"]:
                raise PlaidError("NOT_FOUND", "This bank connection cannot be repaired. Connect a bank instead.")
            if item["environment"] and item["environment"] != self.env:
                raise PlaidError("WRONG_ENVIRONMENT", "Switch to this bank's environment before repairing its sign-in.")
            token = self.secrets["get"](item["token_env"])
            if not token:
                raise PlaidError("TOKEN_MISSING", "This bank's access token is missing. Restore its Hermes credentials or connect it again.")
            for field in ("products", "optional_products", "transactions"):
                body.pop(field, None)
            body["access_token"] = token
        res = self.call("/link/token/create", body)
        url = str(res.get("hosted_link_url") or "")
        host = urllib.parse.urlsplit(url).hostname or ""
        if not url.startswith("https://") or not (host == "plaid.com" or host.endswith(".plaid.com")):
            raise PlaidError("BAD_LINK", "Plaid did not return a sign-in link on plaid.com.")  # we open this URL
        with _lock, conn:
            conn.executemany("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", [
                ("pending_link_item", item_id or ""), ("pending_link_token", res["link_token"]),
                ("pending_link_environment", self.env), ("pending_link_url", url),
                ("pending_link_expires", res.get("expiration") or "")])
        return {"url": url, "expires": res.get("expiration")}

    def finish_link(self, conn) -> list[dict]:
        with _link_lock:
            return self._finish_link(conn)

    def _finish_link(self, conn) -> list[dict]:
        self.link_warnings = []
        token = get_meta(conn, "pending_link_token")
        if not token:
            raise PlaidError("NO_LINK", "No bank connection is in progress. Start one first.")
        environment = get_meta(conn, "pending_link_environment") or connections.token_environment(token)
        if environment and environment != self.env:
            raise PlaidError("LINK_ENVIRONMENT_CHANGED", "This sign-in was started in a different environment. Cancel it and start a new connection.")
        expiration = get_meta(conn, "pending_link_expires")
        if expiration:
            try:
                stamp = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
                expired = stamp.replace(tzinfo=stamp.tzinfo or timezone.utc) < datetime.now(timezone.utc)
            except ValueError:
                expired = False
            if expired:
                raise PlaidError("LINK_EXPIRED", "This bank sign-in has expired. Start a new connection.")
        res = self.call("/link/token/get", {"link_token": token})
        repair_item = get_meta(conn, "pending_link_item")
        if repair_item and any(session.get("on_success") or
                               any(result.get("public_token") for result in (session.get("results") or {}).get("item_add_results") or [])
                               for session in res.get("link_sessions") or []):
            item = conn.execute("SELECT institution FROM items WHERE item_id=?", (repair_item,)).fetchone()
            if not item:
                raise PlaidError("NOT_FOUND", "The original bank connection no longer exists. Start a new connection.")
            with _lock, conn:
                conn.execute("UPDATE items SET status='ok',error=NULL WHERE item_id=?", (repair_item,))
            self.cancel_link(conn)
            return [{"item_id": repair_item, "institution": item["institution"], "repaired": True}]
        publics = []
        for session in res.get("link_sessions") or []:
            results = session.get("results") or {}
            for add in results.get("item_add_results") or []:
                if add.get("public_token"):
                    publics.append((add["public_token"], (add.get("institution") or {}).get("name")))
            done = session.get("on_success") or {}
            if done.get("public_token"):
                publics.append((done["public_token"], ((done.get("metadata") or {}).get("institution") or {}).get("name")))
        if not publics:
            sessions = res.get("link_sessions") or []
            if sessions:
                # An old exited attempt must not stop a later reopened sign-in.
                session = max(enumerate(sessions), key=lambda pair: (pair[1].get("started_at") or "", pair[0]))[1]
                exit_info = session.get("exit") if session.get("exit") is not None else session.get("on_exit")
                if session.get("finished_at") and exit_info is not None:
                    reason = exit_info.get("error")
                    if reason:
                        raise PlaidError("LINK_FAILED", "The bank sign-in did not finish. Reopen the sign-in or start a new connection.")
                    raise PlaidError("LINK_CANCELLED", "Bank sign-in was closed without connecting an account. Start a new connection when ready.")
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
        self.link_warnings = [f"Another bank did not finish connecting ({error.code}). Start a new sign-in for that bank." for error in failed]
        self.cancel_link(conn)
        return added

    def cancel_link(self, conn):
        with _link_lock, _lock, conn:
            conn.execute("DELETE FROM meta WHERE key GLOB 'pending_link_*'")
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('pending_link_token','')")
        return {"status": "cancelled", "message": "Pocketbook stopped checking this sign-in. Close the Plaid browser tab too. Existing banks are unchanged."}

    def link_status(self, conn):
        if not get_meta(conn, "pending_link_token"):
            return None
        return {"url": get_meta(conn, "pending_link_url"), "expires": get_meta(conn, "pending_link_expires"),
                "environment": get_meta(conn, "pending_link_environment") or connections.token_environment(get_meta(conn, "pending_link_token"))}

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
        token_env = connections.token_environment(public_token)
        if token_env and token_env != self.env:
            raise PlaidError("WRONG_ENVIRONMENT", "This sign-in belongs to a different Plaid environment. Start a new connection.")
        res = self.call("/item/public_token/exchange", {"public_token": public_token})
        item_id = res["item_id"]
        env_name = "POCKETBOOK_PLAID_TOKEN_" + hashlib.sha256(item_id.encode()).hexdigest()[:12].upper()
        try:
            self.secrets["save"](env_name, res["access_token"])
            saved = self.secrets["get"](env_name) == res["access_token"]
        except Exception:
            saved = False
        if not saved:
            # A secret-store write can fail before returning, too. Try to remove the new Plaid item.
            try:
                self.call("/item/remove", {"access_token": res["access_token"]})
                cleanup = "The new Plaid connection was removed."
            except PlaidError:
                cleanup = "Plaid removal could not be confirmed. Check this connection in the Plaid Dashboard."
            raise PlaidError("TOKEN_NOT_SAVED", "Hermes could not store the bank's access token. "
                             + cleanup + " Check that Hermes can write its .env.")
        with _lock:
            conn.execute(
                "INSERT INTO items(item_id, institution, token_env, status, created_at, environment) VALUES(?,?,?,'ok',?,?)"
                " ON CONFLICT(item_id) DO UPDATE SET institution=excluded.institution,token_env=excluded.token_env,"
                " status='ok',error=NULL,environment=excluded.environment",
                (item_id, institution or "Bank", env_name, now_iso(), self.env),
            )
            conn.commit()
        return {"item_id": item_id, "institution": institution or "Bank"}

    def remove_item(self, conn, item_id: str) -> None:
        row = conn.execute("SELECT token_env,environment FROM items WHERE item_id=?", (item_id,)).fetchone()
        if not row:
            raise PlaidError("NOT_FOUND", f"No connection {item_id}.")
        token = self.secrets["get"](row[0]) if row[0] else None  # imports and sample data have no token
        if row["token_env"] and not token:
            raise PlaidError("TOKEN_MISSING", "The bank access token is missing, so removal cannot be confirmed. The connection and history were kept. Restore the credentials or turn the bank off.")
        if token and (row["environment"] or connections.token_environment(token)) not in (None, self.env):
            raise PlaidError("WRONG_ENVIRONMENT", "Switch to this bank's environment before disconnecting it, or turn it off to keep its history.")
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

    def sync(self, conn, full: bool = False) -> dict:
        connections.prepare(conn, self.env, self.secrets["get"])
        summary = {"skipped_items": [], "items": 0, "added": 0, "modified": 0, "removed": 0, "accounts": 0, "errors": [], "environment": self.env}
        for item in conn.execute("SELECT * FROM items WHERE token_env IS NOT NULL").fetchall():
            if not item["enabled"] or (item["environment"] and item["environment"] != self.env):
                summary["skipped_items"].append({"institution": item["institution"], "reason": "turned off" if not item["enabled"] else "different environment"})
                continue
            token = self.secrets["get"](item["token_env"])
            if not token:
                _item_status(conn, item["item_id"], "error", "ACCESS_TOKEN_MISSING")
                summary["errors"].append(f"{item['institution']}: access token missing; reconnect it")
                continue
            try:
                counts = self._sync_item(conn, item, token, full=full)
                for k in ("added", "modified", "removed", "accounts"):
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

    def _sync_item(self, conn, item, token: str, full: bool = False) -> dict:
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
        complete_accounts = full or not accts
        if complete_accounts:
            response = self.call("/accounts/get", {"access_token": token})
            accts = response.get("accounts")
            if not isinstance(accts, list) or any(not isinstance(a, dict) or not a.get("account_id") for a in accts):
                raise PlaidError("INVALID_RESPONSE", "The bank did not return a complete account list. Previous balances and history were kept.")
        save_sync(conn, item["item_id"], accts, added, modified, removed, cursor, complete_accounts=complete_accounts)
        counts.update(added=len(added), modified=len(modified), removed=len(removed), accounts=len(accts))
        if any(a.get("type") in ("investment", "brokerage") for a in accts):
            try:
                inv = self.call("/investments/holdings/get", {"access_token": token})
                if not isinstance(inv.get("holdings"), list) or not isinstance(inv.get("securities"), list):
                    raise PlaidError("BAD_RESPONSE", "Plaid did not return a complete holdings snapshot")
                save_holdings(conn, inv["holdings"], inv["securities"],
                              account_ids=[a["account_id"] for a in accts if a.get("type") in ("investment", "brokerage")])
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


def save_sync(conn, item_id, accts, added, modified, removed, cursor, *, complete_accounts=False) -> None:
    """Apply one /transactions/sync result atomically, so a crash never leaves a half-applied page."""
    stamp = now_iso()
    with _lock, conn:
        if complete_accounts:
            conn.execute("UPDATE accounts SET closed=1 WHERE item_id=?", (item_id,))
        for a in accts:
            bal = a.get("balances") or {}
            conn.execute(
                "INSERT INTO accounts(account_id, item_id, name, mask, type, subtype, current_cents,"
                " available_cents, limit_cents, currency, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(account_id) DO UPDATE SET item_id=excluded.item_id,name=excluded.name,mask=excluded.mask,"
                " type=excluded.type,subtype=excluded.subtype,current_cents=excluded.current_cents,"
                " available_cents=excluded.available_cents,limit_cents=excluded.limit_cents,currency=excluded.currency,"
                " updated_at=excluded.updated_at,closed=0",
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
