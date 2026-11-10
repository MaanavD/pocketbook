"""`hermes pocketbook ...` commands."""

from __future__ import annotations

import json
import time
import webbrowser

from . import demo, imports, service
from .insights import category_key
from .store import connect, today


def setup(parser) -> None:
    subs = parser.add_subparsers(dest="pocketbook_cmd")
    subs.add_parser("status", help="Connections, accounts and this month at a glance")
    subs.add_parser("connect", help="Link a bank through Plaid Hosted Link")
    subs.add_parser("test-bank", help="Add a Plaid sandbox bank (sandbox keys only)")
    subs.add_parser("sync", help="Pull new transactions now")
    subs.add_parser("check", help="Run the alert check and print new alerts")
    d = subs.add_parser("demo", help="Load six months of sample data (or --clear it)")
    d.add_argument("--clear", action="store_true")
    c = subs.add_parser("charge", help="Add a sample card charge dated today (demo data only)")
    c.add_argument("merchant")
    c.add_argument("amount", type=float)
    c.add_argument("--category", default="GENERAL_MERCHANDISE")
    i = subs.add_parser("import", help="Import a CSV: Monarch, Mint, YNAB, Rocket Money, Copilot, a bank, or Amazon orders")
    i.add_argument("file")
    i.add_argument("--format", default=None)
    parser.set_defaults(func=run)


def run(args) -> None:
    cmd = getattr(args, "pocketbook_cmd", None) or "status"
    conn = connect()
    if cmd == "status":
        print(service.summary_text(conn))
        for r in conn.execute("SELECT institution, status, error, last_sync FROM items"):
            print(f"  {r['institution']}: {r['status']}{' (' + r['error'] + ')' if r['error'] else ''}, "
                  f"last sync {r['last_sync'] or 'never'}")
    elif cmd == "connect":
        p = service.plaid()
        link = p.start_link(conn)
        print(f"Open this link to connect your bank:\n\n  {link['url']}\n")
        webbrowser.open(link["url"])
        print("Waiting for you to finish (Ctrl+C to stop; you can finish later from chat or the Finance tab)...")
        for _ in range(120):
            time.sleep(5)
            try:
                added = p.finish_link(conn)
            except Exception:
                continue
            print("Connected:", ", ".join(a["institution"] for a in added))
            print(json.dumps(service.sync(conn, force=True), indent=2))
            return
        print("Timed out. Run `hermes pocketbook connect` again when ready.")
    elif cmd == "test-bank":
        added = service.plaid().sandbox_link(conn)
        print("Connected", added["institution"])
        print(json.dumps(service.sync(conn, force=True), indent=2))
    elif cmd == "sync":
        print(json.dumps(service.sync(conn, force=True), indent=2))
    elif cmd == "check":
        res = service.check(conn)
        for a in res["alerts"]:
            print(f"[{a['kind']}] {a['title']}: {a['detail']}")
        if not res["alerts"]:
            print("No new alerts." + (f" {res['held']} held for quiet hours." if res["held"] else ""))
    elif cmd == "demo":
        if args.clear:
            demo.clear(conn)
            print("Sample data removed.")
        else:
            info = demo.load(conn, today())
            print(f"Loaded {info['transactions']} sample transactions across {info['accounts']} accounts.")
            print("Ask Hermes to propose budgets, or open the Finance tab.")
    elif cmd == "import":
        res = imports.import_path(conn, args.file, args.format)
        print(json.dumps(res, indent=2))
    elif cmd == "charge":
        if not conn.execute("SELECT 1 FROM items WHERE item_id='demo'").fetchone():
            print("Load sample data first: hermes pocketbook demo")
            return
        demo.charge(conn, args.merchant, args.amount, category_key(args.category), today())
        print(f"Added sample charge: {args.merchant} ${args.amount:,.2f}")
