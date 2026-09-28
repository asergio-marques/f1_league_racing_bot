export const meta = {
  name: 'work-issue',
  description: 'Work one approved issue in stages, each ending at a gate the owner decides: check the plan against the architecture, the design files and the specs; make every test change, each with the scenario it tests, for the owner to approve before any code is written; then build, review and test until they pass',
  whenToUse: 'Run by the fix-issue, fix-issues and design-review skills, one stage per run. Requires args {stage, issue, plan, modules, ...}; the check at the head of the script says what each stage needs. Each role runs on the model and effort the script sets for it by default, which args models and efforts override. A check of an amended plan takes the last check result as previous. The check stage is read-only. The tests and build stages commit on the given branch in the given checkout, and never push or touch GitHub.',
  phases: [
    { title: 'Check', detail: 'architecture and design (issue-reviewer), spec and acceptance (product-owner), in parallel' },
    { title: 'Tests', detail: 'the builder makes every test change the work needs, those failing marked as expected to fail, and lists each with its scenario' },
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
// reviewer's.
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
// Only the design files that exist: the design-review skill adds a module here in the commit that
// lands its file. A check whose modules have none runs no design agent, since there is nothing to
// hold the plan to beyond architecture.md, which the architecture check already covers.
const DESIGN_FILES = {
  steward: 'docs/design/steward_module.md',
}

// Each role's model and effort. Every model is set explicitly, so that a session on another model
// still verifies with Opus. Sonnet runs the build's builder, which implements an approved plan
// against tests that already exist, and whose every round the suite, mypy and three or four Opus
// reviewers check; and the tester, which runs commands and copies their output, at low effort.
// Opus runs every other role: the tests stage's builder, whose tests everything after it is held
// to, the checkers and reviewers, and triage, each open-ended judgement that nothing later would
// catch. The next issue's per-agent figures and findings confirm the Sonnet builder, or put it back
// on Opus where it costs more rounds than it saves.
const ROLE_DEFAULTS = {
  testsBuilder: { model: 'opus' },
  builder: { model: 'sonnet' },
  issue: { model: 'opus' },
  code: { model: 'opus' },
  product: { model: 'opus' },
  design: { model: 'opus' },
  tester: { model: 'sonnet', effort: 'low' },
  triage: { model: 'opus' },
}
// fable is left out by the owner's ruling, and is refused like any model not listed.
const MODELS = ['opus', 'sonnet', 'haiku']
const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']

const USAGE = `work-issue requires args {stage, issue, plan, modules}. stage is check, tests or build; modules lists the modules the plan touches, from ${Object.keys(SPECS).join(', ')}. check also needs commit, the commit the plan was drafted at, and takes worktree and base when it checks an amended plan against a branch already built, and previous, the last check result for this issue, when it checks an amended plan at all. tests and build need worktree and python (absolute paths), branch and base, and take criteria, checks, decisions, citations, previous, rulings, kind ("fix" or "design-pass"), maxRounds, which may only lower what a run takes, and roundBudget, the rounds a whole stage may take across its runs (tests 3, build 4 unless the owner raises it). build takes testsHead, the commit at which the owner approved the tests at Gate 2: given it, the build may change no test after it but to remove the issue's markers. Every stage takes models and efforts, each {role: value}, overriding the model or the effort of a role: the roles are ${Object.keys(ROLE_DEFAULTS).join(', ')}; a model is ${MODELS.join(', ')}, and an effort ${EFFORTS.join(', ')}.`

if (!ARGS || !['check', 'tests', 'build'].includes(ARGS.stage) || !ARGS.issue || !ARGS.plan || !Array.isArray(ARGS.modules) || !ARGS.modules.length) {
  throw new Error(USAGE)
}
const unknown = ARGS.modules.filter(m => !SPECS[m])
if (unknown.length) throw new Error(`Unknown module ${unknown.join(', ')}. ${USAGE}`)
// `models` and `efforts` override the defaults above, role by role, and are checked before any agent
// runs.
for (const [name, allowed] of [['models', MODELS], ['efforts', EFFORTS]]) {
  const given = ARGS[name] || {}
  if (typeof given !== 'object' || Array.isArray(given)) throw new Error(`${name} is {role: value}. ${USAGE}`)
  const wrong = Object.entries(given).filter(([role, v]) => !ROLE_DEFAULTS[role] || !allowed.includes(v))
  if (wrong.length) throw new Error(`${name} names ${wrong.map(([role, v]) => `${role}: ${JSON.stringify(v)}`).join(', ')}, which is not a role or not a value it takes. ${USAGE}`)
}
const settingsFor = role => {
  const model = (ARGS.models || {})[role] || ROLE_DEFAULTS[role].model
  const effort = (ARGS.efforts || {})[role] || ROLE_DEFAULTS[role].effort
  return effort ? { model, effort } : { model }
}

const { stage, plan, modules } = ARGS
const issue = String(ARGS.issue).replace(/^#/, '')
const kind = ARGS.kind || 'fix'
if (!['fix', 'design-pass'].includes(kind)) throw new Error(`kind must be "fix" or "design-pass". ${USAGE}`)
if (stage === 'check' && !ARGS.commit) throw new Error(`The check stage needs commit. ${USAGE}`)
if (ARGS.previous && ARGS.previous.stage !== stage) throw new Error(`previous is a ${ARGS.previous.stage} result, and this run is the ${stage} stage.`)
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
const DESIGN_LIST = modules.map(m => `${m}: ${DESIGN_FILES[m] || 'none yet, so architecture.md alone governs it'}`).join('; ')
const DESIGN_PASS = kind === 'design-pass'
  ? '\n\nThis is a design pass\'s correction, not a fix: it must change nothing a league sees.'
  : ''

// ---- schemas --------------------------------------------------------------------------------

const EVIDENCE = { type: 'array', items: { type: 'string' }, description: 'file:line, or a command and its output' }

const QUESTION = {
  type: 'object',
  required: ['kind', 'question', 'context', 'options', 'recommendation', 'stops'],
  properties: {
    stops: { type: 'boolean', description: 'true only where the answer changes what a league can do, changes or contradicts a written rule, or cannot be undone (a schema, data a league relies on); false for wording, a log line\'s form, naming and any other call a later change can reverse, which the work then takes on your recommendation until the owner overrules it' },
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

// A question stops the stage only where its answer changes what a league can do, changes or
// contradicts a written rule, or cannot be undone. Any other, a reversible call with a
// recommendation, is taken on that recommendation and listed at the next gate, where the owner
// overrules what they will: stopping the stage for each one cost a run, a Gate 2 and hours apiece.
// A question that does not say, one no checker framed, an upheld dispute and one with no
// recommendation to take all stop, since asking the owner is the safe way to fail.
const stopsTheStage = q => q.stops !== false || !!q.unframed || !!q.finding || !String(q.recommendation || '').trim()

// The issue and the approved plan fix the scope. What a checker finds that the work does not need
// (the same fault elsewhere, a neighbouring gap, a rule the issue does not name) is drafted for the
// tracker rather than asked: an answer to it would widen the work past what the owner approved, and
// each widening raises more questions of its own.
const FOLLOW_UPS = {
  type: 'array',
  items: {
    type: 'object',
    required: ['title', 'kind', 'why', 'evidence'],
    properties: {
      title: { type: 'string' },
      kind: { type: 'string', enum: ['bug', 'feature-request', 'tech-debt'] },
      why: { type: 'string', description: 'what a league sees, or what it costs, in plain terms' },
      evidence: EVIDENCE,
    },
  },
  description: 'what the plan does not need, drafted for the tracker rather than asked',
}

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
  required: ['rulesTouched', 'breachesRemoved', 'breachesAdded', 'notYetBuilt', 'planChanges', 'questions', 'raised', 'followUps', 'notes'],
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
    followUps: FOLLOW_UPS,
    notes: { type: 'array', items: { type: 'string' } },
  },
}

const DESIGN_SCHEMA = {
  type: 'object',
  required: ['modules', 'questions', 'raised', 'followUps', 'notes'],
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
    followUps: FOLLOW_UPS,
    notes: { type: 'array', items: { type: 'string' } },
  },
}

const PRODUCT_PLAN_SCHEMA = {
  type: 'object',
  required: ['specRules', 'criteria', 'questions', 'citations', 'documentsOwed', 'raised', 'followUps', 'notes'],
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
    questions: { ...QUESTIONS, description: 'for the owner: every rule the plan would change, every spec silent, ambiguous or at odds with the code, where the plan cannot be built without the answer' },
    citations: { type: 'array', items: ANSWER, description: 'questions the plan raises that a written rule settles' },
    documentsOwed: {
      type: 'array',
      items: { type: 'object', required: ['document', 'section', 'why'], properties: { document: { type: 'string' }, section: { type: 'string' }, why: { type: 'string' } } },
    },
    raised: { ...QUESTIONS, description: 'engineering questions met on the way, passed on to the issue reviewer untouched' },
    followUps: FOLLOW_UPS,
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
    `${job} ${ISSUE}. ${where}\n\nAnswer each question below as your instructions say: cite a written rule in answers[], or escalate it to the owner in escalations[], marked stops as your instructions say, keeping the question's ref on what settles it, and copying the question word for word into answers[].question. A question that asks the same as one already handled, listed below, goes in duplicates[] with its ref, and is neither answered nor escalated. Where a cited rule means the work must change, add a material finding saying what, in findings[], with an id of the form ${who}-${tag}-t<n>. Never run pytest.${context}${section('Questions', qs)}${section('Already answered or put to the owner', handled)}`,
    { ...settingsFor('triage'), label: `triage:${tag}:${who}`, phase: 'Triage', agentType: who === 'product' ? 'product-owner' : 'issue-reviewer', schema: TRIAGE_SCHEMA },
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
  const head = `${ISSUE}. The plan was drafted at commit ${commit}.${branchNote} The modules it touches: ${modules.join(', ')}.${DESIGN_PASS} The issue and this plan fix the scope: ask only what the plan cannot be built without. The same fault elsewhere, a neighbouring gap or a rule the issue does not name is not a question: draft it in followUps[] for the tracker.`
  const context = `${section('The plan', plan)}${section('The owner\'s decisions so far', ARGS.decisions)}`
  // A re-check of an amended plan gives each checker the plan as it last checked it and its own
  // earlier result, and asks it to judge what the amendment changes: a checker starting over re-reads
  // everything the amendment left alone. A checker whose earlier result was lost, or a check with no
  // earlier plan recorded, checks in full. A question the owner has answered since is not carried
  // over, even where the answer lands in the decisions alone and leaves the plan's text as it was.
  const earlier = ARGS.previous || null
  const amended = lane => {
    const was = earlier && earlier.plan ? earlier[lane] : null
    if (!was) return ''
    return `${section('This plan amends one checked before. The plan as it was then checked', earlier.plan)}${section('Your earlier result on it', was)}\n\nJudge what the amendment changes: carry over each entry of your earlier result that the amendment leaves as it was, and re-examine each entry it touches, checking in full whatever it adds. A question of your earlier result that the owner's decisions above answer is settled, and is not carried over, whether or not the amendment changes the plan's text.`
  }

  phase('Check')
  log(`Checking the plan for #${issue} against the architecture, the design files (${DESIGN_LIST}) and the specs (${SPEC_LIST}).`)
  // With no design file among the modules there is nothing for a design agent to check: the result
  // it would give is written here instead, and says so.
  const noDesignFile = !modules.some(m => DESIGN_FILES[m])
  if (noDesignFile) log('No module the plan touches has a design file yet, so no design agent runs.')
  const [architecture, design, product] = await parallel([
    () => agent(`Job 1 — check a plan against the architecture. ${head}${context}${amended('architecture')}`,
      { ...settingsFor('issue'), label: 'check:architecture', phase: 'Check', agentType: 'issue-reviewer', schema: ARCHITECTURE_SCHEMA }),
    () => noDesignFile
      ? Promise.resolve({
        modules: modules.map(m => ({ module: m, designFile: 'none', exists: false, sectionsTouched: [], designChanges: [] })),
        questions: [],
        raised: [],
        followUps: [],
        notes: ['No module the plan touches has a design file yet, so no design agent ran: each is held to docs/design/architecture.md alone, which the architecture check covers, and no design document is owed.'],
      })
      : agent(`Job 2 — check a plan against the design files. ${head} The design file for each: ${DESIGN_LIST}.${context}${amended('design')}`,
        { ...settingsFor('issue'), label: 'check:design', phase: 'Check', agentType: 'issue-reviewer', schema: DESIGN_SCHEMA }),
    () => agent(`Job 1 — a plan. ${head} The specs: ${SPEC_LIST}, and the core specification wherever the plan touches core's rules.${context}${amended('product')}`,
      { ...settingsFor('product'), label: 'check:product', phase: 'Check', agentType: 'product-owner', schema: PRODUCT_PLAN_SCHEMA }),
  ])
  const failed = [['architecture', architecture], ['design', design], ['product', product]].filter(([, r]) => !r).map(([k]) => k)
  if (failed.length) log(`No result for: ${failed.join(', ')}. Resume the run before relying on the check.`)

  const raised = [architecture, design, product].filter(Boolean).flatMap(r => r.raised).map((q, i) => ({ ...q, ref: `c${i + 1}` }))
  const triaged = raised.length
    ? await triage(raised, 'check', `The plan was drafted at commit ${commit}.${branchNote}`, context,
      [...(product ? product.questions : []), ...(architecture ? architecture.questions : []), ...(design ? design.questions : [])].map(q => q.question))
    : { answers: [], escalations: [], duplicates: [], findings: [] }

  const asked = [
    ...(product ? product.questions : []),
    ...(architecture ? architecture.questions : []),
    ...(design ? design.questions : []),
    ...triaged.escalations,
  ]
  // A reversible call is assumed on its recommendation and listed in the plan for the owner to
  // overrule at Gate 1, rather than asked on its own.
  const questions = asked.filter(stopsTheStage)
  const assumed = asked.filter(q => !stopsTheStage(q))
  // Every spec rule the plan does not simply follow is the owner's to settle; the calling session
  // makes sure each one reaches them as a question.
  const specRulesToSettle = product ? product.specRules.filter(r => r.status !== 'follows') : []
  log(`Questions for the owner: ${questions.length}. Assumed on a recommendation: ${assumed.length}. Spec rules to settle: ${specRulesToSettle.length}. Breaches the plan would add: ${architecture ? architecture.breachesAdded.length : '?'}.`)
  return {
    stage,
    issue,
    commit,
    // The plan checked, for a re-check of its amendment to be given.
    plan,
    architecture,
    design,
    product,
    questions,
    assumed,
    specRulesToSettle,
    citations: [...(product ? product.citations : []), ...triaged.answers],
    planChanges: [...(architecture ? architecture.planChanges : []), ...triaged.findings.map(f => f.fix)],
    // Drafts for the tracker, shown at Gate 1 beside the plan, never put to the owner as questions.
    followUps: [['architecture', architecture], ['design', design], ['product', product]]
      .flatMap(([lane, r]) => ((r && r.followUps) || []).map(f => ({ ...f, lane }))),
    failed,
  }
}

// ---- the round loop, shared by the tests and build stages -----------------------------------

const { worktree, python, branch, base } = ARGS
// The commit at which the owner approved the tests at Gate 2. From it the build changes no test but
// to remove the issue's markers, the ratchet lines the plan names and import lines: any other test
// change it needs goes to the owner as a proposal, and the tests stage makes it.
const testsHead = stage === 'build' ? ARGS.testsHead || (ARGS.previous && ARGS.previous.testsHead) || null : null
const BIN = python.slice(0, python.lastIndexOf('/'))
// Every round of a stage counts against its budget, across all its runs: a stage run again after
// the owner's answers carries on from previous.lastRound instead of starting a fresh allowance, as
// it did when #442's tests stage reached its seventh round over five runs. A stage that spends its
// budget stops as `capped`, and the owner decides what next; only they raise it, through
// roundBudget. maxRounds may lower what one run takes, never raise it.
const ROUND_BUDGET = { tests: 3, build: 4 }
if (ARGS.roundBudget !== undefined && !(Number.isInteger(Number(ARGS.roundBudget)) && Number(ARGS.roundBudget) > 0)) throw new Error(`roundBudget is a whole number of rounds. ${USAGE}`)
const LANE_NAMES = { issue: 'issue reviewer', code: 'code reviewer', product: 'product owner', design: 'design verifier' }

// A stage that stopped for the owner is run again with its last result as `previous` and the
// owner's answers in `decisions`: its rounds, findings and citations carry on where it stopped.
const previous = ARGS.previous || null
const offset = previous ? previous.lastRound : 0
const stageBudget = Number(ARGS.roundBudget) || ROUND_BUDGET[stage]
const left = Math.max(0, stageBudget - offset)
const maxRounds = Math.min(left, Number(ARGS.maxRounds) || left)
const ledger = new Map((previous ? previous.ledger : []).map(f => [f.id, { ...f }]))
// Rules cited in an earlier stage, such as the tests stage's for the build, arrive in `citations`.
// Both the run's own and those passed in: a build resumed after a tests stage run again is given
// that run's citations, which its own `previous` never saw.
const citations = [...(previous ? previous.citations : []), ...(ARGS.citations || [])]
  .filter((c, i, all) => all.findIndex(d => d.question === c.question && d.source === c.source && d.answer === c.answer) === i)
const commits = previous ? [...previous.commits] : []
// How far each checker has reviewed the branch: for each lane, how many of the stage's commits there
// were when it last returned a result. A later review is limited to the commits since, since an
// agent re-reading the whole branch every round pays for it every round. A lane that returned
// nothing is dropped from the map, and reviews in full next time, as one that has never run does.
const reviewedAt = { ...(previous && previous.reviewedAt ? previous.reviewedAt : {}) }
// The tests stage's lists as each of its reviewers, the issue reviewer and the product owner, was
// last given them. A reviewer is given every entry with its scenario the first time, and after that
// only the entries new or changed since in full, the rest in short: the long descriptions are most
// of a round's prompt, and re-reading them every round is paid for every round. A lane that
// returned nothing is dropped, and is given the whole list again.
const listSeen = { ...(previous && previous.listSeen ? previous.listSeen : {}) }
const separateDefects = previous ? [...previous.separateDefects] : []
let lastFailures = previous ? [...previous.lastFailures] : []
// The reversible calls taken on a checker's recommendation rather than asked (stopsTheStage): they
// bind the builder until the owner overrules them at the gate, and no checker asks them again.
const provisional = previous && previous.provisional ? [...previous.provisional] : []
const takeProvisionally = (qs, k) => provisional.push(...qs.map(q => ({ ref: q.ref || '', kind: q.kind, question: q.question, recommendation: q.recommendation, round: k })))
const PROVISIONAL = 'Calls taken on a checker\'s recommendation, for the owner to confirm or overrule at the gate'
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

const STAGE_NAME = stage === 'tests' ? 'the tests stage, where every test change is made and no production code is added' : 'the build'
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
- Stay inside the approved plan. A separate defect you notice, or a change the work does not need, goes in separateDefects[], as a draft for the tracker; it is not made here.
- Commit at the plan's commit points, one change per commit. Stage every path by name, from git -C ${worktree} status --porcelain; never git add -A, git add . or git commit -a. Give each commit a one-line subject in lower case and the past tense, as the branch's history does, with no trailer of any kind.
- Move or rename a file with git mv, in a commit apart from any change to its content.
- Every change to production code carries its tests (CLAUDE.md, "Testing"). Before each commit, run the tests that cover what you changed, as below; before each commit that touches src/, run ${BIN}/mypy from ${worktree}. Do not run the whole suite: the round's tester does.
- Where the owner's decisions call for a change to a wip-spec, the README or a guide, that change is owed by this work: in the build, make it in a commit of its own, in the document's own voice.
- Never push, never touch GitHub, never file anything, and never pip install into the shared virtualenv.
- Where the plan, the owner's decisions and the rules cited to you do not settle something, return it as a question rather than guess: kind "business" for anything about what the bot does, what a league sees or what a spec says, and "engineering" for the rest. Carry on with whatever it does not block, and set blocked only where nothing is left that you can do.
- Finish with everything committed, new files included: git -C ${worktree} status --porcelain --untracked-files=all prints nothing.
- Work in a piece of the round, not the whole of it: carry out at most three of the plan's commit points, or of the findings this round owes, and once you have made some forty tool calls, stop at your next commit. Then finish as above, return planComplete false, and say in remaining what is left: another builder carries on from your commits.

${RUN_PYTEST}`

// A builder works a round in pieces, each a fresh agent carrying on from the last one's commits.
// An agent re-reads its whole conversation at every step, so one builder carrying a whole stage
// costs roughly the square of its steps; pieces of a few commit points keep each conversation
// short. The cap bounds a round whose builder never finishes: reaching it, the round is reviewed
// as it stands, and cannot pass, since the plan is not complete.
const MAX_PIECES = 8

// The command that lists what the branch changes under tests/, which the builder's lists must
// match entry for entry (tools/changed_tests.py).
const CHANGED_TESTS = from => `cd ${worktree} && ${python} tools/changed_tests.py --repo ${worktree} --base ${from}${stage === 'build' ? ` --issue ${issue}` : ''}`

const TESTS_JOB = `This is the tests stage. Make every change to tests/ that this work needs, and no production code at all:
- add the tests the plan says fail before the change;
- change each existing test the change alters, whether its expectation or a call the plan changes;
- delete each test the plan makes obsolete;
- change the fixtures, helpers and data under tests/ that these need.
The one exception is the architecture ratchet lines the plan names as removed: the build deletes each in the commit that removes its breach, and you leave them. After this stage the build may change no test, so anything the plan's change needs of tests/ is made here. Where the build has already begun and stopped to ask for a test change, the branch carries its commits: add yours on top, and still no production code.

Mark each new test, and each changed test that fails before the change, with @pytest.mark.xfail(strict=True, reason="#${issue}: <what is not yet true>"): the suite then stays green on every commit, and the test fails loudly the moment it passes unexpectedly. A failing case added to a parametrised test is marked on the case alone, as pytest.param(<values>, marks=pytest.mark.xfail(strict=True, reason="#${issue}: ...")), so that its passing cases do not XPASS; and a test in a class is marked on its own method, never through the class. A test that uses code the plan has not written yet imports it inside the test, so that its file still collects. A test that passes as committed, because it pins behaviour already built or because the build has already made it pass, is left unmarked. Run the tests both ways, as below: with --runxfail each marked test must fail, for the reason the plan gives; without it each marked test must be reported xfailed and each unmarked one must pass, and nothing else in their files may fail. Commit them before any production code the plan adds: as the first commit of this work, unless the plan places them otherwise or the build has already begun.

List in tests[] every test this work adds, changes, deletes or moves since ${base}, earlier rounds and runs included, and in support[] every fixture, helper, module-level value or file under tests/ that it adds, changes, deletes or moves. Both lists must match what ${CHANGED_TESTS(base)} prints, entry for entry, with its node ids, its names and its change for each, the architecture ratchet lists alone excepted: run it before you finish. An import or a patched path that a move rewrites is never a change; one that binds or patches something else is. The owner approves the tests from these lists before any code is written, so write each entry in plain terms, as a league manager would follow it:
- change: added, modified, deleted or moved.
- scenario: the concrete situation the test sets up and the action it takes: which drivers, seasons, rounds or records, in what state, and which command or call.
- expects: what it asserts.
- before: for a modified test, what it set up and expected until now; where only its wording or docstring changes, say so.
- why: for a deleted test, why it goes, and what pins its rule now if anything does; for a moved one, why it moves.
Where a list already stands, from an earlier round or run, it is given to you below under its labels: keep each entry's description word for word where its test is unchanged and nobody has asked for it to change, since the owner compares the list with the one they last saw. A label the owner's decisions use names the entry that carries it there.
- criterion: the acceptance criterion it pins, where there is one.
- alreadyPasses: true for a test left unmarked because it passes already.
Each entry in support[] gives the file and the name as the command prints them, the change, what it now does, and in affects the node ids of the tests that use it.`

const BUILD_JOB = `This is the build. Carry out the approved plan, commit point by commit point, in its order. The tests that pin the change are already on the branch, marked xfail(strict=True) with a reason naming #${issue} (git -C ${worktree} grep -n -F 'reason="#${issue}:' finds them): remove each marker in the commit that makes its test pass, never before, and list in tests[] every marker you removed, with change markerRemoved. By the end, none may be left.${testsHead ? ` The owner approved the tests at ${testsHead}, and from there you change nothing under tests/ but three things: those markers, removed; the ratchet lines the plan names as removed, each deleted in the commit that removes its breach; and the imports and patched paths that a move of the plan's rewrites, the names they bind or patch unchanged. Any other change to tests/ the build needs, whether a new test, a changed or deleted one, or a fixture, helper, value or file, is not yours to make: propose it in testChanges[], saying what it would test and why the build needs it, and carry on with whatever it does not block. The owner decides it, and the tests stage makes it. A finding whose fix is a test change is answered the same way, and stays open until then. The round's tester runs ${CHANGED_TESTS(testsHead)}, and any change it reports but those is sent back to you to revert.` : ''}`

// ---- the round loop's schemas ---------------------------------------------------------------

const BUILDER_SCHEMA = {
  type: 'object',
  required: ['onBranch', 'commits', 'planComplete', 'remaining', 'tests', 'support', 'testChanges', 'fixed', 'disputed', 'questions', 'blocked', 'clean', 'separateDefects', 'notes'],
  properties: {
    onBranch: { type: 'boolean', description: 'the checkout was on the expected branch' },
    commits: {
      type: 'array',
      items: { type: 'object', required: ['sha', 'subject'], properties: { sha: { type: 'string' }, subject: { type: 'string' } } },
      description: 'the commits you made, oldest first',
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
    testChanges: {
      type: 'array',
      items: {
        type: 'object',
        required: ['nodeid', 'change', 'scenario', 'expects', 'needed'],
        properties: {
          nodeid: { type: 'string', description: 'the test, or file::name for a fixture, helper, value or file' },
          change: { type: 'string', enum: ['added', 'modified', 'deleted', 'moved'] },
          scenario: { type: 'string', description: 'the concrete situation it would set up and the action it would take' },
          expects: { type: 'string', description: 'what it would assert' },
          before: { type: 'string', description: 'a modified test: what it sets up and expects now' },
          why: { type: 'string', description: 'a deleted test: why it would go' },
          needed: { type: 'string', description: 'why the build needs it' },
        },
      },
      description: 'the build: every test change it needs and has not made, for the owner; the tests stage: empty',
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
      description: 'a defect, or a change the work does not need, drafted for the tracker rather than made or asked',
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
      description: 'a defect, or a change the work does not need, drafted for the tracker rather than made or asked',
    },
    notes: { type: 'array', items: { type: 'string' } },
  },
}

// What tools/changed_tests.py prints, copied by the tester that runs it.
const CHANGES = {
  type: 'object',
  required: ['head', 'tests', 'support', 'markersRemoved'],
  properties: {
    head: { type: 'string', description: 'the head commit it printed; empty where it did not run or failed' },
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

// Where the reviewers have seen the list before, the tester also lists what has changed under tests/
// since, which decides the entries each is given in full again.
const TESTS_CHECK_SINCE_SCHEMA = {
  ...TESTS_CHECK_SCHEMA,
  required: [...TESTS_CHECK_SCHEMA.required, 'changedSince', 'changedSinceError'],
  properties: {
    ...TESTS_CHECK_SCHEMA.properties,
    changedSince: { ...CHANGES, description: 'what tools/changed_tests.py printed from the commit named in step 7, copied exactly; empty lists where it failed' },
    changedSinceError: { type: 'string', description: 'empty unless step 7\'s run of tools/changed_tests.py exited non-zero: what it printed on stderr' },
  },
}

const SUITE_SCHEMA = {
  type: 'object',
  required: ['exitCode', 'summary', 'failures', 'mypyClean', 'mypyErrors', 'xfailMarkersLeft', 'uncommitted', 'tmpFree', 'environmentProblem', 'log', 'changes', 'changesError'],
  properties: {
    changes: CHANGES,
    changesError: { type: 'string', description: 'empty unless tools/changed_tests.py exited non-zero: what it printed on stderr' },
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

// What a checker that has reviewed before reads this time. Earlier work the new commits make wrong
// stays in scope: each checker's duty covers the branch at its tip, not only its newest commits.
// So does earlier work the owner's decisions make wrong. A run carried on from an earlier one may
// be given decisions and answers its checkers never saw, and work built before them on the
// builder's own guess can conflict with them however untouched it is since: in the first round of
// such a run, each checker holds the whole branch to the decisions once.
const sinceReviewed = (k, lane) => {
  const at = reviewedAt[lane]
  if (at === undefined || at > commits.length) return ''
  const decided = previous && k === offset + 1
    ? ' This run carries on from an earlier one, and the owner\'s decisions and answers below may have grown since you last reviewed: earlier work they make wrong is in scope too, wherever on the branch it sits.'
    : ''
  if (at === commits.length) return ` Nothing has been committed since you last reviewed the branch: judge only your earlier findings and the questions given below.${decided}`
  if (!at) return ''
  const sha = commits[at - 1].sha
  return ` You reviewed the branch up to ${sha} already, in an earlier round. Read git -C ${worktree} log ${sha}..HEAD and git -C ${worktree} diff ${sha}..HEAD, and judge your earlier findings. Read the rest of the branch only to confirm something, and do not review it again; but a document, test or piece of code written earlier and made wrong by the new commits is still in scope. Where git does not know ${sha}, review the whole branch.${decided}`
}

// `whole` asks for a review of the whole branch, however far the lane has reviewed it: the product
// owner asked again for a summary it left out is a fresh agent that has read nothing of the branch,
// and must read all of it to sum it up and to find what its findings still stop.
const shared = (k, lane, whole = false) => `${ISSUE}, round ${k} of ${STAGE_NAME}. ${BRANCH_READ}${whole ? '' : sinceReviewed(k, lane)} ${NO_PYTEST} Give each new finding an id of the form ${lane}-${k}-<n>. The issue and the approved plan fix the scope: the same fault elsewhere, a neighbouring gap or a rule the issue does not name is neither a finding nor a question; draft it in separateDefects[] for the tracker.${DESIGN_PASS}`

// `earlier` holds the results of the round's pieces before this one: a later piece carries on from
// them, and leaves alone the findings they have already fixed or disputed.
const builderPrompt = (k, earlier = []) => {
  const first = k === offset + 1
  const answeredBefore = new Set(earlier.flatMap(p => [...p.fixed, ...p.disputed].map(x => x.id)))
  const open = [...ledger.values()].filter(f => materialOpen(f) && !answeredBefore.has(f.id)).map(f => ({
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
  const last = earlier[earlier.length - 1]
  const start = last
    ? `You carry on from an earlier builder of this round, which handed off before finishing: read git -C ${worktree} log ${base}..HEAD first, and build on what is there. Its commits, and those of any builder before it this round: ${earlier.flatMap(p => p.commits).map(c => `${c.sha} ${c.subject}`).join('; ')}. What it said remains: ${(last.remaining || []).join('; ') || 'nothing named'}.${answeredBefore.size ? ` The findings it has already fixed or disputed, which are left to the checkers: ${[...answeredBefore].join(', ')}.` : ''} Carry on from there. Report the commits you make, and the findings you fix or dispute, yourself alone: the earlier builders' are reported already${stage === 'tests' ? '; but your tests[] and support[] still cover the whole branch since its base, as below' : ''}.`
    : first && !previous
    ? `Start the stage from the plan. Read git -C ${worktree} log ${base}..HEAD first: where the branch already carries work for this issue, the plan is an amendment to it, and you build on what is there.`
    : `Earlier rounds have already worked on this branch: read git -C ${worktree} log ${base}..HEAD first. Fix each open material finding below in a commit of its own, or dispute it with evidence where you judge it wrong; fix the failures below; and finish whatever this stage still owes. Report every finding id you fixed or disputed.`
  const answered = !(first && previous) ? ''
    : previous.status === 'question' ? ` The last run stopped on questions for the owner. Their answers are in the decisions below, and bind you.${previous.testChanges && previous.testChanges.length ? ` The test changes you proposed went to the owner: each made is on the branch now, committed by the tests stage${testsHead ? ` by ${testsHead}` : ''}, and each refused is in the decisions below, to build without.` : ''}`
      : previous.status === 'passed' ? ' The owner reviewed the last run\'s result at its gate and asked for changes: those in the decisions below, and any finding below that the owner wants made. Make them: they bind you, and this stage owes them until they are done.'
        : ''
  return `You are the builder for ${ISSUE}: ${STAGE_NAME}, round ${k}${earlier.length ? `, piece ${earlier.length + 1}` : ''}.

${WHERE}

${stage === 'tests' ? TESTS_JOB : BUILD_JOB}${DESIGN_PASS}

${start}${answered}${stage === 'tests' ? section('The list as it stands, under its labels', written.length || supportWritten.length ? { tests: written, support: supportWritten } : '') : ''}

${BUILDER_RULES}${section('The approved plan', plan)}${section('The checks the plan passed', ARGS.checks)}${section('What a league should see once it lands', ARGS.criteria)}${section('The owner\'s decisions and answers, which bind you', ARGS.decisions)}${section('Rules cited to you by the product owner and the issue reviewer', citations)}${section(`${PROVISIONAL}: they bind you until the owner overrules them`, provisional)}${section('Open material findings', open)}${section('Failing tests, type errors and other problems from the last round', lastFailures)}`
}

const TESTS_WRITTEN = 'The tests the builder changed, each under its label, with the scenario and expectation it gives the owner. Name a test by its node id in a finding: a label can change before the gate. One marked alreadyPasses passes already: it is unmarked and must pass. A deleted one is gone, and says why. Every other one is marked xfail(strict=True) and must fail for the reason the plan gives'
const SUPPORT_WRITTEN = 'The fixtures, helpers, values and files under tests/ the builder changed, each under its label'
// The tests stage's lists as a reviewer is given them: in full where it has not seen them, and
// otherwise each entry new or changed since it last did in full, and the rest in short. An entry is
// compared as the Gate 2 report compares it, on what it says, and is changed too where the commits
// since change its code: a builder keeps an entry word for word where the test's meaning is
// unchanged, so its code can change under an unchanged entry, and the issue reviewer holds the code
// to the scenario, which the short form leaves out. `listTouched` holds the node ids and support
// keys whose code has changed since, as the round's tester found them; where that is not known,
// every entry is given in full. The tool reports a changed fixture, helper, value or file as support,
// and a test as changed only where its own code is: so where any support has changed since, every
// test entry is given in full, as any of them may use it and its scenario be held to what its setup
// now does.
let listTouched = null
const changedSince = (entry, before, keyOf) => {
  const was = before.find(b => keyOf(b) === keyOf(entry))
  return !was || was.change !== entry.change || DESCRIBED.some(f => described(was, f) !== described(entry, f))
}
const listFor = (lane, tests, support, whole = false) => {
  if (!tests) return ''
  const seen = listSeen[lane]
  if (!seen || whole || !listTouched) return `${section(TESTS_WRITTEN, tests)}${section(SUPPORT_WRITTEN, support)}`
  const testKey = t => bareId(t.nodeid)
  const touched = listTouched
  const newTests = tests.filter(t => touched.support.size || changedSince(t, seen.tests || [], testKey) || touched.tests.has(testKey(t)))
  const newSupport = support.filter(x => changedSince(x, seen.support || [], supportKey) || touched.support.has(supportKey(x)))
  const short = [
    ...tests.filter(t => !newTests.includes(t)).map(t => ({ label: t.label, nodeid: t.nodeid, change: t.change, ...(filled(t.criterion) ? { criterion: t.criterion } : {}) })),
    ...support.filter(x => !newSupport.includes(x)).map(x => ({ label: x.label, file: x.file, name: x.name, change: x.change })),
  ]
  return `${section(`${TESTS_WRITTEN}. You have reviewed the list before: these are the tests new or changed since, in their entries or in their code`, newTests)}${section(`${SUPPORT_WRITTEN}, new or changed since you last reviewed the list, in their entries or in their code`, newSupport)}${section('Unchanged since you last reviewed the list, both in their entries and in their code, and given in short: a test by its label, node id, change and criterion alone, and support by its label, file, name and change', short)}`
}

const COPY_QUESTION = 'giving each answer or escalation the ref of every question it settles, copying the question word for word into answers[].question, and framing an escalation for the owner as your instructions say'

const issuePrompt = (k, questions, testReport, tests, support) => `Job 3 — review a round of the branch. ${shared(k, 'issue')} The modules: ${modules.join(', ')}; their design files: ${DESIGN_LIST}. Settle each engineering question below by citing a written rule in answers[], or escalate it in escalations[], ${COPY_QUESTION}; where a rule you cite means the work must change, also add a material finding saying what. Pass every business question you meet to raised[], untouched. List in designDocsChanged every file under docs/design/ the branch changes since its base. Leave summary empty.${section('The approved plan', plan)}${section('The checks the plan passed', ARGS.checks)}${section('The owner\'s decisions and answers', ARGS.decisions)}${section(`${PROVISIONAL}: do not ask them again`, provisional)}${priorSection('issue')}${section('Engineering questions from the builder', questions)}${listFor('issue', tests, support)}${section('The tester\'s report', testReport)}`

// The summary covers the whole work, however little of it a later round reviews.
const summaryAsk = () => {
  const whole = `The summary covers the whole work since ${base}, not only what is new since you last reviewed.`
  if (stage === 'tests') return `If you find nothing material and escalate nothing, write summary: for the owner to review before any code is written, in plain terms, each acceptance criterion and each spec rule the work touches, numbered, with the labels of the tests that pin it (A1, M1 and so on). The report lists every test with its scenario beside your summary, so do not repeat them. ${whole} Otherwise leave summary empty.`
  if (kind === 'design-pass') return `If you find nothing material and escalate nothing, write summary: a short confirmation that nothing a league sees has changed, and what you checked to be sure. ${whole} Otherwise leave summary empty.`
  return `If you find nothing material and escalate nothing, write summary: the acceptance summary your instructions describe. ${whole} Otherwise leave summary empty.`
}

const productPrompt = (k, questions, testReport, tests, support, whole = false) => `Job 2 — a round of the branch. ${shared(k, 'product', whole)} The specs: ${SPEC_LIST}, and the core specification wherever the work touches core's rules. Answer each business question below by citing a written rule in answers[], or escalate it in escalations[], ${COPY_QUESTION}; where a rule you cite means the work must change, also add a material finding saying what. Pass every engineering question you meet to raised[], untouched. Leave designDocsChanged empty. ${summaryAsk()}${section('The approved plan', plan)}${section('What a league should see once it lands', ARGS.criteria)}${section('The owner\'s decisions and answers', ARGS.decisions)}${section('Rules cited so far in this work', citations)}${section(`${PROVISIONAL}: do not ask them again`, provisional)}${priorSection('product')}${section('Business questions from the builder', questions)}${listFor('product', tests, support, whole)}${section('The tester\'s report', testReport)}`

const testsTesterPrompt = (k, tests, since = '') => `You check the tests changed in round ${k} of the tests stage for issue #${issue}, in ${worktree}. You change nothing: no edits, no commits, no installs, and nothing on GitHub.

1. What is committed: list every line git -C ${worktree} status --porcelain --untracked-files=all prints, in uncommitted. The tests must be committed to count.
2. Collection: pytest tests/ --collect-only -q must exit 0.
3. The real failures: pytest <every nodeid below> -q --runxfail --tb=short. Each test not marked alreadyPasses must fail; for each, give the failure pytest reports: the assertion or exception, and its line. A test marked alreadyPasses must pass here too.
4. As committed: pytest <the files holding them> -q -rxX, and give each listed test's outcome. A test not marked alreadyPasses must be reported xfailed, and one marked alreadyPasses must pass. Nothing else in those files may fail, and nothing may XPASS.
5. If anything fails across the board, run df -h /tmp: where it is full or nearly, report environmentProblem. Set lockTimedOut where any run exited 75.
${tests.length ? '' : 'Every change this round is a deletion or to support alone, so there is no test to run: skip steps 3 and 4.\n'}6. What the branch changes under tests/: run ${CHANGED_TESTS(base)}, with a Bash timeout of 600000 ms, and copy the head, tests, support and markersRemoved it prints into changes, exactly, leaving nothing out. Where it exits non-zero, put what it printed on stderr in changesError, and leave the lists in changes empty.
${since ? `7. What has changed under tests/ since ${since}: run ${CHANGED_TESTS(since)}, with a Bash timeout of 600000 ms, and copy what it prints into changedSince, exactly, leaving nothing out. Where it exits non-zero, put what it printed on stderr in changedSinceError, and leave the lists in changedSince empty.
` : ''}
Name any log file /tmp/work-issue-${issue}-tests-r${k}-<step>.log.

${RUN_PYTEST}${section('The tests the builder changed, to run in steps 3 and 4 (a deleted test is not among them)', tests)}`

const codePrompt = k => `Review round ${k} of the build. ${shared(k, 'code')} To confirm a behaviour, run python against this checkout's code, never the installed copy: cd ${worktree} && PYTHONPATH=src ${python} -c '...'. Put a question you cannot settle from the code in raised[], with its kind. Leave answers[], escalations[], designDocsChanged and summary empty.${section('The approved plan', plan)}${section('The owner\'s decisions and answers', ARGS.decisions)}${section(`${PROVISIONAL}: do not ask them again`, provisional)}${priorSection('code')}`

const designPrompt = (k, files) => `Job 2 — verify a drafted design file, limited to what this branch changes. ${ISSUE}. ${BRANCH_READ} ${NO_PYTEST} The branch changes ${files.join(', ')}. For each, read git -C ${worktree} diff ${base}...HEAD -- <file>, and the file in full for context, and hold the changed and added text to your seven checks.${sinceReviewed(k, 'design')} Judge the change against what .claude/skills/architecture-review/SKILL.md (Phase 9) and .claude/skills/design-review/SKILL.md (Phases 8 and 10) hold a design file to, against .claude/skills/architecture-review/python-practices.md, and against the owner's decisions below. Those phases also tell the main session how to run a review; that part is not yours, and you run no agent. Report each failure as a finding with an id of the form design-${k}-<n>: material where a check fails on substance, not material where only the wording is at fault. Put any question in raised[]. Leave answers[], escalations[], designDocsChanged and summary empty.${section('The owner\'s decisions and answers', ARGS.decisions)}${section(`${PROVISIONAL}: do not ask them again`, provisional)}${priorSection('design')}`

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
${testsHead ? `8. What the build has changed under tests/ since the owner approved the tests: run ${CHANGED_TESTS(testsHead)}, with a Bash timeout of 600000 ms, and copy the head, tests, support and markersRemoved it prints into changes, exactly, leaving nothing out. Where it exits non-zero, put what it printed on stderr in changesError, and leave the lists in changes empty.` : '8. Leave changes empty, its head included, and changesError empty: this build has no approved tests to hold it to.'}

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

// A ratchet list of the architecture checks, whose lines the build deletes as it removes each
// breach the plan names: the issue reviewer holds each deletion to the plan, and neither list of
// the tests stage carries it.
const isRatchet = s => s.file.startsWith('tests/repository/') && s.name.startsWith('KNOWN_') && s.change === 'modified'

// Each entry is labelled for the owner. An entry the owner saw at the last Gate 2 keeps the label
// it had there, so that the owner's answer ("reword A3") names the same test in the next list; a
// new entry takes the next number free. Labels may shift between rounds before a gate, so the
// checkers name tests by node id.
const labelled = (tests, support) => {
  const before = new Map([
    ...(shown ? shown.tests : []).filter(t => t.label).map(t => [`t|${t.nodeid}|${t.change}`, t.label]),
    ...(shown ? shown.support : []).filter(x => x.label).map(x => [`s|${supportKey(x)}|${x.change}`, x.label]),
  ])
  const used = new Set()
  const highest = {}
  for (const label of before.values()) {
    const [, prefix, n] = label.match(/^([A-Z]+)(\d+)$/) || []
    if (prefix) highest[prefix] = Math.max(highest[prefix] || 0, Number(n))
  }
  const give = prefix => {
    highest[prefix] = (highest[prefix] || 0) + 1
    const label = `${prefix}${highest[prefix]}`
    used.add(label)
    return label
  }
  // Kept labels are claimed first, so that no new entry takes a number an old one holds.
  const order = []
  for (const g of [...GROUPS, { change: null, prefix: 'X' }]) {
    const group = tests.filter(t => g.change ? t.change === g.change : !GROUPS.some(x => x.change === t.change))
    for (const [, entries] of byFile(group, t => fileOf(t.nodeid))) for (const t of entries) order.push([t, `t|${t.nodeid}|${t.change}`, g.prefix])
  }
  for (const [, entries] of byFile(support, x => x.file)) for (const x of entries) order.push([x, `s|${supportKey(x)}|${x.change}`, 'S'])
  const labels = new Map()
  for (const [entry, key] of order) if (before.has(key) && !used.has(before.get(key))) { labels.set(entry, before.get(key)); used.add(before.get(key)) }
  for (const [entry, , prefix] of order) if (!labels.has(entry)) labels.set(entry, give(prefix))
  return {
    tests: order.filter(([, , prefix]) => prefix !== 'S').map(([t]) => ({ ...t, label: labels.get(t) })),
    support: order.filter(([, , prefix]) => prefix === 'S').map(([x]) => ({ ...x, label: labels.get(x) })),
  }
}

// The builder's lists against what tools/changed_tests.py prints. The owner approves the tests
// from the lists, so a change they leave out is a change nobody approved, and one they describe
// without its scenario is a change approved blind.
const listProblems = (t, tests, support) => {
  if (t.changesError) return [`tools/changed_tests.py could not list what the branch changes under tests/: ${t.changesError}`]
  if (!filled(t.changes.head)) return ['the tester did not show that it ran tools/changed_tests.py: changes carries no head']
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
  compare(t.changes.support.filter(x => !isRatchet(x)), support.filter(x => !isRatchet(x)), supportKey, 'support[]')
  for (const w of tests) {
    const owed = ['scenario', 'expects', ...(w.change === 'modified' ? ['before'] : []), ...(['deleted', 'moved'].includes(w.change) ? ['why'] : [])]
    const missing = owed.filter(f => !filled(w[f]))
    if (missing.length) problems.push(`${w.nodeid} gives no ${missing.join(' and no ')}, and the owner approves the tests from these`)
  }
  for (const s of support) if (!filled(s.what)) problems.push(`${supportKey(s)} does not say what it now does`)
  return problems
}

// Against the lists the owner last saw at Gate 2: an entry that was not there, or whose
// description has changed since.
// Not alreadyPasses: a test the build has made pass since is the same test.
const DESCRIBED = ['scenario', 'expects', 'before', 'why', 'criterion', 'what', 'affects']
const described = (entry, f) => JSON.stringify(f === 'affects' ? [...(entry.affects || [])].sort() : entry[f] || '')
const sinceShown = (entry, before, keyOf) => {
  if (!shown) return ''
  const was = before.find(b => keyOf(b) === keyOf(entry))
  if (!was || was.change !== entry.change) return ' *(new since the last Gate 2)*'
  return DESCRIBED.some(f => described(was, f) !== described(entry, f)) ? ' *(changed since the last Gate 2)*' : ''
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
  // The reversible calls the stage took rather than asked, for the owner to overrule together here.
  if (provisional.length) lines.push('', '## Taken on a recommendation — overrule any', '', ...provisional.map(p => `- ${p.question} *Taken:* ${p.recommendation}`))
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

// A builder with no test changed yet has nothing for the tester to check. The tester is given each
// test's node id, change and whether it passes already, which is all it runs from. The tester runs no
// deleted test, and runs none at all where every change is a deletion: an empty list of targets
// would be the whole suite.
//
// Where a reviewer has seen the list before and commits have been made since, the tester also lists
// what has changed under tests/ since the earlier of the two reviewers last saw it: from the base
// where that is not known, which gives every entry in full. Its schema then requires the list, so an
// answer that lacks it is not one the runtime passes on. An answer whose list carries no head, and no
// error either, does not show that the step ran, as with `changes`: what changed is then unknown,
// and every entry is given in full.
const reviewTests = async (k, questions) => {
  const run = written.filter(w => w.change !== 'deleted')
  const seenAt = ['issue', 'product'].filter(l => listSeen[l]).map(l => reviewedAt[l])
  const known = seenAt.every(at => at !== undefined && at <= commits.length)
  const earliest = known && seenAt.length ? Math.min(...seenAt) : undefined
  const since = !seenAt.length || earliest === commits.length ? '' : earliest ? commits[earliest - 1].sha : base
  const test = written.length || supportWritten.length
    ? await agent(testsTesterPrompt(k, run.map(w => ({ nodeid: w.nodeid, change: w.change, ...(w.alreadyPasses ? { alreadyPasses: true } : {}) })), since), { ...settingsFor('tester'), label: `tests:r${k}:tester`, phase: 'Review', schema: since ? TESTS_CHECK_SINCE_SCHEMA : TESTS_CHECK_SCHEMA })
    : undefined
  if (test === undefined) log(`Round ${k}: no test is changed yet, so the tester is not sent out.`)
  const nothing = { tests: new Set(), support: new Set() }
  const found = t => t.changedSince || { tests: [], support: [] }
  listTouched = !since ? nothing
    : !test || filled(test.changedSinceError) || !filled(found(test).head) ? null
      : { tests: new Set(found(test).tests.map(x => bareId(x.nodeid))), support: new Set(found(test).support.map(supportKey)) }
  const [issueResult, productResult] = await parallel([
    () => agent(issuePrompt(k, questions.engineering, test, written, supportWritten), { ...settingsFor('issue'), label: `tests:r${k}:issue`, phase: 'Review', agentType: 'issue-reviewer', schema: REVIEW_SCHEMA }),
    () => agent(productPrompt(k, questions.business, test, written, supportWritten), { ...settingsFor('product'), label: `tests:r${k}:product`, phase: 'Review', agentType: 'product-owner', schema: REVIEW_SCHEMA }),
  ])
  for (const [lane, result] of [['issue', issueResult], ['product', productResult]]) {
    if (result) listSeen[lane] = { tests: written, support: supportWritten }
    else delete listSeen[lane]
  }
  const problems = testsProblems(test)
  return { lanes: { issue: issueResult, product: productResult }, test, problems, green: !!test && !hostProblem(test) && !problems.length }
}

// The host, not the code: what the tester reports as such, and a lock flock gave up on.
const hostProblem = t => t ? (t.environmentProblem || (t.exitCode === 75 || t.lockTimedOut ? 'flock gave up waiting an hour for the test lock' : '')) : ''

// What the build changed under tests/ after the owner approved the tests, but the three things it
// may: markers removed, ratchet lines and imports, which the tool does not report.
const unapprovedTestChanges = t => {
  if (!testsHead || !t) return []
  if (t.changesError) return [`tools/changed_tests.py could not list what the build changed under tests/: ${t.changesError}`]
  if (!filled(t.changes.head)) return ['the tester did not show that it ran tools/changed_tests.py: changes carries no head']
  const since = `since the tests the owner approved at ${testsHead}: revert it, and propose it in testChanges[] if the build needs it`
  return [
    ...t.changes.tests.map(x => `${x.nodeid} is ${x.change} ${since}`),
    ...t.changes.support.filter(x => !isRatchet(x)).map(x => `${supportKey(x)} is ${x.change} ${since}`),
  ]
}

const suiteProblems = t => {
  if (t === undefined) return ['the suite was not run: the builder was blocked and made no commit']
  if (!t) return ['the tester returned nothing']
  const problems = [...(hostProblem(t) ? [`the host: ${hostProblem(t)}`] : []), ...t.failures.map(f => `${f.test}: ${f.reason}`)]
  if (t.exitCode !== 0 && !t.failures.length) problems.push(`pytest exited ${t.exitCode}: ${t.summary}`)
  problems.push(...t.mypyErrors.map(e => `mypy: ${e}`))
  if (!t.mypyClean && !t.mypyErrors.length) problems.push('mypy reported errors')
  if (t.xfailMarkersLeft) problems.push(`${t.xfailMarkersLeft} expected-failure marker(s) naming #${issue} are left in tests/`)
  problems.push(...t.uncommitted.map(u => `not committed: ${u}`))
  problems.push(...unapprovedTestChanges(t))
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
    () => quiet ? Promise.resolve(undefined) : agent(buildTesterPrompt(k), { ...settingsFor('tester'), label: `build:r${k}:tester`, phase: 'Review', schema: SUITE_SCHEMA }),
    () => agent(issuePrompt(k, questions.engineering, null), { ...settingsFor('issue'), label: `build:r${k}:issue`, phase: 'Review', agentType: 'issue-reviewer', schema: REVIEW_SCHEMA })
      .then(async issueResult => {
        for (const file of issueResult ? issueResult.designDocsChanged : []) designFiles.add(file)
        if (!designFiles.size) return { issue: issueResult, design: undefined }
        const design = await agent(designPrompt(k, [...designFiles].sort()), { ...settingsFor('design'), label: `build:r${k}:design`, phase: 'Review', agentType: 'design-verifier', schema: REVIEW_SCHEMA })
        return { issue: issueResult, design }
      }),
    () => agent(codePrompt(k), { ...settingsFor('code'), label: `build:r${k}:code`, phase: 'Review', agentType: 'code-reviewer', schema: REVIEW_SCHEMA }),
    () => agent(productPrompt(k, questions.business, null), { ...settingsFor('product'), label: `build:r${k}:product`, phase: 'Review', agentType: 'product-owner', schema: REVIEW_SCHEMA }),
  ])
  const green = !!test && !hostProblem(test) && test.exitCode === 0 && test.mypyClean && test.xfailMarkersLeft === 0 && !test.uncommitted.length && !unapprovedTestChanges(test).length
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

// The round's pieces, merged into the one result the round is reviewed and recorded from. The
// commits, fixes, disputes, questions, test changes and defects of every piece are joined; the
// tests stage's lists come from the last piece, which lists the whole branch since its base, and
// the build's markers removed are joined. Whether the stage is done, and what it still owes, is
// the last piece's to say.
const mergePieces = parts => {
  const last = parts[parts.length - 1]
  const all = field => parts.flatMap(p => p[field] || [])
  return {
    ...last,
    commits: all('commits'),
    fixed: all('fixed'),
    disputed: all('disputed'),
    questions: all('questions'),
    testChanges: all('testChanges'),
    separateDefects: all('separateDefects'),
    notes: all('notes'),
    tests: stage === 'tests' ? last.tests : all('tests'),
    support: stage === 'tests' ? last.support || [] : all('support'),
  }
}

// The builder's pieces of round k, one after another. A further piece starts only where the last
// one committed something, is on the branch, and is neither finished nor blocked, and asked no
// question and proposed no test change: anything the owner must see goes to the round's review at
// once, as a round without pieces would. The commits of every piece that returned on the branch
// are recorded, so that the result says what the branch carries even where a later piece returns
// nothing; and what those pieces fixed, disputed and unmarked is handed back as `kept`, to be taken
// in before the stage fails, so that a run resumed from the failure does not give the next builder
// as still open a finding the branch has fixed.
const buildRound = async k => {
  const parts = []
  const record = () => {
    const on = parts.filter(p => p.onBranch)
    if (!on.length) return null
    const merged = mergePieces(on)
    commits.push(...merged.commits)
    separateDefects.push(...merged.separateDefects)
    return merged
  }
  for (let n = 1; ; n++) {
    const got = await agent(builderPrompt(k, parts), { ...settingsFor(stage === 'tests' ? 'testsBuilder' : 'builder'), label: n === 1 ? `${stage}:r${k}:builder` : `${stage}:r${k}:p${n}:builder`, phase: stage === 'tests' ? 'Tests' : 'Build', agentType: 'general-purpose', schema: BUILDER_SCHEMA })
    if (!got) {
      const kept = record()
      return { kept, missing: n === 1 ? `the builder returned nothing in round ${k}` : `the builder returned nothing in round ${k}, piece ${n}` }
    }
    parts.push(got)
    // A later piece is given the list as it stands, under its labels.
    if (stage === 'tests' && got.onBranch) ({ tests: written, support: supportWritten } = labelled(got.tests, got.support || []))
    const handsOff = got.onBranch && got.commits.length && !got.planComplete && !got.blocked && !got.questions.length && !(got.testChanges || []).length
    if (!handsOff) break
    if (n === MAX_PIECES) { log(`Round ${k}: the builder handed off ${MAX_PIECES} pieces, the most a round takes, without finishing; the round is reviewed as it stands.`); break }
    log(`Round ${k}: piece ${n} of the builder made ${got.commits.length} commit(s) and handed off; piece ${n + 1} carries on.`)
  }
  record()
  return { built: mergePieces(parts) }
}

// What the round's builder changed, taken into the list and the ledger. A claim counts only on a
// finding the builder still owes: a settled or minor one stays as it is.
const takeIn = built => {
  if (stage === 'tests') ({ tests: written, support: supportWritten } = labelled(built.tests, built.support || []))
  else written = [...written, ...built.tests]
  for (const x of built.fixed) { const f = ledger.get(x.id); if (f && materialOpen(f)) { f.status = 'fixed'; f.fixedIn = x.commit; f.notFixedBecause = '' } }
  for (const x of built.disputed) { const f = ledger.get(x.id); if (f && materialOpen(f)) { f.status = 'disputed'; f.dispute = x.reason; f.notFixedBecause = '' } }
}

const rounds = []
let status = 'unfinished'
let failure = ''
let escalations = []
let summary = ''
// The test changes the build needs and did not make, for the owner to decide.
let testChanges = []
let lastTest = null
// A loop that is not converging stops as `stalled` rather than spend the rest of its budget: two
// rounds running that each open at least as many material findings as they close (the first round
// of a stage, which finds what is there to find, excepted), or the same failures two rounds
// running. Both carry across runs, as the rounds do.
let stallStreak = previous && previous.stallStreak ? previous.stallStreak : 0
let lastRed = previous && previous.lastRed ? previous.lastRed : ''
// What a red round failed on, compared by test and by check rather than by the tester's wording.
const redKey = r => {
  if (r.green || !r.test) return ''
  if (stage === 'tests') return [...r.problems].sort().join(' | ')
  return [...r.test.failures.map(f => f.test), ...(r.test.mypyClean ? [] : ['mypy'])].sort().join(' | ')
}

for (let k = offset + 1; k <= offset + maxRounds; k++) {
  phase(stage === 'tests' ? 'Tests' : 'Build')
  const pendingBefore = new Set([...ledger.values()].filter(materialPending).map(f => f.id))
  const { built, kept, missing } = await buildRound(k)
  if (missing) { if (kept) takeIn(kept); status = 'failed'; failure = missing; break }
  if (!built.onBranch) { status = 'failed'; failure = `the checkout at ${worktree} is not on ${branch}`; break }
  takeIn(built)
  if (stage === 'tests' && !previous && built.planComplete && !built.tests.length && !(built.support || []).length) {
    status = 'failed'
    failure = 'the builder changed no test: the tests stage is only for a plan that changes one'
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
  for (const [lane, result] of Object.entries(reviewed.lanes)) {
    if (result === null) delete reviewedAt[lane]
    else if (result) reviewedAt[lane] = commits.length
  }
  lastTest = reviewed.test

  const got = Object.values(reviewed.lanes).filter(Boolean)
  const roundAnswers = got.flatMap(r => r.answers)
  citations.push(...roundAnswers)
  separateDefects.push(...got.flatMap(r => r.separateDefects))
  const roundEscalations = got.flatMap(r => r.escalations)
  // A checker that returned nothing answered none of the builder's questions routed to it: they
  // go to the owner, as a failed triage's do, and as unframed, since no checker has judged them.
  if (reviewed.lanes.product === null) roundEscalations.push(...questions.business.map(q => ({ ...q, unframed: true })))
  if (reviewed.lanes.issue === null) roundEscalations.push(...questions.engineering.map(q => ({ ...q, unframed: true })))
  const raised = got.flatMap(r => r.raised).map((q, i) => ({ ...q, ref: refsIn(q)[0] || `r${k}-${i + 1}` }))
  const duplicates = []
  if (raised.length) {
    const handled = [...roundAnswers.map(a => a.question), ...roundEscalations.map(q => q.question), ...provisional.map(p => p.question)]
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
  // A reversible call is taken on its recommendation, and only the rest stop the stage.
  const taken = roundEscalations.filter(q => !stopsTheStage(q))
  const stopping = roundEscalations.filter(stopsTheStage)
  takeProvisionally(taken, k)
  if (taken.length) log(`Round ${k}: ${taken.length} reversible call(s) taken on a checker's recommendation, for the owner to confirm at the gate.`)

  lastFailures = [...reviewed.problems, ...(built.clean ? [] : ['the builder left uncommitted changes in the checkout'])]
  const open = [...ledger.values()].filter(materialPending)
  const openIds = new Set(open.map(f => f.id))
  const opened = open.filter(f => !pendingBefore.has(f.id)).length
  const closed = [...pendingBefore].filter(id => !openIds.has(id)).length
  stallStreak = k > 1 && opened >= 1 && opened >= closed ? stallStreak + 1 : 0
  const red = redKey(reviewed)
  const sameRed = !!red && red === lastRed
  lastRed = red
  const proposed = stage === 'build' ? built.testChanges || [] : []
  rounds.push({ round: k, commits: built.commits.map(c => c.subject), openMaterial: open.length, opened, closed, green: reviewed.green, questions: stopping.length, provisional: taken.length, testChanges: proposed.length, dead })
  log(`Round ${k}: ${built.commits.length} commit(s); ${open.length} material finding(s) open; ${stage === 'tests' ? 'tests' : 'suite'} ${reviewed.green ? 'green' : 'not green'}; ${stopping.length} question(s) for the owner; ${proposed.length} test change(s) proposed.`)

  // A host problem found in the same round is named beside the questions, to be repaired first.
  // A test change the build needs stops it as a question does: the owner decides it before the
  // build goes on, and the tests stage makes it.
  if (stopping.length || proposed.length) { status = 'question'; escalations = stopping; testChanges = proposed; failure = hostProblem(reviewed.test) ? `the host, not the code: ${hostProblem(reviewed.test)}` : ''; break }
  if (hostProblem(reviewed.test)) { status = 'failed'; failure = `the host, not the code: ${hostProblem(reviewed.test)}`; break }
  if (sameRed || stallStreak >= 2) {
    status = 'stalled'
    failure = sameRed ? `the same failures two rounds running: ${red}` : `two rounds running opened as many material findings as they closed; open now: ${open.map(f => f.id).join(', ')}`
    break
  }
  if (dead.length) { log(`No result from: ${dead.join(', ')}. The round cannot pass; the next one runs them again.`); continue }
  // A round in which the builder asked anything cannot pass: an answer, even one citing a rule,
  // reaches the builder only in the next round. Nor can one in which a call was just taken, which
  // the builder has yet to apply.
  if (reviewed.green && !open.length && built.planComplete && !built.blocked && built.clean && !built.questions.length && !taken.length) {
    const product = reviewed.lanes.product
    summary = product.summary
    // The product owner found nothing but left its summary out: ask again. Whatever else the
    // second call finds counts, so a finding or a question it raises stops the pass. The round has
    // already recorded the product lane as having reviewed the branch and seen the list, but the
    // second call is a fresh agent that has seen neither: it reviews the whole branch, and in the
    // tests stage is given the whole list.
    if (!summary) {
      const asked = await agent(`${productPrompt(k, [], reviewed.test, stage === 'tests' ? written : null, stage === 'tests' ? supportWritten : null, true)}\n\nThe other checkers found nothing in this round. Write summary now.`, { ...settingsFor('product'), label: `${stage}:r${k}:summary`, phase: 'Review', agentType: 'product-owner', schema: REVIEW_SCHEMA })
      if (asked) {
        addFindings('product', asked.findings)
        citations.push(...asked.answers)
        separateDefects.push(...asked.separateDefects)
        const late = [...asked.escalations]
        if (asked.raised.length) {
          const t = await triage(asked.raised.map((q, i) => ({ ...q, ref: refsIn(q)[0] || `r${k}s-${i + 1}` })), `r${k}s`, BRANCH_READ, TRIAGE_CONTEXT, [...roundAnswers, ...asked.answers, ...asked.escalations, ...provisional].map(x => x.question))
          citations.push(...t.answers)
          late.push(...t.escalations)
          for (const f of t.findings) addFindings(f.lane, [f])
        }
        const record = rounds[rounds.length - 1]
        record.openMaterial = [...ledger.values()].filter(materialPending).length
        const lateTaken = late.filter(q => !stopsTheStage(q))
        const lateStopping = late.filter(stopsTheStage)
        takeProvisionally(lateTaken, k)
        record.questions = lateStopping.length
        record.provisional += lateTaken.length
        if (lateStopping.length) { status = 'question'; escalations = lateStopping; break }
        if (record.openMaterial || lateTaken.length) continue
        summary = asked.summary
      }
      if (!summary) log('The product owner wrote no summary. The calling session asks the product-owner agent for it before the gate.')
    }
    status = 'passed'
    break
  }
}
if (status === 'unfinished' && offset + rounds.length >= stageBudget) {
  status = 'capped'
  failure = `the stage has used all ${stageBudget} of its rounds without passing`
}
if (status === 'unfinished') log(`${maxRounds} round(s) used without passing. The calling session runs the stage again, with this result as previous, or asks the owner.`)
if (status === 'capped' || status === 'stalled') log(`The stage stops as ${status}: ${failure}. The owner decides what next.`)

return {
  stage,
  issue,
  status,
  failure,
  lastRound: offset + rounds.length,
  rounds,
  escalations,
  testChanges,
  testsHead,
  summary,
  tests: written,
  support: supportWritten,
  ...(stage === 'tests' ? { shown, counts: counts(), report: gateReport(lastTest, summary), listSeen } : {}),
  lastTest,
  lastFailures,
  openMaterial: [...ledger.values()].filter(materialPending),
  minor: [...ledger.values()].filter(f => !f.material && f.status === 'open'),
  citations,
  provisional,
  separateDefects,
  commits,
  ledger: [...ledger.values()],
  designFiles: [...designFiles].sort(),
  reviewedAt,
  roundBudget: stageBudget,
  stallStreak,
  lastRed,
}
