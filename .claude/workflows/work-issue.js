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

// The control flow below is checked with every agent replaced by a stand-in, by
// tests/repository/test_work_issue_workflow.py and the scenarios in
// tests/repository/work_issue_workflow/. A change to the loop carries the scenario that pins it.

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

const USAGE = `work-issue requires args {stage, issue, plan, modules}. stage is check, tests or build; modules lists the modules the plan touches, from ${Object.keys(SPECS).join(', ')}. check also needs commit, the commit the plan was drafted at, and takes worktree and base when it checks an amended plan against a branch already built. tests and build need worktree and python (absolute paths), branch and base, and take criteria, checks, decisions, citations, previous, rulings, kind ("fix" or "design-pass") and maxRounds.`

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
    ref: { type: 'string', description: 'where this escalates a question of the builder\'s, that question\'s ref' },
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
    ref: { type: 'string', description: 'where this settles a question of the builder\'s, that question\'s ref' },
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
  required: ['answers', 'escalations', 'duplicates', 'findings'],
  properties: {
    answers: { type: 'array', items: ANSWER },
    escalations: QUESTIONS,
    duplicates: {
      type: 'array',
      items: { type: 'object', required: ['ref', 'of'], properties: { ref: { type: 'string' }, of: { type: 'string', description: 'the question already handled that it repeats' } } },
      description: 'questions that repeat one already handled: neither answered nor escalated',
    },
    findings: { type: 'array', items: FINDING, description: 'where a cited rule means the branch must change' },
  },
}

// ---- agents ---------------------------------------------------------------------------------

// A question carries a ref: b<round>-<n> for the builder's, r<round>-<n> or c<n> for one a checker
// raised. A ref field may name several, so refs are read by pattern.
const refsIn = x => String((x && x.ref) || '').match(/\b(?:b\d+-\d+|r\d+-\d+|c\d+)\b/g) || []

// A question a checker meets outside its own ground is triaged by the checker who owns it: the
// product owner for business, the issue reviewer for engineering. Neither decides: each cites a
// written rule or escalates to the owner. A triager that fails leaves its questions escalated,
// since asking the owner is the safe way to fail.
// `handled` holds the questions already answered or put to the owner, so that a question two
// checkers both met reaches the owner once.
const triage = async (questions, tag, where, context, handled = []) => {
  const business = questions.filter(q => q.kind === 'business')
  const engineering = questions.filter(q => q.kind !== 'business')
  const ask = (qs, who, job) => agent(
    `${job} ${ISSUE}. ${where}\n\nAnswer each question below as your instructions say: cite a written rule in answers[], or escalate it to the owner in escalations[], keeping the question's ref on what settles it, and copying the question word for word into answers[].question. A question that asks the same as one already handled, listed below, goes in duplicates[] with its ref, and is neither answered nor escalated. Where a cited rule means the work must change, add a material finding saying what, in findings[], with an id of the form ${who}-${tag}-t<n>. Never run pytest.${context}${section('Questions', qs)}${section('Already answered or put to the owner', handled)}`,
    { label: `triage:${tag}:${who}`, phase: 'Triage', agentType: who === 'product' ? 'product-owner' : 'issue-reviewer', schema: TRIAGE_SCHEMA },
  )
  const [b, e] = await parallel([
    () => business.length ? ask(business, 'product', 'Business questions raised by checkers whose ground they are not, for') : Promise.resolve(null),
    () => engineering.length ? ask(engineering, 'issue', 'Engineering questions raised by checkers whose ground they are not, for') : Promise.resolve(null),
  ])
  const settled = (r, qs, who) => {
    if (r) return r
    if (qs.length) log(`The ${who}'s triage returned nothing; its ${qs.length} question(s) go to the owner.`)
    return { answers: [], escalations: qs.map(q => ({ ...q, unframed: true })), duplicates: [], findings: [] }
  }
  const rb = settled(b, business, 'product owner')
  const re = settled(e, engineering, 'issue reviewer')
  const result = {
    answers: [...rb.answers, ...re.answers],
    escalations: [...rb.escalations, ...re.escalations],
    duplicates: [...rb.duplicates, ...re.duplicates],
    findings: [...rb.findings.map(f => ({ ...f, lane: 'product' })), ...re.findings.map(f => ({ ...f, lane: 'issue' }))],
  }
  // A question the triage neither settled nor named as a duplicate goes to the owner as asked.
  const returned = new Set([...result.answers, ...result.escalations, ...result.duplicates].flatMap(refsIn))
  const lost = questions.filter(q => !returned.has(q.ref))
  if (lost.length) log(`The triage left out ${lost.length} question(s); they go to the owner.`)
  result.escalations.push(...lost.map(q => ({ ...q, unframed: true })))
  return result
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

  const raised = [architecture, design, product].filter(Boolean).flatMap(r => r.raised).map((q, i) => ({ ...q, ref: `c${i + 1}` }))
  const triaged = raised.length
    ? await triage(raised, 'check', `The plan was drafted at commit ${commit}.${branchNote}`, context,
      [...(product ? product.questions : []), ...(architecture ? architecture.questions : []), ...(design ? design.questions : [])].map(q => q.question))
    : { answers: [], escalations: [], duplicates: [], findings: [] }

  const questions = [
    ...(product ? product.questions : []),
    ...(architecture ? architecture.questions : []),
    ...(design ? design.questions : []),
    ...triaged.escalations,
  ]
  // Every spec rule the plan does not simply follow is the owner's to settle; the calling session
  // makes sure each one reaches them as a question.
  const specRulesToSettle = product ? product.specRules.filter(r => r.status !== 'follows') : []
  log(`Questions for the owner: ${questions.length}. Spec rules to settle: ${specRulesToSettle.length}. Breaches the plan would add: ${architecture ? architecture.breachesAdded.length : '?'}.`)
  return {
    stage,
    issue,
    commit,
    architecture,
    design,
    product,
    questions,
    specRulesToSettle,
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
// Rules cited in an earlier stage, such as the tests stage's for the build, arrive in `citations`.
const citations = previous ? [...previous.citations] : [...(ARGS.citations || [])]
const commits = previous ? [...previous.commits] : []
const separateDefects = previous ? [...previous.separateDefects] : []
let lastFailures = previous ? [...previous.lastFailures] : []
let written = previous && previous.tests ? [...previous.tests] : []
let supportWritten = previous && previous.support ? [...previous.support] : []
// The tests stage's lists as the owner last saw them at Gate 2, so that the report can mark what
// is new or changed since: taken from a run that passed, which is what reached the gate, and
// carried unchanged through any run that stopped short of it.
const shown = stage !== 'tests' || !previous ? null
  : previous.status === 'passed' ? { tests: previous.tests || [], support: previous.support || [] }
    : previous.shown || null
// Every design file the branch has changed. Once there is one, the design verifier runs in every
// round, so that a design finding is always judged by the verifier and never closed on the
// builder's word.
const designFiles = new Set(previous && previous.designFiles ? previous.designFiles : [])
// The owner's rulings, in `rulings` as {<finding id>: "fix" or "leave"}. On a dispute: one left as
// built is closed, and any other goes back to the builder, which follows the ruling. On a minor
// finding: one the owner wants made becomes material, so that its checker must confirm it like
// any other, and one left is closed.
const rulings = ARGS.rulings || {}
const badRulings = Object.entries(rulings).filter(([, v]) => v !== 'fix' && v !== 'leave')
if (badRulings.length) throw new Error(`A ruling is "fix" or "leave", not: ${badRulings.map(([id, v]) => `${id}: ${JSON.stringify(v)}`).join(', ')}`)
const unknownRulings = Object.keys(rulings).filter(id => !ledger.has(id))
if (unknownRulings.length) throw new Error(`rulings name findings previous does not hold: ${unknownRulings.join(', ')}. rulings carries the last result's disputes and minor findings only.`)
const unruled = [...ledger.values()].filter(f => f.status === 'upheld' && !rulings[f.id]).map(f => f.id)
if (unruled.length) throw new Error(`previous holds disputes the owner has not ruled on: ${unruled.join(', ')}. Pass each in rulings as "fix" or "leave".`)
for (const f of ledger.values()) {
  const ruling = rulings[f.id]
  if (!ruling) continue
  if (f.status === 'upheld') {
    if (ruling === 'leave') { f.status = 'closed'; f.ownerLeft = true }
    else { f.status = 'open'; f.ownerRuled = true; f.asked = false }
  } else if (f.ownerWants ? ['open', 'fixed', 'disputed'].includes(f.status) : (!f.material && f.status === 'open')) {
    if (ruling === 'fix') { f.material = true; f.ownerWants = true }
    else { f.status = 'closed'; f.ownerLeft = true }
  }
}

const STAGE_NAME = stage === 'tests' ? 'the tests stage, where only the failing tests are written' : 'the build'
const WHERE = `Work only in the checkout at ${worktree}, on branch ${branch}. First check that git -C ${worktree} branch --show-current prints ${branch}; if it does not, change nothing and report onBranch false. Run every git command as git -C ${worktree}, and read and edit files under ${worktree} only. The branch's work starts at ${base}.`
const BRANCH_READ = `The branch is checked out at ${worktree}, on ${branch}, and its work starts at ${base}: read git -C ${worktree} log ${base}..HEAD, git -C ${worktree} diff ${base}...HEAD, and the files under ${worktree}.`
const NO_PYTEST = `Never run pytest: ${stage === 'build' ? 'the suite is running beside you, and a second session corrupts it' : 'the tester has run what is needed'}.`

// One way to run pytest for every agent, short runs included: detached, behind the lock, and
// waited on through its process. A foreground run can outlast the ten-minute cap on one shell
// call, waiting for the lock or running, and a call cut off mid-run leaves the lock held by a
// pytest nobody reads.
const RUN_PYTEST = `How to run pytest here, for a handful of tests and the whole suite alike: always from ${worktree}, behind the test lock, with the pinned interpreter, so that the run tests this checkout's code. Start it detached, and wait on it through its process:

    cd ${worktree} && rm -f LOG LOG.exit && nohup bash -c 'flock -E 75 -w 3600 /tmp/f1-pytest.lock env PYTHONPATH=src ${python} -m pytest <targets> -q > LOG 2>&1; echo $? > LOG.exit' > /dev/null 2>&1 & echo $!
    timeout 540 tail --pid=<that pid> -f /dev/null; cat LOG.exit 2>/dev/null || echo still running

where LOG is a file under /tmp named for the run. Repeat the second line, each Bash call with a timeout of 600000 ms, until the exit code appears: another run may hold the lock for a quarter of an hour, and a shell call is cut off after ten minutes. Then read LOG. An exit code of 75 is flock giving up after an hour without the lock, which is the host's problem and not the code's. Never wait with sleep, pgrep or pkill; never read an exit code through a pipe such as | tail; never start a second pytest session while one of yours runs; and never edit a file while a run you started is going.`

const BUILDER_RULES = `The rules of the work:
- Stay inside the approved plan. A separate defect you notice goes in separateDefects[], as a draft for the owner; it is not fixed here.
- Commit at the plan's commit points, one change per commit. Stage every path by name, from git -C ${worktree} status --porcelain; never git add -A, git add . or git commit -a. Give each commit a one-line subject in lower case and the past tense, as the branch's history does, with no trailer of any kind.
- Move or rename a file with git mv, in a commit apart from any change to its content.
- Every change to production code carries its tests (CLAUDE.md, "Testing"). Before each commit, run the tests that cover what you changed, as below; before each commit that touches src/, run ${BIN}/mypy from ${worktree}. Do not run the whole suite: the round's tester does.
- Where the owner's decisions call for a change to a wip-spec, the README or a guide, that change is owed by this work: in the build, make it in a commit of its own, in the document's own voice.
- Never push, never touch GitHub, never file anything, and never pip install into the shared virtualenv.
- Where the plan, the owner's decisions and the rules cited to you do not settle something, return it as a question rather than guess: kind "business" for anything about what the bot does, what a league sees or what a spec says, and "engineering" for the rest. Carry on with whatever it does not block, and set blocked only where nothing is left that you can do.
- Finish with everything committed, new files included: git -C ${worktree} status --porcelain --untracked-files=all prints nothing.

${RUN_PYTEST}`

// The command that lists what the branch changes under tests/, which the builder's lists must
// match entry for entry (tools/changed_tests.py).
const CHANGED_TESTS = from => `cd ${worktree} && ${python} tools/changed_tests.py --repo ${worktree} --base ${from}${stage === 'build' ? ` --issue ${issue}` : ''}`

const TESTS_JOB = `This is the tests stage. Make every change to tests/ that this work needs, and no production code at all:
- add the tests the plan says fail before the change;
- change each existing test the change alters, whether its expectation or a call the plan changes;
- delete each test the plan makes obsolete;
- change the fixtures, helpers and data under tests/ that these need.
The one exception is the architecture ratchet lines the plan names as removed: the build deletes each in the commit that removes its breach, and you leave them. After this stage the build may change no test, so anything the plan's change needs of tests/ is made here.

Mark each new test, and each changed test that fails before the change, with @pytest.mark.xfail(strict=True, reason="#${issue}: <what is not yet true>"): the suite then stays green on every commit, and the test fails loudly the moment it passes unexpectedly. A test that uses code the plan has not written yet imports it inside the test, so that its file still collects. A test that passes as committed, because it pins behaviour already built or because the build has already made it pass, is left unmarked. Run the tests both ways, as below: with --runxfail each marked test must fail, for the reason the plan gives; without it each marked test must be reported xfailed and each unmarked one must pass, and nothing else in their files may fail. Commit them as the first commit of this work, unless the plan places them otherwise.

List in tests[] every test this work adds, changes, deletes or moves since ${base}, earlier rounds and runs included, and in support[] every fixture, helper, module-level value or file under tests/ that it adds, changes, deletes or moves. Both lists must match what ${CHANGED_TESTS(base)} prints, entry for entry, with its node ids, its names and its change for each: run it before you finish. Import lines are never a change. The owner approves the tests from these lists before any code is written, so write each entry in plain terms, as a league manager would follow it:
- change: added, modified, deleted or moved.
- scenario: the concrete situation the test sets up and the action it takes: which drivers, seasons, rounds or records, in what state, and which command or call.
- expects: what it asserts.
- before: for a modified test, what it set up and expected until now; where only its wording or docstring changes, say so.
- why: for a deleted test, why it goes, and what pins its rule now if anything does; for a moved one, why it moves.
- criterion: the acceptance criterion it pins, where there is one.
- alreadyPasses: true for a test left unmarked because it passes already.
Each entry in support[] gives the file and the name as the command prints them, the change, what it now does, and in affects the node ids of the tests that use it.`

const BUILD_JOB = `This is the build. Carry out the approved plan, commit point by commit point, in its order. The tests that pin the change are already on the branch, marked xfail(strict=True) with a reason naming #${issue} (git -C ${worktree} grep -n -F 'reason="#${issue}:' finds them): remove each marker in the commit that makes its test pass, never before, and list in tests[] every marker you removed. By the end, none may be left.`

// ---- the round loop's schemas ---------------------------------------------------------------

const BUILDER_SCHEMA = {
  type: 'object',
  required: ['onBranch', 'commits', 'planComplete', 'remaining', 'tests', 'support', 'fixed', 'disputed', 'questions', 'blocked', 'clean', 'separateDefects', 'notes'],
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
        required: ['nodeid', 'change'],
        properties: {
          nodeid: { type: 'string' },
          change: { type: 'string', enum: ['added', 'modified', 'deleted', 'moved', 'markerRemoved'], description: 'markerRemoved in the build alone' },
          scenario: { type: 'string', description: 'the tests stage: the concrete situation the test sets up and the action it takes' },
          expects: { type: 'string', description: 'the tests stage: what it asserts' },
          before: { type: 'string', description: 'a modified test: what it set up and expected until now' },
          why: { type: 'string', description: 'a deleted test: why it goes; a moved one: why it moves' },
          criterion: { type: 'string' },
          alreadyPasses: { type: 'boolean', description: 'the tests stage: a test left unmarked because it passes already' },
        },
      },
      description: 'the tests stage: every test the work adds, changes, deletes or moves since its base; the build: every xfail marker removed this round',
    },
    support: {
      type: 'array',
      items: {
        type: 'object',
        required: ['file', 'name', 'change'],
        properties: {
          file: { type: 'string' },
          name: { type: 'string', description: 'as tools/changed_tests.py names it' },
          change: { type: 'string', enum: ['added', 'modified', 'deleted', 'moved'] },
          what: { type: 'string', description: 'what it now does, in plain terms' },
          affects: { type: 'array', items: { type: 'string' }, description: 'the node ids of the tests that use it' },
        },
      },
      description: 'the tests stage: every fixture, helper, module-level value or file under tests/ the work changes since its base; the build: empty',
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
    clean: { type: 'boolean', description: 'git status --porcelain --untracked-files=all printed nothing at the end' },
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

// What tools/changed_tests.py prints, copied by the tester that runs it.
const CHANGES = {
  type: 'object',
  required: ['tests', 'support', 'markersRemoved'],
  properties: {
    tests: {
      type: 'array',
      items: { type: 'object', required: ['nodeid', 'change'], properties: { nodeid: { type: 'string' }, change: { type: 'string' }, from: { type: 'string' } } },
    },
    support: {
      type: 'array',
      items: { type: 'object', required: ['file', 'name', 'change'], properties: { file: { type: 'string' }, name: { type: 'string' }, change: { type: 'string' }, from: { type: 'string' } } },
    },
    markersRemoved: { type: 'array', items: { type: 'string' } },
  },
  description: 'the tests, support and markersRemoved tools/changed_tests.py printed, copied exactly; empty lists where it failed',
}

const TESTS_CHECK_SCHEMA = {
  type: 'object',
  required: ['collectionOk', 'collectionDetail', 'tests', 'otherFailures', 'uncommitted', 'lockTimedOut', 'environmentProblem', 'changes', 'changesError'],
  properties: {
    changes: CHANGES,
    changesError: { type: 'string', description: 'empty unless tools/changed_tests.py exited non-zero: what it printed on stderr' },
    lockTimedOut: { type: 'boolean', description: 'any pytest run exited 75: flock gave up waiting for the lock' },
    collectionOk: { type: 'boolean' },
    collectionDetail: { type: 'string' },
    tests: {
      type: 'array',
      items: {
        type: 'object',
        required: ['nodeid', 'failsWithRunxfail', 'realFailure', 'outcomeAsCommitted'],
        properties: {
          nodeid: { type: 'string' },
          failsWithRunxfail: { type: 'boolean' },
          realFailure: { type: 'string', description: 'the assertion or exception pytest reports under --runxfail, with its line; empty where it passed' },
          outcomeAsCommitted: { type: 'string', enum: ['xfailed', 'passed', 'failed', 'xpassed', 'error', 'not run'] },
        },
      },
    },
    otherFailures: { type: 'array', items: { type: 'string' } },
    uncommitted: { type: 'array', items: { type: 'string' }, description: 'every line git status --porcelain --untracked-files=all prints: work left uncommitted, new files included' },
    environmentProblem: { type: 'string', description: 'empty unless the host, not the code, is at fault' },
  },
}

const SUITE_SCHEMA = {
  type: 'object',
  required: ['exitCode', 'summary', 'failures', 'mypyClean', 'mypyErrors', 'xfailMarkersLeft', 'uncommitted', 'tmpFree', 'environmentProblem', 'log'],
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
    uncommitted: { type: 'array', items: { type: 'string' }, description: 'every line git status --porcelain --untracked-files=all prints: work left uncommitted, new files included' },
    tmpFree: { type: 'string' },
    environmentProblem: { type: 'string', description: 'empty unless the host, not the code, is at fault' },
    log: { type: 'string', description: 'the suite\'s log file' },
  },
}

// ---- the round loop's prompts ---------------------------------------------------------------

// Open: the builder still owes it. Pending: it still stops the stage passing, which a fix the
// builder claims does until its checker confirms it.
const materialOpen = f => f.material && (f.status === 'open' || f.status === 'disputed')
const materialPending = f => materialOpen(f) || (f.material && f.status === 'fixed')

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
    note: [
      f.ownerRuled ? 'the owner has ruled on your dispute: follow the decisions below' : '',
      f.ownerWants ? 'found minor, but the owner wants it made' : '',
      f.status === 'disputed' ? 'your dispute was not judged: fix it or dispute it again' : '',
      f.notFixedBecause ? `the ${LANE_NAMES[f.lane]} judged it not fixed: ${f.notFixedBecause}` : '',
    ].filter(Boolean).join('; '),
  }))
  // A branch may carry this issue's work before the stage's first round: from an earlier run
  // of the stage, or, after the owner refused the result at acceptance, from a whole earlier pass.
  const start = first && !previous
    ? `Start the stage from the plan. Read git -C ${worktree} log ${base}..HEAD first: where the branch already carries work for this issue, the plan is an amendment to it, and you build on what is there.`
    : `Earlier rounds have already worked on this branch: read git -C ${worktree} log ${base}..HEAD first. Fix each open material finding below in a commit of its own, or dispute it with evidence where you judge it wrong; fix the failures below; and finish whatever this stage still owes. Report every finding id you fixed or disputed.`
  const answered = !(first && previous) ? ''
    : previous.status === 'question' ? ' The last run stopped on questions for the owner. Their answers are in the decisions below, and bind you.'
      : previous.status === 'passed' ? ' The owner reviewed the last run\'s result at its gate and asked for changes: those in the decisions below, and any finding below that the owner wants made. Make them: they bind you, and this stage owes them until they are done.'
        : ''
  return `You are the builder for ${ISSUE}: ${STAGE_NAME}, round ${k}.

${WHERE}

${stage === 'tests' ? TESTS_JOB : BUILD_JOB}${DESIGN_PASS}

${start}${answered}

${BUILDER_RULES}${section('The approved plan', plan)}${section('The checks the plan passed', ARGS.checks)}${section('What a league should see once it lands', ARGS.criteria)}${section('The owner\'s decisions and answers, which bind you', ARGS.decisions)}${section('Rules cited to you by the product owner and the issue reviewer', citations)}${section('Open material findings', open)}${section('Failing tests, type errors and other problems from the last round', lastFailures)}`
}

const TESTS_WRITTEN = 'The tests the builder changed, each under its label, with the scenario and expectation it gives the owner. One marked alreadyPasses passes already: it is unmarked and must pass. A deleted one is gone, and says why. Every other one is marked xfail(strict=True) and must fail for the reason the plan gives'
const SUPPORT_WRITTEN = 'The fixtures, helpers, values and files under tests/ the builder changed, each under its label'
const COPY_QUESTION = 'giving each answer or escalation the ref of every question it settles, copying the question word for word into answers[].question, and framing an escalation for the owner as your instructions say'

const issuePrompt = (k, questions, testReport, tests, support) => `Job 3 — review a round of the branch. ${shared(k, 'issue')} The modules: ${modules.join(', ')}; their design files: ${DESIGN_LIST}. Settle each engineering question below by citing a written rule in answers[], or escalate it in escalations[], ${COPY_QUESTION}; where a rule you cite means the work must change, also add a material finding saying what. Pass every business question you meet to raised[], untouched. List in designDocsChanged every file under docs/design/ the branch changes since its base. Leave summary empty.${section('The approved plan', plan)}${section('The checks the plan passed', ARGS.checks)}${section('The owner\'s decisions and answers', ARGS.decisions)}${priorSection('issue')}${section('Engineering questions from the builder', questions)}${section(TESTS_WRITTEN, tests)}${section(SUPPORT_WRITTEN, support)}${section('The tester\'s report', testReport)}`

const summaryAsk = () => {
  if (stage === 'tests') return 'If you find nothing material and escalate nothing, write summary: for the owner to review before any code is written, in plain terms, each acceptance criterion and each spec rule the work touches, numbered, with the labels of the tests that pin it (A1, M1 and so on). The report lists every test with its scenario beside your summary, so do not repeat them. Otherwise leave summary empty.'
  if (kind === 'design-pass') return 'If you find nothing material and escalate nothing, write summary: a short confirmation that nothing a league sees has changed, and what you checked to be sure. Otherwise leave summary empty.'
  return 'If you find nothing material and escalate nothing, write summary: the acceptance summary your instructions describe. Otherwise leave summary empty.'
}

const productPrompt = (k, questions, testReport, tests, support) => `Job 2 — a round of the branch. ${shared(k, 'product')} The specs: ${SPEC_LIST}, and the core specification wherever the work touches core's rules. Answer each business question below by citing a written rule in answers[], or escalate it in escalations[], ${COPY_QUESTION}; where a rule you cite means the work must change, also add a material finding saying what. Pass every engineering question you meet to raised[], untouched. Leave designDocsChanged empty. ${summaryAsk()}${section('The approved plan', plan)}${section('What a league should see once it lands', ARGS.criteria)}${section('The owner\'s decisions and answers', ARGS.decisions)}${section('Rules cited so far in this work', citations)}${priorSection('product')}${section('Business questions from the builder', questions)}${section(TESTS_WRITTEN, tests)}${section(SUPPORT_WRITTEN, support)}${section('The tester\'s report', testReport)}`

const testsTesterPrompt = (k, tests) => `You check the tests changed in round ${k} of the tests stage for issue #${issue}, in ${worktree}. You change nothing: no edits, no commits, no installs, and nothing on GitHub.

1. What is committed: list every line git -C ${worktree} status --porcelain --untracked-files=all prints, in uncommitted. The tests must be committed to count.
2. Collection: pytest tests/ --collect-only -q must exit 0.
3. The real failures: pytest <every nodeid below> -q --runxfail --tb=short. Each test not marked alreadyPasses must fail; for each, give the failure pytest reports: the assertion or exception, and its line. A test marked alreadyPasses must pass here too.
4. As committed: pytest <the files holding them> -q -rxX, and give each listed test's outcome. A test not marked alreadyPasses must be reported xfailed, and one marked alreadyPasses must pass. Nothing else in those files may fail, and nothing may XPASS.
5. If anything fails across the board, run df -h /tmp: where it is full or nearly, report environmentProblem. Set lockTimedOut where any run exited 75.
${tests.length ? '' : 'Every change this round is a deletion or to support alone, so there is no test to run: skip steps 3 and 4.\n'}6. What the branch changes under tests/: run ${CHANGED_TESTS(base)}, with a Bash timeout of 600000 ms, and copy the tests, support and markersRemoved it prints into changes, exactly, leaving nothing out. Where it exits non-zero, put what it printed on stderr in changesError, and leave the lists in changes empty.

Name any log file /tmp/work-issue-${issue}-tests-r${k}-<step>.log.

${RUN_PYTEST}${section('The tests the builder changed, to run in steps 3 and 4 (a deleted test is not among them)', tests)}`

const codePrompt = k => `Review round ${k} of the build. ${shared(k, 'code')} To confirm a behaviour, run python against this checkout's code, never the installed copy: cd ${worktree} && PYTHONPATH=src ${python} -c '...'. Put a question you cannot settle from the code in raised[], with its kind. Leave answers[], escalations[], designDocsChanged and summary empty.${section('The approved plan', plan)}${section('The owner\'s decisions and answers', ARGS.decisions)}${priorSection('code')}`

const designPrompt = (k, files) => `Job 2 — verify a drafted design file, limited to what this branch changes. ${ISSUE}. ${BRANCH_READ} ${NO_PYTEST} The branch changes ${files.join(', ')}. For each, read git -C ${worktree} diff ${base}...HEAD -- <file>, and the file in full for context, and hold the changed and added text to your seven checks. Judge the change against what .claude/skills/architecture-review/SKILL.md (Phase 9) and .claude/skills/design-review/SKILL.md (Phases 8 and 10) hold a design file to, against .claude/skills/architecture-review/python-practices.md, and against the owner's decisions below. Those phases also tell the main session how to run a review; that part is not yours, and you run no agent. Report each failure as a finding with an id of the form design-${k}-<n>: material where a check fails on substance, not material where only the wording is at fault. Put any question in raised[]. Leave answers[], escalations[], designDocsChanged and summary empty.${section('The owner\'s decisions and answers', ARGS.decisions)}${priorSection('design')}`

const buildTesterPrompt = k => {
  const logFile = `/tmp/work-issue-${issue}-build-r${k}.log`
  return `You run the checks for round ${k} of the build for issue #${issue}, in ${worktree}. You change nothing: no edits, no commits, no installs, and nothing on GitHub.

1. Run df -h /tmp, and note in tmpFree what is free.
2. Start the whole suite detached, as below, with tests/ as the targets and ${logFile} as LOG.
3. While it runs, run the type check: cd ${worktree} && ${BIN}/mypy, with a Bash timeout of 600000 ms. Note every error.
4. Count the expected-failure markers left: git -C ${worktree} grep -n -F 'reason="#${issue}:' -- tests/, and report how many lines it finds. List every line git -C ${worktree} status --porcelain --untracked-files=all prints, in uncommitted.
5. Wait for the suite, as below, until its exit code appears.
6. Read the outcome: the exit code from ${logFile}.exit; pytest's closing line, from tail -n 5 ${logFile}; and every line grep -E '^(FAILED|ERROR)' ${logFile} finds, each with the reason pytest gives for it further up the log.
7. A failure spread across unrelated modules is a full /tmp until proved otherwise: run df -h /tmp again, and where it is full or nearly, say so in environmentProblem. Say so there too for an exit code of 75.

${RUN_PYTEST}`
}

// ---- the round loop's bookkeeping -----------------------------------------------------------

const addFindings = (lane, found) => {
  for (const f of found || []) {
    const id = ledger.has(f.id) ? `${f.id}-${ledger.size}` : f.id
    ledger.set(id, { ...f, id, lane, status: 'open' })
  }
}

// A checker's verdicts on its own earlier findings. Only a verdict closes a finding: a fix the
// builder claims stays pending until its checker confirms it, and a dispute it does not judge
// stays a dispute, which the builder is told. A dispute the checker rejects, whether it upholds
// the finding or judges it not fixed, goes to the owner. A checker that returned nothing judges
// nothing, and its findings wait for the next round.
const judge = (lane, result) => {
  if (!result) return
  const verdicts = new Map(result.prior.map(p => [p.id, p]))
  for (const f of ledger.values()) {
    if (f.lane !== lane || !f.material || !['open', 'fixed', 'disputed'].includes(f.status)) continue
    const v = verdicts.get(f.id)
    if (!v) continue
    // Only a dispute the builder made reaches the owner; any other verdict but fixed or accepted
    // means the finding stands, and its grounds go to the builder.
    if (v.status === 'fixed' || v.status === 'dispute-accepted') { f.status = 'closed'; f.notFixedBecause = '' }
    else if (f.status === 'disputed') { f.status = 'upheld'; f.upheldBecause = v.grounds }
    else { f.status = 'open'; f.notFixedBecause = v.grounds }
  }
}

// `finding` names the dispute, so the calling session can pass the owner's choice back in `rulings`.
const upheldQuestion = f => ({
  finding: f.id,
  kind: f.lane === 'product' ? 'business' : 'engineering',
  question: `The builder disputes a finding of the ${LANE_NAMES[f.lane]}'s (${f.id}), which upholds it: ${f.title}`,
  context: `The finding: ${f.why}\nThe builder: ${f.dispute}\nThe ${LANE_NAMES[f.lane]}: ${f.upheldBecause}`,
  options: [{ label: 'Fix it', meaning: f.fix }, { label: 'Leave it as built', meaning: 'the finding is dropped' }],
  recommendation: `The ${LANE_NAMES[f.lane]}'s grounds are above; this is the owner's call.`,
})

// ---- the tests stage's list of test changes, and the Gate 2 report --------------------------

const GROUPS = [
  { change: 'added', title: 'Added', prefix: 'A' },
  { change: 'modified', title: 'Modified', prefix: 'M' },
  { change: 'deleted', title: 'Deleted', prefix: 'D' },
  { change: 'moved', title: 'Moved', prefix: 'MV' },
]
// A parametrised test is one function, whatever cases it runs: it is compared without them.
const bareId = id => String(id || '').replace(/\[.*\]$/, '')
const fileOf = id => bareId(id).split('::')[0]
const supportKey = s => `${s.file}::${s.name}`
const filled = v => String(v === undefined || v === null ? '' : v).trim() !== ''
// Entries in report order: files sorted, and within a file as the builder listed them.
const byFile = (entries, fileOfEntry) => [...new Set(entries.map(fileOfEntry))].sort().map(f => [f, entries.filter(e => fileOfEntry(e) === f)])

// Every entry keeps a label for the whole run, so that the checkers, the product owner's summary
// and the report all name a test the same way.
const labelled = (tests, support) => {
  const out = []
  for (const g of [...GROUPS, { change: null, prefix: 'X' }]) {
    const group = tests.filter(t => g.change ? t.change === g.change : !GROUPS.some(x => x.change === t.change))
    let n = 0
    for (const [, entries] of byFile(group, t => fileOf(t.nodeid))) for (const t of entries) out.push({ ...t, label: `${g.prefix}${++n}` })
  }
  let n = 0
  const outSupport = []
  for (const [, entries] of byFile(support, s => s.file)) for (const s of entries) outSupport.push({ ...s, label: `S${++n}` })
  return { tests: out, support: outSupport }
}

// The builder's lists against what tools/changed_tests.py prints. The owner approves the tests
// from the lists, so a change they leave out is a change nobody approved, and one they describe
// without its scenario is a change approved blind.
const listProblems = (t, tests, support) => {
  if (t.changesError) return [`tools/changed_tests.py could not list what the branch changes under tests/: ${t.changesError}`]
  const problems = []
  const compare = (found, listed, keyOf, where) => {
    const mine = new Map(listed.map(x => [keyOf(x), x]))
    const theirs = new Map(found.map(x => [keyOf(x), x]))
    for (const [key, x] of theirs) {
      const w = mine.get(key)
      if (!w) problems.push(`${key} is ${x.change} on the branch, but ${where} does not list it`)
      else if (w.change !== x.change) problems.push(`${key} is listed as ${w.change}, but the branch has it ${x.change}`)
    }
    for (const [key, w] of mine) if (!theirs.has(key)) problems.push(`${key} is listed as ${w.change}, but the branch does not change it`)
  }
  compare(t.changes.tests, tests, x => bareId(x.nodeid), 'tests[]')
  compare(t.changes.support, support, supportKey, 'support[]')
  for (const w of tests) {
    const owed = ['scenario', 'expects', ...(w.change === 'modified' ? ['before'] : []), ...(w.change === 'deleted' ? ['why'] : [])]
    const missing = owed.filter(f => !filled(w[f]))
    if (missing.length) problems.push(`${w.nodeid} gives no ${missing.join(' and no ')}, and the owner approves the tests from these`)
  }
  for (const s of support) if (!filled(s.what)) problems.push(`${supportKey(s)} does not say what it now does`)
  return problems
}

// Against the lists the owner last saw at Gate 2: an entry that was not there, or whose
// description has changed since.
const DESCRIBED = ['scenario', 'expects', 'before', 'why', 'criterion', 'alreadyPasses', 'what', 'affects']
const sinceShown = (entry, before, keyOf) => {
  if (!shown) return ''
  const was = before.find(b => keyOf(b) === keyOf(entry))
  if (!was || was.change !== entry.change) return ' *(new since the last Gate 2)*'
  return DESCRIBED.some(f => JSON.stringify(was[f] || '') !== JSON.stringify(entry[f] || '')) ? ' *(changed since the last Gate 2)*' : ''
}

const counts = () => ({
  ...Object.fromEntries(GROUPS.map(g => [g.change, written.filter(t => t.change === g.change).length])),
  support: supportWritten.length,
})

// The Gate 2 report: every entry of both lists, none left out and none merged, for the calling
// session to write to a file of its own and put before the owner.
const gateReport = (test, summaryText) => {
  const lines = [
    `# Gate 2 — #${issue}: the tests`,
    '',
    `Every test this work adds, modifies, deletes or moves on \`${branch}\` since \`${base}\`, with the scenario each one tests. A test marked as an expected failure fails today because the behaviour is missing; one that passes already says so.`,
    '',
    `${GROUPS.map(g => `${written.filter(t => t.change === g.change).length} ${g.change}`).join(', ')}; ${supportWritten.length} supporting.`,
  ]
  const field = (name, value) => filled(value) ? [`  - *${name}:* ${value}`] : []
  const movedFrom = new Map(((test && test.changes && test.changes.tests) || []).filter(x => x.from).map(x => [bareId(x.nodeid), x.from]))
  const labelOf = new Map(written.map(t => [bareId(t.nodeid), t.label]))
  const others = written.filter(t => !GROUPS.some(g => g.change === t.change))
  for (const g of [...GROUPS, ...(others.length ? [{ change: null, title: 'Listed with a change the report does not know' }] : [])]) {
    const group = g.change ? written.filter(t => t.change === g.change) : others
    lines.push('', `## ${g.title} (${group.length})`)
    if (!group.length) { lines.push('', 'None.'); continue }
    for (const [file, entries] of byFile(group, t => fileOf(t.nodeid))) {
      lines.push('', `**\`${file}\`**`, '')
      for (const t of entries) {
        const from = movedFrom.get(bareId(t.nodeid))
        lines.push(
          `- **${t.label}** \`${String(t.nodeid).slice(file.length + 2)}\`${sinceShown(t, shown ? shown.tests : [], x => bareId(x.nodeid))}`,
          ...field('Scenario', t.scenario),
          ...field(g.change === 'deleted' ? 'Expected' : 'Expects', t.expects),
          ...field('Before', t.before),
          ...field('From', from ? `\`${from}\`` : ''),
          ...field(g.change === 'deleted' ? 'Why it goes' : 'Why', t.why),
          ...field('Criterion', t.criterion),
          ...(t.alreadyPasses ? ['  - *Passes already:* left unmarked'] : []),
        )
      }
    }
  }
  lines.push('', `## Supporting changes (${supportWritten.length})`, '')
  if (!supportWritten.length) lines.push('None.')
  for (const s of supportWritten) {
    const affects = (s.affects || []).map(id => labelOf.get(bareId(id)) || `\`${id}\``).join(', ')
    lines.push(`- **${s.label}** \`${supportKey(s)}\`, ${s.change}${sinceShown(s, shown ? shown.support : [], supportKey)}. ${filled(s.what) ? `${String(s.what).trim().replace(/[^.!?]$/, '$&.')}` : ''}${affects ? ` *Affects:* ${affects}.` : ''}`)
  }
  if (shown) {
    const gone = [
      ...shown.tests.filter(w => !written.some(t => bareId(t.nodeid) === bareId(w.nodeid) && t.change === w.change)).map(w => `\`${w.nodeid}\`, ${w.change}`),
      ...shown.support.filter(w => !supportWritten.some(s => supportKey(s) === supportKey(w) && s.change === w.change)).map(w => `\`${supportKey(w)}\`, ${w.change}`),
    ]
    if (gone.length) lines.push('', '## In the last Gate 2, and no longer in the list', '', ...gone.map(g => `- ${g}`))
  }
  lines.push('', '## What a league will see, and the tests that pin it', '', filled(summaryText) ? summaryText : '*The product owner wrote no summary. Ask for it, and put it here, before the gate.*')
  if (citations.length) lines.push('', '## Rules cited', '', ...citations.map(c => `- **${c.source}:** ${c.answer}`))
  return `${lines.join('\n')}\n`
}

const testsProblems = t => {
  if (t === undefined) return ['no test is changed yet']
  if (!t) return ['the tester returned nothing']
  const problems = hostProblem(t) ? [`the host: ${hostProblem(t)}`] : []
  if (!t.collectionOk) problems.push(`the suite does not collect: ${t.collectionDetail}`)
  for (const w of written) {
    if (w.change === 'deleted') continue
    const x = t.tests.find(r => r.nodeid === w.nodeid)
    if (!x) { problems.push(`${w.nodeid} was not run by the tester`); continue }
    if (w.alreadyPasses) {
      if (x.outcomeAsCommitted !== 'passed') problems.push(`${w.nodeid} pins behaviour already built, so it must pass, but was ${x.outcomeAsCommitted}`)
      continue
    }
    if (!x.failsWithRunxfail) problems.push(`${w.nodeid} passes already under --runxfail, so it pins nothing yet`)
    if (x.outcomeAsCommitted !== 'xfailed') problems.push(`${w.nodeid} is ${x.outcomeAsCommitted} as committed, not xfailed`)
  }
  return [...problems, ...listProblems(t, written, supportWritten), ...t.otherFailures, ...t.uncommitted.map(u => `not committed: ${u}`)]
}

// A builder with no test changed yet has nothing for the tester to check. The tester runs no
// deleted test, and runs none at all where every change is a deletion: an empty list of targets
// would be the whole suite.
const reviewTests = async (k, questions) => {
  const run = written.filter(w => w.change !== 'deleted')
  const test = written.length || supportWritten.length
    ? await agent(testsTesterPrompt(k, run), { label: `tests:r${k}:tester`, phase: 'Review', effort: 'low', schema: TESTS_CHECK_SCHEMA })
    : undefined
  if (test === undefined) log(`Round ${k}: no test is changed yet, so the tester is not sent out.`)
  const [issueResult, productResult] = await parallel([
    () => agent(issuePrompt(k, questions.engineering, test, written, supportWritten), { label: `tests:r${k}:issue`, phase: 'Review', agentType: 'issue-reviewer', schema: REVIEW_SCHEMA }),
    () => agent(productPrompt(k, questions.business, test, written, supportWritten), { label: `tests:r${k}:product`, phase: 'Review', agentType: 'product-owner', schema: REVIEW_SCHEMA }),
  ])
  const problems = testsProblems(test)
  // A stage run again, as after a test change the build asked for, may add only tests the build
  // has already made pass: the first run alone must hold one that fails.
  const failing = written.filter(w => !w.alreadyPasses && w.change !== 'deleted')
  return { lanes: { issue: issueResult, product: productResult }, test, problems, green: !!test && !hostProblem(test) && (failing.length > 0 || !!previous) && !problems.length }
}

// The host, not the code: what the tester reports as such, and a lock flock gave up on.
const hostProblem = t => t ? (t.environmentProblem || (t.exitCode === 75 || t.lockTimedOut ? 'flock gave up waiting an hour for the test lock' : '')) : ''

const suiteProblems = t => {
  if (t === undefined) return ['the suite was not run: the builder was blocked and made no commit']
  if (!t) return ['the tester returned nothing']
  const problems = [...(hostProblem(t) ? [`the host: ${hostProblem(t)}`] : []), ...t.failures.map(f => `${f.test}: ${f.reason}`)]
  if (t.exitCode !== 0 && !t.failures.length) problems.push(`pytest exited ${t.exitCode}: ${t.summary}`)
  problems.push(...t.mypyErrors.map(e => `mypy: ${e}`))
  if (!t.mypyClean && !t.mypyErrors.length) problems.push('mypy reported errors')
  if (t.xfailMarkersLeft) problems.push(`${t.xfailMarkersLeft} expected-failure marker(s) naming #${issue} are left in tests/`)
  problems.push(...t.uncommitted.map(u => `not committed: ${u}`))
  return problems
}

// The four checkers run side by side, and the design verifier follows the issue reviewer once the
// branch has changed a design file. A checker left undefined was not due this round; one that is
// null returned nothing.
const reviewBuild = async (k, built, questions) => {
  const quiet = built.blocked && !built.commits.length
  if (quiet) log(`Round ${k}: the builder is blocked and made no commit, so the suite is not run.`)
  // The tester goes first: the suite is the longest wait, and the Pi runs two agents at once.
  const [test, issueAndDesign, code, product] = await parallel([
    () => quiet ? Promise.resolve(undefined) : agent(buildTesterPrompt(k), { label: `build:r${k}:tester`, phase: 'Review', effort: 'low', schema: SUITE_SCHEMA }),
    () => agent(issuePrompt(k, questions.engineering, null), { label: `build:r${k}:issue`, phase: 'Review', agentType: 'issue-reviewer', schema: REVIEW_SCHEMA })
      .then(async issueResult => {
        for (const file of issueResult ? issueResult.designDocsChanged : []) designFiles.add(file)
        if (!designFiles.size) return { issue: issueResult, design: undefined }
        const design = await agent(designPrompt(k, [...designFiles].sort()), { label: `build:r${k}:design`, phase: 'Review', agentType: 'design-verifier', schema: REVIEW_SCHEMA })
        return { issue: issueResult, design }
      }),
    () => agent(codePrompt(k), { label: `build:r${k}:code`, phase: 'Review', agentType: 'code-reviewer', schema: REVIEW_SCHEMA }),
    () => agent(productPrompt(k, questions.business, null), { label: `build:r${k}:product`, phase: 'Review', agentType: 'product-owner', schema: REVIEW_SCHEMA }),
  ])
  const green = !!test && !hostProblem(test) && test.exitCode === 0 && test.mypyClean && test.xfailMarkersLeft === 0 && !test.uncommitted.length
  return {
    lanes: { issue: issueAndDesign ? issueAndDesign.issue : null, code, product, design: issueAndDesign ? issueAndDesign.design : undefined },
    test,
    problems: suiteProblems(test),
    green,
  }
}

// ---- the round loop -------------------------------------------------------------------------

const TRIAGE_CONTEXT = `${section('The approved plan', plan)}${section('The owner\'s decisions and answers', ARGS.decisions)}`
// Two wordings of one question count as the same when they differ only in case and spacing.
const sameQuestion = text => String(text).toLowerCase().replace(/\s+/g, ' ').trim()

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
  if (stage === 'tests') ({ tests: written, support: supportWritten } = labelled(built.tests, built.support || []))
  else written = [...written, ...built.tests]
  // A claim counts only on a finding the builder still owes: a settled or minor one stays as it is.
  for (const x of built.fixed) { const f = ledger.get(x.id); if (f && materialOpen(f)) { f.status = 'fixed'; f.fixedIn = x.commit; f.notFixedBecause = '' } }
  for (const x of built.disputed) { const f = ledger.get(x.id); if (f && materialOpen(f)) { f.status = 'disputed'; f.dispute = x.reason; f.notFixedBecause = '' } }
  if (stage === 'tests' && !previous && built.planComplete && !built.tests.some(t => !t.alreadyPasses && t.change !== 'deleted')) {
    status = 'failed'
    failure = 'the builder wrote no failing test: the tests stage is only for a plan that names one'
    break
  }

  phase('Review')
  // Each question of the builder's gets a ref, which the checker that settles it carries on its
  // answer or escalation: a question counts as heard by its ref, since an escalation is reframed
  // for the owner and need not repeat the builder's words.
  const builderQuestions = built.questions.map((q, i) => ({ ...q, ref: `b${k}-${i + 1}` }))
  const questions = {
    business: builderQuestions.filter(q => q.kind === 'business'),
    engineering: builderQuestions.filter(q => q.kind !== 'business'),
  }
  const reviewed = stage === 'tests' ? await reviewTests(k, questions) : await reviewBuild(k, built, questions)
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
  const roundAnswers = got.flatMap(r => r.answers)
  citations.push(...roundAnswers)
  separateDefects.push(...got.flatMap(r => r.separateDefects))
  const roundEscalations = got.flatMap(r => r.escalations)
  // A checker that returned nothing answered none of the builder's questions routed to it: they
  // go to the owner, as a failed triage's do.
  if (reviewed.lanes.product === null) roundEscalations.push(...questions.business)
  if (reviewed.lanes.issue === null) roundEscalations.push(...questions.engineering)
  const raised = got.flatMap(r => r.raised).map((q, i) => ({ ...q, ref: refsIn(q)[0] || `r${k}-${i + 1}` }))
  const duplicates = []
  if (raised.length) {
    const handled = [...roundAnswers.map(a => a.question), ...roundEscalations.map(q => q.question)]
    const t = await triage(raised, `r${k}`, BRANCH_READ, TRIAGE_CONTEXT, handled)
    citations.push(...t.answers)
    roundAnswers.push(...t.answers)
    duplicates.push(...t.duplicates)
    roundEscalations.push(...t.escalations)
    for (const f of t.findings) addFindings(f.lane, [f])
  }
  const upheld = [...ledger.values()].filter(f => f.status === 'upheld' && !f.asked)
  for (const f of upheld) f.asked = true
  roundEscalations.push(...upheld.map(upheldQuestion))
  // A question of the builder's that no checker answered or put to the owner goes to the owner,
  // as a question routed to a checker that returned nothing does: it is never dropped.
  const heard = [...roundAnswers, ...roundEscalations, ...duplicates]
  const heardRefs = new Set(heard.flatMap(refsIn))
  const said = new Set(heard.filter(x => x.question).map(x => sameQuestion(x.question)))
  const unheard = builderQuestions.filter(q => !heardRefs.has(q.ref) && !said.has(sameQuestion(q.question)))
  if (unheard.length) log(`Round ${k}: ${unheard.length} question(s) of the builder's went unanswered, and go to the owner.`)
  roundEscalations.push(...unheard.map(q => ({ ...q, unframed: true })))

  lastFailures = [...reviewed.problems, ...(built.clean ? [] : ['the builder left uncommitted changes in the checkout'])]
  const open = [...ledger.values()].filter(materialPending)
  rounds.push({ round: k, commits: built.commits.map(c => c.subject), openMaterial: open.length, green: reviewed.green, questions: roundEscalations.length, dead })
  log(`Round ${k}: ${built.commits.length} commit(s); ${open.length} material finding(s) open; ${stage === 'tests' ? 'tests' : 'suite'} ${reviewed.green ? 'green' : 'not green'}; ${roundEscalations.length} question(s) for the owner.`)

  // A host problem found in the same round is named beside the questions, to be repaired first.
  if (roundEscalations.length) { status = 'question'; escalations = roundEscalations; failure = hostProblem(reviewed.test) ? `the host, not the code: ${hostProblem(reviewed.test)}` : ''; break }
  if (hostProblem(reviewed.test)) { status = 'failed'; failure = `the host, not the code: ${hostProblem(reviewed.test)}`; break }
  if (dead.length) { log(`No result from: ${dead.join(', ')}. The round cannot pass; the next one runs them again.`); continue }
  // A round in which the builder asked anything cannot pass: an answer, even one citing a rule,
  // reaches the builder only in the next round.
  if (reviewed.green && !open.length && built.planComplete && !built.blocked && built.clean && !built.questions.length) {
    const product = reviewed.lanes.product
    summary = product.summary
    // The product owner found nothing but left its summary out: ask again. Whatever else the
    // second call finds counts, so a finding or a question it raises stops the pass.
    if (!summary) {
      const asked = await agent(`${productPrompt(k, [], reviewed.test, stage === 'tests' ? written : null, stage === 'tests' ? supportWritten : null)}\n\nThe other checkers found nothing in this round. Write summary now.`, { label: `${stage}:r${k}:summary`, phase: 'Review', agentType: 'product-owner', schema: REVIEW_SCHEMA })
      if (asked) {
        addFindings('product', asked.findings)
        citations.push(...asked.answers)
        separateDefects.push(...asked.separateDefects)
        const late = [...asked.escalations]
        if (asked.raised.length) {
          const t = await triage(asked.raised.map((q, i) => ({ ...q, ref: refsIn(q)[0] || `r${k}s-${i + 1}` })), `r${k}s`, BRANCH_READ, TRIAGE_CONTEXT, [...roundAnswers, ...asked.answers, ...asked.escalations].map(x => x.question))
          citations.push(...t.answers)
          late.push(...t.escalations)
          for (const f of t.findings) addFindings(f.lane, [f])
        }
        const record = rounds[rounds.length - 1]
        record.openMaterial = [...ledger.values()].filter(materialPending).length
        record.questions = late.length
        if (late.length) { status = 'question'; escalations = late; break }
        if (record.openMaterial) continue
        summary = asked.summary
      }
      if (!summary) log('The product owner wrote no summary. The calling session asks the product-owner agent for it before the gate.')
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
  support: supportWritten,
  ...(stage === 'tests' ? { shown, counts: counts(), report: gateReport(lastTest, summary) } : {}),
  lastTest,
  lastFailures,
  openMaterial: [...ledger.values()].filter(materialPending),
  minor: [...ledger.values()].filter(f => !f.material && f.status === 'open'),
  citations,
  separateDefects,
  commits,
  ledger: [...ledger.values()],
  designFiles: [...designFiles].sort(),
}
