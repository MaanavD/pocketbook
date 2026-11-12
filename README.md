<img src="assets/icon.svg" width="64" height="64" alt="">

# Pocketbook

Your budget, spending and money alerts inside Hermes. Pocketbook syncs your bank and card accounts through your own Plaid keys, proposes budgets from your history, adds a Finance tab to Hermes Desktop and the web dashboard, and messages you when something needs a look.

## What it does

- **Finance tab**: overview (spending, budgets, alerts, bills), budget, cash flow, net worth and investments, goals, purchases, transactions with account/date filters and editable categories, and bank/account controls.
- **Amazon purchases**: ask Hermes to read your Amazon order history with its browser tools, or import Amazon's `Order History.csv`. Each Amazon charge shows what it bought, repeat buys show up like subscriptions you never signed up for, and items get a cost per day spread over how long they last.
- **Imports**: CSV exports from Monarch, Mint, YNAB, Rocket Money, Copilot or your bank. Use it to bring in old history, or to run Pocketbook without Plaid.
- **Net worth**: assets minus cards and loans across included accounts, with daily history.
- **Investments**: holdings from brokerage and retirement accounts, allocation by asset type, and gain against cost basis.
- **Savings goals**: follow a savings account balance or a number you update, with pace and the monthly amount still needed.
- **Categorization rules**: "anything from Trader Joe's is Groceries" applies to past and future charges. Custom categories work with budgets.
- **Proposed budgets**: Pocketbook takes the median of your last three full months per category. You adjust and approve them in chat or in the tab.
- **Alerts, each sent once**: large purchases, charges well above your usual at a merchant, possible double charges, large first charges from a new merchant, subscription price increases, bills due within three days, budgets over or on pace to go over, goals falling behind or reached, and broken bank connections. Quiet hours hold alerts until morning.
- **Chat**: fifteen `money_*` tools answer questions from computed numbers, and `money_chart` draws PNG charts that attach in Telegram, Discord and other gateways.
- **`/money`**: a snapshot without a model call. `/money check` runs the alert check.

All math runs in code. The model explains results; it never computes totals.

## Setup

1. Install and enable: `hermes plugins install https://github.com/MaanavD/pocketbook` then `hermes plugins enable pocketbook`.
2. For **real bank accounts**, request [Production access in the Plaid Dashboard](https://dashboard.plaid.com/overview/production) and complete Plaid's required approval steps. Creating a Plaid account or obtaining Sandbox keys does **not** enable real banks. A [Trial plan](https://plaid.com/docs/account/billing/) may provide production access for supported products; availability and limits depend on your Plaid account.
3. Once access is enabled, get your client ID and **Production secret** from [Developers → Keys](https://dashboard.plaid.com/developers/keys). In **Settings → Plugins → pocketbook**, enter those credentials and select **`production`**. Configure the Hermes profile and gateway you use for Finance; for an SSH setup, this is the remote backend.
4. Remove Pocketbook's sample data if loaded, then choose **Connect a bank** in Finance (open **Manage banks** if accounts are already connected), or run `hermes pocketbook connect`. Start a fresh connection after changing environments. Complete the Plaid-hosted sign-in. Finance checks for completion automatically; **Check connection** checks immediately. **Cancel sign-in** clears Pocketbook's pending session without disconnecting existing banks; close the Plaid browser tab too.
5. For alerts, schedule the **Money alerts** blueprint (every two hours, 8am to 10pm) and, if you like, the **Weekly money digest** from `/blueprint` or the Cron page. Alerts need the gateway running.

### Sandbox versus real banks

`sandbox` is the default and only uses simulated accounts. Even **Connect a bank** opens a test flow in this environment. **First Platypus Bank** is a Plaid test institution, not your real bank; do not enter real bank credentials there. **Add Plaid test bank** and `hermes pocketbook test-bank` are also sandbox-only.

Use a separate Hermes profile for sandbox testing and production accounts. Sandbox connections and tokens do not work in Production, and changing the environment does not convert test accounts into real ones. See [Plaid's environment documentation](https://plaid.com/docs/sandbox/) for access requirements and test-bank behavior.

To try it without a bank: `hermes pocketbook demo` loads six months of sample data, clearly labeled. Run `hermes pocketbook demo --clear` before you connect a real bank. `hermes pocketbook charge "Delta Air Lines" 640 --category travel` adds a sample charge so you can watch an alert fire.

## Updating

Run `hermes plugins update pocketbook` in the Hermes profile that serves Finance. In an SSH setup, update the remote backend on that machine. Restart its gateway to load Python changes, then quit and reopen Hermes Desktop (or restart the web dashboard). If Desktop maintains a separate local plugin install, update that install too so its Finance bundle matches the backend. Version 0.2.0 adds bank switches and account preferences; installing a newer plugin does not change Plaid credentials or turn Sandbox accounts into real banks.

## Refreshing data

Use **Refresh all** in Finance (or `hermes pocketbook sync`) to bypass the sync interval, fetch all connected account balances including loans, pull transaction changes, update investment holdings, and recompute net worth. The Finance refresh also checks alerts and reloads its views. Failures and unavailable holdings appear in the refresh result; a zero transaction count does not mean every account refreshed successfully.

This retrieves the latest data Plaid already has. It does not force a real-time bank fetch, and banks may take time to provide updates ([Plaid balance freshness](https://plaid.com/docs/api/accounts/)). Sandbox accounts and loans remain simulated after a refresh; refreshing does not remove them or convert them into production accounts. Budget approvals, categories and other user settings are preserved.

### Recalculating after account changes

Successful bank syncs, including the sync after adding or repairing a bank, rebuild budget recommendations, alert conditions and the current net-worth snapshot. Finance then reads spending, cash flow, recurring bills, investments and goal progress from the updated saved data. Approved budget amounts stay unchanged until you approve a recommendation or edit a budget.

Use **Recalculate** in Finance, `hermes pocketbook recalculate`, or `money_accounts action=recalculate` to rebuild these results from saved data without contacting banks. Use **Refresh all** when you also want new bank data. Both paths respect bank/account exclusions. Recommendations use the last three complete months; if Plaid is still fetching a new bank's history, refresh again after that history arrives.

## Choosing banks and accounts

Open **Finance → Accounts** or **Manage banks**. Each bank has an on/off switch. Off stops refresh and excludes its accounts from spending, cash flow, budgets, alerts, investments and net worth. History stays stored, and turning the bank back on restores it. Each account also has a nickname and separate **Spending & budgets** and **Net worth** switches. Preferences survive refreshes. Closed accounts keep their transaction history but no longer contribute a stale balance.

Sandbox and Production connections are tagged and kept separately. Changing to Production automatically excludes known Sandbox connections, including their fake loans, before any token is sent to Plaid. It does not convert a connection or delete it. USD totals exclude unsupported currencies. Net-worth history is grouped by bank/account selection so a change of selection cannot compare real balances against old Sandbox totals; older snapshots remain stored separately. Active budgets remain yours, but proposals from another account selection must be regenerated.

If a bank needs you to sign in again, use **Repair bank sign-in**. This uses [Plaid update mode](https://plaid.com/docs/link/update-mode/) for the existing connection rather than creating another bank. Do not add the same bank again just to repair it, since a fresh connection can duplicate transactions. Hosted Link supports update mode, but production sign-in still depends on your Plaid access and institution.

The Budget tab lets you add, edit and remove monthly category budgets as well as review proposals. Transaction search supports account, category and date filters, and **Show more transactions** loads up to 500 matches. Narrow the filters for older history.

## Commands

| Command | Does |
|---|---|
| `hermes pocketbook status` | Accounts, sync state, this month |
| `hermes pocketbook connect` | Link a bank through Plaid Hosted Link |
| `hermes pocketbook cancel-link` | Clear a pending sign-in; also close its Plaid browser tab |
| `hermes pocketbook test-bank` | Add Plaid's sandbox bank (sandbox keys) |
| `hermes pocketbook sync` | Refresh connected accounts, transactions, holdings and recommendations |
| `hermes pocketbook recalculate` | Rebuild recommendations and alerts from saved data, without bank requests |
| `hermes pocketbook check` | Run the alert check |
| `hermes pocketbook import FILE` | Import a CSV (other apps, a bank, or Amazon orders) |
| `hermes pocketbook demo [--clear]` | Load or remove sample data |

## Data, network and secrets

- **Stored locally**: accounts, transactions, budgets, alert history and Amazon order items in `<hermes home>/plugin-data/pocketbook/data.db`. Charts go to `plugin-data/pocketbook/charts/` and are deleted after a week. Pocketbook sets the folder to owner-only (`0700`) and the database to `0600`.
- **Secrets**: your Plaid client ID and secret, and one access token per connected bank, are stored through Hermes's credential route (`.env`) and read with `agent.secret_scope.get_secret`. They never enter the database, prompts or logs.
- **Network**: only Plaid (`sandbox.plaid.com` or `production.plaid.com`), when you connect, sync, or a scheduled check runs. Sync requests Transactions, plus Investments when you share a brokerage or retirement account. No telemetry, no update checks.
- **Model**: when you ask about money, the numbers and transactions the tools return go to your configured model like any other tool result.
- **Background work**: none on install. Syncing happens on request, from the tab, or from the alert blueprint you choose to schedule (at most once every 30 minutes by default).
- **Writes in scheduled runs**: budget, goal and rule changes, recategorizing, imports, dismissing alerts and bank connect/disconnect refuse to run inside cron jobs.
- **Sample data stays separate**: it loads only into an empty Pocketbook, and you must remove it before you connect a bank or import a file.
- **Imports**: `money_import` reads only `.csv` files up to 20 MB.
- **Approval for deletes**: when the model calls `money_accounts action=disconnect` or `money_import remove`, Hermes asks you to approve first.
- **Disconnecting**: the access token is deleted only after Plaid confirms the removal, so a failed disconnect can be retried.
- Nothing moves money.

## Limits

US banks and USD only. Holdings come from Plaid Investments, which some institutions do not support; their balances still count toward net worth. Net worth history starts at your first sync. One person per Hermes profile. Plaid access and connection limits depend on your plan and approved products. Bills are detected from history (three or more charges on a steady cadence), so a new bill appears after its third charge. Amazon orders match a charge only when the totals agree, so orders split across shipments may stay unmatched; their items still count. Importing a CSV that overlaps a connected bank duplicates those charges, so remove one with `money_import remove=...`. Import column names come from public descriptions of each app's export, so a format change can need a fix.

## Development

```
uv run --with pytest --with Pillow --with fastapi --with httpx pytest tests -q
node --test tests/ui.test.cjs
python3 scripts/build_ui.py             # after editing ui/app.js
hermes plugins validate .
```

## License

MIT
