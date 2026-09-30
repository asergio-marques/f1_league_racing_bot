// A re-check of an amended plan: each checker is given the plan as it last checked it and its own
// earlier result, and judges what the amendment changes. A checker whose earlier result was lost
// checks in full, and the result carries the plan it checked, for the next re-check.
const C = { stage: 'check', issue: '#999', plan: 'NEW PLAN', modules: ['steward'], commit: 'abc123' }
const architecture = (notes = []) => ({ rulesTouched: [], breachesRemoved: [], breachesAdded: [], notYetBuilt: [], planChanges: [], questions: [], raised: [], notes })
const design = (notes = []) => ({ modules: [], questions: [], raised: [], notes })
const product = (notes = []) => ({ specRules: [], criteria: [], questions: [], citations: [], documentsOwed: [], raised: [], notes })
const earlier = (o = {}) => ({
  stage: 'check', issue: '999', commit: 'abc000', plan: 'OLD PLAN',
  architecture: architecture(['ARCH-EARLIER']), design: design(['DESIGN-EARLIER']), product: product(['PRODUCT-EARLIER']),
  questions: [], specRulesToSettle: [], citations: [], planChanges: [], failed: [],
  ...o,
})
const MARKS = { 'check:architecture': 'ARCH-EARLIER', 'check:design': 'DESIGN-EARLIER', 'check:product': 'PRODUCT-EARLIER' }
const AMENDMENT = /what the amendment changes/i
// Given its own earlier result and the plan it checked, and no other checker's.
const givenItsOwn = (label, prompt) => prompt.includes('OLD PLAN') && AMENDMENT.test(prompt)
  && Object.entries(MARKS).every(([l, mark]) => prompt.includes(mark) === (l === label))
const givenNone = prompt => !prompt.includes('OLD PLAN') && !AMENDMENT.test(prompt) && !/earlier result/i.test(prompt)
  && Object.values(MARKS).every(mark => !prompt.includes(mark))
const answer = label => label === 'check:architecture' ? architecture() : label === 'check:design' ? design() : product()

module.exports = {
  recheckGivenEarlierResult: {
    args: { ...C, previous: earlier() },
    respond(label, prompt) {
      if (!givenItsOwn(label, prompt)) throw new Error(`${label} is not given the plan it checked and its own earlier result`)
      return answer(label)
    },
    expect: r => r.plan === 'NEW PLAN' && !r.failed.length,
  },
  recheckFailedLaneChecksInFull: {
    args: { ...C, previous: earlier({ design: null, failed: ['design'] }) },
    respond(label, prompt) {
      if (label === 'check:design' ? !givenNone(prompt) : !givenItsOwn(label, prompt)) throw new Error(`${label} is not given what it should be`)
      return answer(label)
    },
    expect: r => r.plan === 'NEW PLAN' && !r.failed.length,
  },
  firstCheckHasNoAmendmentNote: {
    args: C,
    respond(label, prompt) {
      if (!givenNone(prompt)) throw new Error(`${label} is told of an earlier check on a first check`)
      return answer(label)
    },
    expect: r => r.plan === 'NEW PLAN' && !r.failed.length,
  },
  recheckRefusesOtherStage: {
    args: { ...C, previous: { stage: 'tests', status: 'passed', lastRound: 1, ledger: [], citations: [], commits: [], separateDefects: [], lastFailures: [], tests: [] } },
    respond(label) { throw new Error(`${label} ran on a previous from another stage`) },
    expectThrow: 'previous is a tests result',
  },
}

// A ref is never reused across a plan's checks: a citation the product owner carries over from the
// earlier check does not settle, by its old ref, a new question passed on in the re-check (#483).
const qn = t => ({ kind: 'business', question: t, context: 'c', options: [{ label: 'a', meaning: 'x' }], recommendation: 'a' })
module.exports.carriedCitationSettlesNoNewQuestion = {
  args: { ...C, previous: earlier({ product: { ...product(['PRODUCT-EARLIER']), citations: [{ question: 'Qx?', answer: 'y', source: 'core § 1', ref: 'c1' }] } }) },
  respond(label, prompt) {
    if (label === 'check:architecture') return { ...architecture(), raised: [qn('Qz?')] }
    if (label === 'check:design') return design()
    if (label === 'check:product') {
      if (prompt.includes('"ref": "c1"') && prompt.includes('Qz?') && prompt.indexOf('Qz?') > prompt.indexOf('passed on to you') && /"question": "Qz\?",[^}]*"ref": "c1"/s.test(prompt)) throw new Error('the new question took an old ref')
      return { ...product(), citations: [{ question: 'Qx?', answer: 'y', source: 'core § 1', ref: 'c1' }] }
    }
    throw new Error('unexpected agent ' + label)
  },
  expect: r => r.questions.some(x => x.question === 'Qz?' && x.unframed) && r.refsUsed >= 2,
}
