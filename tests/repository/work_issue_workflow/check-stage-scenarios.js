// The check stage: three checkers side by side, a failed one reported, and a question one checker
// meets outside its ground triaged by the checker that owns it.
const q = (kind, t) => ({ kind, question: t, context: 'the spec says nothing', options: [{ label: 'a', meaning: 'x' }], recommendation: 'a' })
module.exports = {
  deadCheckerAndTriagedQuestion: {
    args: { stage: 'check', issue: '#999', plan: 'PLAN TEXT', modules: ['results'], commit: 'abc123' },
    respond(label, prompt) {
      if (label === 'check:architecture') return { rulesTouched: [], breachesRemoved: [], breachesAdded: [{ what: 'w', rule: 'r', evidence: [] }], notYetBuilt: [], planChanges: ['do x'], questions: [q('engineering', 'eng?')], raised: [q('business', 'biz from arch?')], notes: [] }
      if (label === 'check:design') return undefined
      if (label === 'check:product') return { specRules: [{ rule: 'R', source: 'S', status: 'would-change' }], criteria: [{ criterion: 'c', source: 's' }], questions: [q('business', 'po?')], citations: [], documentsOwed: [], raised: [], notes: [] }
      if (label === 'triage:check:product') {
        if (!prompt.includes('Already answered or put to the owner')) throw new Error('the triage is not told what was handled')
        if (!prompt.includes('biz from arch?')) throw new Error('the triage is not given the question')
        return { answers: [{ question: 'biz from arch?', answer: 'the spec says y', source: 'results § X', ref: 'c1' }], escalations: [], findings: [] }
      }
      throw new Error('unexpected agent ' + label)
    },
    expect: r => r.failed.join() === 'design' && r.questions.length === 2 && r.citations.length === 1 && r.planChanges.join() === 'do x' && r.issue === '999' && r.specRulesToSettle.length === 1,
  },
}
