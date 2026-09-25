export const meta = {
  name: 'review-module-design',
  description: 'Review one module\'s design for #283–#288: inventory its shape and record, review it against architecture.md and current Python practice, verify every finding, then check coverage',
  whenToUse: 'Run by the design-review skill, phase 3. Requires args {module, commit, survey} — module is core, results, attendance, signup, weather or image; survey is the markdown printed by tools/architecture_survey.py --module <module>. Read-only: returns the inventory the design file is written from, and verified divergences for the calling session to plan from.',
  phases: [
    { title: 'Inventory', detail: 'three design-surveyors, one per group of aspects' },
    { title: 'Review', detail: 'three python-design-reviewers, one per concern, reading the whole inventory' },
    { title: 'Verify', detail: 'one design-verifier per concern tries to refute its findings' },
    { title: 'Complete', detail: 'a critic checks the design file\'s contents are all supported; gaps get one more round' },
  ],
}

// The invoking runtime may hand args over as a JSON string; accept both.
const ARGS = typeof args === 'string' ? (() => { try { return JSON.parse(args) } catch (e) { return args } })() : args

const PASSES = {
  core: { issue: 283, file: 'docs/design/core.md', spec: 'docs/wip-specs/core_specification.md' },
  results: { issue: 284, file: 'docs/design/results_module.md', spec: 'docs/wip-specs/results_module_specification.md' },
  attendance: { issue: 285, file: 'docs/design/attendance_module.md', spec: 'docs/wip-specs/attendance_module_specification.md' },
  signup: { issue: 286, file: 'docs/design/signup_module.md', spec: 'docs/wip-specs/signup_module_specification.md' },
  weather: { issue: 287, file: 'docs/design/weather_module.md', spec: 'docs/wip-specs/weather_module_specification.md' },
  image: { issue: 288, file: 'docs/design/image_module.md', spec: 'docs/wip-specs/image_module_specification.md' },
}

if (!ARGS || !ARGS.module || !ARGS.commit || !ARGS.survey) {
  throw new Error('review-module-design requires args: {module: "<core|results|attendance|signup|weather|image>", commit: "<short sha>", survey: "<output of tools/architecture_survey.py --module <module>>"}')
}
const { module, commit, survey } = ARGS
const pass = PASSES[module]
if (!pass) throw new Error(`Unknown module ${JSON.stringify(module)}; expected one of ${Object.keys(PASSES).join(', ')}`)
const issue = `#${pass.issue}`
const MAX_GAPS = 3

const GOVERNING = module === 'core'
  ? 'docs/design/architecture.md'
  : 'docs/design/architecture.md and docs/design/core.md'

const CORE_NOTE = module === 'core'
  ? '\n\nCore implements several cross-cutting mechanisms whose rules are architecture.md\'s — the scheduler, the channel registry and the path to Discord, the failure path, the one-league claim. Inventory and review them as core\'s own code; the rule they serve is architecture.md\'s and is referenced, never restated.'
  : ''

// What every module's design file covers. Kept in step with the design-review skill (Phase 5)
// and the design-verifier (check 6); each maps to a section of architecture.md.
const CONTENTS = [
  'its tables and what each row means, its own columns on core\'s tables, and any link of its own that stops a delete',
  'its services and what each owns',
  'its changes as the change queue will carry them: what starts each, its steps, and which must be all or nothing',
  'its timed events, what each declares when missed, and the start-up work it owes',
  'what it posts, of which kind of post, to which channel, and how it finds a message again',
  'how it meets core and the other modules: the hooks it signs up to or declares, its entry in the dependency table, and its command group',
  'how it fails',
  'the constraints a later reader might otherwise tune away',
]

// The open issues that carry a divergence bot-wide. A module pass does not re-file one of these:
// it names the issue and says what the module adds to it. Read them with gh issue view.
const PROGRAMME = [
  '#438 the leaguebot package and one folder per module',
  '#439 the change queue',
  '#440 the start-up sweep for timed events',
  '#441 one handler per kind of post',
  '#442 keeping the full error in every catch-all',
  '#453 failures in timed jobs, events and background work',
  '#462 each module\'s commands under its own group',
]

const ASPECT_GROUPS = [
  { key: 'data', aspects: ['Tables', 'Changes'] },
  { key: 'behaviour', aspects: ['Services', 'Boundaries', 'Scheduling and restart'] },
  { key: 'effects', aspects: ['Posting', 'Failure', 'Constraints'] },
]

const CONCERNS = [
  {
    key: 'layout-and-data',
    brief: 'Against architecture.md\'s "How the code is laid out" and "The database": what each of the module\'s files may do (database code only in services, a cog calling one service, buttons beside what posts them, models as plain data, private names kept inside the module); its tables and their writers (each table written by one module; its columns on core\'s tables; a link that stops a delete); where its rules sit (schema, enum, trigger or service); a save that waits on anything but its own connection (#155); stored derived copies and what recomputes them (#238).',
  },
  {
    key: 'boundaries-and-changes',
    brief: 'Against "How the code is laid out", "How modules and core fit together" and "How a change is carried out": how its services are built and reached (the one builder, complete once built, no lookups on the bot); what it imports from other modules and what imports it, against the dependency table; where core names it today and the hook that would replace each; how it turns itself on and off; its commands, and any under core\'s groups. Every change it makes, as the queue will carry it: what starts it, its steps, which saves must be one step, what it posts between saves, and what a second press or a stop part-way does today.',
  },
  {
    key: 'time-output-failure',
    brief: 'Against "Timed work and restarts", "Posting to Discord" and "Errors and failures": its timed events as rows the job re-reads, the choice each declares when missed, its start-up work and where it runs today (#426, #429); blocking work on the loop and tasks nobody keeps. Every post, by kind (log line, standing post, notice, bot-owned channel), whether its message id is kept and what finds it again (#189). Its failures: which failure path each starting point reaches, its catch-all handlers and whether they keep the full error, and what a league sees.',
  },
]

// ---- schemas --------------------------------------------------------------------------------

const EVIDENCE = { type: 'array', items: { type: 'string' }, description: 'file:line, or a command and its output' }

const INVENTORY_SCHEMA = {
  type: 'object',
  required: ['commit', 'aspects', 'proseDisagreements', 'unsettled'],
  properties: {
    commit: { type: 'string' },
    aspects: {
      type: 'array',
      items: {
        type: 'object',
        required: ['aspect', 'facts'],
        properties: {
          aspect: { type: 'string' },
          facts: {
            type: 'array',
            items: {
              type: 'object',
              required: ['claim', 'evidence', 'record'],
              properties: {
                claim: { type: 'string' },
                evidence: EVIDENCE,
                record: { type: 'string', description: 'what preserves the reason — file:line, #N, commit, test name — or "no record"' },
              },
            },
          },
        },
      },
    },
    proseDisagreements: {
      type: 'array',
      items: {
        type: 'object',
        required: ['prose', 'where', 'code'],
        properties: { prose: { type: 'string' }, where: { type: 'string' }, code: { type: 'string' } },
      },
    },
    unsettled: { type: 'array', items: { type: 'string' } },
  },
}

const REVIEW_SCHEMA = {
  type: 'object',
  required: ['concern', 'commit', 'findings', 'sound', 'forArchitecture', 'suspectedDefects', 'notes'],
  properties: {
    concern: { type: 'string' },
    commit: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'title', 'evidence', 'rule', 'source', 'defectClass', 'options', 'recommendation', 'size', 'owner'],
        properties: {
          id: { type: 'string', description: 'a short unique slug, prefixed by the concern' },
          title: { type: 'string' },
          evidence: EVIDENCE,
          rule: { type: 'string', description: 'the architecture.md or core.md rule broken, or the practice where they are silent' },
          source: { type: 'string', description: 'the section of architecture.md or core.md, or the practice\'s primary source' },
          defectClass: { type: 'string', description: 'what a league or the maintainer suffers, with issue numbers that instance it' },
          options: {
            type: 'array',
            items: {
              type: 'object',
              required: ['label', 'change', 'cost', 'failureMode'],
              properties: {
                label: { type: 'string' },
                change: { type: 'string' },
                cost: { type: 'string' },
                failureMode: { type: 'string', description: 'what breaks, or stays broken, if this option is chosen' },
              },
            },
          },
          recommendation: { type: 'string' },
          size: { type: 'string', description: 'files and tests touched, roughly' },
          owner: { type: 'string', enum: ['#283', '#284', '#285', '#286', '#287', '#288', '#438', '#439', '#440', '#441', '#442', '#453', '#462', 'new tech-debt'] },
        },
      },
    },
    sound: {
      type: 'array',
      items: { type: 'object', required: ['what', 'evidence'], properties: { what: { type: 'string' }, evidence: EVIDENCE } },
    },
    forArchitecture: {
      type: 'array',
      items: {
        type: 'object',
        required: ['what', 'evidence', 'proposal'],
        properties: { what: { type: 'string' }, evidence: EVIDENCE, proposal: { type: 'string', description: 'the amendment to architecture.md or core.md, for the user to decide' } },
      },
    },
    suspectedDefects: {
      type: 'array',
      items: {
        type: 'object',
        required: ['title', 'evidence', 'whatALeagueSees'],
        properties: { title: { type: 'string' }, evidence: EVIDENCE, whatALeagueSees: { type: 'string' } },
      },
    },
    notes: { type: 'array', items: { type: 'string' } },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['concern', 'verdicts'],
  properties: {
    concern: { type: 'string' },
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'verdict', 'grounds', 'evidence'],
        properties: {
          id: { type: 'string', description: 'the finding id, exactly as given' },
          verdict: { type: 'string', enum: ['confirmed', 'amended', 'refuted', 'uncertain'] },
          grounds: { type: 'string' },
          evidence: EVIDENCE,
          amendment: { type: 'string' },
        },
      },
    },
  },
}

const GAPS_SCHEMA = {
  type: 'object',
  required: ['gaps'],
  properties: {
    gaps: {
      type: 'array',
      items: {
        type: 'object',
        required: ['kind', 'topic', 'why', 'brief'],
        properties: {
          kind: { type: 'string', enum: ['inventory', 'review'], description: 'inventory: facts missing for the design file; review: a concern left unexamined' },
          topic: { type: 'string' },
          why: { type: 'string' },
          brief: { type: 'string', description: 'for inventory, the aspect and what to find; for review, a concern brief' },
        },
      },
    },
  },
}

// ---- prompts --------------------------------------------------------------------------------

const preamble = `Design pass for the ${module} module, issue ${issue}, at commit ${commit}. Read the issue first: gh issue view ${pass.issue} --json body,comments. Its "What the document should settle" names the topics ${pass.file} must cover beyond what every module file covers. The module's rules are in ${pass.spec}; cite them, never restate them.${CORE_NOTE}`

const inventoryPrompt = g => `${preamble}

Scope: ${module}. Aspects: ${g.aspects.join(', ')}.

The design file is written from what you return, so cover every file of the module and every topic the issue names that falls under your aspects. The survey from tools/architecture_survey.py --module ${module} is below; do not re-derive its counts.

${survey}`

const digest = inventory => JSON.stringify(inventory.map(i => ({
  aspects: i.aspects,
  proseDisagreements: i.proseDisagreements,
  unsettled: i.unsettled,
})))

const reviewPrompt = (c, inventory) => `${preamble}

Your concern: **${c.key}**. ${c.brief}

Judge against ${GOVERNING} first, then the repository's recorded decisions, then .claude/skills/architecture-review/python-practices.md for what they leave open. ${GOVERNING} describe the shape the code is to have, and their decisions are settled: a divergence from them is expected, and your finding says what this module must change to reach the shape, citing the section it rests on. Never offer an option they reject; a shape you think better goes in forArchitecture. This module's entries in the ratchet lists (tests/repository/test_architecture_rules.py and .importlinter) and its open issues are this pass's to correct: address each one.

Some divergences are carried bot-wide by open issues of their own:
${PROGRAMME.map(x => `- ${x}`).join('\n')}
A finding one of them carries is owned by that issue: name it, and give only what this module adds to it, never a new issue for it. Otherwise the owner is ${issue} when this pass can carry the correction, another pass's issue when the files are that module's, and "new tech-debt" when the correction is too large for this pass.

The whole inventory, from three surveyors:
${digest(inventory)}

The survey:
${survey}`

const verifyPrompt = (c, review) => `Job 1 — refute findings. Design pass for the ${module} module (${issue}), at commit ${commit}, concern **${c.key}**. The conflict ground is tested against ${GOVERNING} as well as CLAUDE.md, "decided" docstrings and tests that pin a trade-off. On the ownership ground, a finding one of these open issues already carries is amended to name it: ${PROGRAMME.join('; ')}. Return one verdict per finding id, using the ids exactly as given.

${JSON.stringify(review.findings, null, 2)}`

const criticPrompt = (inventory, reviewed) => `Completeness check for the ${module} design pass (${issue}), at commit ${commit}. Do not review the code afresh. Say what this pass has **not** covered.

${pass.file} must cover: ${CONTENTS.join('; ')}; and the topics issue ${issue} names — read them with gh issue view ${pass.issue} --json body.

Inventory, by aspect, with fact counts and every "unsettled" entry:
${JSON.stringify(inventory.map(i => ({ aspects: i.aspects.map(a => ({ aspect: a.aspect, facts: a.facts.length, claims: a.facts.map(f => f.claim) })), unsettled: i.unsettled })))}

Reviews, by concern, with surviving findings and sound parts:
${JSON.stringify(reviewed.map(r => ({
  concern: r.concern,
  findings: r.findings.filter(f => f.verdict && f.verdict.verdict !== 'refuted').map(f => f.title),
  sound: r.sound.map(s => s.what),
})))}

Return an inventory gap for each of the contents, or each named topic, that no fact supports, and for each "unsettled" entry worth one more attempt. Return a review gap for any ratchet entry or open issue in this module that no finding addresses, and any topic reviewed by nobody. An empty list is a correct answer.`

// ---- run ------------------------------------------------------------------------------------

const withVerdicts = (review, verdicts) => {
  const byId = new Map((verdicts ? verdicts.verdicts : []).map(v => [v.id, v]))
  return {
    ...review,
    findings: review.findings.map(f => ({ ...f, verdict: byId.get(f.id) || { id: f.id, verdict: 'uncertain', grounds: 'no verdict returned', evidence: [] } })),
  }
}

const survey3 = groups => parallel(groups.map(g => () =>
  agent(inventoryPrompt(g), { label: `inventory:${g.key}`, phase: 'Inventory', agentType: 'design-surveyor', schema: INVENTORY_SCHEMA })))

const reviewAndVerify = (concerns, inventory) => pipeline(
  concerns,
  c => agent(reviewPrompt(c, inventory), { label: `review:${c.key}`, phase: 'Review', agentType: 'python-design-reviewer', schema: REVIEW_SCHEMA }),
  (returned, c) => {
    if (!returned) return null
    // The concern is stamped from the script, not taken from the agent, so a failed one is found by key.
    const review = { ...returned, concern: c.key }
    if (!review.findings.length) return withVerdicts(review, null)
    return agent(verifyPrompt(c, review), { label: `verify:${c.key}`, phase: 'Verify', agentType: 'design-verifier', schema: VERDICT_SCHEMA })
      .then(v => withVerdicts(review, v))
  },
)

phase('Inventory')
log(`Inventorying the ${module} module at ${commit} for ${pass.file}.`)
// A barrier, deliberately: every reviewer reads the whole inventory, not its own slice.
const surveyed = await survey3(ASPECT_GROUPS)
const inventory = surveyed.filter(Boolean)
const missingAspects = ASPECT_GROUPS.filter((g, i) => !surveyed[i]).map(g => g.key)
if (missingAspects.length) log(`No inventory for: ${missingAspects.join(', ')}; the review proceeds without it. Rerun or resume before writing ${pass.file}.`)

phase('Review')
const reviewed = (await reviewAndVerify(CONCERNS, inventory)).filter(Boolean)
const failed = CONCERNS.filter(c => !reviewed.some(r => r.concern === c.key)).map(c => c.key)
if (failed.length) log(`No result for: ${failed.join(', ')} — rerun or resume before relying on this review.`)

phase('Complete')
const critic = await agent(criticPrompt(inventory, reviewed), { label: 'critic:completeness', phase: 'Complete', schema: GAPS_SCHEMA })
const gaps = critic ? critic.gaps : []
const taken = gaps.slice(0, MAX_GAPS)
if (gaps.length > MAX_GAPS) log(`The critic found ${gaps.length} gaps; working the first ${MAX_GAPS}. Not worked: ${gaps.slice(MAX_GAPS).map(g => g.topic).join('; ')}`)

const inventoryGaps = taken.filter(g => g.kind === 'inventory')
const reviewGaps = taken.filter(g => g.kind === 'review')
const [gapInventory, gapReviews] = await Promise.all([
  inventoryGaps.length
    ? parallel(inventoryGaps.map((g, i) => () => agent(
        `${preamble}\n\nScope: ${module}. A completeness check found this missing from the inventory: ${g.topic}. ${g.why}\n\n${g.brief}`,
        { label: `inventory:gap-${i + 1}`, phase: 'Complete', agentType: 'design-surveyor', schema: INVENTORY_SCHEMA })))
    : Promise.resolve([]),
  reviewGaps.length
    ? reviewAndVerify(reviewGaps.map((g, i) => ({ key: `gap-${i + 1}`, brief: `${g.topic}. ${g.brief}` })), inventory)
    : Promise.resolve([]),
])

const concerns = [...reviewed, ...gapReviews.filter(Boolean)]
const count = v => concerns.flatMap(r => r.findings).filter(f => f.verdict.verdict === v).length
log(`Findings: ${count('confirmed')} confirmed, ${count('amended')} amended, ${count('uncertain')} uncertain, ${count('refuted')} refuted.`)

return {
  module,
  issue,
  designFile: pass.file,
  commit,
  inventory: [...inventory, ...gapInventory.filter(Boolean)],
  concerns,
  gaps,
  failedConcerns: failed,
  missingAspects,
}
