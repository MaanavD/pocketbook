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
  vm.runInContext(fs.readFileSync('ui/app.js', 'utf8') + '\nglobalThis.components = { FinanceApp, Setup, LineChart }', context)
  return { components: context.components, render: (fn, props) => { cursor = 0; return fn(props) } }
}

function nodes (tree) {
  if (!tree || typeof tree !== 'object') return []
  return [tree, ...(tree.children || []).flatMap(nodes)]
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
