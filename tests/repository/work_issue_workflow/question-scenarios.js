// What reaches the owner, and what does not: the scope a checker keeps to, and the questions it
// raises.
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
}
