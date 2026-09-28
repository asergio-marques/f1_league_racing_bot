// The check stage: three checkers side by side, a failed one reported, and a question one checker
// meets outside its ground triaged by the checker that owns it.
const q = (kind, t) => ({ kind, question: t, context: 'the spec says nothing', options: [{ label: 'a', meaning: 'x' }], recommendation: 'a' })
module.exports = {
  deadCheckerAndTriagedQuestion: {
    args: { stage: 'check', issue: '#999', plan: 'PLAN TEXT', modules: ['steward'], commit: 'abc123' },
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
  // A module with no design file gives a design agent nothing to check the plan against: none runs,
  // and the check says each module is held to architecture.md alone.
  noDesignFileRunsNoDesignAgent: {
    args: { stage: 'check', issue: '#999', plan: 'PLAN TEXT', modules: ['results', 'core'], commit: 'abc123' },
    respond(label) {
      if (label === 'check:architecture') return { rulesTouched: [], breachesRemoved: [], breachesAdded: [], notYetBuilt: [], planChanges: [], questions: [], raised: [], notes: [] }
      if (label === 'check:product') return { specRules: [], criteria: [], questions: [], citations: [], documentsOwed: [], raised: [], notes: [] }
      throw new Error('unexpected agent ' + label)
    },
    expect: (r, { labels, logs }) => !labels.includes('check:design') && r.failed.length === 0
      && r.design.modules.map(m => m.module).join() === 'results,core' && r.design.modules.every(m => !m.exists && !m.designChanges.length)
      && r.design.notes.join().includes('architecture.md') && logs.some(l => l.includes('no design agent runs')),
  },
  // Where one module has a design file, the design agent runs, and is told which have none.
  oneDesignFileRunsTheDesignAgent: {
    args: { stage: 'check', issue: '#999', plan: 'PLAN TEXT', modules: ['results', 'steward'], commit: 'abc123' },
    respond(label, prompt) {
      if (label === 'check:architecture') return { rulesTouched: [], breachesRemoved: [], breachesAdded: [], notYetBuilt: [], planChanges: [], questions: [], raised: [], notes: [] }
      if (label === 'check:design') {
        if (!prompt.includes('steward: docs/design/steward_module.md') || !prompt.includes('results: none yet')) throw new Error('design agent not told which modules have a file')
        return { modules: [], questions: [], raised: [], notes: [] }
      }
      if (label === 'check:product') return { specRules: [], criteria: [], questions: [], citations: [], documentsOwed: [], raised: [], notes: [] }
      throw new Error('unexpected agent ' + label)
    },
    expect: (r, { labels }) => labels.includes('check:design') && r.failed.length === 0,
  },
}
