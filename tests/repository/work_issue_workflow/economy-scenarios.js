// What the workflow sends its agents, cut to what each needs (#483): text the plan already holds is
// not sent a second time, and the product owner is asked for its summary up front.
const { builder, review, suite, testsCheck, base } = require('./stubs')
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

// A plan split between the two paths: the commit points built by hand are named to the builder as
// not its own, and to each reviewer as not missing.
module.exports.handBuiltNamedToBuilderAndReviewers = {
  args: { ...B, handBuilt: ['commit point 5: the refusal sites in season_cog'] },
  respond(label, prompt) {
    const named = prompt.includes('commit point 5: the refusal sites in season_cog')
    if (label.endsWith(':builder') && !(named && prompt.includes('not yours to build'))) throw new Error('the builder was not told what is built by hand')
    if (/:(issue|code|product)$/.test(label) && !(named && prompt.includes('none is missing work'))) throw new Error(`${label} was not told what is built by hand`)
    if (label.endsWith(':builder')) return builder({ tests: [] })
    return lanesClean(label)
  },
  expect: r => r.status === 'passed' && r.handBuilt.join() === 'commit point 5: the refusal sites in season_cog',
}
module.exports.handBuiltRefusedUnlessList = {
  args: { ...B, handBuilt: 'commit point 5' },
  respond(label) { throw new Error(`${label} ran with a handBuilt that should have been refused`) },
  expectThrow: 'handBuilt is a list',
}
module.exports.handBuiltRefusedWithEmptyName = {
  args: { ...B, handBuilt: ['commit point 5', ' '] },
  respond(label) { throw new Error(`${label} ran with a handBuilt that should have been refused`) },
  expectThrow: 'handBuilt is a list',
}

// The tester gives each test's real failure as one line, never the traceback: the reviewers judge
// a test's failing by that line, and every word the tester copies is read again by both.
module.exports.testerGivesOneLineFailure = {
  args: { ...base, stage: 'tests' },
  respond(label, prompt, opts) {
    if (label.endsWith(':tester')) {
      const failure = opts.schema.properties.tests.items.properties.realFailure.description
      if (!prompt.includes('at most 200 characters, never the traceback') || !failure.includes('one line')) throw new Error('the tester was not asked for one line')
      return testsCheck()
    }
    if (label.endsWith(':builder')) return builder()
    if (label.endsWith(':product')) return review({ summary: 'S' })
    return review()
  },
  expect: r => r.status === 'passed',
}

// The design verifier is left out of a round in which no design file has changed since its last
// review and it has nothing open; it runs again as soon as one changes (#483).
const designRounds = touchedInRound2 => ({
  args: { ...B, maxRounds: 3 },
  respond(label, prompt) {
    const k = Number(label.match(/:r(\d+):/)[1])
    if (label.endsWith(':builder')) return builder({ tests: [], commits: [{ sha: `c${k}`, subject: `round ${k}` }] })
    if (label.endsWith(':issue')) {
      if (!prompt.includes('designDocsChangedSince')) throw new Error('the issue reviewer was not asked what changed since')
      return k === 1 ? review({ designDocsChanged: ['docs/design/steward_module.md'], designDocsChangedSince: ['docs/design/steward_module.md'], findings: [{ id: 'issue-1-1', title: 't', material: true, why: 'w', fix: 'f', evidence: [] }] })
        : review({ designDocsChanged: ['docs/design/steward_module.md'], designDocsChangedSince: k === 2 && touchedInRound2 ? ['docs/design/steward_module.md'] : [], prior: [{ id: 'issue-1-1', status: 'fixed', grounds: 'ok' }] })
    }
    return lanesClean(label)
  },
})
module.exports.idleDesignVerifierSkipped = {
  ...designRounds(false),
  expect: (r, { labels }) => r.status === 'passed' && labels.includes('build:r1:design') && !labels.includes('build:r2:design'),
}
module.exports.designVerifierRunsWhenDesignFileTouchedAgain = {
  ...designRounds(true),
  expect: (r, { labels }) => r.status === 'passed' && labels.includes('build:r1:design') && labels.includes('build:r2:design'),
}

// The product owner is asked for its summary in every round it finds nothing in, whatever the other
// checkers find, in the tests stage and the build alike.
const upFront = stage => ({
  args: stage === 'tests' ? { ...base, stage } : B,
  respond(label, prompt) {
    if (label.endsWith(':product') && !prompt.includes('whatever you expect the other checkers to find')) throw new Error('the summary was not asked for up front')
    if (label.endsWith(':builder')) return stage === 'tests' ? builder() : builder({ tests: [] })
    if (label.endsWith(':tester')) return stage === 'tests' ? testsCheck() : suite()
    return lanesClean(label)
  },
  expect: r => r.status === 'passed',
})
module.exports.summaryDemandedUpFrontInTests = upFront('tests')
module.exports.summaryDemandedUpFrontInBuild = upFront('build')
