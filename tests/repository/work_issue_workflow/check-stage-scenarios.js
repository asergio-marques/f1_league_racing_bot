// The check stage: the architecture and design checks side by side, then the product owner, a
// failed one reported, and a question one checker meets outside its ground settled by the checker
// that owns it.
const q = (kind, t) => ({ kind, question: t, context: 'the spec says nothing', options: [{ label: 'a', meaning: 'x' }], recommendation: 'a' })
module.exports = {
  // The architecture and design checks run first; the product owner then settles the business
  // questions they met in its own pass, with no triage agent of its own (#483).
  deadCheckerAndPassedOnQuestion: {
    args: { stage: 'check', issue: '#999', plan: 'PLAN TEXT', modules: ['steward'], commit: 'abc123' },
    respond(label, prompt) {
      if (label === 'check:architecture') return { rulesTouched: [], breachesRemoved: [], breachesAdded: [{ what: 'w', rule: 'r', evidence: [] }], notYetBuilt: [], planChanges: ['do x'], questions: [q('engineering', 'eng?')], raised: [q('business', 'biz from arch?')], notes: [] }
      if (label === 'check:design') return undefined
      if (label === 'check:product') {
        if (!prompt.includes('passed on to you') || !prompt.includes('biz from arch?') || !prompt.includes('"ref": "c1"')) throw new Error('the product owner was not given the question passed on')
        if (!prompt.includes('Already put to the owner') || !prompt.includes('eng?')) throw new Error('the product owner is not told what was put to the owner')
        return { specRules: [{ rule: 'R', source: 'S', status: 'would-change' }], criteria: [{ criterion: 'c', source: 's' }], questions: [q('business', 'po?')], citations: [{ question: 'biz from arch?', answer: 'the spec says y', source: 'results § X', ref: 'c1' }], documentsOwed: [], raised: [], notes: [] }
      }
      throw new Error('unexpected agent ' + label)
    },
    expect: (r, { labels }) => r.failed.join() === 'design' && r.questions.length === 2 && r.citations.length === 1 && r.planChanges.join() === 'do x' && r.issue === '999' && r.specRulesToSettle.length === 1
      && labels.indexOf('check:product') > labels.indexOf('check:architecture') && labels.indexOf('check:product') > labels.indexOf('check:design'),
  },
  // The product owner's own engineering questions still go to the issue reviewer's triage.
  checkProductRaisedStillTriaged: {
    args: { stage: 'check', issue: '#999', plan: 'PLAN TEXT', modules: ['results'], commit: 'abc123' },
    respond(label) {
      if (label === 'check:architecture') return { rulesTouched: [], breachesRemoved: [], breachesAdded: [], notYetBuilt: [], planChanges: [], questions: [], raised: [], notes: [] }
      if (label === 'check:product') return { specRules: [], criteria: [], questions: [], citations: [], documentsOwed: [], raised: [q('engineering', 'which table?')], notes: [] }
      if (label === 'triage:check:issue') return { answers: [{ question: 'which table?', answer: 'rounds', source: 'architecture.md § 3', ref: 'c1' }], escalations: [], findings: [] }
      throw new Error('unexpected agent ' + label)
    },
    expect: (r, { labels }) => labels.includes('triage:check:issue') && r.citations.some(c => c.question === 'which table?') && !r.questions.length,
  },
  // A question passed on that the product owner neither settles nor asks reaches the owner as asked.
  checkPassedOnLeftOutReachesOwner: {
    args: { stage: 'check', issue: '#999', plan: 'PLAN TEXT', modules: ['results'], commit: 'abc123' },
    respond(label) {
      if (label === 'check:architecture') return { rulesTouched: [], breachesRemoved: [], breachesAdded: [], notYetBuilt: [], planChanges: [], questions: [], raised: [q('business', 'biz from arch?')], notes: [] }
      if (label === 'check:product') return { specRules: [], criteria: [], questions: [], citations: [], documentsOwed: [], raised: [], notes: [] }
      throw new Error('unexpected agent ' + label)
    },
    expect: (r, { labels }) => !labels.some(l => l.startsWith('triage:')) && r.questions.length === 1 && r.questions[0].question === 'biz from arch?' && r.questions[0].unframed,
  },
  // A product owner that returns nothing settles nothing passed on to it.
  checkPassedOnWithDeadProductReachesOwner: {
    args: { stage: 'check', issue: '#999', plan: 'PLAN TEXT', modules: ['results'], commit: 'abc123' },
    respond(label) {
      if (label === 'check:architecture') return { rulesTouched: [], breachesRemoved: [], breachesAdded: [], notYetBuilt: [], planChanges: [], questions: [], raised: [q('business', 'biz from arch?')], notes: [] }
      if (label === 'check:product') return undefined
      throw new Error('unexpected agent ' + label)
    },
    expect: r => r.failed.join() === 'product' && r.questions.some(x => x.question === 'biz from arch?' && x.unframed),
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
