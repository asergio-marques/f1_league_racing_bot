// What reaches the owner, and what does not: the scope a checker keeps to, and the questions it
// raises. A reversible call with a recommendation is taken on it and listed at the gate; the rest
// stop the stage.
const { builder, review, suite, testsCheck, base, round } = require('./stubs')
// A question with its recommendation, stopping or not as `stops` says; left out where undefined.
const rq = (text, stops, kind = 'business') => ({ kind, question: text, context: 'the spec says nothing', options: [{ label: 'a', meaning: 'x' }], recommendation: 'SAY-X', ...(stops === undefined ? {} : { stops }) })
const BUILD = { ...base, stage: 'build' }
const ARCH = (o = {}) => ({ rulesTouched: [], breachesRemoved: [], breachesAdded: [], notYetBuilt: [], planChanges: [], questions: [], raised: [], followUps: [], notes: [], ...o })
const PRODUCT = (o = {}) => ({ specRules: [], criteria: [], questions: [], citations: [], documentsOwed: [], raised: [], followUps: [], notes: [], ...o })
const followUp = title => ({ title, kind: 'bug', why: 'w', evidence: [] })
const CHECK = { stage: 'check', issue: '#999', plan: 'PLAN TEXT', modules: ['results'], commit: 'abc123' }

module.exports = {
  // Something the plan does not need comes back as a draft for the tracker, tagged with the checker
  // that found it, and each checker is told the scope rule.
  checkGathersFollowUps: {
    args: CHECK,
    respond(label, prompt) {
      if (!prompt.includes('draft it in followUps[]')) throw new Error(`${label} not told the scope rule`)
      if (label === 'check:architecture') return ARCH({ followUps: [followUp('SAME-FAULT-ELSEWHERE')] })
      if (label === 'check:product') return PRODUCT({ followUps: [followUp('NEIGHBOURING-GAP')] })
      throw new Error('unexpected agent ' + label)
    },
    expect: r => r.followUps.length === 2
      && r.followUps.some(f => f.title === 'SAME-FAULT-ELSEWHERE' && f.lane === 'architecture')
      && r.followUps.some(f => f.title === 'NEIGHBOURING-GAP' && f.lane === 'product')
      && r.questions.length === 0,
  },
  // A reversible call does not stop the stage: the next builder builds on its recommendation, the
  // checkers are told not to ask it again, and the result lists it for the gate.
  reversibleEscalationCarriesOn: {
    args: BUILD,
    respond(label, prompt) {
      const k = round(label)
      if (label.endsWith(':builder')) {
        if (k === 2 && !(prompt.includes('WORDING?') && prompt.includes('SAY-X') && prompt.includes('bind you until the owner overrules them'))) throw new Error('builder not given the call taken')
        return builder({ tests: [] })
      }
      if (label.endsWith(':tester')) return suite()
      if (label.endsWith(':product')) {
        if (k === 1) return review({ escalations: [rq('WORDING?', false)] })
        if (!prompt.includes('do not ask them again') || !prompt.includes('WORDING?')) throw new Error('product owner not told of the call taken')
        return review({ summary: 'S' })
      }
      return review()
    },
    expect: r => r.status === 'passed' && r.lastRound === 2 && r.escalations.length === 0
      && r.provisional.length === 1 && r.provisional[0].question === 'WORDING?' && r.provisional[0].recommendation === 'SAY-X' && r.provisional[0].round === 1
      && r.rounds[0].provisional === 1 && r.rounds[0].questions === 0,
  },
  // A question whose answer cannot be reversed stops the stage, as before.
  stoppingEscalationStops: {
    args: BUILD,
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return suite()
      if (label.endsWith(':product')) return review({ escalations: [rq('MUST-ASK?', true)] })
      return review()
    },
    expect: r => r.status === 'question' && r.escalations.length === 1 && r.escalations[0].question === 'MUST-ASK?' && r.provisional.length === 0,
  },
  // A question that does not say whether it stops is taken to stop.
  missingStopsStops: {
    args: BUILD,
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return suite()
      if (label.endsWith(':product')) return review({ escalations: [rq('UNSAID?')] })
      return review()
    },
    expect: r => r.status === 'question' && r.escalations.length === 1 && r.provisional.length === 0,
  },
  // A call already taken is handed to triage as handled, so that it is not raised again.
  provisionalNotReasked: {
    args: BUILD,
    respond(label, prompt) {
      const k = round(label)
      if (label === 'triage:r2:product') {
        if (!prompt.includes('Already answered or put to the owner') || !prompt.includes('WORDING?')) throw new Error('triage not told of the call taken')
        return { answers: [{ question: 'OTHER?', answer: 'the rule says so', source: 'spec § X', ref: 'r2-1' }], escalations: [], findings: [] }
      }
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return suite()
      if (label.endsWith(':product')) return k === 1 ? review({ escalations: [rq('WORDING?', false)] }) : review({ summary: 'S' })
      if (label.endsWith(':code')) return k === 2 ? review({ raised: [rq('OTHER?', true)] }) : review()
      return review()
    },
    expect: r => r.status === 'passed' && r.provisional.length === 1,
  },
  // Calls taken in an earlier run bind the next run's builder, and stay in the result.
  provisionalCarriedAcrossRuns: {
    args: { ...BUILD, previous: { stage: 'build', status: 'question', lastRound: 1, ledger: [], citations: [], commits: [{ sha: 'c1', subject: 'x' }], separateDefects: [], lastFailures: [], tests: [], provisional: [{ ref: '', kind: 'business', question: 'EARLIER?', recommendation: 'EARLIER-REC', round: 1 }] } },
    respond(label, prompt) {
      if (label.endsWith(':builder')) {
        if (!prompt.includes('EARLIER-REC')) throw new Error('builder not given the earlier call')
        return builder({ tests: [] })
      }
      if (label.endsWith(':tester')) return suite()
      if (label.endsWith(':product')) return review({ summary: 'S' })
      return review()
    },
    expect: r => r.status === 'passed' && r.provisional.length === 1 && r.provisional[0].question === 'EARLIER?',
  },
  // A builder's question that no checker judged goes to the owner however it is marked: its
  // recommendation is the builder's own.
  unframedStillStops: {
    args: BUILD,
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [], questions: [rq('BUILDER-Q?', false)] })
      if (label.endsWith(':tester')) return suite()
      if (label.endsWith(':product')) return undefined
      return review()
    },
    expect: r => r.status === 'question' && r.escalations.some(q => q.question === 'BUILDER-Q?' && q.unframed) && r.provisional.length === 0,
  },
  // At the check, a reversible call is assumed and listed in the plan; the rest are asked.
  checkSplitsAssumed: {
    args: CHECK,
    respond(label) {
      if (label === 'check:architecture') return ARCH()
      if (label === 'check:product') return PRODUCT({ questions: [rq('MUST-ASK?', true), rq('WORDING?', false)] })
      throw new Error('unexpected agent ' + label)
    },
    expect: r => r.questions.map(q => q.question).join() === 'MUST-ASK?' && r.assumed.map(q => q.question).join() === 'WORDING?',
  },
  // The Gate 2 report lists every call taken, for the owner to overrule at the gate.
  reversibleCallListedAtGate2: {
    args: { ...base, stage: 'tests' },
    respond(label) {
      const k = round(label)
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck()
      if (label.endsWith(':product')) return k === 1 ? review({ escalations: [rq('WORDING?', false)] }) : review({ summary: 'S' })
      return review()
    },
    expect: r => r.status === 'passed' && r.report.includes('## Taken on a recommendation — overrule any') && r.report.includes('WORDING? *Taken:* SAY-X'),
  },
}
