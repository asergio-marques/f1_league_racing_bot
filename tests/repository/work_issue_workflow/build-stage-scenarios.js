// The build stage: the builder, the four checkers and the design verifier, round by round.
const { q, builder, review, suite, finding, base, round } = require('./stubs')
const B = { ...base, stage: 'build', criteria: 'CRIT', checks: 'CHECKS' }
const lanesClean = (label, k) => {
  if (label.endsWith(':issue')) return review()
  if (label.endsWith(':code')) return review()
  if (label.endsWith(':product')) return review({ summary: 'ACCEPTANCE' })
  if (label.endsWith(':tester')) return suite()
}
module.exports = {
  passFirst: {
    args: B,
    respond(label, prompt) {
      if (label.endsWith(':builder')) { if (!prompt.includes('This is the build') || !prompt.includes('CRIT')) throw new Error('builder prompt'); return builder({ tests: [] }) }
      if (label.endsWith(':tester') && !prompt.includes('/p/.venv/bin/mypy')) throw new Error('tester lacks mypy path')
      if (label.endsWith(':code') && !prompt.includes('diff b0...HEAD')) throw new Error('code reviewer lacks the diff')
      return lanesClean(label)
    },
    expect: r => r.status === 'passed' && r.summary === 'ACCEPTANCE' && r.lastRound === 1,
  },
  designVerified: {
    args: B,
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) return k === 1 ? builder() : builder({ fixed: [{ id: 'design-1-1', commit: 'c2' }] })
      if (label.endsWith(':issue')) return review({ designDocsChanged: ['docs/design/steward_module.md'] })
      if (label.endsWith(':design')) { if (!prompt.includes('docs/design/steward_module.md') || !prompt.includes('Phase 9')) throw new Error('design prompt'); return k === 1 ? review({ findings: [finding('design-1-1')] }) : review({ prior: [{ id: 'design-1-1', status: 'fixed', grounds: 'ok' }] }) }
      return lanesClean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 2,
  },
  redThenGreen: {
    args: B,
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) { if (k === 2 && !prompt.includes('tests/a.py::t: boom')) throw new Error('failures not passed'); return builder() }
      if (label.endsWith(':tester')) return k === 1 ? suite({ exitCode: 1, failures: [{ test: 'tests/a.py::t', reason: 'boom' }], mypyClean: false, mypyErrors: ['src/x.py:1: error'] }) : suite()
      return lanesClean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 2,
  },
  markersLeft: {
    args: { ...B, maxRounds: 1 },
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return suite({ xfailMarkersLeft: 2 })
      return lanesClean(label)
    },
    expect: r => r.status === 'unfinished' && r.lastFailures.some(x => x.includes('2 expected-failure marker')),
  },
  blockedCited: {
    args: B,
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) {
        if (k === 1) return builder({ commits: [], blocked: true, planComplete: false, questions: [q('business', 'reply wording?')] })
        if (!prompt.includes('Done.')) throw new Error('citation not given to builder')
        return builder()
      }
      if (label.endsWith(':tester') && k === 1) throw new Error('tester ran on a quiet round')
      if (label.endsWith(':product') && k === 1) { if (!prompt.includes('reply wording?')) throw new Error('PO lacks q'); return review({ answers: [{ question: 'reply wording?', answer: 'the spec says "Done."', source: 'core § X' }] }) }
      return lanesClean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 2 && r.rounds[0].dead.length === 0,
  },
  codeRaisesBusiness: {
    args: B,
    respond(label) {
      if (label === 'triage:r1:product') return { answers: [], escalations: [{ ...q('business', 'should an empty division post nothing?'), ref: 'r1-1' }], findings: [] }
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':code')) return review({ raised: [q('business', 'should an empty division post nothing?')] })
      return lanesClean(label)
    },
    expect: r => r.status === 'question' && r.escalations.length === 1,
  },
  designPass: {
    args: { ...B, kind: 'design-pass' },
    respond(label, prompt) {
      if (label.endsWith(':product') && !prompt.includes('nothing a league sees has changed')) throw new Error('design-pass PO prompt')
      if (label.endsWith(':builder')) { if (!prompt.includes('design pass')) throw new Error('design-pass builder'); return builder() }
      return lanesClean(label)
    },
    expect: r => r.status === 'passed',
  },
}
