// The tests stage: failing tests written, run both ways, judged, and passed to the owner's gate.
const { q, builder, review, testsCheck, suite, finding, base, round, changes } = require('./stubs')
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

// The list the owner approves at Gate 2: held to the branch's diff entry for entry, each entry
// described, and written out in full as the report.
const A = 'tests/x/test_a.py::test_a'
const entry = (nodeid, change, o = {}) => ({ nodeid, change, scenario: `S-${nodeid.split('::').pop()}`, expects: `E-${nodeid.split('::').pop()}`, ...o })
const ran = (nodeid, o = {}) => ({ nodeid, failsWithRunxfail: true, realFailure: 'AssertionError', outcomeAsCommitted: 'xfailed', ...o })
const passedTests = (o = {}) => ({ stage: 'tests', status: 'passed', lastRound: 1, ledger: [], citations: [], commits: [], separateDefects: [], lastFailures: [], tests: [], support: [], ...o })
const cleanLanes = label => {
  if (label.endsWith(':issue')) return review()
  if (label.endsWith(':product')) return review({ summary: 'SUMMARY' })
}
Object.assign(module.exports, {
  listMissesADiffChange: {
    args: { ...base, stage: 'tests' },
    respond(label, prompt) {
      const k = round(label)
      const M = 'tests/x/test_a.py::test_old'
      if (label.endsWith(':builder')) {
        if (k === 2 && !prompt.includes(`${M} is modified on the branch, but tests[] does not list it`)) throw new Error('builder not told of the unlisted change')
        return builder({ tests: k === 1 ? [entry(A, 'added')] : [entry(A, 'added'), entry(M, 'modified', { before: 'B' })] })
      }
      if (label.endsWith(':tester')) return testsCheck({ tests: k === 1 ? [ran(A)] : [ran(A), ran(M)], changes: changes([[A, 'added'], [M, 'modified']]) })
      return cleanLanes(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 2 && !r.rounds[0].green,
  },
  listNamesAnUnchangedTest: {
    args: { ...base, stage: 'tests', maxRounds: 1 },
    respond(label) {
      const B = 'tests/x/test_a.py::test_b'
      if (label.endsWith(':builder')) return builder({ tests: [entry(A, 'added'), entry(B, 'added')] })
      if (label.endsWith(':tester')) return testsCheck({ tests: [ran(A), ran(B)] })
      return cleanLanes(label)
    },
    expect: r => r.status === 'unfinished' && r.lastFailures.some(x => x.includes('test_b is listed as added, but the branch does not change it')),
  },
  listedWithTheWrongChange: {
    args: { ...base, stage: 'tests', maxRounds: 1 },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [entry(A, 'modified', { before: 'B' })] })
      if (label.endsWith(':tester')) return testsCheck()
      return cleanLanes(label)
    },
    expect: r => r.status === 'unfinished' && r.lastFailures.some(x => x.includes('is listed as modified, but the branch has it added')),
  },
  entryWithoutItsDescription: {
    args: { ...base, stage: 'tests', maxRounds: 1 },
    respond(label) {
      const M = 'tests/x/test_a.py::test_m'
      const D = 'tests/x/test_a.py::test_d'
      if (label.endsWith(':builder')) return builder({ tests: [entry(A, 'added', { scenario: '' }), entry(M, 'modified'), entry(D, 'deleted')] })
      if (label.endsWith(':tester')) return testsCheck({ tests: [ran(A), ran(M)], changes: changes([[A, 'added'], [M, 'modified'], [D, 'deleted']]) })
      return cleanLanes(label)
    },
    expect: r => r.status === 'unfinished'
      && r.lastFailures.some(x => x.includes('test_a gives no scenario'))
      && r.lastFailures.some(x => x.includes('test_m gives no before'))
      && r.lastFailures.some(x => x.includes('test_d gives no why')),
  },
  supportUnlisted: {
    args: { ...base, stage: 'tests', maxRounds: 1 },
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck({ changes: changes([[A, 'added']], [['tests/x/test_a.py', 'seat', 'modified']]) })
      return cleanLanes(label)
    },
    expect: r => r.status === 'unfinished' && r.lastFailures.some(x => x.includes('tests/x/test_a.py::seat is modified on the branch, but support[] does not list it')),
  },
  supportWithoutWhatItDoes: {
    args: { ...base, stage: 'tests', maxRounds: 1 },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ support: [{ file: 'tests/x/test_a.py', name: 'seat', change: 'modified', what: '' }] })
      if (label.endsWith(':tester')) return testsCheck({ changes: changes([[A, 'added']], [['tests/x/test_a.py', 'seat', 'modified']]) })
      return cleanLanes(label)
    },
    expect: r => r.status === 'unfinished' && r.lastFailures.some(x => x.includes('seat does not say what it now does')),
  },
  toolFailed: {
    args: { ...base, stage: 'tests', maxRounds: 1 },
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck({ changes: changes(), changesError: 'tests/x/test_a.py cannot be parsed' })
      return cleanLanes(label)
    },
    expect: r => r.status === 'unfinished' && r.lastFailures.some(x => x.includes('could not list') && x.includes('cannot be parsed')),
  },
  deletedTestIsNotRun: {
    args: { ...base, stage: 'tests' },
    respond(label, prompt) {
      const D = 'tests/x/test_old.py::test_d'
      if (label.endsWith(':builder')) return builder({ tests: [entry(A, 'added'), entry(D, 'deleted', { why: 'W' })] })
      if (label.endsWith(':tester')) {
        if (prompt.includes('test_old.py::test_d')) throw new Error('the tester was asked to run a deleted test')
        return testsCheck({ changes: changes([[A, 'added'], [D, 'deleted']]) })
      }
      return cleanLanes(label)
    },
    expect: r => r.status === 'passed' && r.counts.deleted === 1,
  },
  parametrisedIdMatches: {
    args: { ...base, stage: 'tests' },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [entry(`${A}[one]`, 'added')] })
      if (label.endsWith(':tester')) return testsCheck({ tests: [ran(`${A}[one]`)] })
      return cleanLanes(label)
    },
    expect: r => r.status === 'passed',
  },
  rerunAfterABuildProposal: {
    args: { ...base, stage: 'tests', decisions: 'The build asked for test_b; the owner said make it', previous: passedTests({ tests: [entry(A, 'added', { label: 'A1' })] }) },
    respond(label) {
      const B = 'tests/x/test_a.py::test_b'
      if (label.endsWith(':builder')) return builder({ tests: [entry(A, 'added', { alreadyPasses: true }), entry(B, 'added', { alreadyPasses: true })] })
      if (label.endsWith(':tester')) return testsCheck({ tests: [ran(A, { failsWithRunxfail: false, outcomeAsCommitted: 'passed' }), ran(B, { failsWithRunxfail: false, outcomeAsCommitted: 'passed' })], changes: changes([[A, 'added'], [B, 'added']]) })
      return cleanLanes(label)
    },
    expect: r => r.status === 'passed' && r.report.includes('`test_b` *(new since the last Gate 2)*') && r.report.includes('*Passes already:*') && r.report.includes('**A1** `test_a`\n'),
  },
  reportListsEveryEntry: {
    args: { ...base, stage: 'tests', branch: 'fix/999-x' },
    respond(label, prompt) {
      const M = 'tests/x/test_b.py::test_m'
      const D = 'tests/x/test_b.py::test_d'
      const V = 'tests/y/test_c.py::test_v'
      if (label.endsWith(':builder')) return builder({
        tests: [entry(D, 'deleted', { why: 'WHY-D' }), entry(M, 'modified', { before: 'BEFORE-M' }), entry(A, 'added', { criterion: 'CRIT-1' }), entry(V, 'moved', { why: 'WHY-V' })],
        support: [{ file: 'tests/x/test_a.py', name: 'seat', change: 'added', what: 'WHAT-SEAT', affects: [A] }],
      })
      if (label.endsWith(':tester')) return testsCheck({ tests: [ran(A), ran(M), ran(V)], changes: changes([[A, 'added'], [M, 'modified'], [D, 'deleted'], [V, 'moved', 'tests/x/test_old.py::test_v']], [['tests/x/test_a.py', 'seat', 'added']]) })
      if (label.endsWith(':product') && !prompt.includes('"label": "A1"')) throw new Error('the product owner was not given the labels')
      return cleanLanes(label)
    },
    expect: r => {
      const x = r.report
      const order = ['1 added, 1 modified, 1 deleted, 1 moved; 1 supporting.', '## Added (1)', '**A1** `test_a`', 'S-test_a', 'E-test_a', 'CRIT-1', '## Modified (1)', '**M1** `test_m`', 'BEFORE-M', '## Deleted (1)', '**D1** `test_d`', '*Why it goes:* WHY-D', '## Moved (1)', '**MV1** `test_v`', '*From:* `tests/x/test_old.py::test_v`', '## Supporting changes (1)', '**S1** `tests/x/test_a.py::seat`, added. WHAT-SEAT. *Affects:* A1.', '## What a league will see', 'SUMMARY']
      const at = order.map(s => x.indexOf(s))
      return r.status === 'passed' && at.every((i, n) => i >= 0 && (n === 0 || i > at[n - 1])) && !x.includes('since the last Gate 2')
        && JSON.stringify(r.counts) === JSON.stringify({ added: 1, modified: 1, deleted: 1, moved: 1, support: 1 })
    },
  },
  reportPrintsEmptyGroups: {
    args: { ...base, stage: 'tests' },
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck()
      return cleanLanes(label)
    },
    expect: r => r.status === 'passed' && ['## Modified (0)', '## Deleted (0)', '## Moved (0)', '## Supporting changes (0)'].every(h => r.report.includes(`${h}\n\nNone.`)),
  },
  reportMarksOnlyWhatChangedSinceTheGate: {
    args: {
      ...base, stage: 'tests', decisions: 'GATE 2: reword test_b, add test_c, drop test_d',
      previous: passedTests({ tests: [entry(A, 'added'), entry('tests/x/test_a.py::test_b', 'added'), entry('tests/x/test_a.py::test_d', 'added')] }),
    },
    respond(label) {
      const B = 'tests/x/test_a.py::test_b'
      const C = 'tests/x/test_a.py::test_c'
      if (label.endsWith(':builder')) return builder({ tests: [entry(A, 'added'), entry(B, 'added', { scenario: 'REWORDED' }), entry(C, 'added')] })
      if (label.endsWith(':tester')) return testsCheck({ tests: [ran(A), ran(B), ran(C)], changes: changes([[A, 'added'], [B, 'added'], [C, 'added']]) })
      return cleanLanes(label)
    },
    expect: r => {
      const x = r.report
      return r.status === 'passed' && x.includes('`test_a`\n') && x.includes('`test_b` *(changed since the last Gate 2)*') && x.includes('`test_c` *(new since the last Gate 2)*')
        && x.includes('## In the last Gate 2, and no longer in the list\n\n- `tests/x/test_a.py::test_d`, added')
    },
  },
  shownSurvivesAQuestion: {
    args: {
      ...base, stage: 'tests', decisions: 'OWNER ANSWERED',
      previous: passedTests({ status: 'question', tests: [entry(A, 'added'), entry('tests/x/test_a.py::test_b', 'added')], shown: { tests: [entry(A, 'added')], support: [] } }),
    },
    respond(label) {
      const B = 'tests/x/test_a.py::test_b'
      if (label.endsWith(':builder')) return builder({ tests: [entry(A, 'added'), entry(B, 'added')] })
      if (label.endsWith(':tester')) return testsCheck({ tests: [ran(A), ran(B)], changes: changes([[A, 'added'], [B, 'added']]) })
      return cleanLanes(label)
    },
    expect: r => r.status === 'passed' && r.shown.tests.length === 1 && r.report.includes('`test_b` *(new since the last Gate 2)*'),
  },
  noSummaryIsFlaggedInTheReport: {
    args: { ...base, stage: 'tests' },
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck()
      return review()
    },
    expect: r => r.status === 'passed' && r.summary === '' && r.report.includes('The product owner wrote no summary'),
  },
})

// The review's second round: labels and descriptions kept from one gate to the next, ratchet lines
// left to the build, and the tool's run proved.
Object.assign(module.exports, {
  labelsKeptAcrossGates: {
    args: { ...base, stage: 'tests', decisions: 'GATE 2: add a test for the empty division', previous: passedTests({ tests: [entry('tests/y/test_c.py::test_b', 'added', { label: 'A1' })] }) },
    respond(label) {
      const B = 'tests/y/test_c.py::test_b'
      const N = 'tests/a/test_z.py::test_new'
      if (label.endsWith(':builder')) return builder({ tests: [entry(B, 'added'), entry(N, 'added')] })
      if (label.endsWith(':tester')) return testsCheck({ tests: [ran(B), ran(N)], changes: changes([[B, 'added'], [N, 'added']]) })
      return cleanLanes(label)
    },
    expect: r => r.status === 'passed' && r.report.includes('**A1** `test_b`\n') && r.report.includes('**A2** `test_new` *(new since the last Gate 2)*'),
  },
  builderGivenTheListToKeep: {
    args: { ...base, stage: 'tests', decisions: 'GATE 2: reword A1', previous: passedTests({ tests: [entry(A, 'added', { label: 'A1', scenario: 'THE-SCENARIO-SHOWN' })] }) },
    respond(label, prompt) {
      if (label.endsWith(':builder') && (!prompt.includes('The list as it stands') || !prompt.includes('THE-SCENARIO-SHOWN') || !prompt.includes('"label": "A1"') || !prompt.includes('word for word'))) throw new Error('builder not given the list to keep')
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck()
      return cleanLanes(label)
    },
    expect: r => r.status === 'passed',
  },
  firstBuilderGivenNoList: {
    args: { ...base, stage: 'tests' },
    respond(label, prompt) {
      if (label.endsWith(':builder') && round(label) === 1 && prompt.includes('The list as it stands')) throw new Error('a first builder given a list')
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck()
      return cleanLanes(label)
    },
    expect: r => r.status === 'passed',
  },
  affectsInAnotherOrderIsNoChange: {
    args: { ...base, stage: 'tests', decisions: 'D', previous: passedTests({ tests: [entry(A, 'added', { label: 'A1' })], support: [{ file: 'tests/x/test_a.py', name: 'seat', change: 'added', what: 'W', affects: [A, 'tests/x/test_a.py::test_b'], label: 'S1' }] }) },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [entry(A, 'added')], support: [{ file: 'tests/x/test_a.py', name: 'seat', change: 'added', what: 'W', affects: ['tests/x/test_a.py::test_b', A] }] })
      if (label.endsWith(':tester')) return testsCheck({ changes: changes([[A, 'added']], [['tests/x/test_a.py', 'seat', 'added']]) })
      return cleanLanes(label)
    },
    expect: r => r.status === 'passed' && !r.report.includes('since the last Gate 2'),
  },
  ratchetLinesAreLeftOutOfTheList: {
    args: { ...base, stage: 'tests' },
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck({ changes: changes([[A, 'added']], [['tests/repository/test_architecture_rules.py', 'KNOWN_DIRECT_POSTS', 'modified']]) })
      return cleanLanes(label)
    },
    expect: r => r.status === 'passed',
  },
  movedWithoutWhy: {
    args: { ...base, stage: 'tests', maxRounds: 1 },
    respond(label) {
      const V = 'tests/y/test_v.py::test_v'
      if (label.endsWith(':builder')) return builder({ tests: [entry(A, 'added'), entry(V, 'moved')] })
      if (label.endsWith(':tester')) return testsCheck({ tests: [ran(A), ran(V)], changes: changes([[A, 'added'], [V, 'moved', 'tests/x/test_v.py::test_v']]) })
      return cleanLanes(label)
    },
    expect: r => r.status === 'unfinished' && r.lastFailures.some(x => x.includes('test_v gives no why')),
  },
  testerDidNotRunTheTool: {
    args: { ...base, stage: 'tests', maxRounds: 1 },
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck({ changes: changes([[A, 'added']], [], [], '') })
      return cleanLanes(label)
    },
    expect: r => r.status === 'unfinished' && r.lastFailures.some(x => x.includes('did not show that it ran tools/changed_tests.py')),
  },
  onlyDeletionsSkipTheRuns: {
    args: { ...base, stage: 'tests' },
    respond(label, prompt) {
      const D = 'tests/x/test_a.py::test_d'
      if (label.endsWith(':tester') && !prompt.includes('there is no test to run: skip steps 3 and 4')) throw new Error('the tester was not told to skip the runs')
      if (label.endsWith(':builder')) return builder({ tests: [entry(D, 'deleted', { why: 'W' })] })
      if (label.endsWith(':tester')) return testsCheck({ tests: [], changes: changes([[D, 'deleted']]) })
      return cleanLanes(label)
    },
    expect: r => r.status === 'passed' && r.counts.deleted === 1,
  },
})
