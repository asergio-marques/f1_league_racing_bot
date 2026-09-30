// A builder handed off in pieces within a round: a piece that commits part of the plan and is not
// finished is followed by another, told what the first did and what is left, and the round is
// reviewed once, after the last piece. A piece's question the checkers settle does not end the
// hand-off (#483); one that needs the owner does, as does a piece that commits nothing, so that
// nothing the owner must see is held back. A proposed test change does not: the owner is given every
// piece's proposals together, at the round's end.
const { q, builder, review, testsCheck, suite, finding, base, round, changes } = require('./stubs')
const B = { ...base, stage: 'build', criteria: 'CRIT', checks: 'CHECKS' }
// The piece an agent's label names: `build:r1:p2:builder` is piece 2, and `build:r1:builder` piece 1.
const piece = label => Number((label.match(/:p(\d+):/) || [])[1] || 1)
const pieces = (labels, k) => labels.filter(l => new RegExp(`^[a-z]+:r${k}:(p\\d+:)?builder$`).test(l))
const lanesClean = label => {
  if (label.endsWith(':issue')) return review()
  if (label.endsWith(':code')) return review()
  if (label.endsWith(':product')) return review({ summary: 'ACCEPTANCE' })
  if (label.endsWith(':tester')) return suite()
}
const unfinished = (o = {}) => builder({ tests: [], planComplete: false, remaining: ['commit point 4'], ...o })
const A = 'tests/x/test_a.py::test_a'
const Bt = 'tests/x/test_a.py::test_b'
const entry = (nodeid, o = {}) => ({ nodeid, change: 'added', scenario: 'SCEN ' + nodeid, expects: 'EXP ' + nodeid, ...o })
const ran = nodeid => ({ nodeid, failsWithRunxfail: true, realFailure: 'AssertionError', outcomeAsCommitted: 'xfailed' })

module.exports = {
  builderHandedOffInPieces: {
    args: B,
    respond(label, prompt) {
      if (label === 'build:r1:builder') return unfinished({ commits: [{ sha: 'c1', subject: 'did commit points 1 to 3' }] })
      if (label === 'build:r1:p2:builder') {
        if (!prompt.includes('c1') || !prompt.includes('commit point 4')) throw new Error('the second piece is not told what the first committed and what is left')
        return builder({ tests: [], commits: [{ sha: 'c2', subject: 'did commit point 4' }] })
      }
      if (label.endsWith(':builder')) throw new Error('unexpected builder ' + label)
      return lanesClean(label)
    },
    expect: (r, { labels }) => {
      const reviews = labels.filter(l => /^build:r1:(issue|code|product|tester)$/.test(l))
      return r.status === 'passed' && r.lastRound === 1
        && pieces(labels, 1).join() === 'build:r1:builder,build:r1:p2:builder'
        && reviews.length === 4 && reviews.every(l => labels.indexOf(l) > labels.indexOf('build:r1:p2:builder'))
        && r.commits.map(c => c.sha).join() === 'c1,c2'
    },
  },
  // A piece's question the checker cannot settle, one that needs the owner, ends the hand-off: the
  // round is reviewed as it stands, and the question reaches the owner once, not triaged again.
  handOffStopsOnUnsettledQuestion: {
    args: B,
    respond(label) {
      if (label.endsWith(':builder')) { if (label !== 'build:r1:builder') throw new Error('a piece followed one whose question needs the owner: ' + label); return unfinished({ questions: [q('business', 'what should the reply say?')] }) }
      if (label === 'triage:b1p1:product') return { answers: [], escalations: [{ ...q('business', 'what should the reply say, to a league?'), ref: 'b1-1', stops: true }], findings: [] }
      if (label.startsWith('triage:')) throw new Error('triaged again: ' + label)
      if (label === 'build:r1:product') return review({ summary: 'ACCEPTANCE' })
      return lanesClean(label)
    },
    expect: (r, { labels }) => r.status === 'question' && r.lastRound === 1 && pieces(labels, 1).length === 1
      && labels.includes('build:r1:product') && r.escalations.length === 1 && r.escalations[0].question === 'what should the reply say, to a league?',
  },
  // A piece's question the checker answers from a written rule does not end the hand-off: the next
  // piece is given the answer, the round's review is not asked it again, and the owner sees it at
  // the gate among the calls taken.
  pieceQuestionSettledCarriesOn: {
    args: B,
    respond(label, prompt) {
      if (label === 'build:r1:builder') return unfinished({ commits: [{ sha: 'c1', subject: 'did commit points 1 to 3' }], questions: [q('business', 'does an empty division post nothing?')] })
      if (label === 'triage:b1p1:product') {
        if (!prompt.includes('does an empty division post nothing?') || !prompt.includes('part-way through a round')) throw new Error('triage prompt')
        return { answers: [{ question: 'does an empty division post nothing?', answer: 'it posts nothing', source: 'results § 4.2', ref: 'b1-1' }], escalations: [], findings: [] }
      }
      if (label === 'build:r1:p2:builder') {
        if (!prompt.includes('it posts nothing') || !prompt.includes('settled the questions it asked')) throw new Error('the next piece was not given the answer')
        return builder({ tests: [], commits: [{ sha: 'c2', subject: 'did commit point 4' }] })
      }
      if (label === 'build:r1:product' && prompt.includes('Business questions from the builder')) throw new Error('the review was asked a settled question again')
      if (label.startsWith('triage:')) throw new Error('unexpected ' + label)
      return lanesClean(label)
    },
    expect: (r, { labels }) => r.status === 'passed' && r.lastRound === 1 && pieces(labels, 1).length === 2
      && r.provisional.some(p => p.question === 'does an empty division post nothing?' && p.recommendation.includes('results § 4.2'))
      && r.provisionalNew.some(p => p.question === 'does an empty division post nothing?'),
  },
  // A reversible call the checker takes on its recommendation does not end the hand-off either.
  pieceQuestionReversibleCarriesOn: {
    args: B,
    respond(label) {
      if (label === 'build:r1:builder') return unfinished({ commits: [{ sha: 'c1', subject: 'part' }], questions: [q('engineering', 'name the helper?')] })
      if (label === 'triage:b1p1:issue') return { answers: [], escalations: [{ ...q('engineering', 'name the helper?'), ref: 'b1-1', stops: false }], findings: [] }
      if (label === 'build:r1:p2:builder') return builder({ tests: [], commits: [{ sha: 'c2', subject: 'rest' }] })
      return lanesClean(label)
    },
    expect: (r, { labels }) => r.status === 'passed' && pieces(labels, 1).length === 2 && r.provisionalNew.some(p => p.question === 'name the helper?'),
  },
  // Two questions, one answered and one needing the owner: split, so the hand-off stops, the
  // answered one is listed among the calls taken, and only the other reaches the owner.
  pieceQuestionsSplitStop: {
    args: B,
    respond(label) {
      if (label.endsWith(':builder')) { if (label !== 'build:r1:builder') throw new Error('a piece followed a split: ' + label); return unfinished({ questions: [q('business', 'biz?'), q('engineering', 'eng?')] }) }
      if (label === 'triage:b1p1:product') return { answers: [{ question: 'biz?', answer: 'yes', source: 'core § 1', ref: 'b1-1' }], escalations: [], findings: [] }
      if (label === 'triage:b1p1:issue') return { answers: [], escalations: [{ ...q('engineering', 'eng?'), ref: 'b1-2', stops: true }], findings: [] }
      if (label.startsWith('triage:')) throw new Error('triaged again: ' + label)
      return lanesClean(label)
    },
    expect: (r, { labels }) => r.status === 'question' && pieces(labels, 1).length === 1
      && r.escalations.length === 1 && r.escalations[0].question === 'eng?' && r.provisional.some(p => p.question === 'biz?'),
  },
  // A triage that returns nothing settles nothing: the question goes to the owner as asked.
  pieceQuestionTriageDeadStops: {
    args: B,
    respond(label) {
      if (label.endsWith(':builder')) { if (label !== 'build:r1:builder') throw new Error('a piece followed a dead triage: ' + label); return unfinished({ questions: [q('business', 'what should the reply say?')] }) }
      if (label.startsWith('triage:')) return undefined
      return lanesClean(label)
    },
    expect: (r, { labels }) => r.status === 'question' && pieces(labels, 1).length === 1
      && r.escalations.some(e => e.question === 'what should the reply say?' && e.unframed),
  },
  // A proposed test change does not end the hand-off: the next piece builds on, and the owner is
  // given every piece's proposals together when the round is reviewed.
  handOffCarriesOnPastTestChange: {
    args: { ...B, testsHead: 't0' },
    respond(label) {
      if (label === 'build:r1:builder') return unfinished({ testChanges: [{ nodeid: 'tests/x/test_a.py::test_empty_division', change: 'added', scenario: 's', expects: 'e', needed: 'n' }] })
      if (label === 'build:r1:p2:builder') return builder({ tests: [], commits: [{ sha: 'c2', subject: 'rest' }], testChanges: [{ nodeid: 'tests/x/test_a.py::test_full_division', change: 'added', scenario: 's', expects: 'e', needed: 'n' }] })
      if (label.endsWith(':builder')) throw new Error('unexpected ' + label)
      return lanesClean(label)
    },
    expect: (r, { labels }) => r.status === 'question' && pieces(labels, 1).length === 2 && labels.includes('build:r1:code')
      && labels.indexOf('build:r1:code') > labels.indexOf('build:r1:p2:builder')
      && r.testChanges.map(t => t.nodeid).join() === 'tests/x/test_a.py::test_empty_division,tests/x/test_a.py::test_full_division',
  },
  // A later piece is told what the pieces before it proposed, and a proposal two pieces make reaches
  // the owner once.
  proposedTestChangeNamedToNextPieceAndAskedOnce: {
    args: { ...B, testsHead: 't0' },
    respond(label, prompt) {
      const change = { nodeid: 'tests/x/test_a.py::test_empty_division', change: 'added', scenario: 's', expects: 'e', needed: 'n' }
      if (label === 'build:r1:builder') return unfinished({ testChanges: [change] })
      if (label === 'build:r1:p2:builder') {
        if (!prompt.includes('proposed already this round') || !prompt.includes(change.nodeid)) throw new Error('the next piece was not told what was proposed')
        return builder({ tests: [], commits: [{ sha: 'c2', subject: 'rest' }], testChanges: [change] })
      }
      return lanesClean(label)
    },
    expect: r => r.status === 'question' && r.testChanges.length === 1,
  },
  handOffStopsWithoutCommit: {
    args: { ...B, maxRounds: 1 },
    respond(label) {
      if (label.endsWith(':builder')) { if (label !== 'build:r1:builder') throw new Error('a piece followed one that committed nothing: ' + label); return unfinished({ commits: [] }) }
      return lanesClean(label)
    },
    expect: (r, { labels }) => r.status === 'unfinished' && pieces(labels, 1).length === 1 && labels.includes('build:r1:code'),
  },
  handOffCapped: {
    args: { ...B, maxRounds: 1 },
    respond(label) {
      if (label.endsWith(':builder')) return unfinished({ commits: [{ sha: `c${piece(label)}`, subject: 'part of the plan' }] })
      return lanesClean(label)
    },
    expect: (r, { labels, logs }) => r.status === 'unfinished'
      && pieces(labels, 1).length === 8 && pieces(labels, 1)[7] === 'build:r1:p8:builder'
      && logs.some(l => /\bpieces?\b/i.test(l) && /\b(8|eight)\b/i.test(l))
      && labels.includes('build:r1:code') && labels.indexOf('build:r1:code') > labels.indexOf('build:r1:p8:builder')
      && r.commits.length === 8,
  },
  testsStageHandOffKeepsLastList: {
    args: { ...base, stage: 'tests' },
    respond(label, prompt) {
      if (label === 'tests:r1:builder') return builder({ commits: [{ sha: 'c1', subject: 'added test a' }], tests: [entry(A)], planComplete: false, remaining: ['test b'] })
      if (label === 'tests:r1:p2:builder') {
        const at = prompt.indexOf('The list as it stands')
        if (at < 0 || !prompt.slice(at).includes(A) || !prompt.slice(at).includes('A1')) throw new Error('the second piece is not given the list as it stands, under its labels')
        return builder({ commits: [{ sha: 'c2', subject: 'added test b' }], tests: [entry(A), entry(Bt)] })
      }
      if (label.endsWith(':builder')) throw new Error('unexpected builder ' + label)
      if (label.endsWith(':tester')) return testsCheck({ tests: [ran(A), ran(Bt)], changes: changes([[A, 'added'], [Bt, 'added']]) })
      if (label.endsWith(':issue')) return review()
      if (label.endsWith(':product')) return review({ summary: 'S' })
    },
    expect: (r, { labels }) => r.status === 'passed' && r.lastRound === 1
      && r.tests.map(t => `${t.label} ${t.nodeid}`).join() === `A1 ${A},A2 ${Bt}`
      && r.report.includes('**A1** `test_a`') && r.report.includes('**A2** `test_b`')
      && labels.filter(l => l === 'tests:r1:tester').length === 1 && labels.indexOf('tests:r1:tester') > labels.indexOf('tests:r1:p2:builder'),
  },
  laterPieceReturnsNothing: {
    args: B,
    respond(label) {
      if (label === 'build:r1:builder') return unfinished()
      if (label === 'build:r1:p2:builder') return undefined
      if (label.endsWith(':builder')) throw new Error('unexpected builder ' + label)
      return lanesClean(label)
    },
    expect: r => r.status === 'failed' && /round 1\b/.test(r.failure) && /piece 2\b/.test(r.failure),
  },
  piecesMergeFixesAndDisputes: {
    args: { ...B, maxRounds: 2 },
    respond(label, prompt) {
      const k = round(label)
      if (label === 'build:r1:builder') return builder({ tests: [] })
      if (label === 'build:r2:builder') {
        if (!prompt.includes('code-1-1') || !prompt.includes('code-1-2')) throw new Error('the round 2 builder is not given both findings')
        return unfinished({ commits: [{ sha: 'c2', subject: 'fixed code-1-1' }], fixed: [{ id: 'code-1-1', commit: 'c2' }], remaining: ['answer code-1-2'] })
      }
      if (label === 'build:r2:p2:builder') return builder({ tests: [], commits: [], disputed: [{ id: 'code-1-2', reason: 'DISPUTE REASON', evidence: [] }] })
      if (label.endsWith(':builder')) throw new Error('unexpected builder ' + label)
      if (label.endsWith(':code')) {
        if (k === 1) return review({ findings: [finding('code-1-1'), finding('code-1-2')] })
        if (!prompt.includes('says it is fixed in c2') || !prompt.includes('disputes it: DISPUTE REASON')) throw new Error('the code reviewer is not asked to judge the fix and the dispute')
        return review()
      }
      return lanesClean(label)
    },
    expect: r => {
      const byId = new Map(r.ledger.map(f => [f.id, f]))
      return byId.get('code-1-1').status === 'fixed' && byId.get('code-1-1').fixedIn === 'c2'
        && byId.get('code-1-2').status === 'disputed' && byId.get('code-1-2').dispute === 'DISPUTE REASON'
        && r.commits.map(c => c.sha).join() === 'c1,c2'
    },
  },
}
