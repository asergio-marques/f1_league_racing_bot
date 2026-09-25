export const meta = {
  name: 'work-issue',
  description: 'Work one approved issue in stages, each ending at a gate the owner decides: check the plan against the architecture, the design files and the specs; write only the failing tests; then build, review and test until they pass',
  whenToUse: 'Run by the fix-issue, fix-issues and design-review skills, one stage per run. Requires args {stage, issue, plan, modules, ...}; the check at the head of the script says what each stage needs. The check stage is read-only. The tests and build stages commit on the given branch in the given checkout, and never push or touch GitHub.',
  phases: [
    { title: 'Check', detail: 'architecture and design (issue-reviewer), spec and acceptance (product-owner), in parallel' },
    { title: 'Tests', detail: 'the builder writes only the failing tests, marked as expected to fail' },
    { title: 'Build', detail: 'the builder implements the plan, or fixes what the last round found' },
    { title: 'Review', detail: 'the issue reviewer, the code reviewer, the product owner and the tester; a design verifier where docs/design changed' },
    { title: 'Triage', detail: 'a question a checker raised outside its ground goes to the product owner (business) or the issue reviewer (engineering)' },
  ],
}

// The invoking runtime may hand args over as a JSON string; accept both.
const ARGS = typeof args === 'string' ? (() => { try { return JSON.parse(args) } catch (e) { return args } })() : args

// Each module's wip-spec, the product owner's to judge, and its design file, the issue
// reviewer's. Stats has a spec but no code yet, so no design file.
const SPECS = {
  core: 'docs/wip-specs/core_specification.md',
  results: 'docs/wip-specs/results_module_specification.md',
  attendance: 'docs/wip-specs/attendance_module_specification.md',
  signup: 'docs/wip-specs/signup_module_specification.md',
  weather: 'docs/wip-specs/weather_module_specification.md',
  image: 'docs/wip-specs/image_module_specification.md',
  steward: 'docs/wip-specs/steward_module_specification.md',
  stats: 'docs/wip-specs/stats_module_specification.md',
}
const DESIGN_FILES = {
  core: 'docs/design/core.md',
  results: 'docs/design/results_module.md',
  attendance: 'docs/design/attendance_module.md',
  signup: 'docs/design/signup_module.md',
  weather: 'docs/design/weather_module.md',
  image: 'docs/design/image_module.md',
  steward: 'docs/design/steward_module.md',
}

const USAGE = `work-issue requires args {stage, issue, plan, modules}. stage is check, tests or build; modules lists the modules the plan touches, from ${Object.keys(SPECS).join(', ')}. check also needs commit, the commit the plan was drafted at, and takes worktree and base when it checks an amended plan against a branch already built. tests and build need worktree and python (absolute paths), branch and base, and take criteria, checks, decisions, previous, kind ("fix" or "design-pass") and maxRounds.`

if (!ARGS || !['check', 'tests', 'build'].includes(ARGS.stage) || !ARGS.issue || !ARGS.plan || !Array.isArray(ARGS.modules) || !ARGS.modules.length) {
  throw new Error(USAGE)
}
const unknown = ARGS.modules.filter(m => !SPECS[m])
if (unknown.length) throw new Error(`Unknown module ${unknown.join(', ')}. ${USAGE}`)

const { stage, plan, modules } = ARGS
const issue = String(ARGS.issue).replace(/^#/, '')
const kind = ARGS.kind || 'fix'
if (!['fix', 'design-pass'].includes(kind)) throw new Error(`kind must be "fix" or "design-pass". ${USAGE}`)
if (stage === 'check' && !ARGS.commit) throw new Error(`The check stage needs commit. ${USAGE}`)
if (stage !== 'check') {
  const missing = ['worktree', 'python', 'branch', 'base'].filter(k => !ARGS[k])
  if (missing.length) throw new Error(`The ${stage} stage needs ${missing.join(', ')}. ${USAGE}`)
  if (!ARGS.worktree.startsWith('/') || !ARGS.python.startsWith('/')) throw new Error(`worktree and python must be absolute paths. ${USAGE}`)
}

// ---- text helpers ---------------------------------------------------------------------------

const asText = v => (v === undefined || v === null || (Array.isArray(v) && !v.length)) ? '' : typeof v === 'string' ? v : JSON.stringify(v, null, 2)
const section = (title, v) => { const t = asText(v); return t ? `\n\n## ${title}\n\n${t}` : '' }

const ISSUE = `issue #${issue} (read it with: gh issue view ${issue} --json title,body,labels,comments)`
const SPEC_LIST = modules.map(m => `${m}: ${SPECS[m]}`).join('; ')
const DESIGN_LIST = modules.map(m => `${m}: ${DESIGN_FILES[m] || 'none, since the module has no code yet'}`).join('; ')
const DESIGN_PASS = kind === 'design-pass'
  ? '\n\nThis is a design pass\'s correction, not a fix: it must change nothing a league sees.'
  : ''

// ---- schemas --------------------------------------------------------------------------------

const EVIDENCE = { type: 'array', items: { type: 'string' }, description: 'file:line, or a command and its output' }

const QUESTION = {
  type: 'object',
  required: ['kind', 'question', 'context', 'options', 'recommendation'],
  properties: {
    kind: { type: 'string', enum: ['business', 'engineering'], description: 'business: what the bot does, what a league sees, what a spec says; engineering: anything else' },
    question: { type: 'string', description: 'in plain terms, leading with what a league sees or what the code does' },
    context: { type: 'string', description: 'what the documents say, quoted with where, or that they say nothing' },
    options: {
      type: 'array',
      items: { type: 'object', required: ['label', 'meaning'], properties: { label: { type: 'string' }, meaning: { type: 'string', description: 'what choosing it means, for a league where it is a business question' } } },
    },
    recommendation: { type: 'string' },
  },
}
const QUESTIONS = { type: 'array', items: QUESTION }

const ANSWER = {
  type: 'object',
  required: ['question', 'answer', 'source'],
  properties: {
    question: { type: 'string' },
    answer: { type: 'string', description: 'what the rule says, quoted, and what it means for this work' },
    source: { type: 'string', description: 'the document and section, or [REQ-ID]' },
  },
}

const ARCHITECTURE_SCHEMA = {
  type: 'object',
  required: ['rulesTouched', 'breachesRemoved', 'breachesAdded', 'notYetBuilt', 'planChanges', 'questions', 'raised', 'notes'],
  properties: {
    rulesTouched: {
      type: 'array',
      items: {
        type: 'object',
        required: ['section', 'change', 'keeps', 'evidence'],
        properties: {
          section: { type: 'string', description: 'the section of architecture.md' },
          change: { type: 'string', description: 'the planned change it governs' },
          keeps: { type: 'boolean' },
          remedy: { type: 'string', description: 'where it does not keep it, what the plan must change' },
          evidence: EVIDENCE,
        },
      },
    },
    breachesRemoved: {
      type: 'array',
      items: {
        type: 'object',
        required: ['entry', 'list', 'removedByPlan', 'advice'],
        properties: {
          entry: { type: 'string', description: 'the ratchet line, as written' },
          list: { type: 'string', description: 'where it is listed, file:line' },
          removedByPlan: { type: 'boolean' },
          advice: { type: 'string', description: 'delete the line in the commit that removes it; or the cost of removing it here; or leave it to the issue it names' },
        },
      },
    },
    breachesAdded: {
      type: 'array',
      items: { type: 'object', required: ['what', 'rule', 'evidence'], properties: { what: { type: 'string' }, rule: { type: 'string' }, evidence: EVIDENCE } },
    },
    notYetBuilt: {
      type: 'array',
      items: {
        type: 'object',
        required: ['rule', 'today', 'carriedBy'],
        properties: { rule: { type: 'string' }, today: { type: 'string', description: 'how far the plan meets it with today\'s code' }, carriedBy: { type: 'string', description: 'the open issue that carries the rest' } },
      },
    },
    planChanges: { type: 'array', items: { type: 'string' }, description: 'every change the plan must make to keep to the architecture' },
    questions: { ...QUESTIONS, description: 'engineering questions for the owner that no written rule settles' },
    raised: { ...QUESTIONS, description: 'business questions met on the way, passed on to the product owner untouched' },
    notes: { type: 'array', items: { type: 'string' } },
  },
}

const DESIGN_SCHEMA = {
  type: 'object',
  required: ['modules', 'questions', 'raised', 'notes'],
  properties: {
    modules: {
      type: 'array',
      items: {
        type: 'object',
        required: ['module', 'designFile', 'exists', 'sectionsTouched', 'designChanges'],
        properties: {
          module: { type: 'string' },
          designFile: { type: 'string' },
          exists: { type: 'boolean', description: 'false: checked against architecture.md alone, and nothing is owed' },
          sectionsTouched: {
            type: 'array',
            items: {
              type: 'object',
              required: ['section', 'keeps', 'evidence'],
              properties: { section: { type: 'string' }, keeps: { type: 'boolean' }, remedy: { type: 'string' }, evidence: EVIDENCE },
            },
          },
          designChanges: {
            type: 'array',
            items: { type: 'object', required: ['section', 'change'], properties: { section: { type: 'string' }, change: { type: 'string' } } },
            description: 'the changes the design file needs because of the work: documents owed',
          },
        },
      },
    },
    questions: { ...QUESTIONS, description: 'engineering questions for the owner that no written rule settles' },
    raised: { ...QUESTIONS, description: 'business questions met on the way, passed on to the product owner untouched' },
    notes: { type: 'array', items: { type: 'string' } },
  },
}

const PRODUCT_PLAN_SCHEMA = {
  type: 'object',
  required: ['specRules', 'criteria', 'questions', 'citations', 'documentsOwed', 'raised', 'notes'],
  properties: {
    specRules: {
      type: 'array',
      items: {
        type: 'object',
        required: ['rule', 'source', 'status'],
        properties: {
          rule: { type: 'string', description: 'the rule, quoted' },
          source: { type: 'string', description: '[REQ-ID], or the document and section' },
          status: { type: 'string', enum: ['follows', 'would-change', 'at-odds-with-code', 'unclear'], description: 'every status but follows carries a question for the owner' },
          note: { type: 'string' },
        },
      },
    },
    criteria: {
      type: 'array',
      items: {
        type: 'object',
        required: ['criterion', 'source'],
        properties: { criterion: { type: 'string', description: 'one thing a league will see once the work lands' }, source: { type: 'string' } },
      },
    },
    questions: { ...QUESTIONS, description: 'for the owner: every rule the plan would change, every spec silent, ambiguous or at odds with the code' },
    citations: { type: 'array', items: ANSWER, description: 'questions the plan raises that a written rule settles' },
    documentsOwed: {
      type: 'array',
      items: { type: 'object', required: ['document', 'section', 'why'], properties: { document: { type: 'string' }, section: { type: 'string' }, why: { type: 'string' } } },
    },
    raised: { ...QUESTIONS, description: 'engineering questions met on the way, passed on to the issue reviewer untouched' },
    notes: { type: 'array', items: { type: 'string' } },
  },
}

const FINDING = {
  type: 'object',
  required: ['id', 'title', 'material', 'why', 'fix', 'evidence'],
  properties: {
    id: { type: 'string', description: 'unique, of the form your prompt gives' },
    title: { type: 'string' },
    material: { type: 'boolean', description: 'true where it keeps the loop going; false for wording, naming and tidy-ups' },
    why: { type: 'string', description: 'the rule broken, with its source; or, for a defect, the concrete failure scenario' },
    fix: { type: 'string', description: 'what would settle it' },
    evidence: EVIDENCE,
  },
}

const TRIAGE_SCHEMA = {
  type: 'object',
  required: ['answers', 'escalations', 'findings'],
  properties: {
    answers: { type: 'array', items: ANSWER },
    escalations: QUESTIONS,
    findings: { type: 'array', items: FINDING, description: 'where a cited rule means the branch must change' },
  },
}

// ---- agents ---------------------------------------------------------------------------------

// A question a checker meets outside its own ground is triaged by the checker who owns it: the
// product owner for business, the issue reviewer for engineering. Neither decides: each cites a
// written rule or escalates to the owner. A triager that fails leaves its questions escalated,
// since asking the owner is the safe way to fail.
const triage = async (questions, tag, where, context) => {
  const business = questions.filter(q => q.kind === 'business')
  const engineering = questions.filter(q => q.kind !== 'business')
  const ask = (qs, who, job) => agent(
    `${job} ${ISSUE}. ${where}\n\nAnswer each question below as your instructions say: cite a written rule in answers[], or escalate it to the owner in escalations[]. Where a cited rule means the work must change, add a material finding saying what, in findings[], with an id of the form ${who}-${tag}-t<n>. Never run pytest.${context}${section('Questions', qs)}`,
    { label: `triage:${tag}:${who}`, phase: 'Triage', agentType: who === 'product' ? 'product-owner' : 'issue-reviewer', schema: TRIAGE_SCHEMA },
  )
  const [b, e] = await parallel([
    () => business.length ? ask(business, 'product', 'Business questions raised by checkers whose ground they are not, for') : Promise.resolve(null),
    () => engineering.length ? ask(engineering, 'issue', 'Engineering questions raised by checkers whose ground they are not, for') : Promise.resolve(null),
  ])
  const settled = (r, qs, who) => {
    if (r) return r
    if (qs.length) log(`The ${who}'s triage returned nothing; its ${qs.length} question(s) go to the owner.`)
    return { answers: [], escalations: qs, findings: [] }
  }
  const rb = settled(b, business, 'product owner')
  const re = settled(e, engineering, 'issue reviewer')
  return {
    answers: [...rb.answers, ...re.answers],
    escalations: [...rb.escalations, ...re.escalations],
    findings: [...rb.findings.map(f => ({ ...f, lane: 'product' })), ...re.findings.map(f => ({ ...f, lane: 'issue' }))],
  }
}

// ---- the check stage ------------------------------------------------------------------------

if (stage === 'check') {
  const { commit } = ARGS
  // An amended plan, after the owner refused the result at acceptance, is checked against the
  // branch as it stands rather than against the commit it was first drafted at.
  const branchNote = ARGS.worktree && ARGS.base
    ? ` This plan amends work already built: the branch is checked out at ${ARGS.worktree}, and its work since ${ARGS.base} is git -C ${ARGS.worktree} log ${ARGS.base}..HEAD. Check the amendment against the code as the branch has it, reading files under that path.`
    : ''
  const head = `${ISSUE}. The plan was drafted at commit ${commit}.${branchNote} The modules it touches: ${modules.join(', ')}.${DESIGN_PASS}`
  const context = `${section('The plan', plan)}${section('The owner\'s decisions so far', ARGS.decisions)}`

  phase('Check')
  log(`Checking the plan for #${issue} against the architecture, the design files (${DESIGN_LIST}) and the specs (${SPEC_LIST}).`)
  const [architecture, design, product] = await parallel([
    () => agent(`Job 1 — check a plan against the architecture. ${head}${context}`,
      { label: 'check:architecture', phase: 'Check', agentType: 'issue-reviewer', schema: ARCHITECTURE_SCHEMA }),
    () => agent(`Job 2 — check a plan against the design files. ${head} The design file for each: ${DESIGN_LIST}.${context}`,
      { label: 'check:design', phase: 'Check', agentType: 'issue-reviewer', schema: DESIGN_SCHEMA }),
    () => agent(`Job 1 — a plan. ${head} The specs: ${SPEC_LIST}, and the core specification wherever the plan touches core's rules.${context}`,
      { label: 'check:product', phase: 'Check', agentType: 'product-owner', schema: PRODUCT_PLAN_SCHEMA }),
  ])
  const failed = [['architecture', architecture], ['design', design], ['product', product]].filter(([, r]) => !r).map(([k]) => k)
  if (failed.length) log(`No result for: ${failed.join(', ')}. Resume the run before relying on the check.`)

  const raised = [architecture, design, product].filter(Boolean).flatMap(r => r.raised)
  const triaged = raised.length
    ? await triage(raised, 'check', `The plan was drafted at commit ${commit}.${branchNote}`, context)
    : { answers: [], escalations: [], findings: [] }

  const questions = [
    ...(product ? product.questions : []),
    ...(architecture ? architecture.questions : []),
    ...(design ? design.questions : []),
    ...triaged.escalations,
  ]
  log(`Questions for the owner: ${questions.length}. Breaches the plan would add: ${architecture ? architecture.breachesAdded.length : '?'}.`)
  return {
    stage,
    issue,
    commit,
    architecture,
    design,
    product,
    questions,
    citations: [...(product ? product.citations : []), ...triaged.answers],
    planChanges: [...(architecture ? architecture.planChanges : []), ...triaged.findings.map(f => f.fix)],
    failed,
  }
}

// ---- the round loop, shared by the tests and build stages -----------------------------------

const { worktree, python, branch, base } = ARGS
const BIN = python.slice(0, python.lastIndexOf('/'))
const MAX_ROUNDS = { tests: 2, build: 3 }
const maxRounds = Number(ARGS.maxRounds) || MAX_ROUNDS[stage]
const LANE_NAMES = { issue: 'issue reviewer', code: 'code reviewer', product: 'product owner', design: 'design verifier' }

// A stage that stopped for the owner is run again with its last result as `previous` and the
// owner's answers in `decisions`: its rounds, findings and citations carry on where it stopped.
const previous = ARGS.previous || null
if (previous && previous.stage !== stage) throw new Error(`previous is a ${previous.stage} result, and this run is the ${stage} stage.`)
const offset = previous ? previous.lastRound : 0
const ledger = new Map((previous ? previous.ledger : []).map(f => [f.id, { ...f }]))
const citations = previous ? [...previous.citations] : []
const commits = previous ? [...previous.commits] : []
const separateDefects = previous ? [...previous.separateDefects] : []
let lastFailures = previous ? [...previous.lastFailures] : []
let written = previous && previous.tests ? [...previous.tests] : []
// A dispute the owner has since ruled on goes back to the builder, which follows the ruling.
for (const f of ledger.values()) if (f.status === 'upheld') { f.status = 'open'; f.ownerRuled = true; f.asked = false }

const STAGE_NAME = stage === 'tests' ? 'the tests stage, where only the failing tests are written' : 'the build'
const WHERE = `Work only in the checkout at ${worktree}, on branch ${branch}. First check that git -C ${worktree} branch --show-current prints ${branch}; if it does not, change nothing and report onBranch false. Run every git command as git -C ${worktree}, and read and edit files under ${worktree} only. The branch's work starts at ${base}.`
const BRANCH_READ = `The branch is checked out at ${worktree}, on ${branch}, and its work starts at ${base}: read git -C ${worktree} log ${base}..HEAD, git -C ${worktree} diff ${base}...HEAD, and the files under ${worktree}.`
const NO_PYTEST = `Never run pytest: ${stage === 'build' ? 'the suite is running beside you, and a second session corrupts it' : 'the tester has run what is needed'}.`

const RUN_PYTEST = `How to run pytest here: always from ${worktree}, behind the test lock, with the pinned interpreter, so that the run tests this checkout's code:

    cd ${worktree} && flock -w 3600 /tmp/f1-pytest.lock env PYTHONPATH=src ${python} -m pytest <targets> -q

Another run may hold the lock for a quarter of an hour, and a shell call is cut off after ten minutes, so give every such Bash call a timeout of 600000 ms. A run that may outlast that (the full suite always does on this host) is started detached, and waited on through its process:

    cd ${worktree} && rm -f LOG LOG.exit && nohup bash -c 'flock -w 3600 /tmp/f1-pytest.lock env PYTHONPATH=src ${python} -m pytest <targets> -q > LOG 2>&1; echo $? > LOG.exit' > /dev/null 2>&1 & echo $!
    timeout 540 tail --pid=<that pid> -f /dev/null; cat LOG.exit 2>/dev/null || echo still running

repeating the second line, each call with a timeout of 600000 ms, until the exit code appears. Never wait with sleep, pgrep or pkill; never read an exit code through a pipe such as | tail; never start a second pytest session while one of yours runs; and never edit a file while a run you started is going.`

const BUILDER_RULES = `The rules of the work:
- Stay inside the approved plan. A separate defect you notice goes in separateDefects[], as a draft for the owner; it is not fixed here.
- Commit at the plan's commit points, one change per commit. Stage every path by name, from git -C ${worktree} status --porcelain; never git add -A, git add . or git commit -a. Give each commit a one-line subject in lower case and the past tense, as the branch's history does, with no trailer of any kind.
- Move or rename a file with git mv, in a commit apart from any change to its content.
- Every change to production code carries its tests (CLAUDE.md, "Testing"). Before each commit, run the tests that cover what you changed, as below; before each commit that touches src/, run ${BIN}/mypy from ${worktree}. Do not run the whole suite: the round's tester does.
- Never push, never touch GitHub, never file anything, and never pip install into the shared virtualenv.
- Where the plan, the owner's decisions and the rules cited to you do not settle something, return it as a question rather than guess: kind "business" for anything about what the bot does, what a league sees or what a spec says, and "engineering" for the rest. Carry on with whatever it does not block, and set blocked only where nothing is left that you can do.
- Finish with everything committed: git -C ${worktree} status --porcelain shows no tracked change.

${RUN_PYTEST}`

const TESTS_JOB = `This is the tests stage. Write only the tests the plan says fail before the change, and no production code at all. Mark each new test, and each existing test whose expectation the change alters, with @pytest.mark.xfail(strict=True, reason="#${issue}: <what is not yet true>"): the suite then stays green on every commit, and the test fails loudly the moment it passes unexpectedly. A test that uses code the plan has not written yet imports it inside the test, so that its file still collects. Run the new tests both ways, as below: with --runxfail each must fail, for the reason the plan gives; without it each must be reported xfailed, and nothing else in their files may fail. Commit them as the first commit of this work, unless the plan places them otherwise. List every test in tests[], each with what it checks in plain terms, and the acceptance criterion it pins where there is one.`

const BUILD_JOB = `This is the build. Carry out the approved plan, commit point by commit point, in its order. The tests that pin the change are already on the branch, marked xfail(strict=True) with a reason naming #${issue} (git -C ${worktree} grep -n -F 'reason="#${issue}:' finds them): remove each marker in the commit that makes its test pass, never before, and list in tests[] every marker you removed. By the end, none may be left.`

// ---- the round loop's schemas ---------------------------------------------------------------

const BUILDER_SCHEMA = {
  type: 'object',
  required: ['onBranch', 'commits', 'planComplete', 'remaining', 'tests', 'fixed', 'disputed', 'questions', 'blocked', 'clean', 'separateDefects', 'notes'],
  properties: {
    onBranch: { type: 'boolean', description: 'the checkout was on the expected branch' },
    commits: {
      type: 'array',
      items: { type: 'object', required: ['sha', 'subject'], properties: { sha: { type: 'string' }, subject: { type: 'string' } } },
      description: 'the commits made this round, oldest first',
    },
    planComplete: { type: 'boolean', description: 'everything this stage owes is done' },
    remaining: { type: 'array', items: { type: 'string' }, description: 'what this stage still owes' },
    tests: {
      type: 'array',
      items: {
        type: 'object',
        required: ['nodeid', 'pins'],
        properties: { nodeid: { type: 'string' }, pins: { type: 'string', description: 'what it checks, in plain terms' }, criterion: { type: 'string' } },
      },
      description: 'the tests stage: every failing test written so far; the build: every xfail marker removed this round',
    },
    fixed: {
      type: 'array',
      items: { type: 'object', required: ['id', 'commit'], properties: { id: { type: 'string' }, commit: { type: 'string' } } },
    },
    disputed: {
      type: 'array',
      items: { type: 'object', required: ['id', 'reason', 'evidence'], properties: { id: { type: 'string' }, reason: { type: 'string' }, evidence: EVIDENCE } },
    },
    questions: QUESTIONS,
    blocked: { type: 'boolean' },
    clean: { type: 'boolean', description: 'git status --porcelain showed no tracked change at the end' },
    separateDefects: {
      type: 'array',
      items: { type: 'object', required: ['title', 'evidence', 'whatALeagueSees'], properties: { title: { type: 'string' }, evidence: EVIDENCE, whatALeagueSees: { type: 'string' } } },
    },
    notes: { type: 'array', items: { type: 'string' } },
  },
}

const PRIOR = {
  type: 'object',
  required: ['id', 'status', 'grounds'],
  properties: {
    id: { type: 'string' },
    status: { type: 'string', enum: ['fixed', 'not-fixed', 'dispute-accepted', 'dispute-upheld'] },
    grounds: { type: 'string' },
  },
}

// One shape for every checker of a round; a field outside a checker's ground stays empty.
const REVIEW_SCHEMA = {
  type: 'object',
  required: ['findings', 'prior', 'answers', 'escalations', 'raised', 'designDocsChanged', 'summary', 'separateDefects', 'notes'],
  properties: {
    findings: { type: 'array', items: FINDING },
    prior: { type: 'array', items: PRIOR, description: 'a verdict on every earlier finding of yours the prompt lists for judging' },
    answers: { type: 'array', items: ANSWER, description: 'questions of your own ground that a written rule settles' },
    escalations: { ...QUESTIONS, description: 'questions of your own ground for the owner' },
    raised: { ...QUESTIONS, description: 'questions outside your ground, passed on untouched' },
    designDocsChanged: { type: 'array', items: { type: 'string' }, description: 'the files under docs/design/ the branch changes since its base' },
    summary: { type: 'string', description: 'the summary your prompt asks for, if it asks for one; otherwise empty' },
    separateDefects: {
      type: 'array',
      items: { type: 'object', required: ['title', 'evidence', 'whatALeagueSees'], properties: { title: { type: 'string' }, evidence: EVIDENCE, whatALeagueSees: { type: 'string' } } },
    },
    notes: { type: 'array', items: { type: 'string' } },
  },
}

const TESTS_CHECK_SCHEMA = {
  type: 'object',
  required: ['collectionOk', 'collectionDetail', 'tests', 'otherFailures', 'environmentProblem'],
  properties: {
    collectionOk: { type: 'boolean' },
    collectionDetail: { type: 'string' },
    tests: {
      type: 'array',
      items: {
        type: 'object',
        required: ['nodeid', 'failsWithRunxfail', 'realFailure', 'xfailedAsCommitted'],
        properties: {
          nodeid: { type: 'string' },
          failsWithRunxfail: { type: 'boolean' },
          realFailure: { type: 'string', description: 'the assertion or exception pytest reports under --runxfail, with its line' },
          xfailedAsCommitted: { type: 'boolean' },
        },
      },
    },
    otherFailures: { type: 'array', items: { type: 'string' } },
    environmentProblem: { type: 'string', description: 'empty unless the host, not the code, is at fault' },
  },
}

const SUITE_SCHEMA = {
  type: 'object',
  required: ['exitCode', 'summary', 'failures', 'mypyClean', 'mypyErrors', 'xfailMarkersLeft', 'tmpFree', 'environmentProblem', 'log'],
  properties: {
    exitCode: { type: 'integer', description: 'the suite\'s exit code, read from its .exit file' },
    summary: { type: 'string', description: 'pytest\'s closing line' },
    failures: {
      type: 'array',
      items: { type: 'object', required: ['test', 'reason'], properties: { test: { type: 'string' }, reason: { type: 'string' } } },
    },
    mypyClean: { type: 'boolean' },
    mypyErrors: { type: 'array', items: { type: 'string' } },
    xfailMarkersLeft: { type: 'integer', description: 'the lines git grep finds for the issue\'s expected-failure reason' },
    tmpFree: { type: 'string' },
    environmentProblem: { type: 'string', description: 'empty unless the host, not the code, is at fault' },
    log: { type: 'string', description: 'the suite\'s log file' },
  },
}

// ---- the round loop's prompts ---------------------------------------------------------------

const materialOpen = f => f.material && (f.status === 'open' || f.status === 'disputed')

const priorSection = lane => {
  const mine = [...ledger.values()].filter(f => f.lane === lane)
  const toJudge = mine.filter(f => f.material && ['open', 'fixed', 'disputed'].includes(f.status)).map(f => ({
    id: f.id,
    title: f.title,
    why: f.why,
    builder: f.status === 'fixed' ? `says it is fixed in ${f.fixedIn}` : f.status === 'disputed' ? `disputes it: ${f.dispute}` : 'has not addressed it',
  }))
  const known = mine.filter(f => !toJudge.some(j => j.id === f.id)).map(f => `${f.id}: ${f.title}`)
  return section('Your earlier findings: in prior[], say of each whether it is fixed, and judge each dispute', toJudge)
    + section('Recorded already, and not to be raised again', known)
}

const shared = (k, lane) => `${ISSUE}, round ${k} of ${STAGE_NAME}. ${BRANCH_READ} ${NO_PYTEST} Give each new finding an id of the form ${lane}-${k}-<n>.${DESIGN_PASS}`

const builderPrompt = k => {
  const first = k === offset + 1
  const open = [...ledger.values()].filter(materialOpen).map(f => ({
    id: f.id,
    from: LANE_NAMES[f.lane],
    title: f.title,
    why: f.why,
    fix: f.fix,
    evidence: f.evidence,
    note: f.ownerRuled ? 'the owner has ruled on your dispute: follow the decisions below' : f.status === 'disputed' ? 'your dispute was not judged: fix it or dispute it again' : '',
  }))
  const start = first && !previous
    ? 'Start the stage from the plan.'
    : `Earlier rounds have already worked on this branch: read git -C ${worktree} log ${base}..HEAD first. Fix each open material finding below in a commit of its own, or dispute it with evidence where you judge it wrong; fix the failures below; and finish whatever this stage still owes. Report every finding id you fixed or disputed.`
  const answered = first && previous && previous.status === 'question'
    ? ' The last run stopped on questions for the owner. Their answers are in the decisions below, and bind you.'
    : ''
  return `You are the builder for ${ISSUE}: ${STAGE_NAME}, round ${k}.

${WHERE}

${stage === 'tests' ? TESTS_JOB : BUILD_JOB}${DESIGN_PASS}

${start}${answered}

${BUILDER_RULES}${section('The approved plan', plan)}${section('The checks the plan passed', ARGS.checks)}${section('What a league should see once it lands', ARGS.criteria)}${section('The owner\'s decisions and answers, which bind you', ARGS.decisions)}${section('Rules cited to you by the product owner and the issue reviewer', citations)}${section('Open material findings', open)}${section('Failing tests, type errors and other problems from the last round', lastFailures)}`
}

const issuePrompt = (k, questions, testReport) => `Job 3 — review a round of the branch. ${shared(k, 'issue')} The modules: ${modules.join(', ')}; their design files: ${DESIGN_LIST}. Settle each engineering question below by citing a written rule in answers[], or escalate it in escalations[]. Pass every business question you meet to raised[], untouched. List in designDocsChanged every file under docs/design/ the branch changes since its base. Leave summary empty.${section('The approved plan', plan)}${section('The checks the plan passed', ARGS.checks)}${section('The owner\'s decisions and answers', ARGS.decisions)}${priorSection('issue')}${section('Engineering questions from the builder', questions)}${section('The tester\'s report', testReport)}`

const summaryAsk = () => {
  if (stage === 'tests') return 'If you find nothing material and escalate nothing, write summary: the tests, for the owner to review before any code is written, in plain terms: each acceptance criterion and each spec rule the work touches, the test that pins it and what that test checks, and every rule you cited. Otherwise leave summary empty.'
  if (kind === 'design-pass') return 'If you find nothing material and escalate nothing, write summary: a short confirmation that nothing a league sees has changed, and what you checked to be sure. Otherwise leave summary empty.'
  return 'If you find nothing material and escalate nothing, write summary: the acceptance summary your instructions describe. Otherwise leave summary empty.'
}

const productPrompt = (k, questions, testReport) => `Job 2 — a round of the branch. ${shared(k, 'product')} The specs: ${SPEC_LIST}, and the core specification wherever the work touches core's rules. Answer each business question below by citing a written rule in answers[], or escalate it in escalations[]. Pass every engineering question you meet to raised[], untouched. Leave designDocsChanged empty. ${summaryAsk()}${section('The approved plan', plan)}${section('What a league should see once it lands', ARGS.criteria)}${section('The owner\'s decisions and answers', ARGS.decisions)}${section('Rules cited so far in this work', citations)}${priorSection('product')}${section('Business questions from the builder', questions)}${section('The tester\'s report', testReport)}`

const testsTesterPrompt = (k, tests) => `You check the failing tests written in round ${k} of the tests stage for issue #${issue}, in ${worktree}. You change nothing: no edits, no commits, no installs, and nothing on GitHub.

1. Collection: pytest tests/ --collect-only -q must exit 0.
2. The real failures: pytest <every nodeid below> -q --runxfail --tb=short. Each test must fail. For each, give the failure pytest reports: the assertion or exception, and its line.
3. As committed: pytest <the files holding them> -q -rxX. Each new test must be reported xfailed, nothing else in those files may fail, and nothing may XPASS.
4. If anything fails across the board, run df -h /tmp: where it is full or nearly, report environmentProblem.

Name any log file /tmp/work-issue-${issue}-tests-r${k}-<step>.log.

${RUN_PYTEST}${section('The tests the builder wrote', tests)}`

const codePrompt = k => `Review round ${k} of the build. ${shared(k, 'code')} Put a question you cannot settle from the code in raised[], with its kind. Leave answers[], escalations[], designDocsChanged and summary empty.${section('The approved plan', plan)}${priorSection('code')}`

const designPrompt = (k, files) => `Job 2 — verify a drafted design file, limited to what this branch changes. ${ISSUE}. ${BRANCH_READ} ${NO_PYTEST} The branch changes ${files.join(', ')}. For each, read git -C ${worktree} diff ${base}...HEAD -- <file>, and the file in full for context, and hold the changed and added text to your seven checks. Read and follow .claude/skills/architecture-review/SKILL.md, Phase 9, and .claude/skills/architecture-review/python-practices.md, and judge against the owner's decisions below as well. Report each failure as a finding with an id of the form design-${k}-<n>: material where a check fails on substance, not material where only the wording is at fault. Put any question in raised[]. Leave answers[], escalations[], designDocsChanged and summary empty.${section('The owner\'s decisions and answers', ARGS.decisions)}${priorSection('design')}`

const buildTesterPrompt = k => {
  const logFile = `/tmp/work-issue-${issue}-build-r${k}.log`
  return `You run the checks for round ${k} of the build for issue #${issue}, in ${worktree}. You change nothing: no edits, no commits, no installs, and nothing on GitHub.

1. Run df -h /tmp, and note in tmpFree what is free.
2. Start the whole suite detached, as below, with tests/ as the targets and ${logFile} as LOG.
3. While it runs, run the type check: cd ${worktree} && ${BIN}/mypy, with a Bash timeout of 600000 ms. Note every error.
4. Count the expected-failure markers left: git -C ${worktree} grep -n -F 'reason="#${issue}:' -- tests/, and report how many lines it finds.
5. Wait for the suite, as below, until its exit code appears.
6. Read the outcome: the exit code from ${logFile}.exit; pytest's closing line, from tail -n 5 ${logFile}; and every line grep -E '^(FAILED|ERROR)' ${logFile} finds, each with the reason pytest gives for it further up the log.
7. A failure spread across unrelated modules is a full /tmp until proved otherwise: run df -h /tmp again, and where it is full or nearly, say so in environmentProblem.

${RUN_PYTEST}`
}

// ---- the round loop's bookkeeping -----------------------------------------------------------

const addFindings = (lane, found) => {
  for (const f of found || []) {
    const id = ledger.has(f.id) ? `${f.id}-${ledger.size}` : f.id
    ledger.set(id, { ...f, id, lane, status: 'open' })
  }
}

// A checker's verdicts on its own earlier findings. One it saw and said nothing of keeps the
// builder's word: fixed is closed, anything else stays open. One whose checker returned nothing
// waits for the next round.
const judge = (lane, result) => {
  const verdicts = new Map((result ? result.prior : []).map(p => [p.id, p]))
  for (const f of ledger.values()) {
    if (f.lane !== lane || !f.material || !['open', 'fixed', 'disputed'].includes(f.status)) continue
    const v = verdicts.get(f.id)
    if (v && (v.status === 'fixed' || v.status === 'dispute-accepted')) f.status = 'closed'
    else if (v && v.status === 'dispute-upheld') { f.status = 'upheld'; f.upheldBecause = v.grounds }
    else if (v) f.status = 'open'
    else if (result) f.status = f.status === 'fixed' ? 'closed' : 'open'
  }
}

const upheldQuestion = f => ({
  kind: f.lane === 'product' ? 'business' : 'engineering',
  question: `The builder disputes a finding of the ${LANE_NAMES[f.lane]}'s, which upholds it: ${f.title}`,
  context: `The finding: ${f.why}\nThe builder: ${f.dispute}\nThe ${LANE_NAMES[f.lane]}: ${f.upheldBecause}`,
  options: [{ label: 'Fix it', meaning: f.fix }, { label: 'Leave it as built', meaning: 'the finding is dropped' }],
  recommendation: `The ${LANE_NAMES[f.lane]}'s grounds are above; this is the owner's call.`,
})

const testsProblems = t => {
  if (!t) return ['the tester returned nothing']
  const problems = []
  if (!t.collectionOk) problems.push(`the suite does not collect: ${t.collectionDetail}`)
  for (const x of t.tests) {
    if (!x.failsWithRunxfail) problems.push(`${x.nodeid} passes already under --runxfail, so it pins nothing yet`)
    if (!x.xfailedAsCommitted) problems.push(`${x.nodeid} is not reported xfailed as committed`)
  }
  const missing = written.filter(w => !t.tests.some(x => x.nodeid === w.nodeid))
  for (const w of missing) problems.push(`${w.nodeid} was not run by the tester`)
  return [...problems, ...t.otherFailures]
}

const reviewTests = async (k, built, questions) => {
  const test = await agent(testsTesterPrompt(k, built.tests), { label: `tests:r${k}:tester`, phase: 'Review', effort: 'low', schema: TESTS_CHECK_SCHEMA })
  const [issueResult, productResult] = await parallel([
    () => agent(issuePrompt(k, questions.engineering, test), { label: `tests:r${k}:issue`, phase: 'Review', agentType: 'issue-reviewer', schema: REVIEW_SCHEMA }),
    () => agent(productPrompt(k, questions.business, test), { label: `tests:r${k}:product`, phase: 'Review', agentType: 'product-owner', schema: REVIEW_SCHEMA }),
  ])
  const problems = testsProblems(test)
  return { lanes: { issue: issueResult, product: productResult }, test, problems, green: !!test && !test.environmentProblem && written.length > 0 && !problems.length }
}

const suiteProblems = t => {
  if (t === undefined) return ['the suite was not run: the builder was blocked and made no commit']
  if (!t) return ['the tester returned nothing']
  const problems = t.failures.map(f => `${f.test}: ${f.reason}`)
  if (t.exitCode !== 0 && !t.failures.length) problems.push(`pytest exited ${t.exitCode}: ${t.summary}`)
  problems.push(...t.mypyErrors.map(e => `mypy: ${e}`))
  if (!t.mypyClean && !t.mypyErrors.length) problems.push('mypy reported errors')
  if (t.xfailMarkersLeft) problems.push(`${t.xfailMarkersLeft} expected-failure marker(s) naming #${issue} are left in tests/`)
  return problems
}

// The four checkers run side by side, and the design verifier follows the issue reviewer where
// the branch changes a design file. A checker left undefined was not due this round; one that is
// null returned nothing.
const reviewBuild = async (k, built, questions) => {
  const quiet = built.blocked && !built.commits.length
  if (quiet) log(`Round ${k}: the builder is blocked and made no commit, so the suite is not run.`)
  const [issueAndDesign, code, product, test] = await parallel([
    () => agent(issuePrompt(k, questions.engineering, null), { label: `build:r${k}:issue`, phase: 'Review', agentType: 'issue-reviewer', schema: REVIEW_SCHEMA })
      .then(async issueResult => {
        if (!issueResult || !issueResult.designDocsChanged.length) return { issue: issueResult, design: undefined }
        const design = await agent(designPrompt(k, issueResult.designDocsChanged), { label: `build:r${k}:design`, phase: 'Review', agentType: 'design-verifier', schema: REVIEW_SCHEMA })
        return { issue: issueResult, design }
      }),
    () => agent(codePrompt(k), { label: `build:r${k}:code`, phase: 'Review', agentType: 'code-reviewer', schema: REVIEW_SCHEMA }),
    () => agent(productPrompt(k, questions.business, null), { label: `build:r${k}:product`, phase: 'Review', agentType: 'product-owner', schema: REVIEW_SCHEMA }),
    () => quiet ? Promise.resolve(undefined) : agent(buildTesterPrompt(k), { label: `build:r${k}:tester`, phase: 'Review', effort: 'low', schema: SUITE_SCHEMA }),
  ])
  const green = !!test && !test.environmentProblem && test.exitCode === 0 && test.mypyClean && test.xfailMarkersLeft === 0
  return {
    lanes: { issue: issueAndDesign ? issueAndDesign.issue : null, code, product, design: issueAndDesign ? issueAndDesign.design : undefined },
    test,
    problems: suiteProblems(test),
    green,
  }
}

// ---- the round loop -------------------------------------------------------------------------

const rounds = []
let status = 'unfinished'
let failure = ''
let escalations = []
let summary = ''
let lastTest = null

for (let k = offset + 1; k <= offset + maxRounds; k++) {
  phase(stage === 'tests' ? 'Tests' : 'Build')
  const built = await agent(builderPrompt(k), { label: `${stage}:r${k}:builder`, phase: stage === 'tests' ? 'Tests' : 'Build', agentType: 'general-purpose', schema: BUILDER_SCHEMA })
  if (!built) { status = 'failed'; failure = `the builder returned nothing in round ${k}`; break }
  if (!built.onBranch) { status = 'failed'; failure = `the checkout at ${worktree} is not on ${branch}`; break }
  commits.push(...built.commits)
  separateDefects.push(...built.separateDefects)
  written = stage === 'tests' ? built.tests : [...written, ...built.tests]
  for (const x of built.fixed) { const f = ledger.get(x.id); if (f) { f.status = 'fixed'; f.fixedIn = x.commit } }
  for (const x of built.disputed) { const f = ledger.get(x.id); if (f) { f.status = 'disputed'; f.dispute = x.reason } }
  if (stage === 'tests' && built.planComplete && !built.tests.length) {
    status = 'failed'
    failure = 'the builder wrote no failing test: the tests stage is only for a plan that names one'
    break
  }

  phase('Review')
  const questions = {
    business: built.questions.filter(q => q.kind === 'business'),
    engineering: built.questions.filter(q => q.kind !== 'business'),
  }
  const reviewed = stage === 'tests' ? await reviewTests(k, built, questions) : await reviewBuild(k, built, questions)
  const dead = []
  for (const [lane, result] of Object.entries(reviewed.lanes)) {
    if (result === undefined) continue
    if (result === null) dead.push(LANE_NAMES[lane])
    judge(lane, result)
    if (result) addFindings(lane, result.findings)
  }
  if (reviewed.test === null) dead.push('tester')
  lastTest = reviewed.test

  const got = Object.values(reviewed.lanes).filter(Boolean)
  citations.push(...got.flatMap(r => r.answers))
  separateDefects.push(...got.flatMap(r => r.separateDefects))
  const roundEscalations = got.flatMap(r => r.escalations)
  const raised = got.flatMap(r => r.raised)
  if (raised.length) {
    const t = await triage(raised, `r${k}`, BRANCH_READ, `${section('The approved plan', plan)}${section('The owner\'s decisions and answers', ARGS.decisions)}`)
    citations.push(...t.answers)
    roundEscalations.push(...t.escalations)
    for (const f of t.findings) addFindings(f.lane, [f])
  }
  const upheld = [...ledger.values()].filter(f => f.status === 'upheld' && !f.asked)
  for (const f of upheld) f.asked = true
  roundEscalations.push(...upheld.map(upheldQuestion))

  lastFailures = [...reviewed.problems, ...(built.clean ? [] : ['the builder left uncommitted changes in the checkout'])]
  const open = [...ledger.values()].filter(materialOpen)
  rounds.push({ round: k, commits: built.commits.map(c => c.subject), openMaterial: open.length, green: reviewed.green, questions: roundEscalations.length, dead })
  log(`Round ${k}: ${built.commits.length} commit(s); ${open.length} material finding(s) open; ${stage === 'tests' ? 'tests' : 'suite'} ${reviewed.green ? 'green' : 'not green'}; ${roundEscalations.length} question(s) for the owner.`)

  if (roundEscalations.length) { status = 'question'; escalations = roundEscalations; break }
  if (reviewed.test && reviewed.test.environmentProblem) { status = 'failed'; failure = `the host, not the code: ${reviewed.test.environmentProblem}`; break }
  if (dead.length) { log(`No result from: ${dead.join(', ')}. The round cannot pass; the next one runs them again.`); continue }
  if (reviewed.green && !open.length && built.planComplete && !built.blocked && built.clean) {
    const product = reviewed.lanes.product
    summary = product.summary
    if (!summary) {
      const asked = await agent(`${productPrompt(k, [], reviewed.test)}\n\nThe round has passed every check. Write summary now; nothing else is needed.`, { label: `${stage}:r${k}:summary`, phase: 'Review', agentType: 'product-owner', schema: REVIEW_SCHEMA })
      summary = asked ? asked.summary : ''
      if (!summary) log('The product owner wrote no summary; the calling session asks it again before the gate.')
    }
    status = 'passed'
    break
  }
}
if (status === 'unfinished') log(`${maxRounds} round(s) used without passing. The calling session runs the stage again, with this result as previous, or asks the owner.`)

return {
  stage,
  issue,
  status,
  failure,
  lastRound: offset + rounds.length,
  rounds,
  escalations,
  summary,
  tests: written,
  lastTest,
  lastFailures,
  openMaterial: [...ledger.values()].filter(materialOpen),
  minor: [...ledger.values()].filter(f => !f.material && f.status === 'open'),
  citations,
  separateDefects,
  commits,
  ledger: [...ledger.values()],
}
