// Later review rounds limited to the new work: a checker that has reviewed the branch before is told
// how far it reviewed, and reads the commits since, while earlier work the new commits make wrong
// stays in scope. A checker that returned nothing, or has never run, reviews in full. In the tests
// stage, each reviewer is given the whole list with its scenarios the first time, and afterwards
// only what is new or changed in full; the tester is given only what it runs.
const { builder, review, testsCheck, suite, finding, base, round, changes } = require('./stubs')
const B = { ...base, stage: 'build', criteria: 'CRIT', checks: 'CHECKS' }
const lanesClean = label => {
  if (label.endsWith(':issue')) return review()
  if (label.endsWith(':code')) return review()
  if (label.endsWith(':product')) return review({ summary: 'ACCEPTANCE' })
  if (label.endsWith(':tester')) return suite()
}
const upTo = sha => new RegExp(`reviewed the branch up to \`?${sha}\\b`)
// The note a checker is given where it has reviewed before and commits have been made since.
const incremental = (prompt, sha) => upTo(sha).test(prompt) && prompt.includes(`${sha}..HEAD`) && /still in scope/i.test(prompt)
const noNote = prompt => !/reviewed the branch up to/.test(prompt) && !/nothing has been committed since/i.test(prompt)

const A = 'tests/x/test_a.py::test_a'
const Bt = 'tests/x/test_a.py::test_b'
const entryA = { nodeid: A, change: 'added', scenario: 'SCEN-A', expects: 'EXP-A', criterion: 'CRIT-A' }
const entryB = { nodeid: Bt, change: 'added', scenario: 'SCEN-B', expects: 'EXP-B', criterion: 'CRIT-B' }
const ran = (nodeid, o = {}) => ({ nodeid, failsWithRunxfail: true, realFailure: 'AssertionError', outcomeAsCommitted: 'xfailed', ...o })
const bothRun = (o = {}) => testsCheck({ tests: [ran(A), ran(Bt)], changes: changes([[A, 'added'], [Bt, 'added']]), ...o })
// A later round's tester, which also lists what has changed under tests/ since the reviewers last
// saw the list: by default it ran, and found no test's code changed.
const bothRunSince = (since = changes([], [], [], 'h2'), error = '') => bothRun({ changedSince: since, changedSinceError: error })
// Whether the tester was asked to list what has changed since a commit, and its answer required to.
const askedSince = (prompt, opts, sha) => prompt.includes(`--base ${sha}`) && opts.schema.required.includes('changedSince')
const full = (prompt, ...marks) => marks.every(m => prompt.includes(m))
// A1 given compactly: its label and node id, but not its scenario or what it expects.
const compactA = prompt => prompt.includes('A1') && prompt.includes(A) && prompt.includes('CRIT-A') && !prompt.includes('SCEN-A') && !prompt.includes('EXP-A')
const compactB = prompt => prompt.includes('A2') && prompt.includes(Bt) && prompt.includes('CRIT-B') && !prompt.includes('SCEN-B') && !prompt.includes('EXP-B')
// A tests stage of two rounds: round 1 adds A1 and A2, and the issue reviewer finds issue-1-1;
// round 2 fixes it in c2 and keeps both entries word for word. `tester2` answers for round 2's
// tester, and `reviewed2` checks each round-2 reviewer's prompt.
const twoRoundsKeptWordForWord = (tester2, reviewed2) => ({
  args: { ...base, stage: 'tests' },
  respond(label, prompt, opts) {
    const k = round(label)
    if (label.endsWith(':builder')) return k === 1
      ? builder({ tests: [entryA, entryB] })
      : builder({ commits: [{ sha: 'c2', subject: 'rewrote test a' }], tests: [entryA, entryB], fixed: [{ id: 'issue-1-1', commit: 'c2' }] })
    if (label.endsWith(':tester')) {
      if (k === 1 && opts.schema.required.includes('changedSince')) throw new Error('round 1\'s tester is asked what has changed since a review nobody has made')
      if (k === 2 && !askedSince(prompt, opts, 'c1')) throw new Error('round 2\'s tester is not asked what has changed under tests/ since c1')
      return k === 1 ? bothRun() : tester2()
    }
    if (/:(issue|product)$/.test(label)) {
      if (k === 2 && !reviewed2(prompt)) throw new Error(`${label} is not given the list as it should be in round 2`)
      if (label.endsWith(':issue')) return k === 1 ? review({ findings: [finding('issue-1-1')] }) : review({ prior: [{ id: 'issue-1-1', status: 'fixed', grounds: 'ok' }] })
      return review({ summary: 'S' })
    }
  },
  expect: r => r.status === 'passed' && r.lastRound === 2,
})

module.exports = {
  laterRoundReviewsOnlyNewCommits: {
    args: B,
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) return k === 1 ? builder({ tests: [] }) : builder({ tests: [], commits: [{ sha: 'c2', subject: 'fixed code-1-1' }], fixed: [{ id: 'code-1-1', commit: 'c2' }] })
      if (/:(issue|code|product)$/.test(label)) {
        if (!(k === 1 ? noNote(prompt) : incremental(prompt, 'c1'))) throw new Error(`${label} is not told how far it reviewed`)
        if (label.endsWith(':code')) return k === 1 ? review({ findings: [finding('code-1-1')] }) : review({ prior: [{ id: 'code-1-1', status: 'fixed', grounds: 'ok' }] })
      }
      return lanesClean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 2,
  },
  deadLaneReviewsInFull: {
    args: B,
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) return k === 1 ? builder({ tests: [] }) : builder({ tests: [], commits: [{ sha: 'c2', subject: 'finished' }] })
      if (label === 'build:r1:product') return undefined
      if (label === 'build:r2:product' && !noNote(prompt)) throw new Error('a product owner that returned nothing is told it reviewed before')
      if (label === 'build:r2:issue' && !incremental(prompt, 'c1')) throw new Error('the issue reviewer is not told how far it reviewed')
      return lanesClean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 2 && r.rounds[0].dead.includes('product owner'),
  },
  noNewCommitsJudgesFindingsOnly: {
    args: { ...B, maxRounds: 2 },
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) return k === 1 ? builder({ tests: [] }) : builder({ tests: [], commits: [], blocked: true, planComplete: false, remaining: ['code-1-1'] })
      if (k === 2 && /:(issue|code|product)$/.test(label) && !/nothing has been committed since/i.test(prompt)) throw new Error(`${label} is not told that nothing has been committed since it reviewed`)
      if (label === 'build:r1:code') return review({ findings: [finding('code-1-1')] })
      return lanesClean(label)
    },
    expect: (r, { labels }) => r.lastRound === 2 && ['build:r2:issue', 'build:r2:code', 'build:r2:product'].every(l => labels.includes(l)),
  },
  reviewedAtCarriedAcrossRuns: {
    args: {
      ...B,
      decisions: 'ANSWERS',
      previous: {
        stage: 'build', status: 'question', lastRound: 2, ledger: [], citations: [], separateDefects: [], lastFailures: [], tests: [],
        commits: ['c1', 'c2', 'c3', 'c4', 'c5'].map(sha => ({ sha, subject: 's' })),
        reviewedAt: { issue: 5, code: 5, product: 5 },
      },
    },
    respond(label, prompt) {
      if (label.endsWith(':builder')) return builder({ tests: [], commits: [{ sha: 'c6', subject: 'answered' }] })
      if (label === 'build:r3:code' && !incremental(prompt, 'c5')) throw new Error('the code reviewer is not told it reviewed up to c5 in the last run')
      return lanesClean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 3 && !!r.reviewedAt && r.reviewedAt.code === 6 && r.reviewedAt.issue === 6 && r.reviewedAt.product === 6,
  },
  designVerifierIncremental: {
    args: B,
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) return k === 1 ? builder({ tests: [] }) : builder({ tests: [], commits: [{ sha: 'c2', subject: 'fixed design-1-1' }], fixed: [{ id: 'design-1-1', commit: 'c2' }] })
      if (label.endsWith(':issue')) return review({ designDocsChanged: ['docs/design/steward_module.md'] })
      if (label.endsWith(':design')) {
        if (k === 1 && !noNote(prompt)) throw new Error('the design verifier is told it reviewed before its first review')
        if (k === 2 && !incremental(prompt, 'c1')) throw new Error('the design verifier is not told how far it reviewed')
        return k === 1 ? review({ findings: [finding('design-1-1')] }) : review({ prior: [{ id: 'design-1-1', status: 'fixed', grounds: 'ok' }] })
      }
      return lanesClean(label)
    },
    expect: (r, { labels }) => r.status === 'passed' && r.lastRound === 2 && labels.includes('build:r2:design'),
  },
  summaryCoversWholeWork: {
    args: B,
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) return k === 1 ? builder({ tests: [] }) : builder({ tests: [], commits: [{ sha: 'c2', subject: 'fixed code-1-1' }], fixed: [{ id: 'code-1-1', commit: 'c2' }] })
      if (label.endsWith(':product') && !/the whole (of the )?work since `?b0\b/i.test(prompt)) throw new Error(`${label} is not told the acceptance summary covers the whole work since the base`)
      if (label.endsWith(':code')) return k === 1 ? review({ findings: [finding('code-1-1')] }) : review({ prior: [{ id: 'code-1-1', status: 'fixed', grounds: 'ok' }] })
      return lanesClean(label)
    },
    expect: (r, { labels }) => r.status === 'passed' && r.lastRound === 2 && labels.includes('build:r2:product'),
  },
  testsStageListTrimmedAfterFirstReview: {
    args: { ...base, stage: 'tests' },
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) return k === 1
        ? builder({ tests: [entryA, entryB] })
        : builder({ commits: [{ sha: 'c2', subject: 'reworded test b' }], tests: [entryA, { ...entryB, scenario: 'SCEN-B2' }], fixed: [{ id: 'issue-1-1', commit: 'c2' }] })
      if (label.endsWith(':tester')) return k === 1 ? bothRun() : bothRunSince()
      if (/:(issue|product)$/.test(label)) {
        const ok = k === 1
          ? ['SCEN-A', 'EXP-A', 'SCEN-B', 'EXP-B'].every(x => prompt.includes(x))
          : compactA(prompt) && prompt.includes('SCEN-B2') && prompt.includes('EXP-B') && prompt.includes('A2') && prompt.includes(Bt)
        if (!ok) throw new Error(`${label} is not given the list as it should be in round ${k}`)
        if (label.endsWith(':issue')) return k === 1 ? review({ findings: [finding('issue-1-1')] }) : review({ prior: [{ id: 'issue-1-1', status: 'fixed', grounds: 'ok' }] })
        return review({ summary: 'S' })
      }
    },
    expect: r => r.status === 'passed' && r.lastRound === 2 && !!r.listSeen,
  },
  listSeenCarriedAcrossRuns: {
    args: {
      ...base,
      stage: 'tests',
      decisions: 'ANSWERS',
      previous: {
        stage: 'tests', status: 'question', lastRound: 1, ledger: [], citations: [], separateDefects: [], lastFailures: [],
        commits: [{ sha: 'c1', subject: 'added the tests' }],
        tests: [{ ...entryA, label: 'A1' }, { ...entryB, label: 'A2' }],
        support: [],
        reviewedAt: { issue: 1, product: 1 },
        listSeen: {
          issue: { tests: [{ ...entryA, label: 'A1' }, { ...entryB, label: 'A2' }], support: [] },
          product: { tests: [{ ...entryA, label: 'A1' }, { ...entryB, label: 'A2' }], support: [] },
        },
      },
    },
    respond(label, prompt, opts) {
      if (label.endsWith(':builder')) return builder({ commits: [{ sha: 'c2', subject: 'answered' }], tests: [entryA, { ...entryB, scenario: 'SCEN-B2' }] })
      if (label.endsWith(':tester')) {
        if (!askedSince(prompt, opts, 'c1')) throw new Error('the tester is not asked what has changed under tests/ since the reviewers last saw the list, at c1')
        return bothRunSince()
      }
      if (/:(issue|product)$/.test(label)) {
        if (!compactA(prompt) || !prompt.includes('SCEN-B2')) throw new Error(`${label} is not given the unchanged entry compactly in the first round of the new run`)
        return label.endsWith(':product') ? review({ summary: 'S' }) : review()
      }
    },
    expect: r => r.status === 'passed' && r.lastRound === 2 && !!r.listSeen,
  },
  testsTesterGivenNodeIdsOnly: {
    args: { ...base, stage: 'tests' },
    respond(label, prompt) {
      if (label.endsWith(':builder')) return builder({ tests: [entryA, { ...entryB, alreadyPasses: true }] })
      if (label.endsWith(':tester')) {
        const scenarioText = ['SCEN-A', 'EXP-A', 'SCEN-B', 'EXP-B'].filter(x => prompt.includes(x))
        if (scenarioText.length) throw new Error(`the tester is given scenario or expectation text: ${scenarioText.join(', ')}`)
        if (!prompt.includes(A) || !prompt.includes(Bt) || !/alreadyPasses"?:\s*true/.test(prompt)) throw new Error('the tester is not given every node id, with which passes already')
        return testsCheck({ tests: [ran(A), ran(Bt, { failsWithRunxfail: false, realFailure: '', outcomeAsCommitted: 'passed' })], changes: changes([[A, 'added'], [Bt, 'added']]) })
      }
      if (label.endsWith(':issue')) return review()
      if (label.endsWith(':product')) return review({ summary: 'S' })
    },
    expect: r => r.status === 'passed' && r.lastRound === 1,
  },
  // The product owner leaves its summary out and is asked again: the second call is a fresh agent,
  // so it reviews the whole branch, and in the tests stage is given the whole list, however far the
  // product lane reviewed in the round.
  summaryReaskReviewsWholeBranch: {
    args: B,
    respond(label, prompt) {
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label === 'build:r1:product') return review()
      if (label === 'build:r1:summary') {
        if (!noNote(prompt)) throw new Error('the product owner asked again for the summary is told it has reviewed the branch already')
        return review({ summary: 'LATE SUMMARY' })
      }
      return lanesClean(label)
    },
    expect: (r, { labels }) => r.status === 'passed' && r.lastRound === 1 && r.summary === 'LATE SUMMARY' && labels.includes('build:r1:summary'),
  },
  summaryReaskReviewsWholeList: {
    args: { ...base, stage: 'tests' },
    respond(label, prompt) {
      if (label.endsWith(':builder')) return builder({ tests: [entryA, entryB] })
      if (label.endsWith(':tester')) return bothRun()
      if (label.endsWith(':issue')) return review()
      if (label === 'tests:r1:product') return review()
      if (label === 'tests:r1:summary') {
        if (!noNote(prompt)) throw new Error('the product owner asked again for the summary is told it has reviewed the branch already')
        if (!full(prompt, 'SCEN-A', 'EXP-A', 'SCEN-B', 'EXP-B')) throw new Error('the product owner asked again for the summary is not given the whole list')
        return review({ summary: 'LATE SUMMARY' })
      }
    },
    expect: (r, { labels }) => r.status === 'passed' && r.lastRound === 1 && r.summary === 'LATE SUMMARY' && labels.includes('tests:r1:summary'),
  },
  // A test whose code the commits since changed, under an entry kept word for word, is given in full,
  // so that the issue reviewer can hold the new code to its scenario. The short form does not ask the
  // reviewer to hold an entry to a description it is not given.
  testsStageCodeChangedEntryGivenInFull: twoRoundsKeptWordForWord(
    () => bothRunSince(changes([[A, 'modified']], [], [], 'h2')),
    prompt => full(prompt, 'SCEN-A', 'EXP-A') && compactB(prompt) && !/that description|when you last saw it/i.test(prompt),
  ),
  // Where the tester could not list what has changed since, every entry is given in full.
  testsStageChangedSinceErrorGivesAllInFull: twoRoundsKeptWordForWord(
    () => bothRunSince(changes([], [], [], ''), 'boom'),
    prompt => full(prompt, 'SCEN-A', 'EXP-A', 'SCEN-B', 'EXP-B'),
  ),
  // A run carried on after the owner answered questions holds the whole branch to the answers in its
  // first round, and only then limits its review to the commits since.
  resumedRunHoldsBranchToNewDecisions: {
    args: {
      ...B,
      decisions: 'ANSWERS',
      previous: {
        stage: 'build', status: 'question', lastRound: 1, ledger: [], citations: [], separateDefects: [], lastFailures: [], tests: [],
        commits: ['c1', 'c2', 'c3', 'c4', 'c5'].map(sha => ({ sha, subject: 's' })),
        reviewedAt: { issue: 5, code: 5, product: 5 },
      },
    },
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) return k === 2
        ? builder({ tests: [], commits: [{ sha: 'c6', subject: 'answered' }] })
        : builder({ tests: [], commits: [{ sha: 'c7', subject: 'fixed code-2-1' }], fixed: [{ id: 'code-2-1', commit: 'c7' }] })
      if (/:(issue|code|product)$/.test(label)) {
        const decided = /decisions[^.]*make wrong[^.]*wherever on the branch/i.test(prompt)
        if (k === 2 && !(incremental(prompt, 'c5') && decided)) throw new Error(`${label} is not told that earlier work the owner's decisions make wrong is in scope wherever it sits`)
        if (k === 3 && !(incremental(prompt, 'c6') && !/wherever on the branch/i.test(prompt))) throw new Error(`${label} is told again to hold the whole branch to the decisions`)
        if (label.endsWith(':code')) return k === 2 ? review({ findings: [finding('code-2-1')] }) : review({ prior: [{ id: 'code-2-1', status: 'fixed', grounds: 'ok' }] })
      }
      return lanesClean(label)
    },
    expect: (r, { labels }) => r.status === 'passed' && r.lastRound === 3 && labels.includes('build:r3:code'),
  },
}
