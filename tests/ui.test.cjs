const assert = require('node:assert/strict')
const test = require('node:test')
const fs = require('node:fs')
const vm = require('node:vm')

function renderHarness (initial = [], api = () => {}) {
  let cursor = 0
  const state = [...initial]
  const React = {
    createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }),
    useState: value => {
      const index = cursor++
      if (!(index in state)) state[index] = value
      return [state[index], next => { state[index] = next }]
    },
    useEffect: () => {},
    useCallback: fn => fn,
    useMemo: fn => fn()
  }
  const context = vm.createContext({ React, api, openUrl: () => {}, onRefreshEvent: () => {} })
  vm.runInContext(fs.readFileSync('ui/app.js', 'utf8') + '\nglobalThis.components = { FinanceApp, Setup, LineChart, BankManager, AccountControls, errorText, linkNotice }', context)
  return { components: context.components, render: (fn, props) => { cursor = 0; return fn(props) } }
}

function nodes (tree) {
  if (!tree || typeof tree !== 'object' || !tree.type) return []
  return [tree, ...[...Object.values(tree.props || {}), ...(tree.children || [])].filter(v => v && typeof v === 'object').flatMap(v => Array.isArray(v) ? v.flatMap(nodes) : nodes(v))]
}

function text (tree) {
  if (tree == null || tree === false) return ''
  if (typeof tree !== 'object') return String(tree)
  return (tree.children || []).map(text).join(' ')
}

const data = {
  overview: { accounts: 1, demo: false },
  setup: { live_items: true, plaid_configured: true, plaid_env: 'sandbox' },
  goals: [], budgets: { proposed: [] }, recent: [], purchases: { repeats: [] }
}

test('connected banks still expose the add-bank controls', () => {
  const h = renderHarness([data, null, false, null, null, 'overview'])
  const page = h.render(h.components.FinanceApp)
  const manage = nodes(page).find(n => text(n) === 'Manage banks' && typeof n.props.onClick === 'function')
  assert.ok(manage, 'Manage banks must remain available after connecting a bank')
  manage.props.onClick()
  const setup = nodes(h.render(h.components.FinanceApp)).find(n => n.type === h.components.Setup)
  assert.ok(setup, 'Manage banks opens the existing connection flow')
  const buttons = nodes(h.render(h.components.Setup, setup.props))
  assert.ok(buttons.find(n => text(n) === 'Connect a bank' && n.props.disabled === false))
  assert.ok(buttons.find(n => text(n) === 'Add Plaid test bank' && n.props.disabled === false))
})

test('negative spending series stay inside the chart plot', () => {
  const h = renderHarness()
  const chart = h.render(h.components.LineChart, {
    n: 4, series: [{ values: [-70000, 54000, 90000, 89900], area: true },
      { values: [0, -80000, -160000, -1000], dashed: true }], xTicks: [], tip: () => null
  })
  const paths = nodes(chart).filter(n => n.type === 'path')
  assert.ok(paths.length)
  for (const path of paths) {
    for (const match of path.props.d.matchAll(/[ML](-?[\d.]+),(-?[\d.]+)/g)) {
      const y = Number(match[2])
      assert.ok(y >= 9.9 && y <= 194.1, `path y=${y} must stay inside the plot`)
    }
  }
})


test('refresh all reports partial failures instead of claiming success', async () => {
  const calls = []
  const h = renderHarness([data, null, false, null, null, 'overview'], async (path, opts) => {
    calls.push(path)
    if (path === '/summary') return data
    return { items: 1, added: 0, modified: 0, removed: 0,
      errors: ['Test bank: balance unavailable'], notes: ['Holdings unavailable'], environment: 'sandbox' }
  })
  const page = h.render(h.components.FinanceApp)
  const refresh = nodes(page).find(n => text(n) === 'Refresh all' && typeof n.props.onClick === 'function')
  assert.ok(refresh, 'Refresh all must be available for connected banks')
  await refresh.props.onClick()
  const output = text(h.render(h.components.FinanceApp))
  assert.ok(calls.includes('/sync'))
  assert.match(output, /balance unavailable/)
  assert.match(output, /Holdings unavailable/)
  assert.match(output, /sandbox|simulated/i)
})


test('bank switches persist reversible preferences and separate environments', async () => {
  const calls = []
  const harness = renderHarness()
  const tree = harness.render(harness.components.BankManager, { environment: 'production', busy: false,
    connections: [
      { item_id: 'real', institution: 'Real bank', environment: 'production', enabled: true, environment_match: true, accounts: [] },
      { item_id: 'test', institution: 'Test bank', environment: 'sandbox', enabled: true, environment_match: false, accounts: [] }
    ], post: async (...args) => calls.push(args) })
  const switches = nodes(tree).filter(n => n.props.role === 'switch')
  assert.equal(switches.length, 2)
  assert.equal(switches[0].props.checked, true)
  await switches[0].props.onChange({ target: { checked: false } })
  assert.equal(calls[0][0], '/connections')
  assert.equal(calls[0][1].item_id, 'real')
  assert.equal(calls[0][1].enabled, false)
  assert.equal(switches[1].props.checked, false)
  assert.equal(switches[1].props.disabled, true)
})

test('all banks off still leaves account management reachable', () => {
  const harness = renderHarness([{ ...data, overview: { accounts: 0 }, connections: [{ item_id: 'real' }] }, null, false, null, null, 'overview'])
  const tree = harness.render(harness.components.FinanceApp)
  assert.ok(nodes(tree).some(n => text(n) === 'Manage banks' && n.props.onClick))
  assert.ok(nodes(tree).some(n => n.props.role === 'tab' && text(n) === 'Accounts'))
})

test('Electron wrapped API failures show only the useful message', () => {
  const harness = renderHarness()
  const message = 'Error invoking remote method: Error: 400: {"detail":"LINK_PENDING: Finish signing in, then try again."}'
  assert.equal(harness.components.errorText(new Error(message)), 'Finish signing in, then try again.')
})

test('pending bank connection is a waiting state, not a failed action', async () => {
  const harness = renderHarness([{ ...data, setup: { ...data.setup, linking: true } }, null, false, null, null, 'accounts'],
    async path => path === '/connect/finish' ? { status: 'pending', message: 'Waiting for your bank sign-in.' } : data)
  const page = harness.render(harness.components.FinanceApp)
  const setup = nodes(page).find(n => n.type === harness.components.Setup)
  await setup.props.act('finish')
  const notice = nodes(harness.render(harness.components.FinanceApp)).find(n => n.props.className === 'pb-notice')
  assert.ok(notice)
  assert.match(text(notice), /Waiting for your bank sign-in/)
})


test('connection success keeps failed banks and refresh errors visible', () => {
  const harness = renderHarness()
  const [message, failed] = harness.components.linkNotice({ added: [{ institution: 'Bank A' }],
    warnings: ['Bank B did not finish'], sync: { errors: ['Balances unavailable'], notes: ['Holdings unavailable'] } })
  assert.equal(failed, true)
  assert.match(message, /Bank A/)
  assert.match(message, /Bank B did not finish/)
  assert.match(message, /Balances unavailable/)
  assert.match(message, /Holdings unavailable/)
})


test('recalculate uses saved data and exposes updated budget recommendations', async () => {
  const calls = []
  const harness = renderHarness([data, null, false, null, null, 'overview'], async path => {
    calls.push(path)
    if (path === '/summary') return data
    if (path === '/recalculate') return { accounts: 2, budget_recommendations: 3, note: 'Approved amounts stay unchanged.' }
    throw Error('Unexpected API call')
  })
  const page = harness.render(harness.components.FinanceApp)
  const recalculate = nodes(page).find(n => text(n) === 'Recalculate' && n.props.onClick)
  assert.ok(recalculate)
  await recalculate.props.onClick()
  const output = text(harness.render(harness.components.FinanceApp))
  assert.deepEqual(calls, ['/recalculate', '/summary'])
  assert.match(output, /3 budget recommendations/)
  assert.match(output, /Approved amounts stay unchanged/)
})
