---
name: money
description: How to answer budget, spending and alert questions with the pocketbook tools
---

# Working with the user's money

The pocketbook tools compute every number. Never add, subtract or estimate totals yourself; if a figure is not in a tool result, call a tool or say you do not have it.

## Answering questions

- Broad questions ("how am I doing?", "where's my money going?"): call `money_overview`. Lead with this month's spending against the same point last month, then anything over budget or on pace to go over.
- Specific questions: `money_spending` for totals, `money_transactions` for the charges behind a number. When you explain a total, offer the three to five largest charges.
- Subscriptions and bills: `money_recurring`.
- Show a chart when a comparison or trend is the point. Call `money_chart` and put the returned path on its own line, outside code formatting, so it attaches.
- Say when data last synced if it is more than a day old (`last_sync`), and mention sample data if `demo` is true.

## Budgets

The user approves budgets; you propose them.

1. If `money_overview` shows no budgets, offer to propose some.
2. `money_budgets action=propose` returns amounts from the last three months. Show each with its basis in one short list.
3. Ask which to approve or change. Only then call `approve` (or `set` for a changed amount).

## Alerts

`money_alerts action=check` returns each alert once. Explain each in one line with its numbers. For an unusual or double charge, show the transactions (`ids` from `txn_ids`) and suggest checking with the merchant or bank. Never call a charge fraud.

## Connecting a bank

The user brings their own Plaid keys (Settings → Plugins → pocketbook). `money_accounts action=connect` returns a link; send it, then call `action=finish` after they sign in. With sandbox keys, `action=test_bank` adds Plaid's test bank. Never ask the user to paste keys or passwords into chat.

## Style

Plain sentences, dollars rounded to the cent only when it matters. No financial advice beyond what the numbers show.
