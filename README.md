<img src="assets/icon.svg" width="64" height="64" alt="">

# Pocketbook

Your budget, spending and money alerts inside Hermes. Pocketbook syncs your bank and card accounts through your own Plaid keys, proposes budgets from your history, adds a Finance tab to Hermes Desktop and the web dashboard, and messages you when something needs a look.

## What it does

- **Finance tab**: overview (spending, budgets, alerts, bills), budget, cash flow, net worth and investments, goals, purchases, and transactions with editable categories and rules.
- **Amazon purchases**: ask Hermes to read your Amazon order history with its browser tools, or import Amazon's `Order History.csv`. Each Amazon charge shows what it bought, repeat buys show up like subscriptions you never signed up for, and items get a cost per day spread over how long they last.
- **Imports**: CSV exports from Monarch, Mint, YNAB, Rocket Money, Copilot or your bank. Use it to bring in old history, or to run Pocketbook without Plaid.
- **Net worth**: assets minus cards and loans across every connected account, with daily history.
- **Investments**: holdings from brokerage and retirement accounts, allocation by asset type, and gain against cost basis.
- **Savings goals**: follow a savings account balance or a number you update, with pace and the monthly amount still needed.
- **Categorization rules**: "anything from Trader Joe's is Groceries" applies to past and future charges. Custom categories work with budgets.
- **Proposed budgets**: Pocketbook takes the median of your last three full months per category. You adjust and approve them in chat or in the tab.
- **Alerts, each sent once**: large purchases, charges well above your usual at a merchant, possible double charges, large first charges from a new merchant, subscription price increases, bills due within three days, budgets over or on pace to go over, goals falling behind or reached, and broken bank connections. Quiet hours hold alerts until morning.
- **Chat**: fifteen `money_*` tools answer questions from computed numbers, and `money_chart` draws PNG charts that attach in Telegram, Discord and other gateways.
- **`/money`**: a snapshot without a model call. `/money check` runs the alert check.

All math runs in code. The model explains results; it never computes totals.

## Setup

1. Install and enable: `hermes plugins install <repo>` then `hermes plugins enable pocketbook`.
2. Get Plaid keys at [dashboard.plaid.com](https://dashboard.plaid.com) (Developers → Keys). Sandbox keys work right away; production needs Plaid's free Trial plan.
3. Paste the client ID and secret in **Settings → Plugins → pocketbook** and pick `sandbox` or `production`.
4. Connect a bank from the Finance tab, by asking Hermes, or with `hermes pocketbook connect`. Plaid hosts the sign-in page, so no server or webhook is needed.
5. For alerts, schedule the **Money alerts** blueprint (every two hours, 8am to 10pm) and, if you like, the **Weekly money digest** from `/blueprint` or the Cron page. Alerts need the gateway running.

To try it without a bank: `hermes pocketbook demo` loads six months of sample data, clearly labeled. `hermes pocketbook charge "Delta Air Lines" 640 --category travel` adds a sample charge so you can watch an alert fire.

## Commands

| Command | Does |
|---|---|
| `hermes pocketbook status` | Accounts, sync state, this month |
| `hermes pocketbook connect` | Link a bank through Plaid Hosted Link |
| `hermes pocketbook test-bank` | Add Plaid's sandbox bank (sandbox keys) |
| `hermes pocketbook sync` | Pull new transactions now |
| `hermes pocketbook check` | Run the alert check |
| `hermes pocketbook import FILE` | Import a CSV (other apps, a bank, or Amazon orders) |
| `hermes pocketbook demo [--clear]` | Load or remove sample data |

## Data, network and secrets

- **Stored locally**: accounts, transactions, budgets, alert history and Amazon order items in `<hermes home>/plugin-data/pocketbook/data.db`. Charts go to `plugin-data/pocketbook/charts/`.
- **Secrets**: your Plaid client ID and secret, and one access token per connected bank, are stored through Hermes's credential route (`.env`) and read with `agent.secret_scope.get_secret`. They never enter the database, prompts or logs.
- **Network**: only Plaid (`sandbox.plaid.com` or `production.plaid.com`), when you connect, sync, or a scheduled check runs. Sync requests Transactions, plus Investments when you share a brokerage or retirement account. No telemetry, no update checks.
- **Model**: when you ask about money, the numbers and transactions the tools return go to your configured model like any other tool result.
- **Background work**: none on install. Syncing happens on request, from the tab, or from the alert blueprint you choose to schedule (at most once every 30 minutes by default).
- **Writes in scheduled runs**: budget changes, recategorizing and bank connect/disconnect refuse to run inside cron jobs.
- Nothing moves money.

## Limits

US banks and USD only. Holdings come from Plaid Investments, which some institutions do not support; their balances still count toward net worth. Net worth history starts at your first sync. One person per Hermes profile. Plaid's Trial plan allows ten production connections in total. Bills are detected from history (three or more charges on a steady cadence), so a new bill appears after its third charge. Amazon orders match a charge only when the totals agree, so orders split across shipments may stay unmatched; their items still count. Importing a CSV that overlaps a connected bank duplicates those charges, so remove one with `money_import remove=...`. Import column names come from public descriptions of each app's export, so a format change can need a fix.

## Development

```
uv run --with pytest pytest tests      # or any Python with Pillow and pytest
python3 scripts/build_ui.py             # after editing ui/app.js
hermes plugins validate .
```

## License

MIT
