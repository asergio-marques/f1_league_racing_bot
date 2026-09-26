// The finding ledger and the owner's questions: what closes a finding, what reaches the owner,
// the owner's rulings, and what the host rather than the code is blamed for.
const { q, builder, review, testsCheck, suite, finding, base, round } = require('./stubs')
const B = { ...base, stage: 'build', criteria: 'CRIT', checks: 'CHECKS', decisions: 'DECISIONS-TEXT' }
const clean = label => {
  if (label.endsWith(':product')) return review({ summary: 'ACCEPTANCE' })
  if (label.endsWith(':tester')) return suite()
  return review()
}
const seen = {}
module.exports = {
  questionAnswered: {
    args: B,
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) {
        if (k === 2 && !prompt.includes('SAVED-RULE')) throw new Error('round 2 builder lacks the citation')
        return k === 1 ? builder({ questions: [q('business', 'Done or Saved?')] }) : builder()
      }
      if (label.endsWith(':product') && k === 1) return review({ summary: 'ACCEPTANCE', answers: [{ question: 'Done or Saved?', answer: 'SAVED-RULE', source: 'results § X' }] })
      return clean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 2,
  },
  deadLaneQuestion: {
    args: B,
    respond(label) {
      const k = round(label)
      if (label.endsWith(':builder')) return builder({ questions: [q('business', 'LOST-Q')] })
      if (label.endsWith(':product') && k === 1) return undefined
      return clean(label)
    },
    expect: r => r.status === 'question' && r.lastRound === 1 && JSON.stringify(r.escalations).includes('LOST-Q'),
  },
}
Object.assign(module.exports, {
  designFixVerified: {
    args: B,
    respond(label) {
      const k = round(label)
      if (label.endsWith(':builder')) return k === 1 ? builder() : builder({ fixed: [{ id: 'design-1-1', commit: 'c2' }] })
      if (label.endsWith(':issue')) return k === 1 ? review({ designDocsChanged: ['docs/design/results_module.md'] }) : review()
      if (label.endsWith(':design')) { if (k === 2) seen.verifierRanAgain = true; return k === 1 ? review({ findings: [finding('design-1-1')] }) : review({ prior: [{ id: 'design-1-1', status: 'fixed', grounds: 'ok' }] }) }
      return clean(label)
    },
    expect: r => r.status === 'passed' && seen.verifierRanAgain && r.ledger.find(f => f.id === 'design-1-1').status === 'closed' && r.designFiles.length === 1,
  },
})
const upheldPrev = (id, lane) => ({ stage: 'build', status: 'question', lastRound: 1, ledger: [{ id, lane, title: 'T', material: true, why: 'w', fix: 'f', evidence: [], status: 'upheld', asked: true, dispute: 'no', upheldBecause: 'yes' }], citations: [], commits: [], separateDefects: [], lastFailures: [], tests: [], designFiles: [] })
Object.assign(module.exports, {
  rulingLeave: {
    args: { ...B, rulings: { 'code-1-1': 'leave' }, previous: upheldPrev('code-1-1', 'code') },
    respond(label, prompt) {
      if (label.endsWith(':code')) { if (!prompt.includes('DECISIONS-TEXT')) throw new Error('code reviewer lacks decisions'); if (prompt.includes('code-1-1: T') === false && prompt.includes('"id": "code-1-1"')) throw new Error('left finding still judged'); return review() }
      if (label.endsWith(':builder')) { if (prompt.includes('"id": "code-1-1"')) throw new Error('builder still told to fix a left finding'); return builder() }
      return clean(label)
    },
    expect: r => r.status === 'passed' && r.ledger.find(f => f.id === 'code-1-1').status === 'closed',
  },
  rulingFix: {
    args: { ...B, rulings: { 'code-1-1': 'fix' }, previous: upheldPrev('code-1-1', 'code') },
    respond(label, prompt) {
      if (label.endsWith(':builder')) { if (!prompt.includes('the owner has ruled')) throw new Error('builder not told of the ruling'); return builder({ fixed: [{ id: 'code-1-1', commit: 'c9' }] }) }
      if (label.endsWith(':code')) return review({ prior: [{ id: 'code-1-1', status: 'fixed', grounds: 'ok' }] })
      return clean(label)
    },
    expect: r => r.status === 'passed',
  },
  rejectedDisputeAsked: {
    args: B,
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) return k === 1 ? builder() : builder({ commits: [], disputed: [{ id: 'issue-1-1', reason: 'not a breach', evidence: [] }] })
      if (label.endsWith(':issue')) return k === 1 ? review({ findings: [finding('issue-1-1')] }) : review({ prior: [{ id: 'issue-1-1', status: 'not-fixed', grounds: 'GROUNDS-XYZ' }] })
      return clean(label)
    },
    expect: r => r.status === 'question' && r.lastRound === 2 && r.escalations.length === 1 && r.escalations[0].finding === 'issue-1-1' && r.escalations[0].context.includes('GROUNDS-XYZ'),
  },
  notFixedGroundsShown: {
    args: B,
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) { if (k === 3 && !prompt.includes('GROUNDS-XYZ')) throw new Error('builder lacks grounds'); return k === 1 ? builder() : builder({ fixed: [{ id: 'issue-1-1', commit: 'c' + k }] }) }
      if (label.endsWith(':issue')) return k === 1 ? review({ findings: [finding('issue-1-1')] }) : k === 2 ? review({ prior: [{ id: 'issue-1-1', status: 'not-fixed', grounds: 'GROUNDS-XYZ' }] }) : review({ prior: [{ id: 'issue-1-1', status: 'fixed', grounds: 'ok' }] })
      return clean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 3,
  },
  unconfirmedFixBlocks: {
    args: { ...B, maxRounds: 2 },
    respond(label) {
      const k = round(label)
      if (label.endsWith(':builder')) return k === 1 ? builder() : builder({ fixed: [{ id: 'issue-1-1', commit: 'c2' }] })
      if (label.endsWith(':issue')) return k === 1 ? review({ findings: [finding('issue-1-1')] }) : review()
      return clean(label)
    },
    expect: r => r.status === 'unfinished' && r.openMaterial.length === 1 && r.openMaterial[0].status === 'fixed',
  },
})
Object.assign(module.exports, {
  testsBlockedNoTester: {
    args: { ...base, stage: 'tests' },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ commits: [], tests: [], blocked: true, planComplete: false, questions: [q('business', 'which?')] })
      if (label.endsWith(':tester')) throw new Error('tester sent out with no tests')
      if (label.endsWith(':product')) return review({ escalations: [q('business', 'which?')] })
      return review()
    },
    expect: r => r.status === 'question' && r.rounds[0].dead.length === 0,
  },
  testerFirst: {
    args: B,
    respond(label) {
      seen.order = seen.order || []
      if (label.startsWith('build:r1:') && !label.endsWith(':builder')) seen.order.push(label.split(':')[2])
      if (label.endsWith(':builder')) return builder()
      return clean(label)
    },
    expect: r => r.status === 'passed' && seen.order[0] === 'tester',
  },
})
Object.assign(module.exports, {
  uncommittedTestFile: {
    args: { ...base, stage: 'tests', maxRounds: 1 },
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck({ uncommitted: ['?? tests/x/test_a.py'] })
      if (label.endsWith(':product')) return review({ summary: 'S' })
      return review()
    },
    expect: r => r.status === 'unfinished' && r.lastFailures.some(x => x.includes('not committed')),
  },
  uncommittedBuild: {
    args: { ...B, maxRounds: 1 },
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return suite({ uncommitted: [' M src/leaguebot/x.py'] })
      return clean(label)
    },
    expect: r => r.status === 'unfinished',
  },
})
Object.assign(module.exports, {
  summaryLateQuestion: {
    args: B,
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':summary')) return review({ summary: 'LATE', escalations: [q('business', 'LATE-Q')] })
      if (label.endsWith(':product')) return review()
      return clean(label)
    },
    expect: r => r.status === 'question' && JSON.stringify(r.escalations).includes('LATE-Q'),
  },
  summaryLateFinding: {
    args: { ...B, maxRounds: 1 },
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':summary')) return review({ summary: 'LATE', findings: [finding('product-1-9')] })
      if (label.endsWith(':product')) return review()
      return clean(label)
    },
    expect: r => r.status === 'unfinished' && r.ledger.some(f => f.id === 'product-1-9'),
  },
  summaryLateFine: {
    args: B,
    respond(label, prompt) {
      if (label.endsWith(':code') && !prompt.includes('PYTHONPATH=src /p/.venv/bin/python -c')) throw new Error('code reviewer lacks the interpreter')
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':summary')) return review({ summary: 'LATE-SUMMARY' })
      if (label.endsWith(':product')) return review()
      return clean(label)
    },
    expect: r => r.status === 'passed' && r.summary === 'LATE-SUMMARY',
  },
})
Object.assign(module.exports, {
  triageSeesHandled: {
    args: B,
    respond(label, prompt) {
      if (label === 'triage:r1:product') { if (!prompt.includes('Already answered or put to the owner') || !prompt.includes('BUS-Q')) throw new Error('triage not told of handled questions'); return { answers: [], escalations: [], duplicates: [{ ref: 'r1-1', of: 'BUS-Q' }], findings: [] } }
      if (label.endsWith(':builder')) return builder({ questions: [q('business', 'BUS-Q')] })
      if (label.endsWith(':product')) return review({ escalations: [q('business', 'BUS-Q')] })
      if (label.endsWith(':issue')) return review({ raised: [q('business', 'BUS-Q again')] })
      return clean(label)
    },
    expect: r => r.status === 'question' && r.escalations.length === 1,
  },
})
Object.assign(module.exports, {
  alreadyPassingPin: {
    args: { ...base, stage: 'tests' },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [{ nodeid: 'tests/x/test_a.py::test_a', pins: 'p' }, { nodeid: 'tests/x/test_a.py::test_pin', pins: 'q', alreadyPasses: true }] })
      if (label.endsWith(':tester')) return testsCheck({ tests: [
        { nodeid: 'tests/x/test_a.py::test_a', failsWithRunxfail: true, realFailure: 'AssertionError', outcomeAsCommitted: 'xfailed' },
        { nodeid: 'tests/x/test_a.py::test_pin', failsWithRunxfail: false, realFailure: '', outcomeAsCommitted: 'passed' }] })
      if (label.endsWith(':product')) return review({ summary: 'S' })
      return review()
    },
    expect: r => r.status === 'passed',
  },
  alreadyPassingPinFails: {
    args: { ...base, stage: 'tests', maxRounds: 1 },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [{ nodeid: 'tests/x/test_a.py::test_a', pins: 'p' }, { nodeid: 'tests/x/test_a.py::test_pin', pins: 'q', alreadyPasses: true }] })
      if (label.endsWith(':tester')) return testsCheck({ tests: [
        { nodeid: 'tests/x/test_a.py::test_a', failsWithRunxfail: true, realFailure: 'AssertionError', outcomeAsCommitted: 'xfailed' },
        { nodeid: 'tests/x/test_a.py::test_pin', failsWithRunxfail: true, realFailure: 'AssertionError', outcomeAsCommitted: 'failed' }] })
      if (label.endsWith(':product')) return review({ summary: 'S' })
      return review()
    },
    expect: r => r.status === 'unfinished' && r.lastFailures.some(x => x.includes('must pass')),
  },
  onlyPinsIsFailure: {
    args: { ...base, stage: 'tests' },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [{ nodeid: 'tests/x/test_a.py::test_pin', pins: 'q', alreadyPasses: true }] })
      return review()
    },
    expect: r => r.status === 'failed' && r.failure.includes('no failing test'),
  },
})
const minorPrev = { stage: 'build', status: 'passed', lastRound: 1, ledger: [{ id: 'issue-1-2', lane: 'issue', title: 'MINOR-RENAME', material: false, why: 'w', fix: 'f', evidence: [], status: 'open' }], citations: [], commits: [], separateDefects: [], lastFailures: [], tests: [], designFiles: [] }
Object.assign(module.exports, {
  minorRuledFixIsChecked: {
    args: { ...B, maxRounds: 2, rulings: { 'issue-1-2': 'fix' }, previous: minorPrev },
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) { if (!prompt.includes('the owner wants it made')) throw new Error('builder not told'); return builder({ fixed: [{ id: 'issue-1-2', commit: 'c' + k }] }) }
      if (label.endsWith(':issue')) { if (!prompt.includes('MINOR-RENAME') || !prompt.includes('says it is fixed')) throw new Error('issue reviewer not asked to judge'); return k === 2 ? review({ prior: [{ id: 'issue-1-2', status: 'not-fixed', grounds: 'NOPE' }] }) : review({ prior: [{ id: 'issue-1-2', status: 'fixed', grounds: 'ok' }] }) }
      return clean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 3 && r.ledger.find(f => f.id === 'issue-1-2').status === 'closed',
  },
  minorRuledLeaveCloses: {
    args: { ...B, rulings: { 'issue-1-2': 'leave' }, previous: minorPrev },
    respond(label) { if (label.endsWith(':builder')) return builder(); return clean(label) },
    expect: r => r.status === 'passed' && r.minor.length === 0,
  },
  ownerRuledSeesGrounds: {
    args: { ...B, rulings: { 'code-1-1': 'fix' }, previous: upheldPrev('code-1-1', 'code') },
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) { if (k === 3 && !(prompt.includes('the owner has ruled') && prompt.includes('G-GROUNDS'))) throw new Error('builder lacks ruling or grounds'); return builder({ fixed: [{ id: 'code-1-1', commit: 'c' + k }] }) }
      if (label.endsWith(':code')) return k === 2 ? review({ prior: [{ id: 'code-1-1', status: 'not-fixed', grounds: 'G-GROUNDS' }] }) : review({ prior: [{ id: 'code-1-1', status: 'fixed', grounds: 'ok' }] })
      return clean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 3,
  },
})
Object.assign(module.exports, {
  paraphrasedAnswerByRef: {
    args: B,
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) return k === 1 ? builder({ questions: [q('business', 'Should the reply say `Saved`?')] }) : builder()
      if (label.endsWith(':product') && k === 1) { if (!prompt.includes('"ref": "b1-1"')) throw new Error('PO not given the ref'); return review({ summary: 'A', answers: [{ question: 'Reply wording: "Saved"', answer: 'the spec says Saved', source: 's', ref: 'b1-1' }] }) }
      return clean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 2,
  },
  reframedEscalationOnce: {
    args: B,
    respond(label) {
      if (label.endsWith(':builder')) return builder({ questions: [q('business', 'Does save_result() post when the round is amended?')] })
      if (label.endsWith(':product')) return review({ escalations: [{ ...q('business', 'When a manager amends a round, should the league see results posted again?'), ref: 'b1-1' }] })
      return clean(label)
    },
    expect: r => r.status === 'question' && r.escalations.length === 1,
  },
  triageDuplicateAnsweredByRef: {
    args: B,
    respond(label) {
      if (label === 'triage:r1:issue') return { answers: [{ question: 'x', answer: 'already put to the owner this round', source: 'this round', ref: 'b1-1' }], escalations: [], findings: [] }
      if (label.endsWith(':builder')) return builder({ questions: [q('engineering', 'ENG-RAW')] })
      if (label.endsWith(':issue')) return review({ raised: [{ ...q('engineering', 'ENG-RAW'), ref: 'b1-1' }] })
      if (label.endsWith(':product')) return review({ escalations: [q('business', 'the framed one')] })
      return clean(label)
    },
    expect: r => r.status === 'question' && r.escalations.length === 1 && r.escalations[0].question === 'the framed one',
  },
  exit75IsHost: {
    args: B,
    respond(label) { if (label.endsWith(':builder')) return builder(); if (label.endsWith(':tester')) return suite({ exitCode: 75, summary: '' }); return clean(label) },
    expect: r => r.status === 'failed' && r.failure.includes('lock'),
  },
  unjudgedDisputeFlagged: {
    args: { ...B, maxRounds: 3 },
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) {
        if (k === 3 && !prompt.includes('your dispute was not judged')) throw new Error('builder not told the dispute went unjudged')
        return k === 1 ? builder() : k === 2 ? builder({ commits: [], disputed: [{ id: 'issue-1-1', reason: 'no', evidence: [] }] }) : builder({ fixed: [{ id: 'issue-1-1', commit: 'c3' }] })
      }
      if (label.endsWith(':issue')) return k === 1 ? review({ findings: [finding('issue-1-1')] }) : k === 2 ? review() : review({ prior: [{ id: 'issue-1-1', status: 'fixed', grounds: 'ok' }] })
      return clean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 3,
  },
  unruledDisputeRefused: {
    args: { ...B, previous: upheldPrev('code-1-1', 'code') },
    respond() { throw new Error('should not run') },
    expect: () => false,
    expectThrow: 'has not ruled on: code-1-1',
  },
})
Object.assign(module.exports, {
  joinedRefsCountForBoth: {
    args: B,
    respond(label) {
      const k = round(label)
      if (label.endsWith(':builder')) return k === 1 ? builder({ questions: [q('business', 'Q-ONE'), q('business', 'Q-TWO')] }) : builder()
      if (label.endsWith(':product') && k === 1) return review({ summary: 'A', answers: [{ question: 'both', answer: 'the spec says so', source: 's', ref: 'b1-1, b1-2' }] })
      return clean(label)
    },
    expect: r => r.status === 'passed' && r.lastRound === 2,
  },
  raisedLeftOutByTriage: {
    args: B,
    respond(label) {
      if (label === 'triage:r1:product') return { answers: [], escalations: [], findings: [] }
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':code')) return review({ raised: [q('business', 'RAISED-LOST')] })
      return clean(label)
    },
    expect: r => r.status === 'question' && r.escalations.length === 1 && r.escalations[0].question === 'RAISED-LOST' && r.escalations[0].unframed === true,
  },
  duplicateIsNoCitation: {
    args: B,
    respond(label) {
      if (label === 'triage:r1:product') return { answers: [], escalations: [], duplicates: [{ ref: 'r1-1', of: 'FRAMED' }], findings: [] }
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':code')) return review({ raised: [q('business', 'SAME-AS-FRAMED')] })
      if (label.endsWith(':product')) return review({ escalations: [q('business', 'FRAMED')] })
      return clean(label)
    },
    expect: r => r.status === 'question' && r.escalations.length === 1 && r.escalations[0].question === 'FRAMED' && r.citations.length === 0,
  },
  unheardBuilderQuestionIsUnframed: {
    args: B,
    respond(label) {
      if (label.endsWith(':builder')) return builder({ questions: [q('engineering', 'RAW-Q')] })
      return clean(label)
    },
    expect: r => r.status === 'question' && r.escalations.length === 1 && r.escalations[0].unframed === true,
  },
  unknownRulingRefused: {
    args: { ...B, rulings: { 'nope-1': 'fix' }, previous: { stage: 'build', status: 'unfinished', lastRound: 1, ledger: [], citations: [], commits: [], separateDefects: [], lastFailures: [], tests: [], designFiles: [] } },
    respond() { throw new Error('should not run') },
    expect: () => false,
    expectThrow: 'does not hold: nope-1',
  },
  testsLockTimedOutIsHost: {
    args: { ...base, stage: 'tests' },
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck({ lockTimedOut: true })
      if (label.endsWith(':product')) return review({ summary: 'S' })
      return review()
    },
    expect: r => r.status === 'failed' && r.failure.includes('lock'),
  },
  hostProblemBesideQuestion: {
    args: B,
    respond(label) {
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return suite({ exitCode: 75, summary: '' })
      if (label.endsWith(':product')) return review({ escalations: [q('business', 'Q')] })
      return clean(label)
    },
    expect: r => r.status === 'question' && r.failure.includes('lock'),
  },
})
