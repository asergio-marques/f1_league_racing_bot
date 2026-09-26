// Runs .claude/workflows/work-issue.js under node, with every agent replaced by a stand-in.
//
// Usage: node harness.js <scenario file> [scenario name]
//
// A scenario file exports scenarios by name. Each has `args` for the workflow, `respond(label,
// prompt)` standing in for the agent the workflow calls under that label (returning undefined
// stands for an agent that returned nothing), and `expect(result)`, true when the workflow did
// what the scenario pins. A scenario whose run must be refused sets `expectThrow` to part of the
// error instead. The process exits 1 if any scenario fails, printing what the workflow logged and
// returned.
//
// The workflow runtime validates each agent's answer against the call's schema; the stand-ins do
// not. The one field the harness fills in for them is a triage's `duplicates`, which the triage
// schema requires and most stand-ins have no reason to give.
const fs = require('fs')
const path = require('path')

const WORKFLOW = path.join(__dirname, '..', '..', '..', '.claude', 'workflows', 'work-issue.js')
const source = fs.readFileSync(WORKFLOW, 'utf8').replace(/^export const meta/, 'const meta')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const scenarios = require(path.resolve(process.argv[2]))
const only = process.argv[3]

;(async () => {
  let failed = 0
  for (const [name, s] of Object.entries(scenarios)) {
    if (only && name !== only) continue
    const labels = []
    const logs = []
    const agent = async (prompt, opts) => {
      labels.push(opts.label)
      const answer = s.respond(opts.label, prompt, opts)
      if (answer && opts.label.startsWith('triage:') && !answer.duplicates) answer.duplicates = []
      return answer === undefined ? null : answer
    }
    const parallel = async thunks => Promise.all(thunks.map(t => Promise.resolve().then(t).catch(e => {
      logs.push(`THUNK THREW ${e.message}`)
      return null
    })))
    const run = new AsyncFunction('args', 'agent', 'parallel', 'pipeline', 'phase', 'log', 'budget', 'workflow', source)
    try {
      const result = await run(s.args, agent, parallel, null, () => {}, m => logs.push(m), { total: null }, null)
      const ok = !s.expectThrow && s.expect(result) && !logs.some(l => l.startsWith('THUNK THREW'))
      if (!ok) failed++
      console.log(`${ok ? 'PASS' : 'FAIL'} ${name}: status=${result.status} lastRound=${result.lastRound} agents=[${labels.join(' ')}]`)
      if (!ok) console.log('  logs:', logs, '\n  result:', JSON.stringify(result, null, 1).slice(0, 4000))
    } catch (e) {
      if (s.expectThrow && e.message.includes(s.expectThrow)) { console.log(`PASS ${name}: refused as expected`); continue }
      failed++
      console.log(`FAIL ${name}: threw ${e.message}`)
    }
  }
  process.exitCode = failed ? 1 : 0
})()
