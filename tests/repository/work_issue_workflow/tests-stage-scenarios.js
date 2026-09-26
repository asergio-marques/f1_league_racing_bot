// The tests stage: failing tests written, run both ways, judged, and passed to the owner's gate.
const { q, builder, review, testsCheck, suite, finding, base, round } = require('./stubs')
const scenarios = {
  passFirst: {
    args: { ...base, stage: 'tests' },
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck()
      if (label.endsWith(':issue')) return review()
      if (label.endsWith(':product')) return review({ summary: 'TESTS SUMMARY' })
    },
    expect: r => r.status === 'passed' && r.summary === 'TESTS SUMMARY' && r.lastRound === 1,
  },
  findingThenFixed: {
    args: { ...base, stage: 'tests' },
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) {
        if (k === 2 && !prompt.includes('issue-1-1')) throw new Error('round 2 builder not told of the finding')
        return k === 1 ? builder() : builder({ fixed: [{ id: 'issue-1-1', commit: 'c2' }] })
      }
      if (label.endsWith(':tester')) return testsCheck()
      if (label.endsWith(':issue')) return k === 1 ? review({ findings: [finding('issue-1-1'), finding('issue-1-2', false)] }) : review({ prior: [{ id: 'issue-1-1', status: 'fixed', grounds: 'ok' }] })
      if (label.endsWith(':product')) return review({ summary: 'S' })
    },
    expect: r => r.status === 'passed' && r.lastRound === 2 && r.minor.length === 1 && r.openMaterial.length === 0,
  },
  businessEscalated: {
    args: { ...base, stage: 'tests' },
    respond(label, prompt) {
      if (label.endsWith(':builder')) return builder({ questions: [q('business', 'what should a league see?')], blocked: false })
      if (label.endsWith(':tester')) return testsCheck()
      if (label.endsWith(':issue')) { if (prompt.includes('what should a league see?')) throw new Error('business question went to the issue reviewer'); return review() }
      if (label.endsWith(':product')) { if (!prompt.includes('what should a league see?')) throw new Error('PO not given the question'); return review({ escalations: [q('business', 'what should a league see?')] }) }
    },
    expect: r => r.status === 'question' && r.escalations.length === 1 && r.lastRound === 1,
  },
  rerunAfterQuestion: {
    args: { ...base, stage: 'tests', decisions: 'OWNER SAID YES', previous: { stage: 'tests', status: 'question', lastRound: 1, ledger: [], citations: [], commits: [{ sha: 'c1', subject: 's' }], separateDefects: [], lastFailures: [], tests: [] } },
    respond(label, prompt) {
      if (label.endsWith(':builder')) { if (round(label) !== 2 || !prompt.includes('OWNER SAID YES') || !prompt.includes('bind you')) throw new Error('re-run builder wrong: ' + label); return builder() }
      if (label.endsWith(':tester')) return testsCheck()
      if (label.endsWith(':issue')) return review()
      if (label.endsWith(':product')) return review({ summary: 'S' })
    },
    expect: r => r.status === 'passed' && r.lastRound === 2 && r.commits.length === 2,
  },
  disputeUpheld: {
    args: { ...base, stage: 'tests' },
    respond(label) {
      const k = round(label)
      if (label.endsWith(':builder')) return k === 1 ? builder() : builder({ disputed: [{ id: 'product-1-1', reason: 'spec says otherwise', evidence: [] }] })
      if (label.endsWith(':tester')) return testsCheck()
      if (label.endsWith(':issue')) return review()
      if (label.endsWith(':product')) return k === 1 ? review({ findings: [finding('product-1-1')] }) : review({ prior: [{ id: 'product-1-1', status: 'dispute-upheld', grounds: 'no it does not' }] })
    },
    expect: r => r.status === 'question' && r.escalations.length === 1 && r.escalations[0].kind === 'business' && r.lastRound === 2,
  },
  environment: {
    args: { ...base, stage: 'tests' },
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck({ environmentProblem: '/tmp is full' })
      if (label.endsWith(':issue') || label.endsWith(':product')) return review()
    },
    expect: r => r.status === 'failed' && r.failure.includes('/tmp is full'),
  },
  unfinished: {
    args: { ...base, stage: 'tests' },
    respond(label) {
      const k = round(label)
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck({ tests: [{ nodeid: 'tests/x/test_a.py::test_a', failsWithRunxfail: false, realFailure: '', outcomeAsCommitted: 'passed' }] })
      if (label.endsWith(':issue') || label.endsWith(':product')) return review()
    },
    expect: r => r.status === 'unfinished' && r.lastRound === 2 && r.lastFailures.some(x => x.includes('passes already')),
  },
  raisedTriaged: {
    args: { ...base, stage: 'tests' },
    respond(label, prompt) {
      const k = round(label)
      if (label === 'triage:r1:product') return { answers: [{ question: 'is Z wanted?', answer: 'the spec says Z', source: 'results § Z', ref: 'r1-1' }], escalations: [], findings: [{ id: 'product-r1-t1', title: 'do Z', material: true, why: 'w', fix: 'f', evidence: [] }] }
      if (label.endsWith(':builder')) { if (k === 2 && (!prompt.includes('the spec says Z') || !prompt.includes('product-r1-t1'))) throw new Error('citation or finding not passed to builder'); return k === 1 ? builder() : builder({ fixed: [{ id: 'product-r1-t1', commit: 'c2' }] }) }
      if (label.endsWith(':tester')) return testsCheck()
      if (label.endsWith(':issue')) return k === 1 ? review({ raised: [q('business', 'is Z wanted?')] }) : review()
      if (label.endsWith(':product')) return k === 1 ? review({ summary: 'S' }) : review({ summary: 'S', prior: [{ id: 'product-r1-t1', status: 'fixed', grounds: 'ok' }] })
    },
    expect: r => r.status === 'passed' && r.lastRound === 2 && r.citations.length === 1 && r.ledger.find(f => f.id === 'product-r1-t1').status === 'closed',
  },
  deadLane: {
    args: { ...base, stage: 'tests' },
    respond(label) {
      const k = round(label)
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck()
      if (label.endsWith(':issue')) return k === 1 ? undefined : review()
      if (label.endsWith(':product')) return review({ summary: 'S' })
    },
    expect: r => r.status === 'passed' && r.lastRound === 2 && r.rounds[0].dead.join() === 'issue reviewer',
  },
}
module.exports = scenarios
