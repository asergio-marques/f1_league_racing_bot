// The build stage: the builder, the four checkers and the design verifier, round by round.
const { q, builder, review, suite, finding, base, round, changes } = require('./stubs')
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

// After Gate 2 the build changes no test but to remove the issue's markers: any other test change
// it needs is proposed to the owner, and one it makes anyway is sent back to be reverted.
const H = { ...B, testsHead: 't0' }
const X = 'tests/x/test_a.py::test_a'
const proposal = { nodeid: 'tests/x/test_a.py::test_cancel', change: 'added', scenario: 'a season is cancelled', expects: 'portraits go', needed: 'the cancel path is untested' }
const buildPrev = o => ({ stage: 'build', status: 'question', lastRound: 1, ledger: [], citations: [], commits: [], separateDefects: [], lastFailures: [], tests: [], support: [], designFiles: [], ...o })
Object.assign(module.exports, {
  builderToldTheRule: {
    args: H,
    respond(label, prompt) {
      if (label.endsWith(':builder') && (!prompt.includes('The owner approved the tests at t0') || !prompt.includes('propose it in testChanges[]'))) throw new Error('builder not told the rule')
      if (label.endsWith(':tester') && !prompt.includes('tools/changed_tests.py --repo /tmp/wt --base t0 --issue 999')) throw new Error('tester not told to list the changes since t0')
      return label.endsWith(':builder') ? builder({ tests: [] }) : lanesClean(label)
    },
    expect: r => r.status === 'passed' && r.testsHead === 't0' && r.testChanges.length === 0,
  },
  noTestsHeadNoRule: {
    args: B,
    respond(label, prompt) {
      if (label.endsWith(':builder') && prompt.includes('propose it in testChanges[]')) throw new Error('builder given a rule with no approved tests')
      if (label.endsWith(':tester') && prompt.includes('tools/changed_tests.py')) throw new Error('tester asked to list changes with no approved tests')
      if (label.endsWith(':tester')) return suite({ changes: changes([[X, 'modified']]) })
      return label.endsWith(':builder') ? builder({ tests: [] }) : lanesClean(label)
    },
    expect: r => r.status === 'passed',
  },
  proposalStopsTheRound: {
    args: H,
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [], testChanges: [proposal] })
      return lanesClean(label)
    },
    expect: r => r.status === 'question' && r.lastRound === 1 && r.escalations.length === 0 && r.testChanges.length === 1 && r.testChanges[0].needed === 'the cancel path is untested' && r.rounds[0].testChanges === 1,
  },
  proposalBesideAQuestion: {
    args: H,
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [], testChanges: [proposal] })
      if (label.endsWith(':product')) return review({ escalations: [q('business', 'which?')] })
      return lanesClean(label)
    },
    expect: r => r.status === 'question' && r.escalations.length === 1 && r.testChanges.length === 1,
  },
  resumedAfterAProposal: {
    args: { ...H, testsHead: 't1', decisions: 'MADE test_cancel', previous: buildPrev({ testChanges: [proposal] }) },
    respond(label, prompt) {
      if (label.endsWith(':builder') && (!prompt.includes('The test changes you proposed went to the owner') || !prompt.includes('by t1') || !prompt.includes('MADE test_cancel'))) throw new Error('resumed builder not told what became of its proposal')
      return label.endsWith(':builder') ? builder({ tests: [] }) : lanesClean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 2 && r.testChanges.length === 0,
  },
  testChangedAfterApprovalIsSentBack: {
    args: H,
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) {
        if (k === 2 && !prompt.includes(`${X} is modified since the tests the owner approved at t0: revert it`)) throw new Error('builder not told to revert')
        return builder({ tests: [] })
      }
      if (label.endsWith(':tester')) return k === 1 ? suite({ changes: changes([[X, 'modified']]) }) : suite()
      return lanesClean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 2 && !r.rounds[0].green,
  },
  supportChangedAfterApprovalIsSentBack: {
    args: { ...H, maxRounds: 1 },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return suite({ changes: changes([], [['tests/x/conftest.py', 'seat', 'modified'], ['tests/core/test_y.py', 'KNOWN_Z', 'modified'], ['tests/repository/test_architecture_rules.py', 'PASS', 'modified']]) })
      return lanesClean(label)
    },
    expect: r => r.status === 'unfinished' && ['tests/x/conftest.py::seat', 'tests/core/test_y.py::KNOWN_Z', 'tests/repository/test_architecture_rules.py::PASS'].every(k => r.lastFailures.some(x => x.startsWith(`${k} is modified since`))),
  },
  markersAndRatchetLinesAreTheBuildsToChange: {
    args: H,
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [{ nodeid: X, change: 'markerRemoved' }] })
      if (label.endsWith(':tester')) return suite({ changes: changes([], [['tests/repository/test_architecture_rules.py', 'KNOWN_DIRECT_POSTS', 'modified']], [X]) })
      return lanesClean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 1,
  },
  toolFailsInTheBuild: {
    args: { ...H, maxRounds: 1 },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return suite({ changesError: 'bad revision t0' })
      return lanesClean(label)
    },
    expect: r => r.status === 'unfinished' && r.lastFailures.some(x => x.includes('could not list') && x.includes('bad revision t0')),
  },
})

Object.assign(module.exports, {
  testsHeadCarriedFromTheLastRun: {
    args: { ...B, decisions: 'D', previous: buildPrev({ testsHead: 't0' }) },
    respond(label, prompt) {
      if (label.endsWith(':tester') && !prompt.includes('--base t0 --issue 999')) throw new Error('testsHead not carried from previous')
      return label.endsWith(':builder') ? builder({ tests: [] }) : lanesClean(label)
    },
    expect: r => r.status === 'passed' && r.testsHead === 't0',
  },
  citationsFromARerunTestsStageReachTheBuild: {
    args: { ...H, decisions: 'D', citations: [{ question: 'q1', answer: 'a1', source: 's1' }, { question: 'q2', answer: 'a2', source: 's2' }], previous: buildPrev({ citations: [{ question: 'q1', answer: 'a1', source: 's1' }] }) },
    respond(label, prompt) {
      if (label.endsWith(':builder') && !prompt.includes('a2')) throw new Error('the tests stage citation did not reach the builder')
      return label.endsWith(':builder') ? builder({ tests: [] }) : lanesClean(label)
    },
    expect: r => r.status === 'passed' && r.citations.length === 2,
  },
  buildTesterDidNotRunTheTool: {
    args: { ...H, maxRounds: 1 },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return suite({ changes: changes([], [], [], '') })
      return lanesClean(label)
    },
    expect: r => r.status === 'unfinished' && r.lastFailures.some(x => x.includes('did not show that it ran')),
  },
})
