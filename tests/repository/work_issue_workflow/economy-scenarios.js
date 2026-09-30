// What the workflow sends its agents, cut to what each needs (#483): text the plan already holds is
// not sent a second time, and the product owner is asked for its summary up front.
const { builder, review, suite, base } = require('./stubs')
const B = { ...base, stage: 'build', criteria: 'CRIT', checks: 'CHECKS' }
const lanesClean = label => {
  if (label.endsWith(':tester')) return suite()
  if (label.endsWith(':product')) return review({ summary: 'ACCEPTANCE' })
  return review()
}
const times = (text, part) => text.split(part).length - 1

module.exports = {
  // The plan carries its criteria, its checks and the owner's decisions word for word, as the
  // skill's plan does in its items 7 and 8: each agent is given them once, in the plan.
  planTextSentOnce: {
    args: { ...B, plan: 'PLAN\n7. CRITERIA-TEXT\n8. CHECKS-TEXT\nDecisions: DECISIONS-TEXT', criteria: 'CRITERIA-TEXT', checks: 'CHECKS-TEXT', decisions: 'DECISIONS-TEXT' },
    respond(label, prompt) {
      for (const part of ['CRITERIA-TEXT', 'CHECKS-TEXT', 'DECISIONS-TEXT']) if (times(prompt, part) > 1) throw new Error(`${label} was sent ${part} twice`)
      if (label.endsWith(':builder')) return builder({ tests: [] })
      return lanesClean(label)
    },
    expect: r => r.status === 'passed',
  },
  // Text the plan does not hold is still sent.
  textNotInPlanStillSent: {
    args: { ...B, plan: 'PLAN', criteria: 'CRITERIA-TEXT', checks: 'CHECKS-TEXT', decisions: 'DECISIONS-TEXT' },
    respond(label, prompt) {
      if (label.endsWith(':builder') && !['CRITERIA-TEXT', 'CHECKS-TEXT', 'DECISIONS-TEXT'].every(p => prompt.includes(p))) throw new Error('the builder lost text the plan does not hold')
      if (label.endsWith(':product') && !['CRITERIA-TEXT', 'DECISIONS-TEXT'].every(p => prompt.includes(p))) throw new Error('the product owner lost text the plan does not hold')
      if (label.endsWith(':builder')) return builder({ tests: [] })
      return lanesClean(label)
    },
    expect: r => r.status === 'passed',
  },
}
