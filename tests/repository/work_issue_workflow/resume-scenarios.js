// A stage run again: after the owner's ruling, after a change asked for at a gate, and on a branch
// that already carries the issue's work.
const { builder, review, testsCheck, base, round } = require('./stubs')
module.exports = { ownerRuled: {
  args: { ...base, stage: 'tests', decisions: 'Fix product-1-1', rulings: { 'product-1-1': 'fix' }, previous: { stage: 'tests', status: 'question', lastRound: 2, ledger: [{ id: 'product-1-1', lane: 'product', title: 'T', material: true, why: 'w', fix: 'f', evidence: [], status: 'upheld', asked: true, dispute: 'no' }], citations: [], commits: [], separateDefects: [], lastFailures: [], tests: [] } },
  respond(label, prompt) {
    if (label.endsWith(':builder')) { if (!prompt.includes('the owner has ruled on your dispute') || !prompt.includes('product-1-1')) throw new Error('builder not told of the ruling'); return builder({ fixed: [{ id: 'product-1-1', commit: 'c3' }] }) }
    if (label.endsWith(':tester')) return testsCheck()
    if (label.endsWith(':issue')) return review()
    if (label.endsWith(':product')) { if (!prompt.includes('says it is fixed in c3')) throw new Error('PO not asked to judge the fix'); return review({ summary: 'S', prior: [{ id: 'product-1-1', status: 'fixed', grounds: 'ok' }] }) }
  },
  expect: r => r.status === 'passed' && r.lastRound === 3,
} }
module.exports.gateChange = {
  args: { ...base, stage: 'tests', decisions: 'GATE 2: also pin the empty division', previous: { stage: 'tests', status: 'passed', lastRound: 1, ledger: [], citations: [], commits: [], separateDefects: [], lastFailures: [], tests: [] } },
  respond(label, prompt) {
    if (label.endsWith(':builder')) { if (!prompt.includes('asked for changes') || !prompt.includes('also pin the empty division')) throw new Error('builder not told of the gate change'); return builder() }
    if (label.endsWith(':tester')) return testsCheck()
    if (label.endsWith(':issue')) return review()
    if (label.endsWith(':product')) return review({ summary: 'S' })
  },
  expect: r => r.status === 'passed' && r.lastRound === 2,
}
module.exports.freshOnBuiltBranch = {
  args: { ...base, stage: 'build' },
  respond(label, prompt) {
    if (label.endsWith(':builder')) { if (!prompt.includes('the plan is an amendment to it')) throw new Error('fresh builder not told to look for earlier work'); return builder() }
    if (label.endsWith(':tester')) return { exitCode: 0, summary: 'ok', failures: [], mypyClean: true, mypyErrors: [], xfailMarkersLeft: 0, uncommitted: [], tmpFree: '1G', environmentProblem: '', log: 'l' }
    if (label.endsWith(':product')) return review({ summary: 'A' })
    return review()
  },
  expect: r => r.status === 'passed',
}
