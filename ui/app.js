// Shared Finance tab. scripts/build_ui.py wraps this for the Desktop app and the web dashboard.
// Expects in scope: React, api(path, opts) -> Promise, openUrl(url), onRefreshEvent(fn) -> disposer.

const h = React.createElement
const { useState, useEffect, useCallback, useMemo } = React

const V = {
  text: 'inherit',
  muted: 'var(--ui-text-tertiary, var(--color-muted-foreground, #888))',
  faint: 'var(--ui-text-quaternary, var(--color-muted-foreground, #aaa))',
  line: 'var(--ui-stroke-secondary, var(--color-border, rgba(127,127,127,.25)))',
  card: 'var(--ui-bg-card, var(--color-card, transparent))',
  track: 'var(--ui-bg-tertiary, var(--color-muted, rgba(127,127,127,.15)))',
  accent: 'var(--ui-accent, var(--color-primary, #3478f6))',
  red: 'var(--ui-red, var(--color-destructive, #e0483c))',
  yellow: 'var(--ui-orange, var(--ui-yellow, #e8982a))',
  green: 'var(--ui-green, #20966a)'
}

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })
const usd0 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
const money = (c, whole) => (whole ? usd0 : usd).format((c || 0) / 100)
const short = c => {
  const d = (c || 0) / 100
  return Math.abs(d) >= 1000 ? `$${(d / 1000).toFixed(1)}k` : `$${Math.round(d)}`
}
const monthName = m => new Date(`${m}-15T12:00:00`).toLocaleString('en-US', { month: 'long' })
const monthAbbr = m => new Date(`${m}-15T12:00:00`).toLocaleString('en-US', { month: 'short' })
const day = iso => new Date(`${iso}T12:00:00`).toLocaleString('en-US', { month: 'short', day: 'numeric' })
const ago = iso => {
  if (!iso) return 'never'
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  if (mins < 1440) return `${Math.round(mins / 60)} h ago`
  return `${Math.round(mins / 1440)} d ago`
}

const S = {
  page: { display: 'flex', flexDirection: 'column', gap: 16, padding: '20px 24px 40px', maxWidth: 1080, margin: '0 auto',
    fontSize: 13, lineHeight: 1.45, color: V.text, fontVariantNumeric: 'tabular-nums' },
  card: { border: `1px solid ${V.line}`, borderRadius: 10, padding: 16, background: V.card, minWidth: 0 },
  h2: { fontSize: 13, fontWeight: 600, margin: '0 0 12px' },
  muted: { color: V.muted },
  row: { display: 'flex', alignItems: 'center', gap: 10 },
  grid: (min) => ({ display: 'grid', gap: 16, gridTemplateColumns: `repeat(auto-fit, minmax(${min}px, 1fr))` }),
  btn: { border: `1px solid ${V.line}`, borderRadius: 7, padding: '5px 11px', background: 'transparent', color: 'inherit',
    font: 'inherit', cursor: 'pointer', whiteSpace: 'nowrap' },
  primary: { border: '1px solid transparent', borderRadius: 7, padding: '5px 11px', background: V.accent, color: 'var(--ui-accent-foreground, var(--color-primary-foreground, #fff))',
    font: 'inherit', fontWeight: 500, cursor: 'pointer', whiteSpace: 'nowrap' },
  input: { border: `1px solid ${V.line}`, borderRadius: 7, padding: '5px 9px', background: 'transparent', color: 'inherit',
    font: 'inherit', minWidth: 0 }
}

function Button ({ primary, onClick, disabled, children, title }) {
  return h('button', { style: { ...(primary ? S.primary : S.btn), opacity: disabled ? 0.5 : 1 }, onClick, disabled, title }, children)
}

function Card ({ title, right, children, style }) {
  return h('section', { style: { ...S.card, ...style } },
    (title || right) && h('div', { style: { ...S.row, justifyContent: 'space-between', marginBottom: 12 } },
      h('h2', { style: { ...S.h2, margin: 0 } }, title), right),
    children)
}

function Stat ({ label, value, sub, tone }) {
  return h('div', { style: S.card },
    h('div', { style: { ...S.muted, fontSize: 12 } }, label),
    h('div', { style: { fontSize: 22, fontWeight: 600, margin: '4px 0 2px', color: tone || 'inherit' } }, value),
    sub && h('div', { style: { ...S.muted, fontSize: 12 } }, sub))
}

// ---------- charts (inline SVG, theme colors) ----------

function PaceChart ({ pace, budget }) {
  const W = 640, H = 220, L = 44, R = 12, T = 12, B = 26
  const all = [...pace.current, ...pace.previous, budget || 0, 1]
  const peak = Math.max(...all) * 1.08
  const x = i => L + (W - L - R) * i / Math.max(1, pace.days - 1)
  const y = v => T + (H - T - B) * (1 - v / peak)
  const path = arr => arr.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('')
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => peak * f)
  return h('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img', 'aria-label': 'Spending this month vs last month' },
    ticks.map((t, i) => h('g', { key: i },
      h('line', { x1: L, x2: W - R, y1: y(t), y2: y(t), stroke: V.line }),
      h('text', { x: L - 6, y: y(t) + 4, textAnchor: 'end', fontSize: 10, fill: V.muted }, short(t)))),
    budget ? h('g', null,
      h('line', { x1: L, x2: W - R, y1: y(budget), y2: y(budget), stroke: V.yellow, strokeDasharray: '4 4' }),
      h('text', { x: W - R, y: y(budget) - 5, textAnchor: 'end', fontSize: 10, fill: V.yellow }, `Budget ${short(budget)}`)) : null,
    h('path', { d: path(pace.previous), fill: 'none', stroke: V.faint, strokeWidth: 2 }),
    h('path', { d: path(pace.current), fill: 'none', stroke: V.accent, strokeWidth: 2.5, strokeLinejoin: 'round' }),
    pace.current.length ? h('circle', { cx: x(pace.current.length - 1), cy: y(pace.current[pace.current.length - 1]), r: 3.5, fill: V.accent }) : null,
    [1, 8, 15, 22, pace.days].map(d => h('text', { key: d, x: x(d - 1), y: H - 8, textAnchor: 'middle', fontSize: 10, fill: V.muted }, d)))
}

function MonthsChart ({ months }) {
  const W = 640, H = 200, L = 44, R = 12, T = 18, B = 26
  const peak = Math.max(1, ...months.map(m => Math.max(m.spend_cents, m.income_cents))) * 1.1
  const slot = (W - L - R) / months.length
  const y = v => T + (H - T - B) * (1 - v / peak)
  return h('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img', 'aria-label': 'Monthly spending' },
    [0, 0.5, 1].map((f, i) => h('g', { key: i },
      h('line', { x1: L, x2: W - R, y1: y(peak * f), y2: y(peak * f), stroke: V.line }),
      h('text', { x: L - 6, y: y(peak * f) + 4, textAnchor: 'end', fontSize: 10, fill: V.muted }, short(peak * f)))),
    months.map((m, i) => {
      const cx = L + slot * (i + 0.5)
      const top = y(m.spend_cents)
      return h('g', { key: m.month },
        h('title', null, `${monthName(m.month)}: spent ${money(m.spend_cents, true)}, income ${money(m.income_cents, true)}`),
        h('rect', { x: cx - slot * 0.28, width: slot * 0.56, y: top, height: Math.max(0, H - B - top), rx: 4,
          fill: V.accent, opacity: m.partial ? 0.45 : 1 }),
        h('text', { x: cx, y: top - 5, textAnchor: 'middle', fontSize: 10, fill: V.muted }, short(m.spend_cents)),
        m.income_cents ? h('circle', { cx, cy: y(m.income_cents), r: 3.5, fill: V.green }) : null,
        h('text', { x: cx, y: H - 8, textAnchor: 'middle', fontSize: 10, fill: V.muted }, monthAbbr(m.month)))
    }))
}

// ---------- sections ----------

function Setup ({ setup, act, busy }) {
  const ready = setup.plaid_configured
  return h(Card, { title: 'Connect your money' },
    h('ol', { style: { margin: '0 0 14px', paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 6 } },
      h('li', null, 'Create free Plaid keys at dashboard.plaid.com (Developers → Keys).'),
      h('li', null, 'Paste them in Settings → Plugins → pocketbook. ',
        h('span', { style: { color: ready ? V.green : V.muted } }, ready ? `Keys found (${setup.plaid_env}).` : 'No keys yet.')),
      h('li', null, 'Connect a bank. Pocketbook syncs two years of history and proposes budgets from it.')),
    h('div', { style: { ...S.row, flexWrap: 'wrap' } },
      h(Button, { primary: true, disabled: !ready || busy, onClick: () => act('connect') }, 'Connect a bank'),
      setup.plaid_env === 'sandbox' && h(Button, { disabled: !ready || busy, onClick: () => act('test-bank') }, 'Add Plaid test bank'),
      h(Button, { disabled: busy, onClick: () => act('demo') }, 'Try sample data')),
    setup.linking && h('div', { style: { ...S.row, marginTop: 12 } },
      h('span', { style: S.muted }, 'Finished signing in to your bank?'),
      h(Button, { primary: true, disabled: busy, onClick: () => act('finish') }, 'Finish connecting')))
}

const KIND = { large: 'Large purchase', unusual: 'Unusual charge', duplicate: 'Double charge', new_merchant: 'New merchant',
  subscription: 'Price increase', bill: 'Bill due', budget: 'Budget', connection: 'Connection', goal: 'Goal' }

function Alerts ({ alerts, act, showTxns }) {
  if (!alerts.length) {
    return h(Card, { title: 'Needs a look' }, h('div', { style: S.muted }, 'Nothing unusual. Pocketbook will tell you when something changes.'))
  }
  return h(Card, { title: `Needs a look (${alerts.length})`, right: h(Button, { onClick: () => act('dismiss', { keys: null }) }, 'Clear all') },
    h('div', { style: { display: 'flex', flexDirection: 'column' } },
      alerts.map((a, i) => h('div', { key: a.key, style: { ...S.row, alignItems: 'flex-start', padding: '10px 0', borderTop: i ? `1px solid ${V.line}` : 'none' } },
        h('span', { style: { width: 8, height: 8, borderRadius: 4, marginTop: 6, flex: 'none', background: a.severity === 'warn' ? V.yellow : V.accent } }),
        h('div', { style: { flex: 1, minWidth: 0 } },
          h('div', { style: { fontWeight: 500 } }, a.title),
          h('div', { style: S.muted }, `${KIND[a.kind] || a.kind} · ${a.detail}`)),
        a.txn_ids.length ? h(Button, { onClick: () => showTxns(a.txn_ids, a.title) }, 'Show') : null,
        h(Button, { onClick: () => act('dismiss', { keys: [a.key] }), title: 'Dismiss' }, '✕')))))
}

function BudgetRow ({ b }) {
  const scale = Math.max(b.budget_cents, b.spent_cents, b.projected_cents) || 1
  const color = b.status === 'over' ? V.red : b.status === 'warn' ? V.yellow : V.accent
  const note = b.status === 'over' ? `over by ${money(-b.remaining_cents, true)}`
    : b.status === 'warn' ? `on pace for ${money(b.projected_cents, true)}` : `${money(b.remaining_cents, true)} left`
  return h('div', { style: { padding: '7px 0' } },
    h('div', { style: { ...S.row, justifyContent: 'space-between', marginBottom: 5 } },
      h('span', { style: { fontWeight: 500 } }, b.label),
      h('span', null, `${money(b.spent_cents, true)} `, h('span', { style: S.muted }, `of ${money(b.budget_cents, true)} · `),
        h('span', { style: { color: b.status === 'ok' ? V.muted : color } }, note))),
    h('div', { style: { position: 'relative', height: 8, borderRadius: 4, background: V.track } },
      h('div', { style: { position: 'absolute', inset: 0, width: `${Math.min(100, 100 * b.spent_cents / scale)}%`, borderRadius: 4, background: color } }),
      b.projected_cents > b.spent_cents && h('div', { style: { position: 'absolute', top: 0, bottom: 0, left: `${Math.min(100, 100 * b.spent_cents / scale)}%`,
        width: `${Math.min(100, 100 * (b.projected_cents - b.spent_cents) / scale)}%`, background: color, opacity: 0.25 } }),
      h('div', { style: { position: 'absolute', top: -3, bottom: -3, width: 2, left: `calc(${100 * b.budget_cents / scale}% - 1px)`, background: 'currentColor', opacity: 0.6 } })))
}

function Proposals ({ proposed, act, busy }) {
  const [edits, setEdits] = useState({})
  if (!proposed.length) return null
  const save = async () => {
    const changed = proposed.filter(p => {
      const v = edits[p.category]
      return v !== undefined && v !== '' && Math.round(Number(v) * 100) !== p.proposed_cents
    })
    for (const p of changed) await act('budget-set', { category: p.category, amount: Number(edits[p.category]) }, true)
    const rest = proposed.filter(p => !changed.includes(p)).map(p => p.category)
    if (rest.length) await act('approve', { categories: rest })
    else await act('refresh')
  }
  return h('div', { style: { marginTop: 8 } },
    h('div', { style: { ...S.muted, marginBottom: 8 } }, 'Proposed from your last three months. Adjust any amount, then approve.'),
    proposed.map(p => h('div', { key: p.category, style: { ...S.row, padding: '5px 0' } },
      h('span', { style: { flex: 1, fontWeight: 500 } }, p.label),
      h('span', { style: { ...S.muted, flex: 2, fontSize: 12 } }, p.basis),
      h('span', { style: S.muted }, '$'),
      h('input', { style: { ...S.input, width: 80, textAlign: 'right' }, inputMode: 'decimal',
        value: edits[p.category] ?? String(Math.round(p.proposed_cents / 100)),
        onChange: e => setEdits({ ...edits, [p.category]: e.target.value }) }))),
    h('div', { style: { ...S.row, justifyContent: 'flex-end', marginTop: 10 } },
      h(Button, { primary: true, disabled: busy, onClick: save }, 'Approve budgets')))
}

function Budgets ({ budgets, act, busy }) {
  const has = budgets.budgets.length > 0
  return h(Card, {
    title: has ? `Budgets · ${money(budgets.total_spent_cents, true)} of ${money(budgets.total_budget_cents, true)}` : 'Budgets',
    right: h(Button, { disabled: busy, onClick: () => act('propose') }, has ? 'Re-propose' : 'Propose budgets')
  },
  !has && !budgets.proposed.length && h('div', { style: S.muted }, 'No budgets yet. Pocketbook can propose them from your spending history.'),
  budgets.budgets.map(b => h(BudgetRow, { key: b.category, b })),
  h(Proposals, { proposed: budgets.proposed, act, busy }))
}

function Transactions ({ initial, api, filter, clearFilter, categories, onChanged }) {
  const [q, setQ] = useState('')
  const [rows, setRows] = useState(initial)
  useEffect(() => {
    if (filter) { api(`/transactions?ids=${encodeURIComponent(filter.ids.join(','))}`).then(r => setRows(r.transactions)).catch(() => {}); return }
    if (!q) { setRows(initial); return }
    const t = setTimeout(() => api(`/transactions?query=${encodeURIComponent(q)}&limit=100`).then(r => setRows(r.transactions)).catch(() => {}), 250)
    return () => clearTimeout(t)
  }, [q, filter, initial])
  return h(Card, {
    title: filter ? filter.title : 'Transactions',
    right: filter ? h(Button, { onClick: clearFilter }, 'Show all')
      : h('input', { style: { ...S.input, width: 200 }, placeholder: 'Search merchants…', value: q, onChange: e => setQ(e.target.value) })
  },
  h('div', { style: { overflowX: 'auto' } },
    h('table', { style: { width: '100%', borderCollapse: 'collapse' } },
      h('tbody', null, rows.map(t => h('tr', { key: t.id, style: { borderTop: `1px solid ${V.line}` } },
        h('td', { style: { padding: '7px 8px 7px 0', ...S.muted, whiteSpace: 'nowrap' } }, day(t.date)),
        h('td', { style: { padding: '7px 8px', fontWeight: 500 } }, t.merchant, t.pending && h('span', { style: { ...S.muted, fontWeight: 400 } }, ' · pending')),
        h('td', { style: { padding: '7px 8px', ...S.muted } }, h(CategoryCell, { t, categories, api, onChanged })),
        h('td', { style: { padding: '7px 8px', ...S.muted, whiteSpace: 'nowrap' } }, t.account),
        h('td', { style: { padding: '7px 0 7px 8px', textAlign: 'right', whiteSpace: 'nowrap', color: t.amount_cents < 0 ? V.green : 'inherit' } },
          t.amount_cents < 0 ? `+${money(-t.amount_cents)}` : money(t.amount_cents))))))),
  !rows.length && h('div', { style: S.muted }, 'No matching transactions.'))
}


// ---------- net worth, investments, goals, rules ----------

function AreaChart ({ points }) {
  const W = 640, H = 200, L = 52, R = 12, T = 12, B = 26
  if (points.length < 2) return h('div', { style: S.muted }, 'History starts with your first sync.')
  const vals = points.map(p => p.net_cents)
  let lo = Math.min(...vals), hi = Math.max(...vals)
  const pad = Math.max(1, (hi - lo) * 0.15); lo -= pad; hi += pad
  const x = i => L + (W - L - R) * i / (vals.length - 1)
  const y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo))
  const line = vals.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('')
  const months = []
  points.forEach((p, i) => { if (p.date.slice(8) <= '07' && !months.some(m => m.m === p.date.slice(0, 7))) months.push({ m: p.date.slice(0, 7), i }) })
  return h('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img', 'aria-label': 'Net worth over time' },
    [0, 0.5, 1].map((f, i) => h('g', { key: i },
      h('line', { x1: L, x2: W - R, y1: y(lo + (hi - lo) * f), y2: y(lo + (hi - lo) * f), stroke: V.line }),
      h('text', { x: L - 6, y: y(lo + (hi - lo) * f) + 4, textAnchor: 'end', fontSize: 10, fill: V.muted }, short(lo + (hi - lo) * f)))),
    h('path', { d: `${line}L${x(vals.length - 1)},${H - B}L${L},${H - B}Z`, fill: V.accent, opacity: 0.12 }),
    h('path', { d: line, fill: 'none', stroke: V.accent, strokeWidth: 2, strokeLinejoin: 'round' }),
    months.map(m => h('text', { key: m.m, x: x(m.i), y: H - 8, textAnchor: 'middle', fontSize: 10, fill: V.muted }, monthAbbr(m.m))))
}

const PALETTE = [V.accent, 'var(--ui-blue, #5fa0fa)', V.green, V.yellow, 'var(--ui-purple, #9670e6)', V.faint, V.red]
const signed = c => `${c >= 0 ? '+' : '−'}${money(Math.abs(c), true)}`

function NetWorth ({ nw }) {
  const side = s => nw.accounts.filter(a => a.side === s)
  const list = (title, rows, total) => h('div', { style: { flex: 1, minWidth: 220 } },
    h('div', { style: { ...S.row, justifyContent: 'space-between', fontWeight: 600, marginBottom: 4 } }, h('span', null, title), h('span', null, money(total, true))),
    rows.map(a => h('div', { key: a.account_id, style: { ...S.row, justifyContent: 'space-between', padding: '4px 0', borderTop: `1px solid ${V.line}` } },
      h('span', null, a.name, h('span', { style: S.muted }, a.mask ? ` ••${a.mask}` : '')),
      h('span', null, money(a.current_cents, true)))))
  return h(Card, { title: `Net worth ${money(nw.net_cents, true)}`,
    right: nw.change_30d_cents != null && h('span', { style: { color: nw.change_30d_cents >= 0 ? V.green : V.red } }, `${signed(nw.change_30d_cents)} in 30 days`) },
  h(AreaChart, { points: nw.history }),
  h('div', { style: { display: 'flex', gap: 24, flexWrap: 'wrap', marginTop: 12 } },
    list('Assets', side('asset'), nw.assets_cents), list('Liabilities', side('liability'), nw.liabilities_cents)))
}

function Investments ({ inv }) {
  if (!inv.holdings.length) {
    return h(Card, { title: 'Investments' }, h('div', { style: S.muted },
      inv.accounts_without_holdings.length ? 'Your investment accounts did not share holdings. Balances still count toward net worth.'
        : 'No investment accounts yet. Connect a brokerage or retirement account to see holdings and allocation.'))
  }
  return h(Card, { title: `Investments ${money(inv.total_cents, true)}`,
    right: inv.gain_cents != null && h('span', { style: { color: inv.gain_cents >= 0 ? V.green : V.red } }, `${signed(inv.gain_cents)} (${inv.gain_pct}%) vs cost`) },
  h('div', { style: { display: 'flex', height: 12, borderRadius: 6, overflow: 'hidden', margin: '4px 0 10px' } },
    inv.allocation.map((a, i) => h('div', { key: a.type, title: `${a.type} ${a.pct}%`, style: { width: `${a.pct}%`, background: PALETTE[i % PALETTE.length] } }))),
  h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: '6px 16px', marginBottom: 12 } },
    inv.allocation.map((a, i) => h('span', { key: a.type, style: S.row },
      h('span', { style: { width: 9, height: 9, borderRadius: 2, background: PALETTE[i % PALETTE.length] } }),
      `${a.type} `, h('span', { style: S.muted }, `${a.pct}%`)))),
  h('div', { style: { overflowX: 'auto' } }, h('table', { style: { width: '100%', borderCollapse: 'collapse' } },
    h('tbody', null, inv.holdings.map((x, i) => h('tr', { key: i, style: { borderTop: `1px solid ${V.line}` } },
      h('td', { style: { padding: '6px 8px 6px 0', fontWeight: 600 } }, x.ticker || '—'),
      h('td', { style: { padding: '6px 8px', ...S.muted } }, x.name),
      h('td', { style: { padding: '6px 8px', ...S.muted, whiteSpace: 'nowrap' } }, x.account),
      h('td', { style: { padding: '6px 8px', textAlign: 'right' } }, money(x.value_cents, true)),
      h('td', { style: { padding: '6px 0 6px 8px', textAlign: 'right', whiteSpace: 'nowrap', color: x.gain_cents == null ? V.muted : x.gain_cents >= 0 ? V.green : V.red } },
        x.gain_cents == null ? '—' : signed(x.gain_cents))))))))
}

function SavedEditor ({ g, post }) {
  const [v, setV] = useState(null)
  if (v === null) return h(Button, { onClick: () => setV(String(g.current_cents / 100)) }, 'Update saved')
  const save = () => { if (v !== '' && !isNaN(Number(v))) post('/goals', { action: 'update', id: g.id, saved: Number(v) }); setV(null) }
  return h('span', { style: S.row },
    h('input', { style: { ...S.input, width: 90, textAlign: 'right' }, autoFocus: true, inputMode: 'decimal', value: v,
      onChange: e => setV(e.target.value), onKeyDown: e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setV(null) } }),
    h(Button, { primary: true, onClick: save }, 'Save'))
}

const GOAL_STATUS = { on_track: ['On track', null], behind: ['Behind', 'yellow'], overdue: ['Past due', 'red'], done: ['Reached', 'green'], no_date: ['No date', null] }

function Goals ({ goals, accounts, post, busy }) {
  const blank = { name: '', target: '', target_date: '', account_id: '', saved: '' }
  const [form, setForm] = useState(blank)
  const [open, setOpen] = useState(false)
  const set = k => e => setForm({ ...form, [k]: e.target.value })
  const add = async () => {
    await post('/goals', { action: 'add', name: form.name, target: Number(form.target), target_date: form.target_date || null,
      account_id: form.account_id || null, saved: form.account_id ? null : Number(form.saved || 0) })
    setForm(blank); setOpen(false)
  }
  return h(Card, { title: 'Savings goals', right: h(Button, { onClick: () => setOpen(!open) }, open ? 'Cancel' : 'New goal') },
    open && h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14 } },
      h('input', { style: { ...S.input, flex: '2 1 160px' }, placeholder: 'Name, e.g. Emergency fund', value: form.name, onChange: set('name') }),
      h('input', { style: { ...S.input, flex: '1 1 100px' }, placeholder: 'Target $', inputMode: 'decimal', value: form.target, onChange: set('target') }),
      h('input', { style: { ...S.input, flex: '1 1 130px' }, type: 'date', value: form.target_date, onChange: set('target_date') }),
      h('select', { style: { ...S.input, flex: '2 1 180px' }, value: form.account_id, onChange: set('account_id') },
        h('option', { value: '' }, 'Track manually'),
        accounts.filter(a => a.type === 'depository' || a.type === 'investment').map(a => h('option', { key: a.account_id, value: a.account_id }, `Follow ${a.name} balance`))),
      !form.account_id && h('input', { style: { ...S.input, flex: '1 1 100px' }, placeholder: 'Saved so far $', inputMode: 'decimal', value: form.saved, onChange: set('saved') }),
      h(Button, { primary: true, disabled: busy || !form.name || !(Number(form.target) > 0), onClick: add }, 'Add goal')),
    !goals.length && !open && h('div', { style: S.muted }, 'No goals yet. A goal can follow a savings account balance or a number you update.'),
    goals.map(g => {
      const [word, tone] = GOAL_STATUS[g.status] || [g.status, null]
      const color = tone ? V[tone] : V.accent
      return h('div', { key: g.id, style: { padding: '10px 0', borderTop: `1px solid ${V.line}` } },
        h('div', { style: { ...S.row, justifyContent: 'space-between', marginBottom: 6 } },
          h('span', { style: { fontWeight: 600 } }, g.name, h('span', { style: { ...S.muted, fontWeight: 400 } }, g.account ? ` · follows ${g.account}` : ' · manual')),
          h('span', { style: S.row },
            h('span', { style: { color: tone ? color : V.muted } }, word),
            g.tracking === 'manual' && h(SavedEditor, { g, post }),
            h(Button, { onClick: () => post('/goals', { action: 'remove', id: g.id }), title: 'Remove goal' }, '✕'))),
        h('div', { style: { height: 8, borderRadius: 4, background: V.track, position: 'relative' } },
          h('div', { style: { position: 'absolute', inset: 0, width: `${g.pct}%`, borderRadius: 4, background: color } }),
          g.expected_cents != null && g.status !== 'done' && h('div', { title: 'Where the pace says you should be', style: { position: 'absolute', top: -3, bottom: -3, width: 2,
            left: `calc(${Math.min(100, 100 * g.expected_cents / g.target_cents)}% - 1px)`, background: 'currentColor', opacity: 0.5 } })),
        h('div', { style: { ...S.muted, fontSize: 12, marginTop: 5 } },
          `${money(g.current_cents, true)} of ${money(g.target_cents, true)} (${g.pct}%)`,
          g.target_date ? ` · by ${day(g.target_date)}, ${g.target_date.slice(0, 4)}` : '',
          g.needed_per_month_cents && g.status !== 'done' ? ` · ${money(g.needed_per_month_cents, true)}/month to finish` : ''))
    }))
}

function Rules ({ rules, categories, post, busy }) {
  const [match, setMatch] = useState('')
  const [cat, setCat] = useState('')
  const add = async () => { await post('/rules', { action: 'add', match, category: cat }); setMatch(''); setCat('') }
  return h(Card, { title: 'Categorization rules' },
    h('div', { style: { ...S.muted, marginBottom: 10 } }, 'Charges whose merchant contains the text get the category, now and on every sync. Type a new name to make your own category.'),
    h('div', { style: { display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 } },
      h('input', { style: { ...S.input, flex: '2 1 160px' }, placeholder: 'Merchant contains…', value: match, onChange: e => setMatch(e.target.value) }),
      h('input', { style: { ...S.input, flex: '2 1 160px' }, placeholder: 'Category', list: 'pb-categories', value: cat, onChange: e => setCat(e.target.value) }),
      h('datalist', { id: 'pb-categories' }, Object.values(categories).map(l => h('option', { key: l, value: l }))),
      h(Button, { primary: true, disabled: busy || !match.trim() || !cat.trim(), onClick: add }, 'Add rule')),
    rules.map(r => h('div', { key: r.id, style: { ...S.row, padding: '6px 0', borderTop: `1px solid ${V.line}` } },
      h('span', { style: { flex: 1 } }, h('span', { style: S.muted }, 'contains '), `“${r.pattern}”`, h('span', { style: S.muted }, ' → '), h('span', { style: { fontWeight: 500 } }, r.category_label)),
      h('span', { style: S.muted }, `${r.matches} charges`),
      h(Button, { onClick: () => post('/rules', { action: 'remove', id: r.id }), title: 'Remove rule' }, '✕'))))
}

function CategoryCell ({ t, categories, api, onChanged }) {
  const [editing, setEditing] = useState(false)
  if (!editing) return h('span', { style: { cursor: 'pointer', borderBottom: `1px dashed ${V.line}` }, title: 'Change category', onClick: () => setEditing(true) }, t.category_label)
  return h('select', { style: { ...S.input, padding: '2px 4px' }, autoFocus: true, defaultValue: t.category, onBlur: () => setEditing(false),
    onChange: async e => {
      const all = window.confirm(`Apply to every charge from ${t.merchant}? Cancel changes just this one.`)
      await api('/transactions/category', { method: 'POST', body: { id: t.id, category: e.target.value, all_from_merchant: all } })
      setEditing(false); onChanged()
    } },
  Object.entries(categories).map(([k, l]) => h('option', { key: k, value: k }, l)))
}

function Tabs ({ value, onChange, items }) {
  return h('div', { role: 'tablist', style: { display: 'flex', gap: 4, borderBottom: `1px solid ${V.line}` } },
    items.map(([k, l]) => h('button', { key: k, role: 'tab', 'aria-selected': value === k, onClick: () => onChange(k),
      style: { border: 'none', background: 'transparent', color: value === k ? 'inherit' : V.muted, font: 'inherit', fontWeight: value === k ? 600 : 400,
        padding: '8px 12px', cursor: 'pointer', borderBottom: `2px solid ${value === k ? V.accent : 'transparent'}`, marginBottom: -1 } }, l)))
}

function FinanceApp () {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)
  const [filter, setFilter] = useState(null)
  const [tab, setTab] = useState('overview')

  const load = useCallback(() => api('/summary').then(d => { setData(d); setError(null) }).catch(e => setError(String(e && e.message || e))), [])
  useEffect(() => {
    load()
    const t = setInterval(load, 60000)
    const off = onRefreshEvent(load)
    return () => { clearInterval(t); off && off() }
  }, [load])

  const act = useCallback(async (what, body, quiet) => {
    setBusy(true); setNotice(null)
    try {
      const post = (path, b) => api(path, { method: 'POST', body: b || {} })
      if (what === 'connect') {
        const r = await post('/connect')
        if (r.url) openUrl(r.url)
        setNotice('Plaid opened in your browser. Sign in to your bank, then press Finish connecting.')
      } else if (what === 'finish') {
        const r = await post('/connect/finish'); setNotice(`Connected ${r.added.map(a => a.institution).join(', ')}.`)
      } else if (what === 'test-bank') {
        await post('/test-bank'); setNotice('Plaid test bank connected and synced.')
      } else if (what === 'demo') {
        await post('/demo', { action: 'load' }); setNotice('Sample data loaded. Budgets below are proposals waiting for approval.')
      } else if (what === 'clear-demo') {
        await post('/demo', { action: 'clear' })
      } else if (what === 'sync') {
        const r = await post('/sync'); setNotice(r.skipped ? `Sync skipped: ${r.skipped}.` : `Synced: ${r.added} new, ${r.modified} updated.`)
      } else if (what === 'propose') {
        await post('/budgets/propose')
      } else if (what === 'approve') {
        await post('/budgets/approve', body)
      } else if (what === 'budget-set') {
        await post('/budgets/set', body)
      } else if (what === 'dismiss') {
        await post('/alerts/dismiss', body)
      }
      if (!quiet) await load()
    } catch (e) {
      setNotice(`Error: ${e && e.message || e}`)
    } finally {
      setBusy(false)
    }
  }, [load])

  if (error && !data) return h('div', { style: S.page }, h(Card, { title: 'Finance' }, h('div', { style: S.muted }, `Could not load: ${error}. Is the pocketbook plugin enabled and the gateway running?`)))
  if (!data) return h('div', { style: { ...S.page, ...S.muted } }, 'Loading…')

  const o = data.overview
  const empty = !o.accounts
  const delta = o.spent_cents - o.spent_same_point_last_month_cents
  const b = data.budgets
  const left = b.total_budget_cents - b.total_spent_cents
  const showTxns = (ids, title) => { setFilter({ ids, title }); setTab('activity') }
  const post = async (path, body) => {
    setBusy(true)
    try { await api(path, { method: 'POST', body }); await load() } catch (e) { setNotice(`Error: ${e && e.message || e}`) } finally { setBusy(false) }
  }

  return h('div', { style: S.page },
    h('header', { style: { ...S.row, justifyContent: 'space-between', flexWrap: 'wrap' } },
      h('div', null,
        h('div', { style: { fontSize: 20, fontWeight: 600 } }, `${monthName(o.month)} finances`),
        h('div', { style: S.muted }, empty ? 'No accounts connected' : `${o.accounts} accounts · synced ${ago(o.last_sync)}`,
          o.demo && h('span', { style: { marginLeft: 8, padding: '1px 7px', borderRadius: 9, border: `1px solid ${V.line}` } }, 'Sample data'))),
      !empty && h('div', { style: S.row },
        o.demo && h(Button, { disabled: busy, onClick: () => act('clear-demo') }, 'Remove sample data'),
        h(Button, { disabled: busy, onClick: () => act('sync') }, busy ? 'Working…' : 'Sync now'))),
    notice && h('div', { style: { ...S.card, padding: '10px 14px' } }, notice),
    (empty || !data.setup.live_items) && !o.demo && h(Setup, { setup: data.setup, act, busy }),
    empty ? null : h(React.Fragment, null,
      h('div', { style: S.grid(180) },
        h(Stat, { label: 'Spent this month', value: money(o.spent_cents, true),
          sub: `${delta >= 0 ? '+' : '−'}${money(Math.abs(delta), true)} vs same point last month` }),
        h(Stat, { label: 'Budget left', value: b.total_budget_cents ? money(left, true) : '—',
          sub: b.total_budget_cents ? `of ${money(b.total_budget_cents, true)} · ${b.days_in_month - b.days_elapsed} days to go` : 'No budgets yet',
          tone: b.total_budget_cents && left < 0 ? V.red : null }),
        h(Stat, { label: 'Net worth', value: money(o.net_worth_cents, true),
          sub: o.net_worth_change_30d_cents != null ? `${signed(o.net_worth_change_30d_cents)} in 30 days` : `Cash ${money(o.cash_cents, true)}` }),
        h(Stat, { label: 'Investments', value: money(o.investments_cents, true),
          sub: data.investments.gain_cents != null ? `${signed(data.investments.gain_cents)} vs cost` : 'Brokerage and retirement' })),
      h(Tabs, { value: tab, onChange: setTab, items: [['overview', 'Overview'], ['wealth', 'Net worth'], ['goals', `Goals${data.goals.length ? ` (${data.goals.length})` : ''}`], ['activity', 'Transactions']] }),
      tab === 'overview' && h(React.Fragment, null,
        h(Alerts, { alerts: data.alerts, act, showTxns }),
        h('div', { style: S.grid(420) },
          h(Budgets, { budgets: b, act, busy }),
          h(Card, { title: 'This month vs last' },
            h(PaceChart, { pace: data.pace, budget: b.total_budget_cents }),
            h('div', { style: { ...S.row, ...S.muted, fontSize: 12, marginTop: 6 } },
              h('span', { style: { color: V.accent } }, '━'), ' This month', h('span', { style: { color: V.faint, marginLeft: 10 } }, '━'), ' Last month'))),
        h('div', { style: S.grid(420) },
          h(Card, { title: 'Last six months' }, h(MonthsChart, { months: data.months }),
            h('div', { style: { ...S.muted, fontSize: 12, marginTop: 6 } }, 'Bars show spending; green dots show income.')),
          h(Card, { title: 'Bills and subscriptions' },
            data.recurring.length ? data.recurring.map(r => h('div', { key: r.merchant, style: { ...S.row, padding: '6px 0', borderTop: `1px solid ${V.line}` } },
              h('span', { style: { flex: 1, fontWeight: 500 } }, r.merchant),
              h('span', { style: S.muted }, `${r.cadence} · next ${day(r.next_date)}`),
              h('span', { style: { minWidth: 70, textAlign: 'right', color: r.last_cents > r.previous_cents ? V.yellow : 'inherit' } }, money(r.last_cents))))
              : h('div', { style: S.muted }, 'None detected yet. Pocketbook needs three charges to spot a pattern.')))),
      tab === 'wealth' && h(React.Fragment, null,
        h(NetWorth, { nw: data.networth }),
        h(Investments, { inv: data.investments })),
      tab === 'goals' && h(Goals, { goals: data.goals, accounts: data.accounts, post, busy }),
      tab === 'activity' && h(React.Fragment, null,
        h(Transactions, { initial: data.recent, api, filter, clearFilter: () => setFilter(null), categories: data.categories, onChanged: load }),
        h(Rules, { rules: data.rules, categories: data.categories, post, busy }),
        h(Card, { title: 'Accounts' },
          data.accounts.map(a => h('div', { key: a.account_id, style: { ...S.row, padding: '6px 0', borderTop: `1px solid ${V.line}` } },
            h('span', { style: { flex: 1 } }, h('span', { style: { fontWeight: 500 } }, a.name), h('span', { style: S.muted }, a.mask ? ` ••${a.mask}` : '')),
            h('span', { style: S.muted }, `${a.institution || ''}${a.item_status && a.item_status !== 'ok' ? ' · needs reconnect' : ''}`),
            h('span', { style: { minWidth: 90, textAlign: 'right' } }, money(a.current_cents))))))))
}
