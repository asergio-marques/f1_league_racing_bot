// Stand-ins for the answers the workflow's agents give, each overridable field by field.
// What tools/changed_tests.py prints, from [nodeid, change] pairs and [file, name, change] triples.
const changes = (tests = [], support = [], markersRemoved = []) => ({
  tests: tests.map(([nodeid, change, from]) => ({ nodeid, change, ...(from ? { from } : {}) })),
  support: support.map(([file, name, change]) => ({ file, name, change })),
  markersRemoved,
})
const q = (kind, t) => ({ kind, question: t, context: 'ctx', options: [{ label: 'a', meaning: 'x' }], recommendation: 'a' })
const builder = (o = {}) => ({ onBranch: true, commits: [{ sha: 'c1', subject: 'added a test' }], planComplete: true, remaining: [], tests: [{ nodeid: 'tests/x/test_a.py::test_a', change: 'added', scenario: 's', expects: 'e' }], support: [], fixed: [], disputed: [], questions: [], blocked: false, clean: true, separateDefects: [], notes: [], ...o })
const review = (o = {}) => ({ findings: [], prior: [], answers: [], escalations: [], raised: [], designDocsChanged: [], summary: '', separateDefects: [], notes: [], ...o })
const testsCheck = (o = {}) => ({ collectionOk: true, collectionDetail: '', uncommitted: [], lockTimedOut: false, tests: [{ nodeid: 'tests/x/test_a.py::test_a', failsWithRunxfail: true, realFailure: 'AssertionError', outcomeAsCommitted: 'xfailed' }], otherFailures: [], environmentProblem: '', changes: changes([['tests/x/test_a.py::test_a', 'added']]), changesError: '', ...o })
const suite = (o = {}) => ({ exitCode: 0, summary: '9000 passed', failures: [], mypyClean: true, mypyErrors: [], xfailMarkersLeft: 0, uncommitted: [], tmpFree: '700M', environmentProblem: '', log: '/tmp/x.log', ...o })
const finding = (id, material = true) => ({ id, title: 't ' + id, material, why: 'w', fix: 'f', evidence: [] })
const base = { issue: 999, plan: 'PLAN', modules: ['results'], worktree: '/tmp/wt', python: '/p/.venv/bin/python', branch: 'fix/999-x', base: 'b0' }
// The round an agent's label names: `build:r2:issue` is round 2.
const round = label => Number((label.match(/:r(\d+):/) || [])[1])
module.exports = { q, builder, review, testsCheck, suite, finding, base, round, changes }
