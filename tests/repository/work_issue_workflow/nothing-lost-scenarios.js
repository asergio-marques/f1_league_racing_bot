// Nothing the owner relies on is lost on the way. A tests-stage reviewer is given an entry in short
// only where the round's tester showed that it listed what changed since, and that no test's code
// nor any fixture, helper, value or file under it did. A re-check does not carry over, as still to be
// asked, a question the owner's decisions have answered. A later piece of the builder that returns
// nothing fails the stage, but what the earlier pieces fixed, disputed and unmarked is kept.
const { builder, review, testsCheck, finding, base, round, changes, suite } = require('./stubs')

const A = 'tests/x/test_a.py::test_a'
const Bt = 'tests/x/test_a.py::test_b'
const entryA = { nodeid: A, change: 'added', scenario: 'SCEN-A', expects: 'EXP-A', criterion: 'CRIT-A' }
const entryB = { nodeid: Bt, change: 'added', scenario: 'SCEN-B', expects: 'EXP-B', criterion: 'CRIT-B' }
const FIXTURE = ['tests/x/conftest.py', 'two_drivers']
const fixture = { file: FIXTURE[0], name: FIXTURE[1], change: 'added', what: 'seeds a division with two drivers', affects: [A] }
const ran = nodeid => ({ nodeid, failsWithRunxfail: true, realFailure: 'AssertionError', outcomeAsCommitted: 'xfailed' })
const everyEntryInFull = prompt => ['SCEN-A', 'EXP-A', 'SCEN-B', 'EXP-B'].every(m => prompt.includes(m))

// A tests stage of two rounds: round 1 adds A1 and A2 (and, with `support`, the fixture), and the
// issue reviewer finds issue-1-1; round 2 fixes it in c2 and keeps every entry word for word.
// `since` is what round 2's tester answers for the changes since c1. Both round-2 reviewers must be
// given every test entry in full.
const keptWordForWord = (since, support = []) => {
  const listed = changes([[A, 'added'], [Bt, 'added']], support.map(x => [x.file, x.name, x.change]))
  return {
    args: { ...base, stage: 'tests' },
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) return k === 1
        ? builder({ tests: [entryA, entryB], support })
        : builder({ commits: [{ sha: 'c2', subject: 'answered issue-1-1' }], tests: [entryA, entryB], support, fixed: [{ id: 'issue-1-1', commit: 'c2' }] })
      if (label.endsWith(':tester')) return testsCheck({ tests: [ran(A), ran(Bt)], changes: listed, ...(k === 2 ? since : {}) })
      if (/:(issue|product)$/.test(label)) {
        if (k === 2 && !everyEntryInFull(prompt)) throw new Error(`${label} is given a test entry in short in round 2`)
        if (label.endsWith(':issue')) return k === 1 ? review({ findings: [finding('issue-1-1')] }) : review({ prior: [{ id: 'issue-1-1', status: 'fixed', grounds: 'ok' }] })
        return review({ summary: 'S' })
      }
    },
    expect: r => r.status === 'passed' && r.lastRound === 2,
  }
}

// The check stage, re-checking an amended plan after the owner answered a question each checker
// raised.
const C = { stage: 'check', issue: '#999', plan: 'NEW PLAN', modules: ['steward'], commit: 'abc123', decisions: 'Q-ANSWERED: yes, as the plan says' }
const asked = { kind: 'business', question: 'Q-ANSWERED', context: 'ctx', options: [{ label: 'yes', meaning: 'x' }], recommendation: 'yes' }
const architecture = (questions = []) => ({ rulesTouched: [], breachesRemoved: [], breachesAdded: [], notYetBuilt: [], planChanges: [], questions, raised: [], notes: [] })
const design = (questions = []) => ({ modules: [], questions, raised: [], notes: [] })
const product = (questions = []) => ({ specRules: [], criteria: [], questions, citations: [], documentsOwed: [], raised: [], notes: [] })
// A sentence telling the checker that a question the owner's decisions answer is not carried over.
const answeredDropped = prompt => prompt.split(/(?<=\.)\s+|\n+/).some(s => /question/i.test(s) && /decisions/i.test(s)
  && /not (be )?carried over|not carr(y|ied) (it |them )?over|dropped|drop (it|them)/i.test(s))

const lanesClean = label => {
  if (label.endsWith(':issue')) return review()
  if (label.endsWith(':code')) return review()
  if (label.endsWith(':product')) return review({ summary: 'ACCEPTANCE' })
  if (label.endsWith(':tester')) return suite()
}

module.exports = {
  // The tester was asked what changed since the reviewers last saw the list, but its answer carries
  // no head and no error: it did not show that it ran the step, so nothing is known to be unchanged.
  changedSinceNotShownGivesAllInFull: keptWordForWord({ changedSince: changes([], [], [], ''), changedSinceError: '' }),
  // The fixture A1 uses is changed under an entry kept word for word: every test entry is given in
  // full, so that the issue reviewer can hold each scenario to what its setup now does.
  touchedFixtureGivesEveryTestInFull: keptWordForWord({ changedSince: changes([], [[...FIXTURE, 'modified']], [], 'h2'), changedSinceError: '' }, [fixture]),
  recheckDropsAnsweredQuestions: {
    args: {
      ...C,
      previous: {
        stage: 'check', issue: '999', commit: 'abc000', plan: 'OLD PLAN',
        architecture: architecture([{ ...asked, kind: 'engineering' }]), design: design([{ ...asked, kind: 'engineering' }]), product: product([asked]),
        questions: [asked], specRulesToSettle: [], citations: [], planChanges: [], failed: [],
      },
    },
    respond(label, prompt) {
      if (!prompt.includes('OLD PLAN')) throw new Error(`${label} is not given the plan it checked`)
      if (!answeredDropped(prompt)) throw new Error(`${label} is not told that a question the owner's decisions answer is not carried over`)
      return label === 'check:architecture' ? architecture() : label === 'check:design' ? design() : product()
    },
    expect: r => r.plan === 'NEW PLAN' && !r.failed.length,
  },
  // Round 2's first piece fixes code-1-1, disputes code-1-2 and removes the marker from test_a, and
  // hands off; the second piece returns nothing.
  laterPieceReturningNothingKeepsEarlierPieces: {
    args: { ...base, stage: 'build', maxRounds: 2 },
    respond(label) {
      if (label === 'build:r1:builder') return builder({ tests: [] })
      if (label === 'build:r2:builder') return builder({
        commits: [{ sha: 'c2', subject: 'fixed code-1-1' }], planComplete: false, remaining: ['the rest of the plan'],
        tests: [{ nodeid: A, change: 'markerRemoved' }],
        fixed: [{ id: 'code-1-1', commit: 'c2' }],
        disputed: [{ id: 'code-1-2', reason: 'DISPUTE REASON', evidence: [] }],
      })
      if (label === 'build:r2:p2:builder') return undefined
      if (label.endsWith(':builder')) throw new Error('unexpected builder ' + label)
      if (label === 'build:r1:code') return review({ findings: [finding('code-1-1'), finding('code-1-2')] })
      return lanesClean(label)
    },
    expect: r => {
      const byId = new Map(r.ledger.map(f => [f.id, f]))
      return r.status === 'failed' && /round 2\b/.test(r.failure) && /piece 2\b/.test(r.failure)
        && r.commits.map(c => c.sha).join() === 'c1,c2'
        && byId.get('code-1-1').status === 'fixed' && byId.get('code-1-1').fixedIn === 'c2'
        && byId.get('code-1-2').status === 'disputed' && byId.get('code-1-2').dispute === 'DISPUTE REASON'
        && r.tests.some(t => t.nodeid === A && t.change === 'markerRemoved')
    },
  },
}
