// Fewer agents, fewer stops and less re-read text (#483): text the plan already holds is not sent a
// second time; commit points built by hand are named to the builder and the reviewers; the tester
// gives each failure in one line; an idle design verifier is left out; the product owner is asked
// for its summary up front; and the build adjusts a stub the plan's change breaks rather than stop.
const { builder, review, suite, testsCheck, base, changes } = require('./stubs')
const B = { ...base, stage: 'build', criteria: 'CRIT', checks: 'CHECKS' }
const lanesClean = label => {
  if (label.endsWith(':tester')) return suite()
  if (label.endsWith(':product')) return review({ summary: 'ACCEPTANCE' })
  return review()
}
const times = (text, part) => text.split(part).length - 1

module.exports = {
  // The plan carries its criteria, its checks and the owner's decisions word for word, as the
  // skill's plan does in its items 7 and 8: each agent is given them once, in the plan.
  planTextSentOnce: {
    args: { ...B, plan: 'PLAN\n7. CRITERIA-TEXT\n8. CHECKS-TEXT\nDecisions: DECISIONS-TEXT', criteria: 'CRITERIA-TEXT', checks: 'CHECKS-TEXT', decisions: 'DECISIONS-TEXT' },
    respond(label, prompt) {
      for (const part of ['CRITERIA-TEXT', 'CHECKS-TEXT', 'DECISIONS-TEXT']) if (times(prompt, part) > 1) throw new Error(`${label} was sent ${part} twice`)
      if (label.endsWith(':builder')) return builder({ tests: [] })
      return lanesClean(label)
    },
    expect: r => r.status === 'passed',
  },
  // Text the plan does not hold is still sent.
  textNotInPlanStillSent: {
    args: { ...B, plan: 'PLAN', criteria: 'CRITERIA-TEXT', checks: 'CHECKS-TEXT', decisions: 'DECISIONS-TEXT' },
    respond(label, prompt) {
      if (label.endsWith(':builder') && !['CRITERIA-TEXT', 'CHECKS-TEXT', 'DECISIONS-TEXT'].every(p => prompt.includes(p))) throw new Error('the builder lost text the plan does not hold')
      if (label.endsWith(':product') && !['CRITERIA-TEXT', 'DECISIONS-TEXT'].every(p => prompt.includes(p))) throw new Error('the product owner lost text the plan does not hold')
      if (label.endsWith(':builder')) return builder({ tests: [] })
      return lanesClean(label)
    },
    expect: r => r.status === 'passed',
  },
}

// A plan split between the two paths: the commit points built by hand are named to the builder as
// not its own, and to each reviewer as not missing.
module.exports.handBuiltNamedToBuilderAndReviewers = {
  args: { ...B, handBuilt: ['commit point 5: the refusal sites in season_cog'] },
  respond(label, prompt) {
    const named = prompt.includes('commit point 5: the refusal sites in season_cog')
    if (label.endsWith(':builder') && !(named && prompt.includes('not yours to build'))) throw new Error('the builder was not told what is built by hand')
    if (/:(issue|code|product)$/.test(label) && !(named && prompt.includes('none is missing work'))) throw new Error(`${label} was not told what is built by hand`)
    if (label.endsWith(':builder')) return builder({ tests: [] })
    return lanesClean(label)
  },
  expect: r => r.status === 'passed' && r.handBuilt.join() === 'commit point 5: the refusal sites in season_cog',
}
module.exports.handBuiltRefusedUnlessList = {
  args: { ...B, handBuilt: 'commit point 5' },
  respond(label) { throw new Error(`${label} ran with a handBuilt that should have been refused`) },
  expectThrow: 'handBuilt is a list',
}
module.exports.handBuiltRefusedWithEmptyName = {
  args: { ...B, handBuilt: ['commit point 5', ' '] },
  respond(label) { throw new Error(`${label} ran with a handBuilt that should have been refused`) },
  expectThrow: 'handBuilt is a list',
}

// The tester gives each test's real failure as one line, never the traceback: the reviewers judge
// a test's failing by that line, and every word the tester copies is read again by both.
module.exports.testerGivesOneLineFailure = {
  args: { ...base, stage: 'tests' },
  respond(label, prompt, opts) {
    if (label.endsWith(':tester')) {
      const failure = opts.schema.properties.tests.items.properties.realFailure.description
      if (!prompt.includes('at most 200 characters, never the traceback') || !failure.includes('one line')) throw new Error('the tester was not asked for one line')
      return testsCheck()
    }
    if (label.endsWith(':builder')) return builder()
    if (label.endsWith(':product')) return review({ summary: 'S' })
    return review()
  },
  expect: r => r.status === 'passed',
}

// The design verifier is left out of a round in which no design file has changed since its last
// review and it has nothing open; it runs again as soon as one changes (#483).
const designRounds = touchedInRound2 => ({
  args: { ...B, maxRounds: 3 },
  respond(label, prompt) {
    const k = Number(label.match(/:r(\d+):/)[1])
    if (label.endsWith(':builder')) return builder({ tests: [], commits: [{ sha: `c${k}`, subject: `round ${k}` }] })
    if (label.endsWith(':issue')) {
      if (!prompt.includes('designDocsChangedSince')) throw new Error('the issue reviewer was not asked what changed since')
      return k === 1 ? review({ designDocsChanged: ['docs/design/steward_module.md'], designDocsChangedSince: ['docs/design/steward_module.md'], findings: [{ id: 'issue-1-1', title: 't', material: true, why: 'w', fix: 'f', evidence: [] }] })
        : review({ designDocsChanged: ['docs/design/steward_module.md'], designDocsChangedSince: k === 2 && touchedInRound2 ? ['docs/design/steward_module.md'] : [], prior: [{ id: 'issue-1-1', status: 'fixed', grounds: 'ok' }] })
    }
    return lanesClean(label)
  },
})
module.exports.idleDesignVerifierSkipped = {
  ...designRounds(false),
  expect: (r, { labels }) => r.status === 'passed' && labels.includes('build:r1:design') && !labels.includes('build:r2:design'),
}
module.exports.designVerifierRunsWhenDesignFileTouchedAgain = {
  ...designRounds(true),
  expect: (r, { labels }) => r.status === 'passed' && labels.includes('build:r1:design') && labels.includes('build:r2:design'),
}

// The product owner is asked for its summary in every round it finds nothing in, whatever the other
// checkers find, in the tests stage and the build alike.
const upFront = stage => ({
  args: stage === 'tests' ? { ...base, stage } : B,
  respond(label, prompt) {
    if (label.endsWith(':product') && !prompt.includes('whatever you expect the other checkers to find')) throw new Error('the summary was not asked for up front')
    if (label.endsWith(':builder')) return stage === 'tests' ? builder() : builder({ tests: [] })
    if (label.endsWith(':tester')) return stage === 'tests' ? testsCheck() : suite()
    return lanesClean(label)
  },
  expect: r => r.status === 'passed',
})
module.exports.summaryDemandedUpFrontInTests = upFront('tests')
module.exports.summaryDemandedUpFrontInBuild = upFront('build')

// After the owner approved the tests, the build may adjust a stub, fixture or exact-call assertion
// the plan's own change breaks, listing it for the code reviewer to judge and the owner to see at
// acceptance; any other test change is still sent back (#483).
const STUB = 'tests/x/conftest.py::season_stub'
const stubRun = (listed, o = {}) => ({
  args: { ...B, testsHead: 't0', maxRounds: 1 },
  respond(label, prompt) {
    if (label.endsWith(':builder')) {
      if (!prompt.includes('adjusted[]') || !prompt.includes('but four things')) throw new Error('the builder was not told it may adjust a broken stub')
      return builder({ tests: [], adjusted: listed ? [{ target: STUB, why: 'the plan adds season_number to Season' }] : [] })
    }
    if (label.endsWith(':tester')) return suite({ changes: changes([], [['tests/x/conftest.py', 'season_stub', 'modified']], [], 'h1') })
    if (label.endsWith(':code') && listed && !(prompt.includes(STUB) && prompt.includes('asserts less about behaviour'))) throw new Error('the code reviewer was not given the adjustment to judge')
    return lanesClean(label)
  },
  ...o,
})
module.exports.buildAdjustsBrokenStub = stubRun(true, {
  expect: r => r.status === 'passed' && r.adjusted.length === 1 && r.adjusted[0].target === STUB,
})
module.exports.unlistedTestChangeStillFlagged = stubRun(false, {
  expect: r => r.status !== 'passed' && r.lastFailures.some(f => f.includes(`${STUB} is modified since the tests the owner approved`)),
})

// Nor is the design verifier left out of the first round of a run carried on after the owner's
// answers, whatever the issue reviewer says changed.
module.exports.designVerifierRunsInFirstRoundOfCarriedRun = {
  args: async runOnce => {
    const first = await runOnce({ ...B, maxRounds: 1 }, label => {
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':issue')) return review({ designDocsChanged: ['docs/design/steward_module.md'], designDocsChangedSince: ['docs/design/steward_module.md'] })
      if (label.endsWith(':product')) return review({ escalations: [{ kind: 'business', question: 'which season?', context: 'c', options: [{ label: 'a', meaning: 'x' }], recommendation: 'a', stops: true }] })
      return lanesClean(label)
    })
    if (first.status !== 'question') throw new Error('the first run did not stop for the owner')
    return { ...B, maxRounds: 1, decisions: 'OWNER: the current season', previous: first }
  },
  respond(label) {
    if (label.endsWith(':builder')) return builder({ tests: [], commits: [{ sha: 'c2', subject: 'x' }] })
    if (label.endsWith(':issue')) return review({ designDocsChanged: ['docs/design/steward_module.md'], designDocsChangedSince: [] })
    return lanesClean(label)
  },
  expect: (r, { labels }) => labels.includes('build:r2:design'),
}

// A test the build deletes or adds is never an adjustment, whatever the builder lists.
module.exports.deletedTestListedAsAdjustedStillFlagged = {
  args: { ...B, testsHead: 't0', maxRounds: 1 },
  respond(label) {
    if (label.endsWith(':builder')) return builder({ tests: [], adjusted: [{ target: 'tests/x/test_a.py::test_old', why: 'the plan broke it' }] })
    if (label.endsWith(':tester')) return suite({ changes: changes([['tests/x/test_a.py::test_old', 'deleted']], [], [], 'h1') })
    return lanesClean(label)
  },
  expect: r => r.status !== 'passed' && r.lastFailures.some(f => f.includes('tests/x/test_a.py::test_old is deleted since the tests the owner approved')),
}

// An adjustment made after one Gate 2 lets nothing through after a later one: the build lists the
// test afresh, or it is flagged.
module.exports.adjustmentFromEarlierGateLetsNothingThrough = {
  args: async runOnce => {
    const first = await runOnce({ ...B, testsHead: 't0', maxRounds: 1 }, label => {
      if (label.endsWith(':builder')) return builder({ tests: [], adjusted: [{ target: STUB, why: 'the plan adds season_number' }], testChanges: [{ nodeid: 'tests/x/test_a.py::test_new', change: 'added', scenario: 's', expects: 'e', needed: 'n' }] })
      if (label.endsWith(':tester')) return suite({ changes: changes([], [['tests/x/conftest.py', 'season_stub', 'modified']], [], 'h1') })
      return lanesClean(label)
    })
    if (first.status !== 'question' || first.adjusted[0].since !== 't0') throw new Error('the first run did not stop on its test change with the adjustment recorded')
    return { ...B, testsHead: 't1', maxRounds: 1, decisions: 'OWNER: make test_new', previous: first }
  },
  respond(label) {
    if (label.endsWith(':builder')) return builder({ tests: [] })
    if (label.endsWith(':tester')) return suite({ changes: changes([], [['tests/x/conftest.py', 'season_stub', 'modified']], [], 'h2') })
    return lanesClean(label)
  },
  expect: r => r.status !== 'passed' && r.lastFailures.some(f => f.includes(`${STUB} is modified since the tests the owner approved at t1`)) && r.adjusted.length === 1,
}

// A design verifier left out stays out while nothing changes, round after round; and one is never
// left out in a round where a design file first appears, whatever the issue reviewer says changed.
module.exports.idleDesignVerifierStaysOut = {
  args: { ...B, maxRounds: 3 },
  respond(label) {
    const k = Number(label.match(/:r(\d+):/)[1])
    if (label.endsWith(':builder')) return builder({ tests: [], commits: [{ sha: `c${k}`, subject: `round ${k}` }] })
    if (label.endsWith(':issue')) {
      const docs = { designDocsChanged: ['docs/design/steward_module.md'], designDocsChangedSince: k === 1 ? ['docs/design/steward_module.md'] : [] }
      if (k === 1) return review({ ...docs, findings: [{ id: 'issue-1-1', title: 't', material: true, why: 'w', fix: 'f', evidence: [] }] })
      return review({ ...docs, prior: [{ id: 'issue-1-1', status: k === 3 ? 'fixed' : 'not-fixed', grounds: 'g' }] })
    }
    return lanesClean(label)
  },
  expect: (r, { labels }) => r.status === 'passed' && r.lastRound === 3 && labels.includes('build:r1:design') && !labels.includes('build:r2:design') && !labels.includes('build:r3:design'),
}
module.exports.designVerifierRunsWhenFileFirstAppears = {
  args: { ...B, maxRounds: 2 },
  respond(label) {
    const k = Number(label.match(/:r(\d+):/)[1])
    if (label.endsWith(':builder')) return builder({ tests: [], commits: [{ sha: `c${k}`, subject: `round ${k}` }] })
    if (label.endsWith(':issue')) {
      if (k === 1) return review({ designDocsChanged: ['docs/design/steward_module.md'], designDocsChangedSince: ['docs/design/steward_module.md'], findings: [{ id: 'issue-1-1', title: 't', material: true, why: 'w', fix: 'f', evidence: [] }] })
      return review({ designDocsChanged: ['docs/design/steward_module.md', 'docs/design/results_module.md'], designDocsChangedSince: [], prior: [{ id: 'issue-1-1', status: 'fixed', grounds: 'g' }] })
    }
    return lanesClean(label)
  },
  expect: (r, { labels }) => labels.includes('build:r2:design'),
}

// The count of markers left finds a reason given through a constant as well as one written out: a
// search for reason="#N: alone let a build pass with a test still marked (#483).
module.exports.markerCountCatchesReasonConstants = {
  args: B,
  respond(label, prompt) {
    const grep = `grep -n -E '(^|[^"])"#999:' -- tests/`
    if (label.endsWith(':tester') && !prompt.includes(grep)) throw new Error('the tester counts markers by their literal reason alone')
    if (label.endsWith(':builder') && !(prompt.includes(grep) && prompt.includes('a reason constant with the last marker that uses it'))) throw new Error('the builder is not told how markers are found')
    if (label.endsWith(':builder')) return builder({ tests: [] })
    return lanesClean(label)
  },
  expect: r => r.status === 'passed',
}
