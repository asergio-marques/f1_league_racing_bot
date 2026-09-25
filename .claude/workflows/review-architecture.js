export const meta = {
  name: 'review-architecture',
  description: 'Review the bot\'s architecture for #282: five concern reviewers against current Python practice, each finding adversarially verified, then a completeness check',
  whenToUse: 'Run by the architecture-review skill, phase 3. Requires args {commit, survey} where survey is the markdown printed by tools/architecture_survey.py. Read-only: returns findings, verdicts and candidate decisions for the calling session to bring to the user.',
  phases: [
    { title: 'Survey', detail: 'one surveyor inventories the cross-cutting mechanisms, alongside the review' },
    { title: 'Review', detail: 'one python-design-reviewer per architectural concern' },
    { title: 'Verify', detail: 'one design-verifier per concern tries to refute its findings' },
    { title: 'Complete', detail: 'a critic checks #282\'s list is covered; gaps get one more round' },
  ],
}

// The invoking runtime may hand args over as a JSON string; accept both.
const ARGS = typeof args === 'string' ? (() => { try { return JSON.parse(args) } catch (e) { return args } })() : args
if (!ARGS || !ARGS.commit || !ARGS.survey) {
  throw new Error('review-architecture requires args: {commit: "<short sha>", survey: "<output of tools/architecture_survey.py>"}')
}
const { commit, survey } = ARGS
const MAX_GAPS = 3

// ---- what #282 asked the architecture to settle, and its six candidates, since settled -----

const SETTLE = [
  'The layering — cogs, services, db — what each layer may and may not do, which way a dependency may point, and what a service may import',
  'How modules depend on one another, how a dependency is declared, and what enabling or disabling one does to the others',
  'How time-driven work is scheduled, and how a restart recovers what came due while the bot was down',
  'How anything reaches Discord: the output router, the log channel, retries, and what happens to a posting that fails',
  'Where failures go, and the one path they take',
  'What the one-league claim means structurally, and why nothing below the command tree scopes by server',
  'Where the rules binding every module already live, as pointers: the League* bases, report_failure, and (CLAUDE.md\'s, not restated) the per-module coverage floor and the single schema baseline',
]

const CANDIDATES = [
  '1. Settled: the services stay typed attributes on LeagueBot, built by one builder; a separate container was rejected (architecture.md, "How the code is laid out")',
  '2. Settled: no LeagueCog base; the League* bases for the tree, views and forms stay (architecture.md, "Errors and failures")',
  '3. Settled: the layering rules are enforced by import-linter contracts (.importlinter, run by tests/repository/test_import_contracts.py) and ast checks (tests/repository/test_architecture_rules.py), each listing today\'s breaches against the issue that removes them (architecture.md, "How the rules are checked")',
  '4. Settled: one installed package, leaguebot, with a folder per module and a folder per kind of code inside each; ownership is the folder a file sits in (architecture.md, "How the code is laid out")',
  '5. Settled: a cog holds one command group, so season_cog.py is split along its groups, with git mv (architecture.md, "How the code is laid out")',
  '6. Settled: report_failure, the League* bases and the one-league claim kept; one handler per kind of post, the log line\'s handler being core/services/output_router.py (architecture.md, "Posting to Discord")',
]

const CONCERNS = [
  {
    key: 'layering',
    brief: 'The layers and the direction of every dependency: each module\'s folders for cogs, services, utils and models, core\'s db, and the entry point src/leaguebot/__main__.py; SQL outside the services; services importing cogs (TYPE_CHECKING and function-local imports count); what a service may import. Data access as the layer beneath: connection handling in src/leaguebot/core/db/database.py, transaction length and the write lock held across a network call (#155 — assign_driver and persist_snapshots), tables written by a module that does not own them, a module\'s own columns on a core table included, stored derived copies (#238). The one-league claim structurally: what core/utils/league_server.py guarantees and why nothing below the command tree scopes by server.',
    candidates: '3 and the one-league part of 6',
  },
  {
    key: 'composition',
    brief: 'The composition root in src/leaguebot/__main__.py and how services are built and reached: the services attached to bot, LeagueBot in src/leaguebot/core/utils/league_bot.py, bot_of(), services that take bot or reach for another service at call time (service locator), import-time side effects and configuration (BOT_TOKEN read at import), and the hooks core offers the modules.',
    candidates: '1 and 2',
  },
  {
    key: 'modules',
    brief: 'Module boundaries: how modules depend on one another (the survey\'s module-dependency table), how a dependency is declared and what enabling or disabling one does (module_service and the module cogs), ownership, which is the folder a file sits in, and any file in a folder that is not its module\'s or its kind\'s, a public surface per module, the rules between modules as .importlinter encodes them against the dependency table, the hubs with the highest fan-in, and the largest files — season_cog.py above all.',
    candidates: '4 and 5',
  },
  {
    key: 'runtime',
    brief: 'Time-driven work and the event loop: how jobs are registered (scheduler_service and every add_job), how job ids are built, what the database versus the APScheduler job store is authority for, what a restart re-registers, and the one start-up sweep, which only asks each kind which of its events came due and hands each, in order and one at a time, to the handler its module provides, the handler deciding what becomes of it (architecture.md, "Timed work and restarts"; #426, #429); blocking calls inside async def (subprocess, the Inkscape rasteriser), tasks created and not kept, locks held across awaits, timeouts on outbound calls, and the injected clock.',
    candidates: 'none directly — say whether a new candidate is owed',
  },
  {
    key: 'output-and-failure',
    brief: 'How anything reaches Discord: the handler for each kind of post, core/services/output_router.py being the log line\'s, the channel registry, the log channel, retries (retry_service, retry_cog), direct .send calls around the router, and whether a posted message\'s id is kept so it can be found again (#189). Where failures go: report_failure in core/utils/interaction_errors.py, the League* bases\' on_error, scheduled jobs\' failures, broad exception handlers by layer, and what a league sees when a post fails.',
    candidates: 'the OutputRouter, report_failure and League* parts of 6',
  },
]

// ---- schemas --------------------------------------------------------------------------------

const EVIDENCE = { type: 'array', items: { type: 'string' }, description: 'file:line, or a command and its output' }

const REVIEW_SCHEMA = {
  type: 'object',
  required: ['concern', 'commit', 'findings', 'candidateDecisions', 'sound', 'suspectedDefects', 'notes'],
  properties: {
    concern: { type: 'string' },
    commit: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'title', 'evidence', 'rule', 'source', 'defectClass', 'options', 'recommendation', 'size', 'owner'],
        properties: {
          id: { type: 'string', description: 'a short unique slug, prefixed by the concern, e.g. layering-sql-in-cogs' },
          title: { type: 'string' },
          evidence: EVIDENCE,
          rule: { type: 'string', description: 'the rule or practice broken, in plain terms' },
          source: { type: 'string', description: 'its primary source: PEP, Python docs page, tool docs, cosmicpython chapter' },
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
          recommendation: { type: 'string', description: 'the option label recommended, and why in a sentence' },
          size: { type: 'string', description: 'files and tests touched, roughly' },
          owner: { type: 'string', enum: ['#282', '#283', '#284', '#285', '#286', '#287', '#288', 'new tech-debt'] },
        },
      },
    },
    candidateDecisions: {
      type: 'array',
      items: {
        type: 'object',
        required: ['candidate', 'stance', 'reasoning'],
        properties: {
          candidate: { type: 'string', description: '1 to 6, or "new: <title>"' },
          stance: { type: 'string', enum: ['confirm', 'amend', 'overrule', 'new'] },
          reasoning: { type: 'string' },
        },
      },
    },
    sound: {
      type: 'array',
      items: { type: 'object', required: ['what', 'evidence'], properties: { what: { type: 'string' }, evidence: EVIDENCE } },
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
          grounds: { type: 'string', description: 'which grounds were tested — evidence, source, proportion, conflict, ownership, function — and how each came out' },
          evidence: EVIDENCE,
          amendment: { type: 'string', description: 'for amended: what changes' },
        },
      },
    },
  },
}

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

const GAPS_SCHEMA = {
  type: 'object',
  required: ['gaps'],
  properties: {
    gaps: {
      type: 'array',
      items: {
        type: 'object',
        required: ['topic', 'why', 'brief'],
        properties: {
          topic: { type: 'string' },
          why: { type: 'string', description: 'what is missing: an item uncovered, a candidate unanswered, a claim unverified' },
          brief: { type: 'string', description: 'a concern brief for one more reviewer, in the style of the others' },
        },
      },
    },
  },
}

// ---- prompts --------------------------------------------------------------------------------

const reviewPrompt = c => `Architecture pass for issue #282, at commit ${commit}. Your concern: **${c.key}**.

${c.brief}

#282's candidate decisions this concern answers: ${c.candidates}. The six, for reference:
${CANDIDATES.map(x => `- ${x}`).join('\n')}

Read #282 in full first: gh issue view 282 --json body,comments. docs/design/architecture.md exists, so this run revises it: judge against it first, then decisions already recorded in the repository, then .claude/skills/architecture-review/python-practices.md, as your instructions say. Its decisions are settled; a divergence from it is owned by the open issue on the Architecture & design milestone that carries it where there is one (gh issue list --milestone "Architecture & design"), and a case for changing one of its decisions is a finding for the architecture, for the user to decide. Assign each finding's owner by the files it touches: #282 for a cross-cutting rule or its enforcement, a module pass for that module's files, "new tech-debt" for a correction too large for its pass.

The survey, from tools/architecture_survey.py at ${commit}:

${survey}`

const verifyPrompt = (c, review) => `Job 1 — refute findings. Architecture pass for #282, at commit ${commit}, concern **${c.key}**.

docs/design/architecture.md exists: test the conflict ground against it first, then CLAUDE.md, "decided" docstrings and tests that pin a trade-off. Return one verdict per finding id, using the ids exactly as given.

The reviewer's findings:

${JSON.stringify(review.findings, null, 2)}`

const mechanismsPrompt = `Scope: bot. Aspects: Mechanisms. Commit ${commit}.

Inventory each cross-cutting mechanism your instructions list, with where it is, what it does, who bypasses it, and what records why it is shaped so. This inventory is what docs/design/architecture.md is checked against, so be complete rather than brief. The survey from tools/architecture_survey.py is below; do not re-derive its counts.

${survey}`

const criticPrompt = (reviewed, mechanisms) => `Completeness check for the #282 architecture pass, at commit ${commit}. Do not review the code afresh. Say what this pass has **not** covered.

docs/design/architecture.md must settle each of these:
${SETTLE.map((s, i) => `${i + 1}. ${s}`).join('\n')}

And check that each settled decision still holds in the code:
${CANDIDATES.map(x => `- ${x}`).join('\n')}

What the pass produced, per concern — confirmed and amended findings by title, candidate stances, sound parts:
${JSON.stringify(reviewed.map(r => ({
  concern: r.concern,
  findings: r.findings.filter(f => f.verdict && f.verdict.verdict !== 'refuted').map(f => f.title),
  candidateDecisions: r.candidateDecisions,
  sound: r.sound.map(s => s.what),
})), null, 2)}

Mechanisms inventoried: ${mechanisms ? mechanisms.aspects.map(a => `${a.aspect} (${a.facts.length} facts)`).join(', ') : 'none — the surveyor returned nothing'}.

Return a gap for each settle item with no finding and no sound part behind it, each candidate no concern answered, and anything #282's survey table names that no concern examined. Give each gap a brief another reviewer could work from. An empty list is a correct answer.`

// ---- run ------------------------------------------------------------------------------------

const withVerdicts = (review, verdicts) => {
  const byId = new Map((verdicts ? verdicts.verdicts : []).map(v => [v.id, v]))
  return {
    ...review,
    findings: review.findings.map(f => ({ ...f, verdict: byId.get(f.id) || { id: f.id, verdict: 'uncertain', grounds: 'no verdict returned', evidence: [] } })),
  }
}

const reviewAndVerify = items => pipeline(
  items,
  c => agent(reviewPrompt(c), { label: `review:${c.key}`, phase: 'Review', agentType: 'python-design-reviewer', schema: REVIEW_SCHEMA }),
  (returned, c) => {
    if (!returned) return null
    // The concern is stamped from the script, not taken from the agent, so a failed one is found by key.
    const review = { ...returned, concern: c.key }
    if (!review.findings.length) return withVerdicts(review, null)
    return agent(verifyPrompt(c, review), { label: `verify:${c.key}`, phase: 'Verify', agentType: 'design-verifier', schema: VERDICT_SCHEMA })
      .then(v => withVerdicts(review, v))
  },
)

phase('Survey')
const mechanismsPending = agent(mechanismsPrompt, { label: 'survey:mechanisms', phase: 'Survey', agentType: 'design-surveyor', schema: INVENTORY_SCHEMA })

phase('Review')
log(`Reviewing ${CONCERNS.length} concerns at ${commit}; each is verified as soon as its review lands.`)
const reviewed = (await reviewAndVerify(CONCERNS)).filter(Boolean)
const failed = CONCERNS.filter(c => !reviewed.some(r => r.concern === c.key)).map(c => c.key)
if (failed.length) log(`No result for: ${failed.join(', ')} — rerun or resume before relying on this review.`)

const mechanisms = await mechanismsPending

phase('Complete')
const critic = await agent(criticPrompt(reviewed, mechanisms), { label: 'critic:completeness', phase: 'Complete', schema: GAPS_SCHEMA })
let gapResults = []
if (critic && critic.gaps.length) {
  const taken = critic.gaps.slice(0, MAX_GAPS)
  if (critic.gaps.length > MAX_GAPS) {
    log(`The critic found ${critic.gaps.length} gaps; reviewing the first ${MAX_GAPS}. Not reviewed: ${critic.gaps.slice(MAX_GAPS).map(g => g.topic).join('; ')}`)
  }
  gapResults = (await reviewAndVerify(
    taken.map((g, i) => ({ key: `gap-${i + 1}`, brief: `${g.topic}. ${g.brief}`, candidates: 'any it touches' })),
  )).filter(Boolean)
}

const all = [...reviewed, ...gapResults]
const count = v => all.flatMap(r => r.findings).filter(f => f.verdict.verdict === v).length
log(`Findings: ${count('confirmed')} confirmed, ${count('amended')} amended, ${count('uncertain')} uncertain, ${count('refuted')} refuted.`)

return {
  commit,
  concerns: all,
  mechanisms,
  gaps: critic ? critic.gaps : [],
  failedConcerns: failed,
}
