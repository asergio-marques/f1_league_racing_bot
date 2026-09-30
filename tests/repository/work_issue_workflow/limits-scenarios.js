// How many rounds a stage may take: a budget counted across all its runs, and a stop where the loop
// is not converging.
const { builder, review, suite, testsCheck, finding, base, round } = require('./stubs')
const BUILD = { ...base, stage: 'build' }
const earlier = (lastRound, o = {}) => ({ stage: 'build', status: 'question', lastRound, ...o, ledger: [], citations: [], commits: [{ sha: 'c1', subject: 'x' }], separateDefects: [], lastFailures: [], tests: [] })

module.exports = {
  // A build run again after three rounds has one left, and stops as capped when it does not pass.
  budgetSpentAcrossRuns: {
    args: { ...BUILD, previous: earlier(3) },
    respond(label) {
      const k = round(label)
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return suite()
      if (label.endsWith(':code')) return review({ findings: [finding(`code-${k}-1`)] })
      return review()
    },
    expect: (r, { labels }) => r.status === 'capped' && r.lastRound === 4 && !labels.some(l => l.startsWith('build:r5')) && r.failure.includes('4'),
  },
  // maxRounds lowers what a run takes, but never raises the stage's budget.
  maxRoundsCannotRaiseBudget: {
    args: { ...base, stage: 'tests', maxRounds: 9 },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ clean: false })
      if (label.endsWith(':tester')) return testsCheck()
      return review()
    },
    expect: (r, { labels }) => r.status === 'capped' && r.lastRound === 3 && !labels.some(l => l.startsWith('tests:r4')),
  },
  // The owner can raise a stage's budget.
  roundBudgetRaises: {
    args: { ...BUILD, previous: earlier(4), roundBudget: 5 },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return suite()
      if (label.endsWith(':product')) return review({ summary: 'S' })
      return review()
    },
    expect: r => r.status === 'passed' && r.lastRound === 5,
  },
  roundBudgetMustBeWhole: {
    args: { ...BUILD, roundBudget: 'lots' },
    respond() { throw new Error('no agent should run') },
    expectThrow: 'roundBudget',
  },
  // Two rounds running that each open two material findings and close one stall the stage.
  notConvergingStalls: {
    args: BUILD,
    respond(label) {
      const k = round(label)
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return suite()
      if (label.endsWith(':code')) return review({
        findings: [finding(`code-${k}-1`), finding(`code-${k}-2`)],
        prior: k > 1 ? [{ id: `code-${k - 1}-1`, status: 'fixed', grounds: 'ok' }] : [],
      })
      return review()
    },
    expect: (r, { labels }) => r.status === 'stalled' && r.lastRound === 3 && r.failure.includes('opened as many material findings as they closed') && !labels.some(l => l.startsWith('build:r4')),
  },
  // The same failing test two rounds running stalls the stage, however the tester words the reason.
  sameRedTwiceStalls: {
    args: BUILD,
    respond(label) {
      const k = round(label)
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return suite({ exitCode: 1, summary: '1 failed', failures: [{ test: 'tests/a.py::t', reason: k === 1 ? 'boom' : 'bang' }] })
      return review()
    },
    expect: r => r.status === 'stalled' && r.lastRound === 2 && r.failure.includes('tests/a.py::t'),
  },
  // A round that closes more than it opens is converging, and the stage carries on to pass.
  convergingCarriesOn: {
    args: BUILD,
    respond(label) {
      const k = round(label)
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return suite()
      if (label.endsWith(':code')) {
        if (k === 1) return review({ findings: [finding('code-1-1'), finding('code-1-2')] })
        if (k === 2) return review({ findings: [finding('code-2-1')], prior: [{ id: 'code-1-1', status: 'fixed', grounds: 'ok' }, { id: 'code-1-2', status: 'fixed', grounds: 'ok' }] })
        return review({ prior: [{ id: 'code-2-1', status: 'fixed', grounds: 'ok' }] })
      }
      if (label.endsWith(':product')) return review({ summary: 'S' })
      return review()
    },
    expect: r => r.status === 'passed' && r.lastRound === 3,
  },
  // A rerun the owner starts gets a round even with the budget spent, so that their answer is acted on.
  ownerRerunGetsARound: {
    args: { ...BUILD, decisions: 'GATE 3: reword the reply', previous: earlier(4, { status: 'passed' }) },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return suite()
      if (label.endsWith(':product')) return review({ summary: 'S' })
      return review()
    },
    expect: r => r.status === 'passed' && r.lastRound === 5,
  },
  // Where that one round cannot pass, the stage stops as capped again after it.
  ownerRerunThatCannotPassIsCapped: {
    args: { ...BUILD, previous: earlier(4) },
    respond(label) {
      const k = round(label)
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return suite()
      if (label.endsWith(':code')) return review({ findings: [finding(`code-${k}-1`)] })
      return review()
    },
    expect: (r, { labels }) => r.status === 'capped' && r.lastRound === 5 && !labels.some(l => l.startsWith('build:r6')),
  },
  // A budget the owner raised carries to the stage's later runs.
  raisedBudgetCarries: {
    args: { ...BUILD, previous: earlier(4, { status: 'unfinished', roundBudget: 6 }) },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [], clean: false })
      if (label.endsWith(':tester')) return suite()
      return review()
    },
    expect: (r, { labels }) => r.status === 'capped' && r.lastRound === 6 && r.roundBudget === 6 && !labels.some(l => l.startsWith('build:r7')),
  },
  // Two different type errors in a row are progress, not the same failure.
  differentMypyErrorsDoNotStall: {
    args: BUILD,
    respond(label) {
      const k = round(label)
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return k === 3 ? suite() : suite({ mypyClean: false, mypyErrors: [k === 1 ? 'a.py:1: error X' : 'b.py:9: error Y'] })
      if (label.endsWith(':product')) return review({ summary: 'S' })
      return review()
    },
    expect: r => r.status === 'passed' && r.lastRound === 3,
  },
  // Rounds that each close more material findings than they open are converging, however many open.
  closingMoreThanOpeningConverges: {
    args: BUILD,
    respond(label) {
      const k = round(label)
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return suite()
      if (label.endsWith(':code')) {
        const fixed = ids => ids.map(id => ({ id, status: 'fixed', grounds: 'ok' }))
        if (k === 1) return review({ findings: [finding('code-1-1'), finding('code-1-2'), finding('code-1-3')] })
        if (k === 2) return review({ findings: [finding('code-2-1')], prior: fixed(['code-1-1', 'code-1-2']) })
        if (k === 3) return review({ findings: [finding('code-3-1')], prior: fixed(['code-1-3', 'code-2-1']) })
        return review({ prior: fixed(['code-3-1']) })
      }
      if (label.endsWith(':product')) return review({ summary: 'S' })
      return review()
    },
    expect: r => r.status === 'passed' && r.lastRound === 4,
  },
}

// A stage run again after it capped gets a round where the owner has decided something since, as
// when they approve a test mid-build; with the same decisions it stays capped, and only a raised
// budget gives it one (#483).
const cappedTests = decisions => async runOnce => {
  const capped = await runOnce({ ...base, stage: 'tests', decisions: 'FIRST', maxRounds: 3 }, label => {
    if (label.endsWith(':builder')) return builder()
    if (label.endsWith(':tester')) return testsCheck({ otherFailures: [`red in round ${round(label)}`] })
    return review()
  })
  if (capped.status !== 'capped') throw new Error(`the first run was ${capped.status}, not capped`)
  return { ...base, stage: 'tests', decisions, previous: capped }
}
Object.assign(module.exports, {
  cappedStageRunsAgainAfterNewDecisions: {
    args: cappedTests('FIRST. OWNER: add test_c'),
    respond(label, prompt) {
      if (label.endsWith(':builder') && !prompt.includes('the owner has decided something since')) throw new Error('the builder was not told why the stage runs again')
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck()
      if (label.endsWith(':product')) return review({ summary: 'S' })
      return review()
    },
    expect: (r, { labels }) => r.status === 'passed' && r.lastRound === 4 && labels.includes('tests:r4:builder'),
  },
  cappedStageStaysCappedWithoutNewDecisions: {
    args: cappedTests('FIRST'),
    respond(label) { throw new Error(`${label} ran for a capped stage nobody gave a round`) },
    expect: (r, { labels }) => r.status === 'capped' && !labels.length,
  },
})

// Only whether two rounds failed alike is read of the last failure, so a result carries it as a hash;
// a stage resumed from that result still stalls on the same failure (#483).
Object.assign(module.exports, {
  lastRedCarriedAsHashStillStalls: {
    args: async runOnce => {
      const first = await runOnce({ ...base, stage: 'tests', maxRounds: 1 }, label => {
        if (label.endsWith(':builder')) return builder()
        if (label.endsWith(':tester')) return testsCheck({ otherFailures: ['THE SAME LONG FAILURE '.repeat(40)] })
        return review()
      })
      if (!/^[0-9a-f]{8}$/.test(first.lastRed)) throw new Error(`lastRed is carried in full: ${first.lastRed.slice(0, 40)}`)
      return { ...base, stage: 'tests', previous: first }
    },
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck({ otherFailures: ['THE SAME LONG FAILURE '.repeat(40)] })
      return review()
    },
    expect: r => r.status === 'stalled' && r.lastRound === 2,
  },
})
