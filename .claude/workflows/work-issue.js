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
const triage = async (questions, where, context) => {
  const business = questions.filter(q => q.kind === 'business')
  const engineering = questions.filter(q => q.kind !== 'business')
  const ask = (qs, who, job) => agent(
    `${job} ${ISSUE}. ${where}\n\nAnswer each question below as your instructions say: cite a written rule in answers[], or escalate it to the owner in escalations[]. Where a cited rule means the work must change, add a material finding saying what, in findings[], with an id of the form ${who}-triage-<n>. Never run pytest.${context}${section('Questions', qs)}`,
    { label: `triage:${who}`, phase: 'Triage', agentType: who === 'product' ? 'product-owner' : 'issue-reviewer', schema: TRIAGE_SCHEMA },
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
    ? await triage(raised, `The plan was drafted at commit ${commit}.${branchNote}`, context)
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

throw new Error(`The ${stage} stage is not written yet.`)
