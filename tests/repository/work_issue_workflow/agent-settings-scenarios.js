// Each role's model and effort: by default Sonnet runs the build's builder and the tester, and Opus
// every other role, each set explicitly, at high effort but the tester's, which is low; `models` and
// `efforts` override a role, and an unknown role, model or effort is refused. The models accepted
// are opus, sonnet and haiku alone: fable is refused like any unknown model. No effort above high is
// accepted (#483). Every agent is asked to answer briefly.
const { q, builder, review, testsCheck, suite, base } = require('./stubs')
const B = { ...base, stage: 'build', criteria: 'CRIT', checks: 'CHECKS' }
const C = { stage: 'check', issue: '#999', plan: 'PLAN', modules: ['steward'], commit: 'abc123' }

// The role a call is made in, from its label and the stage.
const roleOf = (label, stage) => {
  if (label.startsWith('triage:')) return 'triage'
  if (label.startsWith('check:')) return label === 'check:product' ? 'product' : 'issue'
  if (label.endsWith(':builder')) return stage === 'tests' ? 'testsBuilder' : 'builder'
  if (label.endsWith(':tester')) return 'tester'
  if (label.endsWith(':summary')) return 'product'
  return label.split(':').pop()
}
const OPUS = { model: 'opus', effort: 'high' }
const DEFAULTS = { testsBuilder: OPUS, builder: { model: 'sonnet', effort: 'high' }, tester: { model: 'sonnet', effort: 'low' }, issue: OPUS, code: OPUS, product: OPUS, design: OPUS, triage: OPUS }
// Every call has its role's model and effort, and no other; and each role named was called.
const settingsHold = (calls, stage, want, roles) => {
  const wrong = calls.filter(({ label, opts }) => {
    const w = want[roleOf(label, stage)]
    return !w || opts.model !== w.model || opts.effort !== w.effort
  })
  for (const { label, opts } of wrong) console.log(`  ${label}: model=${opts.model} effort=${opts.effort}`)
  const called = new Set(calls.map(({ label }) => roleOf(label, stage)))
  const missing = roles.filter(r => !called.has(r))
  if (missing.length) console.log(`  never called: ${missing.join(', ')}`)
  return !wrong.length && !missing.length
}

// A build with the builder handed off in two pieces, a design file changed, a question the code
// reviewer raised triaged, and the product owner asked again for its summary.
const buildRespond = (label, prompt, opts) => {
  if (label === 'build:r1:builder') return builder({ tests: [], planComplete: false, remaining: ['commit point 4'] })
  if (label.endsWith(':builder')) return builder({ tests: [], commits: [{ sha: 'c2', subject: 'finished' }] })
  if (label.startsWith('triage:')) return { answers: [{ question: 'should an empty division post nothing?', answer: 'yes', source: 'results § X', ref: 'r1-1' }], escalations: [], findings: [] }
  if (label.endsWith(':tester')) return suite()
  if (label.endsWith(':issue')) return review({ designDocsChanged: ['docs/design/results_module.md'] })
  if (label.endsWith(':code')) return review({ raised: [q('business', 'should an empty division post nothing?')] })
  if (label.endsWith(':summary')) return review({ summary: 'ACCEPTANCE' })
  return review()
}
const refused = extra => ({
  args: { ...B, ...extra },
  respond(label) { throw new Error(`${label} ran with settings that should have been refused`) },
  expectThrow: 'work-issue requires args',
})

module.exports = {
  defaultSettingsTestsStage: {
    args: { ...base, stage: 'tests' },
    respond(label) {
      if (label.startsWith('triage:')) return { answers: [{ question: 'what should a league see?', answer: 'x', source: 'core § Y', ref: 'r1-1' }], escalations: [], findings: [] }
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck()
      if (label.endsWith(':issue')) return review({ raised: [q('business', 'what should a league see?')] })
      if (label.endsWith(':product')) return review({ summary: 'S' })
    },
    expect: (r, { calls }) => r.status === 'passed' && settingsHold(calls, 'tests', DEFAULTS, ['testsBuilder', 'tester', 'issue', 'product', 'triage']),
  },
  defaultSettingsBuild: {
    args: B,
    respond: buildRespond,
    expect: (r, { calls, labels }) => r.status === 'passed' && labels.includes('build:r1:p2:builder')
      && settingsHold(calls, 'build', DEFAULTS, ['builder', 'tester', 'issue', 'code', 'product', 'design', 'triage']),
  },
  settingsOverride: {
    args: { ...B, models: { builder: 'opus', code: 'haiku' }, efforts: { product: 'medium' } },
    respond(label) {
      if (label.endsWith(':builder')) return builder({ tests: [] })
      if (label.endsWith(':tester')) return suite()
      if (label.endsWith(':product')) return review({ summary: 'ACCEPTANCE' })
      return review()
    },
    expect: (r, { calls }) => r.status === 'passed' && settingsHold(calls, 'build', {
      ...DEFAULTS, builder: { model: 'opus', effort: 'high' }, code: { model: 'haiku', effort: 'high' }, product: { model: 'opus', effort: 'medium' },
    }, ['builder', 'tester', 'issue', 'code', 'product']),
  },
  checkStageSettings: {
    args: { ...C, models: { issue: 'sonnet' } },
    respond(label) {
      if (label === 'check:architecture') return { rulesTouched: [], breachesRemoved: [], breachesAdded: [], notYetBuilt: [], planChanges: [], questions: [], raised: [q('business', 'biz from arch?')], notes: [] }
      if (label === 'check:design') return { modules: [], questions: [], raised: [], notes: [] }
      if (label === 'check:product') return { specRules: [], criteria: [], questions: [], citations: [], documentsOwed: [], raised: [], notes: [] }
      if (label === 'triage:check:product') return { answers: [{ question: 'biz from arch?', answer: 'y', source: 'results § X', ref: 'c1' }], escalations: [], findings: [] }
      throw new Error('unexpected agent ' + label)
    },
    expect: (r, { calls }) => !r.failed.length && settingsHold(calls, 'check', { ...DEFAULTS, issue: { model: 'sonnet', effort: 'high' } }, ['issue', 'product', 'triage'])
      && calls.filter(c => c.label === 'check:architecture' || c.label === 'check:design').length === 2,
  },
  unknownRoleRefused: refused({ models: { wizard: 'opus' } }),
  unknownModelRefused: refused({ models: { builder: 'gpt-4' } }),
  fableRefused: refused({ models: { tester: 'fable' } }),
  unknownEffortRefused: refused({ efforts: { tester: 'extreme' } }),
  effortAboveHighRefused: refused({ efforts: { issue: 'max' } }),
  effortXhighRefused: refused({ efforts: { builder: 'xhigh' } }),
  // Every prompt, in the check, the tests stage and the build, ends with the rule to answer briefly.
  everyPromptAsksForBrevity: {
    args: B,
    respond(label, prompt, opts) {
      if (!prompt.includes('## How to answer') || !prompt.includes('Be brief.')) throw new Error(`${label} was not asked to answer briefly`)
      return buildRespond(label, prompt, opts)
    },
    expect: (r, { labels }) => r.status === 'passed' && labels.includes('build:r1:design') && labels.some(l => l.startsWith('triage:')),
  },
  everyTestsStagePromptAsksForBrevity: {
    args: { ...base, stage: 'tests' },
    respond(label, prompt) {
      if (!prompt.includes('Be brief.')) throw new Error(`${label} was not asked to answer briefly`)
      if (label.endsWith(':builder')) return builder()
      if (label.endsWith(':tester')) return testsCheck()
      if (label.endsWith(':product')) return review({ summary: 'S' })
      return review()
    },
    expect: r => r.status === 'passed',
  },
  everyCheckPromptAsksForBrevity: {
    args: C,
    respond(label, prompt) {
      if (!prompt.includes('Be brief.')) throw new Error(`${label} was not asked to answer briefly`)
      if (label === 'check:architecture') return { rulesTouched: [], breachesRemoved: [], breachesAdded: [], notYetBuilt: [], planChanges: [], questions: [], raised: [], notes: [] }
      if (label === 'check:design') return { modules: [], questions: [], raised: [], followUps: [], notes: [] }
      if (label === 'check:product') return { specRules: [], criteria: [], questions: [], citations: [], documentsOwed: [], raised: [], followUps: [], notes: [] }
    },
    expect: r => !r.failed.length,
  },
}
