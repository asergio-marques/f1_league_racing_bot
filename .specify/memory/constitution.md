<!--
SYNC IMPACT REPORT
==================
[2026-09-02 — v7.10.0 → v7.11.0: MINOR — a class may be held to no aspect where its artwork is
per-datum, and a class's fallback may stand for the absence of artwork]
  Version change    : 7.10.0 → 7.11.0
  Bump rationale    : MINOR. Two changes to Principle XIV — one to Rule 6, one to Rule 13. No Core
                      Principle is removed and none is redefined, and MAJOR is reserved for those.

                      **Both are relaxations, and neither invalidates an existing template or asset
                      set.** Rule 6 admitted only two states for an asset class; it now admits a
                      third, and the seven classes that existed before are all still named by
                      exactly one enumeration and are governed precisely as they were. Rule 13
                      raised a notice whenever a fallback was drawn; it now permits a class to be
                      declared silent, and no class that existed before is so declared. A league
                      that upgrades sees no change to any graphic it already posts.

  Modified principles: XIV Rule 6 (asset aspect) — "Two enumerations MUST exist, and both are
                       closed" widened. The requirement was that **every** asset class be named by
                       exactly one enumeration, enforced by a test refusing any omission. It is now
                       that no class leave the check **unremarked**: a class may be named by
                       neither where the exemption is *declared* with its ground, and the test
                       refuses an **undeclared** omission. The declaration must be written twice —
                       beside the code and beside the test — so that a class escapes the check only
                       by someone recording why, never by being forgotten.

                       The ground for the new exemption is stated narrowly and is **not** marker's.
                       A class qualifies only where a league supplies one file **per datum** rather
                       than one per class, so slots of differing shape are each answered by artwork
                       of their own — which is the exact circumstance in which the one-aspect rule's
                       reasoning (one file letterboxed wherever a slot disagrees) has nothing to
                       bite on. `marker` remains exempt on its own separate ground, one directory
                       serving three shapes at once, and the two grounds are deliberately kept
                       apart so neither can be read as the general case.

                       Guarded against the obvious misreading: a class is **not** exempt because a
                       template author would prefer two shapes. And a class held to no aspect MUST
                       STILL NOT declare `preserveAspectRatio="none"` — the stretching enumeration
                       stays exclusive to `marker`. Freedom from one aspect is freedom to choose
                       the box, not licence to distort what goes in it.

                       XIV Rule 13 (asset resolution) — a class may be declared one whose fallback
                       stands for the **absence** of artwork rather than for artwork that should
                       have been supplied, whereupon drawing it raises no notice at all, neither
                       ASSET_FALLBACK_USED nor PACKAGED_ASSET_OFF_SHAPE. A class qualifies only
                       where it is drawn solely where a league's own template declares its field,
                       the module shipping no template that declares one, so a league meets the
                       class only by asking for it; having asked and drawn nothing is then the
                       ordinary state of the class and not an incomplete one. Such a class MUST
                       ship a fallback with nothing drawn upon it — the silence is defensible only
                       because what is drawn is nothing.

                       The cost is recorded rather than glossed: a misnamed file is silent too, and
                       so is renaming a datum after its artwork was drawn.

  Added sections     : none
  Removed sections   : none

  Occasioned by      : the `division_logo` class, added 2026-09-02. A league had no way to put its
                       identity on a generated graphic short of baking a mark into every template
                       at build time, which fixes it for the whole league and cannot differ between
                       divisions. The class is keyed on the division's name and is the first that is
                       optional decoration rather than data the module went looking for — which is
                       what made both of the above rules bind wrongly. It is the only class named by
                       neither aspect enumeration and the only one declared silent. The count of
                       asset classes in Rule 13's packaged-directory paragraph is corrected from
                       seven to eight in consequence; that rule is otherwise unchanged.

  Follow-up TODOs    : none. The implementation, its tests and the league-facing documents were
                       brought into line in the same change.

[2026-09-01 — v7.9.0 → v7.10.0: MINOR — a class's aspect is the league's to choose, and must
merely be consistent within one template]
  Version change    : 7.9.0 → 7.10.0
  Bump rationale    : MINOR. One change to Principle XIV, Rule 6. No Core Principle is removed and
                      none is redefined, and MAJOR is reserved for those.

                      **Predominantly a relaxation**: templates refused under v7.9.0 are now
                      accepted. A league drawing every flag slot at 2:1 was refused against a table
                      that said flags are 3:2; it is now valid. Nothing the module ships changes
                      shape, and a league that has re-shaped nothing sees no difference whatever.

                      One tightening travels with it, and it breaks no rule that was not already
                      written. v7.9.0 stated that a `preserveAspectRatio="none"` slot of a
                      non-stretching class is "not exempt but **invalid**". The implementation only
                      ever enforced that indirectly — the exemption was refused and the slot then
                      failed the *aspect* comparison, so such a slot at the class's own aspect
                      passed. It is now refused on the declaration alone. This is the code being
                      brought up to governance already in force, not new governance.

  Motivation        : The rule was stricter than its own Rationale required. That Rationale argues
                      from one file per datum and a generator that never pads: the same
                      `united_kingdom.svg` cannot be correct in a 3:2 slot and a 1:1 one at once.
                      What that argues for is **agreement** among the slots one file is drawn into.
                      It never argued for any particular number, and the rule asserted one anyway —
                      binding a template a league had authored itself, against artwork the league
                      had also authored to match it. Recorded as an owed decision in
                      `known_issues.md` under P3 on 2026-09-01, and settled here the same day.

                      The tightening is a consequence of the relaxation rather than a separate
                      thought. Once the reference is read from the template instead of from a table,
                      a template declaring *every* driver portrait slot stretching agrees with
                      itself perfectly, is passed over by the comparison, and draws every face in
                      the league squashed with nothing said. The indirect enforcement that sufficed
                      under a fixed table does not survive a derived one.

  Modified sections :
    - Principle XIV, Rule 6, the one-aspect paragraph: the aspect binds within a single template
      rather than across every template of every image type, and is read from the template being
      validated rather than named by the module. A paragraph is added forbidding any check of
      agreement *between* templates, with its reason.
    - Principle XIV, Rule 6, the enumeration paragraphs: a second enumeration is required, naming
      the classes held to one aspect at all. `marker` leaves that enumeration entirely — it is now
      exempt from the aspect rule outright rather than merely permitted to claim a per-slot
      exemption. The "every class MUST declare an aspect" requirement is withdrawn and replaced by
      a partition requirement over the two enumerations.
    - Principle XIV, Rule 6: the refusal of a `preserveAspectRatio="none"` slot outside the
      stretching enumeration is restated as a standalone obligation on Layer 2, discharged on the
      declaration alone and independently of any aspect comparison.
    - Principle XIV, Rule 6: a paragraph is added on the module's own artwork, which carries a
      fixed aspect per class a league cannot alter, and the notice owed where it is drawn stretched.
    - Principle XIV, Rule 6, Rationale: corrected to say that the argument compels agreement and
      never a number.
  Added sections    : none
  Removed sections  : none
  Deferred items    : none. No placeholder tokens remain in the document.
  Templates review  : `.specify/templates/` states neither rule and needed no change. The
                      module-facing restatements were amended in the same change:
                      `src/models/image_constants.py` (`RATIO_CONSISTENT_ASSET_CLASSES` added;
                      `ASSET_CLASS_ASPECTS` renamed `PACKAGED_ASSET_ASPECTS` and redocumented as our
                      own artwork's shape rather than a league's rule;
                      `NOTICE_PACKAGED_ASSET_OFF_SHAPE` added),
                      `src/services/image_validity_service.py` (`aspect_faults_of` replaced by
                      `class_aspect_faults_of`, `stretch_faults_of` and `class_aspect_of`),
                      `src/models/image_module.py` (`PROBLEM_ASPECT_DISAGREEMENT`),
                      `src/utils/svg_fill.py` (the off-shape notice), `README.md`,
                      `resources/README.md`, `resources/defaults/templates/README.md`,
                      `docs/how-to/configuring-the-image-module.md`,
                      `docs/wip-specs/image_module_specification.md` and
                      `docs/wip-specs/known_issues.md` (P3 retired as answered).

[2026-09-01 — v7.8.0 → v7.9.0: MINOR — only a stretching class's slots may claim the stretch exemption]
  Version change    : 7.8.0 → 7.9.0
  Bump rationale    : MINOR. One change to Principle XIV, Rule 6. No Core Principle is removed and
                      none is redefined, and MAJOR is reserved for those.

                      **Unlike v7.5.0, this one can make a previously accepted template
                      non-conforming, and that is its purpose.** A template authored under v7.5.0
                      that declared a slot of a non-stretching class `preserveAspectRatio="none"` was
                      accepted; it is now refused, naming the field. This is recorded plainly rather
                      than glossed: the version is MINOR by this project's convention that MAJOR is
                      reserved for Core Principles, not because nothing breaks.

                      What is affected in practice is narrow. Nothing the module ships is touched —
                      every slot carrying the declaration across all fifteen packaged templates
                      belongs to `marker`, verified by a test added with this amendment. Only a
                      league-authored template can be caught, and only where it was already drawing
                      distorted artwork with nothing reported.

  Motivation        : The exemption was open to any slot of any class, and a slot's own claim was
                      taken as sufficient. A league authoring its own lineup template could declare
                      a driver portrait slot at 2:1, tell it to stretch, and be told nothing — every
                      face in the league drawn squashed to half height, which no artwork the league
                      supplies could correct. The check existed precisely to catch that, and the
                      declaration had become a way to opt out of it.

                      Reachable rather than theoretical as of this date: driver portraits are now
                      obtained from Discord profile pictures, so the class draws real faces for every
                      league rather than a grey placeholder nobody would look twice at.

                      The v7.5.0 reasoning is preserved entire. That amendment moved the exemption to
                      the slot so that admitting the standings marks to `marker` would not take the
                      position-change arrows' 1:1 check away as collateral. It still does not: within
                      `marker` the arrows are checked exactly as before, because membership of the
                      enumeration is permission to claim the exemption and never the exemption
                      itself. What is withdrawn is only its availability to classes whose data have a
                      shape of their own.

  Modified sections :
    - Principle XIV, Rule 6, the stretching paragraphs: the exemption is now available only to a
      slot whose class is enumerated as stretching. A paragraph is added requiring that enumeration
      to exist and be closed, and making a `preserveAspectRatio="none"` slot of a non-enumerated
      class invalid rather than exempt. The "sole mechanism" and "never to the class" statements are
      restated rather than struck: the declaration remains the sole mechanism and the slot remains
      the claimant, within a class permitted to claim.
    - Principle XIV, Rule 6, Rationale: one paragraph added, stating why the escape must be closed
      to classes whose subjects have a shape of their own.
  Added sections    : none
  Removed sections  : none
  Deferred items    : none. No placeholder tokens remain in the document.
  Templates review  : `.specify/templates/` states neither rule and needed no change. The
                      module-facing restatements were amended in the same change:
                      `src/models/image_constants.py` (`STRETCHABLE_ASSET_CLASSES` added, holding
                      `marker` alone), `src/services/image_validity_service.py` (`aspect_faults_of`
                      now resolves the asset class *before* testing the exemption, the reverse of how
                      it read), `README.md`, `resources/README.md`,
                      `docs/how-to/configuring-the-image-module.md` and
                      `docs/wip-specs/image_module_specification.md`.
                      `resources/defaults/templates/README.md` states the slot-level rule and is
                      **left as it stands**: every slot it describes belongs to `marker`, so every
                      sentence in it remains true.
                      Three tests were added: a non-stretching slot claiming the exemption is now a
                      fault (driver portrait, flag and team badge), the `marker` case still passes,
                      and every packaged template is asserted to remain valid under the narrower rule.
                      `docs/wip-specs/known_issues.md` records the open question this does *not*
                      settle — whether a class's aspect ought to bind a template a league authored
                      itself at all.
-->

<!--
SYNC IMPACT REPORT
==================
[2026-09-01 — v7.7.0 → v7.8.0: MINOR — the tyre class is closed, and the module supplies the five compounds]
  Version change    : 7.7.0 → 7.8.0
  Bump rationale    : MINOR. No Core Principle is removed and none is redefined. Rule XIV.13's
                      closed-set clause is unchanged in its statement, its granularity and its
                      consequence; one class moves across the line the clause already drew, and the
                      data model gains a constraint on one nullable column.

                      Nothing conforming to v7.7.0 becomes non-conforming. Every resolution that
                      succeeded before succeeds identically: a league's own file still wins, its own
                      fallback still beats the packaged tier, and the notice kind is unchanged. Only
                      a render that would have drawn a **generic placeholder** for a compound now
                      draws the module's own correct file for it — an improvement in what is drawn,
                      never a refusal of anything previously accepted.

                      It is MINOR rather than PATCH because the tyre class genuinely changes side:
                      the packaged directory is now searched under the datum's own name for it,
                      which it was expressly forbidden to be. It is not MAJOR because that is
                      reserved for Core Principles, and because the change is permissive.

  Motivation        : A tyre compound is not a value a league named. It is what the game offers —
                      five of them and no sixth — in exactly the sense the "class settles it"
                      granularity already describes for the three directions of a change of standing
                      position and the eight weathers. The clause's own test is whether the league
                      chose the vocabulary and can be incomplete against it, and against five fixed
                      compounds it cannot be.

                      The consequence of having it on the wrong side was concrete: `resources/
                      defaults/tyres/` shipped a `fallback.svg` and nothing else, so a league that
                      had not drawn five icons got a grey placeholder and a notice on **every**
                      qualifying row — for artwork it was never expected to supply. That is the
                      "three identical arrows and a notice apiece" outcome the clause's closing
                      paragraph already names as the thing it exists to prevent, arrived at for
                      tyres by an accident of which list the class sat in.

  Modified sections :
    - Principle XIV, Rule 13, "The class settles it" bullet: gains the five tyre compounds
      (`soft`, `medium`, `hard`, `intermediate`, `wet`) beside the position-change directions and
      the weather vocabularies. The bullet's rule is unchanged; a third vocabulary meets it.
    - Principle XIV, Rule 13, the MUST NOT sentence: `tyre` is struck from the list of classes whose
      data are values a league named. It now reads "and likewise team and driver". The sentence's
      reasoning — a league supplying most of its country flags must not be handed ours for the
      remainder — is untouched and still governs flag, track, team and driver.
    - Data model appendix, qualifying session result: the `tyre` column is constrained to the five
      compounds or null, as `outcome_modifier` beside it is constrained to its four values. Null
      keeps its existing meaning: the submission recorded no compound.

  Not changed, and deliberately:
    - The four resolution outcomes, and the rule that the datum's own file is sought in the
      configured directory alone. The closed-set bullet remains the sole exception to it.
    - Rule XIV.13's per-field absent-datum declaration and its worked example — "A qualifying entry
      for which no tyre was recorded draws the tyre fallback, the submission of a session not
      obliging one". A vocabulary constrains what a compound may **be**, never whether one was
      recorded, so an absent tyre is unaffected and still reports nothing.
    - Rule XIV.13's normalisation rule, which already names a tyre compound among the data one rule
      serves. It is what makes the accepted spellings of a compound and its filename the same rule.
    - Principle XIV's "imagery that identifies" clause, which already classes a tyre compound as a
      picture of a **fact** rather than of an entity — the reading this amendment follows to its
      conclusion rather than departs from.
    - `ASSET_CLASS_ASPECTS`, the seven asset directories, and the `tyre-directory` configuration
      command. A closed-set class keeps its directory command: `marker` and `weather` have theirs.

  Added sections    : none
  Removed sections  : none
  Deferred items    : none. No placeholder tokens remain in the document.

  Follow-up TODOs   : None. `resources/defaults/tyres/` gains `soft.svg`, `medium.svg`, `hard.svg`,
                      `intermediate.svg` and `wet.svg` beside its `fallback.svg`;
                      `CLOSED_SET_ASSET_CLASSES` gains `tyre`; and the qualifying submission parsers
                      canonicalise the compound so a stored value is always one the artwork answers.
                      `README.md`, `resources/README.md`, the image and results wip-specs and the
                      image and results how-to guides are brought into step by the same change, per
                      the close-out discipline in CLAUDE.md.
-->

<!--
SYNC IMPACT REPORT
==================
[2026-09-01 — v7.6.0 → v7.7.0: MINOR — the render-notice audit record is withdrawn; the log channel is the record]
  Version change    : 7.6.0 → 7.7.0
  Bump rationale    : MINOR. No Core Principle is removed and none is redefined, which is what this
                      document reserves MAJOR for. Principle XIV stands, Rule XIV.4 stands, and
                      XIV.4's "Where each is reported" — the operative obligation — is unchanged to
                      the word: the calculation log channel always, a commanding command's output
                      additionally, never a channel the drivers read.

                      Nothing conforming to v7.6.0 becomes non-conforming. An obligation is
                      withdrawn, not imposed, and nothing is forbidden that was previously required.

                      It is MINOR rather than PATCH because a requirement genuinely goes rather than
                      being reworded, as the narrowing at v7.5.0 was MINOR for the same reason. The
                      policy's MINOR wording speaks of guidance *expanded*, which does not literally
                      describe a withdrawal; of the three slots this document offers it is
                      nonetheless the right one, MAJOR being reserved for Core Principles and PATCH
                      for changes carrying no semantic weight.

  Motivation        : The record was never read. `_persist` was its only writer, no SELECT against
                      `image_render_notices` existed anywhere in `src/`, and the index it carried on
                      (server_id, rendered_at) served a reader that was never built. It had no
                      retention rule and grew one row per field per render — some 1,599 for a single
                      standings image — reaching 4.8 MB of the first production database's 5.3 MB,
                      or 91.6% of it, against some 450 KB for the entire league it was supposedly
                      auditing. Dropping it returned that database to 0.44 MB.

                      The requirement was also asserted rather than stated. XIV.4 requires a notice
                      be *reported*, and names the two destinations; it never required one be
                      stored. The obligation to store came from the data model appendix describing
                      the entity as "the audit record required by Principle XIV.4" — a requirement
                      the rule's own text does not contain.

  Modified sections :
    - Principle XIV, Rule 4, "How they are reported": the closing sentence no longer presupposes a
      stored record. It still says the grouping is presentational — that notices are grouped for the
      message and not merged — which is the load-bearing half of what it said.
    - Data model appendix: the **RenderNotice** entity and its seven columns are replaced by a
      statement that notices are not persisted, naming where they are reported instead. This makes
      them symmetrical with render *problems*, which the paragraph beneath already declared
      unpersisted for much the same reason.

  Not changed, and deliberately:
    - Principle XIV, Rule 4, "Where each is reported". It is now the whole of the obligation, and it
      needed no amendment to become so.
    - The four notice kinds, and what each of them means.
    - Principle V. Its subject is configuration mutations and weather computations, and its
      rationale already asks for a record that is channel-visible.

  Added sections    : none
  Removed sections  : the RenderNotice entity in the data model appendix.
  Deferred items    : none. No placeholder tokens remain in the document.

  Follow-up TODOs   : None. Migration 046 drops `image_render_notices` and its index; `_persist`,
                      the `db_path` the render service held only for it, and the three model columns
                      that existed only to be written there go with them. The reporting path —
                      `grouped_notice_lines`, `format_notices`, `post_notices` — is untouched.
                      `README.md` and `docs/how-to/configuring-the-core-bot.md` are brought into
                      step by the same change, per the close-out discipline in CLAUDE.md. The image
                      module wip-spec never described the table and needs no correction.
-->

<!--
SYNC IMPACT REPORT
==================
[2026-09-01 — v7.5.0 → v7.6.0: MINOR — the vertical crop carries up what spans it, not the footer alone]
  Version change    : 7.5.0 → 7.6.0
  Bump rationale    : MINOR. One change to Principle XIV, Rule 2, and it **states a third effect of
                      an operation that already existed**. No principle is removed and none is
                      redefined incompatibly.

                      Nothing conforming to v7.5.0 becomes non-conforming. A template drawing
                      nothing across its crop points is cropped exactly as it was; a template
                      drawing something across one was, under v7.5.0, drawing something the rule
                      below already told it not to draw there, and is now drawn correctly instead of
                      being told off for it. No template is invalidated and none must be redrawn.

                      It is MINOR rather than PATCH because the crop now does something it did not
                      do before — it rewrites the geometry of elements other than the root — which
                      is materially expanded guidance and not a clarification of what was written.

  Motivation        : Every shipped grid rules a separator down its round columns, from the headings
                      to just above the caption band. The crop rewrote the canvas and carried the
                      band up; nothing moved the separators, so on a division drawn short they ran
                      to the new cut edge and straight through the band that had just moved above
                      them — the one place the template had said they must not reach. The fault was
                      in the operation rather than in any one template, and so is the remedy.

  Modified sections :
    - Principle XIV, Rule 2, fill-operations table: the **Vertical crop** row's effect gains the
      shortening, so the table names all three halves of what the operation does.
    - Principle XIV, Rule 2, "The vertical crop": two paragraphs added after the footer-group
      paragraph. The first states the shortening and the distance it preserves; the second states
      which shapes it applies to (a line, a rectangle, a path drawing one straight vertical rule),
      that a purely translating transform is followed, and that a scaled or rotated subtree, the
      footer group's own subtree and any definition are left as they stand.
    - Principle XIV, Rule 2, the sentence forbidding a template to draw below a crop point: **not**
      amended here. It governs what lies wholly below the cut, which is still lost, and the wip-spec
      carries the same correction in the module's own words.
  Added sections    : none
  Removed sections  : none
  Deferred items    : none. No placeholder tokens remain in the document.
  Templates review  : `.specify/templates/` states nothing about the crop and needed no change. The
                      rule was implemented and restated in the same change: `src/utils/svg_fill.py`
                      (`_shorten_across_crop`, `_canvas_offset`, `_path_rule`),
                      `docs/wip-specs/image_module_specification.md` (§ The vertical crop, and the
                      calendar's prohibition on spanning elements qualified for the canvas
                      background), `README.md` and `docs/how-to/configuring-the-image-module.md`
                      (the template-authoring callouts). Covered by
                      `tests/unit/test_image_row_crop.py`, whose new cases fail without the rule.
-->

<!--
SYNC IMPACT REPORT
==================
[2026-09-01 — v7.4.0 → v7.5.0: MINOR — the stretching exemption moves from the class to the slot]
  Version change    : 7.4.0 → 7.5.0
  Bump rationale    : MINOR. One change to Principle XIV, Rule 6, and it **narrows the scope of an
                      exemption while widening what may claim it**. Neither removes a principle nor
                      redefines one incompatibly.

                      Nothing conforming to v7.4.0 becomes non-conforming. v7.4.0 already required
                      every slot of an exempt class to be authored `preserveAspectRatio="none"`, so
                      every slot that was exempt under it declares exactly what v7.5.0 asks of it
                      and stays exempt. What changes is the bookkeeping around them: the class-level
                      enumeration is withdrawn and every class declares an aspect again, which makes
                      the aspect table complete rather than deliberately gapped.

                      It is MINOR rather than PATCH because a class MAY now do something it could
                      not before — carry slots of its own aspect and stretching slots at once —
                      which is materially expanded guidance and not a clarification.

  Motivation        : `marker` came to draw three vocabularies at once: the square position-change
                      markers, the standings result marks, and the attendance limit marks. Their
                      cells are three different shapes. Under v7.4.0 the only way to admit them to
                      one class was to declare the whole class exempt, which would have taken the
                      markers' 1:1 check away as collateral. Locating the exemption on the slot —
                      where the author who chose the box already declares it — keeps every check
                      that still has something to check.

  Modified sections :
    - Principle XIV, Rule 6, the stretching paragraphs: rewritten from a class-level exemption to a
      slot-level one. The requirement to enumerate exempt classes and hold two tables to each other
      is struck, replaced by the requirement that every class declare an aspect and that a class
      missing one be refused. A paragraph is added admitting one class serving fixed-shape and
      stretching slots together.
    - Principle XIV, Rule 6, Rationale: one sentence added, stating why a stretching slot escapes
      the one-file-per-datum argument rather than weakening it.
  Added sections    : none
  Removed sections  : none
  Deferred items    : none. No placeholder tokens remain in the document.
  Templates review  : `.specify/templates/` states neither rule and needed no change. The
                      module-facing restatements were amended in the same change:
                      `src/models/image_constants.py` (STRETCHING_ASSET_CLASSES deleted, every class
                      given an aspect), `src/services/image_validity_service.py` (`aspect_faults_of`
                      passes over a stretching slot), `resources/README.md`,
                      `resources/defaults/templates/README.md`, `README.md`,
                      `docs/how-to/configuring-the-image-module.md` and
                      `docs/wip-specs/image_module_specification.md`. The two tests that held the
                      old enumerations — `test_every_asset_class_declares_an_aspect_or_is_declared_exempt`
                      and the shipped-asset aspect check — were rewritten to the slot rule and the
                      per-datum exemption respectively.
-->

<!--
SYNC IMPACT REPORT
==================
[2026-08-31 — v7.3.0 → v7.4.0: MINOR — a class whose slots stretch, and the withdrawal of the
 gradient prohibition]
  Version change    : 7.3.0 → 7.4.0
  Bump rationale    : MINOR. Two changes to Principle XIV, Rule 6. Neither removes a principle
                      and neither redefines one incompatibly, which is what the versioning
                      policy reserves MAJOR for.

                      The first **adds an alternative** to the one-aspect rule rather than
                      relaxing it. A class may declare that its slots stretch, and the rule then
                      does not bind it. Every class conforming to v7.3.0 declared an aspect and
                      carries on unchanged; nothing previously valid becomes invalid. The
                      exemption is guarded rather than free — it must be declared, because
                      absence is the mechanism that skips the check and a forgotten aspect would
                      otherwise escape it silently.

                      The second **strikes a prohibition**, which widens what conforms rather
                      than narrowing it: every asset valid under v7.3.0 is valid under v7.4.0.
                      The contrast with v6.3.0 → v7.0.0 is the point — that took MAJOR because
                      **Truncate** was struck from a *closed enumeration of operations*, making a
                      previously conforming implementation newly wrong. Striking a ban on an
                      input makes nothing newly wrong.

  Modified sections :
    - Principle XIV, Rule 6, the plain-SVG sentence: `gradient` struck from the list of forbidden
      constructs, with a paragraph recording why the ban is withdrawn — it was never justified
      anywhere, the module's own templates relied on gradients, and the id-collision hazard that
      might have justified it was tested and does not arise. `clipPath`, filter and the no-text
      rule are untouched.
    - Principle XIV, Rule 6, after the one-aspect paragraph: two paragraphs added, admitting a
      class whose slots stretch and requiring that such a class be enumerated rather than merely
      lacking an aspect.
  Added sections    : none
  Removed sections  : none
  Deferred items    : none. No placeholder tokens remain in the document.
  Templates review  : `.specify/templates/` states neither rule and needed no change. The
                      module-facing restatements — `resources/README.md`, the asset tables in
                      `src/models/image_constants.py`, and the shipped-asset tests — were amended
                      in the same change, `STRETCHING_ASSET_CLASSES` being the enumeration this
                      amendment requires.
-->
<!--
SYNC IMPACT REPORT
==================
[2026-08-31 — v7.2.0 → v7.3.0: MINOR — a valueless field may be drawn by colour alone, and a
 data-driven palette belongs to the template]
  Version change    : 7.2.0 → 7.3.0
  Bump rationale    : MINOR. Principle XIV's recolour rules gain materially expanded guidance in
                      two parts, and neither strikes anything from a closed enumeration. The
                      enumeration of fill operations is untouched: recolour is still one of the
                      six, and no operation is added or removed.

                      The first part **narrows an obligation and exempts a case**. v7.2.0 read "a
                      recoloured field MUST still be filled" without qualification, which no field
                      had yet contradicted — every recoloured field to date carried text. The
                      standings highlight chips are the first fields drawn by their colour alone,
                      and Rule 3 already admits a valueless field for the vertical crop point; this
                      says the same exemption reaches a recoloured one. An implementation
                      conforming to v7.2.0 filled every recoloured field and therefore still
                      conforms, so this is not read as a backward-incompatible redefinition.

                      The second part is a **new obligation**: where the data decide the colour,
                      the palette MUST come from the template's stylesheet rather than from bot
                      configuration, and an unnamed kind MUST NOT be painted. That is added
                      guidance, which the versioning policy classes as MINOR. It records a decision
                      taken in conversation on 2026-08-31.

  Modified sections :
    - Principle XIV, the fill-operations table: the Recolour row now names where a data-driven
      palette comes from.
    - Principle XIV, the recolour paragraph following the vertical crop. Split into three: the
      existing merge-into-inline-style requirement with "must still be filled" now bound to a field
      that carries a value; a new paragraph exempting a valueless field, referred to Rule 3 and to
      the crop point it already covers; a new paragraph placing a data-driven palette in the
      template's stylesheet and forbidding the painting of a kind it names no rule for.
  Added sections    : none
  Removed sections  : none
  Deferred items    : none. No placeholder tokens remain in the document.
  Templates review  : `.specify/templates/` carries no statement of the recolour rules and needed
                      no change. The rules themselves are restated for the image module in
                      `docs/wip-specs/image_module_specification.md`, which was amended in the same
                      change under "What a generation does to a field".
-->
<!--
SYNC IMPACT REPORT
==================
[2026-08-31 — v7.1.0 → v7.2.0: MINOR — an asset href MUST be absolute, and an absent linked image MUST refuse]
  Version change    : 7.1.0 → 7.2.0
  Bump rationale    : MINOR. Principle XIV, Rule 6 gains materially expanded guidance in two parts,
                      and neither strikes anything from a closed enumeration.

                      The first part is a **clarification of a requirement that already stood**.
                      v7.1.0 already read "An asset MUST be referenced by an href that is a URI. A
                      bare filesystem path is not one." A relative filesystem path is a bare
                      filesystem path, so an implementation writing one was already non-conforming;
                      naming the project root as the base it MUST be resolved against says how to
                      conform, not what conformance now is. On its own this half would be PATCH.

                      The second part is a **new obligation**: the module MUST refuse a graphic
                      linking an image absent from the host. That is added guidance, which the
                      versioning policy classes as MINOR. It is deliberately not read as MAJOR: the
                      policy reserves that for "backward incompatible governance/principle removals
                      or redefinitions", and nothing is removed or redefined here. The contrast with
                      v6.3.0 → v7.0.0 is the point — that took MAJOR because **Truncate** was
                      *struck* from a closed enumeration of fill operations, making a previously
                      conforming implementation newly wrong by an act of deletion. This version
                      deletes nothing.

  Modified sections :
    - Principle XIV, Rule 6 (Assets), the asset-href paragraph. Rewritten in three parts: the href
      MUST now be an **absolute** URI, with a relative reference resolved against the project root
      before it is placed on a field; the module MUST refuse a graphic linking an absent image,
      naming the element and the file, and this binds an `<image>` a league authored into its own
      template as much as one the module placed; and the closing sentence is corrected in tense.

  Corrected claim   : the paragraph asserted that a broken asset href is caught by no render-time
                      check. That ceased to be true on 2026-08-31, when such a check was built. The
                      sentence is now scoped to the period before that date, and Rule 14's
                      remaining justification is narrowed to the cases still uncaught by any check —
                      flowed text, substituted fonts, and the crop. Rule 14 itself is untouched:
                      its own list already names "unresolvable asset references", which remains a
                      case where browser and rasteriser disagree even though the module now refuses
                      it before the rasteriser is reached.

  Evidence          : measured on the host's Inkscape 1.4. An href relative to the project root and
                      an href naming a file that never existed produce **byte-identical** PNGs, both
                      exiting 0 with an empty stderr. There is therefore no diagnostic to read after
                      a render, which is why the obligation is to refuse *before* rasterising rather
                      than to inspect the rasteriser's output.

  Added sections    : none.
  Removed sections  : none.
  Deferred TODOs    : none.

[2026-08-28 — v7.0.0 → v7.1.0: MINOR — the vertical crop becomes a general operation and carries a declared footer group]
  Version change    : 7.0.0 → 7.1.0
  Bump rationale    : MINOR. The fill-operations table is introduced by "The module MUST support
                      exactly these fill operations, and no others", and **Vertical crop** is added
                      to it. That **widens** a closed enumeration rather than narrowing one: an
                      implementation conforming to v7.0.0 — which already performed the crop, for
                      the calendar — does not violate this version, and no template authored to
                      v7.0.0 is made wrong by it.

                      The contrast is deliberate. v6.3.0 → v7.0.0 took MAJOR on the same table
                      because **Truncate** was *struck* from it, making an implementation that
                      truncated newly wrong. Nothing is struck here. The only sentence removed —
                      "Vertical crop … is specific to the calendar image type and is specified
                      there, not as a general operation" — is a *restriction* being lifted, and
                      lifting a restriction is the MINOR case by the policy's own reading.

                      The footer group is a new **optional** template field, which the policy
                      classifies as materially expanded guidance. It is optional in the strong
                      sense: a template declaring neither crop points nor a footer group renders at
                      full height exactly as it does today, so no league's hand-authored file is
                      invalidated. Only the calendar continues to *require* crop points, which it
                      already did.

  Modified sections :
    - Principle XIV, Rule 2, fill-operations table: a **Vertical crop** row is added, targeting the
      root at a declared crop point and stating both halves of the effect — the footer group is
      carried up, then the root `height` and `viewBox` are rewritten. The table rises from five
      operations to **six**. The module docstring of `src/utils/svg_fill.py` already counted six
      under a different grouping (it counted the crop and separated group removal) and so is
      unaffected by the count.
    - Principle XIV, Rule 2 — the sentence confining the crop to the calendar is **struck**, and a
      new subsection, "The vertical crop", stands in its place. It states: that any image type
      whose capacity is fixed by the template (Rule 12) may declare a crop point per member; that
      the cut is taken at the crop point of the last member the **data** fill; that a crop point is
      a valueless field under Rule 3, so its absence is fatal and its never carrying a value is
      not; that a declared footer group is carried up by the height difference *before* the canvas
      is rewritten; that the last **declared** member's crop point is expected to sit at the
      declared canvas height, a divergence being a notice under Rule 4 and never a refusal; and
      that declaring crop points is optional for every type but the calendar, a template declaring
      none rendering at full height.
    - Principle XIV, Rule 12 — "for an image type that defines a vertical crop" becomes "where the
      template declares crop points", with a cross-reference to Rule 2. The rule already
      anticipated a crop reaching beyond the calendar; only its phrasing tied the mechanism to the
      image type rather than to the file.

  Added sections    : None as such — "The vertical crop" is a subsection of Rule 2, replacing the
                      one-sentence deferral that stood there.

  Removed sections  : None.

  Templates checked : `.specify/templates/` — plan, spec, tasks, checklist, agent-file and
                      constitution templates carry no reference to the fill-operations table or to
                      the crop, so none needed updating.

  Follow-up TODOs   : None.

  Session context   : Decided in conversation on 2026-08-28, while shortening the standings,
                      attendance and results templates to the rows a division actually fills. The
                      calendar had cropped itself since 037; the four row templates could not,
                      and a division of twenty drivers was drawn on a fifty-row canvas.

                      The obstacle was that all four shipped templates carry caption text *below*
                      their last row — "DRIVER CHAMPIONSHIP", "RACE CLASSIFICATION" — which a plain
                      crop cut off. Three answers were weighed: re-lay the four templates to put
                      the captions above the rows (no amendment, but it loses the caption for any
                      league whose own template keeps one below); derive the cut from the row pitch
                      and the template's bottom margin (no amendment and no template edit, but it
                      guesses, and still loses the caption); or carry the footer up with the crop.
                      The third was chosen: it is the only one that draws the graphic correctly,
                      and the amendment it costs is a widening rather than a break.

[2026-08-27 — v6.3.0 → v7.0.0: MAJOR — text is never cut; a field declares a box and a line budget, and is reduced until it fits; lines are centred in their box]
  Version change    : 6.3.0 → 7.0.0
  Bump rationale    : MAJOR. An operation is removed from a closed list and a mandatory behaviour
                      is withdrawn outright, so an implementation conforming to v6.3.0 violates
                      this version.

                      The fill-operations table is introduced by "The module MUST support exactly
                      these fill operations, and no others". **Truncate** is struck from it. That
                      is a removal from a closed enumeration, which the project's own policy
                      reserves MAJOR for, and it is not softened by anything added beside it.

                      Rule 5 loses the cut and the ellipsis entirely. v6.3.0 *required* overflow to
                      be "cut at a word boundary, ended with an ellipsis"; this version forbids it.
                      A module that truncates is now wrong where before it was obliged to, and the
                      two rules cannot both be satisfied. The precedent is v5.0.1 → v6.0.0, which
                      took MAJOR on the same reasoning when Rule XIV.11 lost the key discriminator.

                      Two clauses of the wrapping contract are superseded rather than deleted: the
                      line count derived from the rectangle's height, and its recomputation at the
                      reduced leading, both now yield to a declared `max-lines` and both still
                      govern a field that declares none.

                      What prompted it: circuit names (Imola), country names (USA), grand prix
                      names (US GP) and date-times were overrunning their room and overlapping
                      neighbouring fields on the calendar, RSVP and weather graphics. The cause was
                      that those fields declared no bound at all, and the remedy the constitution
                      offered — an `inline-size` that cuts — draws "Autodromo Enzo e Dino Ferra…",
                      which names no circuit. The league's own words are now reduced instead, never
                      cut.

  Modified sections :
    - Principle XIV, fill-operations table: the **Truncate** and **Text wrap** rows are replaced by
      a single **Text fit** row, targeting a `<text>` carrying a declared box and stating that
      nothing is ever cut. The table therefore falls from six operations to **five**: wrapping and
      reducing are no longer separable, since a field is wrapped to its budget and reduced until it
      meets it by one and the same rule. The module docstring of `src/utils/svg_fill.py` enumerates
      the same operations under a different grouping — it counts the vertical crop and separates
      group removal — and so stays at six, needing only "Text wrap" renamed to "Text fit".
    - Principle XIV, Rule 5 — retitled in substance though not in name. The opening two paragraphs
      become a definition of the **box**, declarable in CSS (`inline-size` for width, `max-lines` ×
      `line-height` for height, centred on the field's declared `y`) or by `shape-inside` as
      before, with the rectangle winning where both are declared. `max-lines` is introduced as the
      line budget, with the prior height-derived rule retained for fields declaring none.
    - Principle XIV, Rule 5 — "The wrapping contract" becomes "The fitting contract". The
      within-itself word break is promoted from the withdrawn first paragraph to a bullet of its
      own. The floor bullet is split in two: one stating that the text is never cut, one stating
      that half the declared size raises a notice but **stops nothing**, with a one-pixel hard stop
      against a box of no usable width. A bullet requiring vertical centring is added, naming
      `shape-inside` prose as the top-aligned exception. A rationale paragraph, "Why the cut was
      withdrawn", closes the contract.
    - Principle XIV, Rule 5 — the three structural defects: the second is restated of any field
      needing a `line-height` rather than of a wrapped one; the third narrows from "no usable width
      and height" to **no usable width**, height having ceased to fix the budget, and gains a
      `max-lines` that is not a positive whole number.
    - Principle XIV, Rule 5 — "The module places no ceiling on free text" loses "and the cut", the
      reduction now being the whole of the answer, and asks the league for a rectangle its longest
      prose fits **at a size worth reading**.
    - Governance footer: version.

  Added sections    : None. Every change lands inside Principle XIV, Rule 5 and its table.

  Removed sections  : The **Truncate** fill operation. The ellipsis is removed from the module
                      entirely — it appears nowhere in the constitution after this amendment except
                      in the rationale recording why it went.

  Not changed, and deliberately:
    - The **Measurement** paragraph. Erring narrow matters more under this version, not less: a
      measurement that erred wide previously cost an unnecessary cut and now costs an unnecessary
      reduction, which is milder. The rule was already right.
    - Rule 4's unit of failure, and Rule 9's three moments. A `max-lines` defect is structural in
      exactly the way the other two are — read off the template alone — so it joins the existing
      list rather than needing a new mechanism.
    - "Overflow MUST NOT be silently truncated" in the collections rule. That governs *rows* of a
      collection overflowing a template's declared capacity, not text overflowing its width, and is
      a separate concern that this amendment does not touch.
    - Recolour, group removal, image fill and vertical crop, all untouched.
    - No template under `.specify/templates/` mentions text bounds, truncation or wrapping, so none
      needed updating. Checked: plan, spec, tasks, checklist, agent-file, constitution templates.

  Follow-up TODOs   : The implementing change does **not** yet exist in the tree and this amendment
                      precedes it, which is the reverse of the usual order and is deliberate — the
                      constitution forbade the target behaviour, so it had to move first. Owed by
                      the same change, per the close-out discipline in CLAUDE.md:
                        - `src/utils/svg_fill.py` — collapse `_set_text` and `_lay_out` onto one
                          fitting routine; delete `_truncate_to_width`, `_ellipsise_line` and the
                          `ELLIPSIS` constant; centre lines vertically.
                        - `src/models/image_constants.py` — retire `NOTICE_INLINE_SIZE_TRUNCATED`
                          and `NOTICE_WRAP_TRUNCATED` for one notice naming a field reduced below
                          its floor.
                        - `src/services/image_validity_service.py` — carry the narrowed and added
                          structural defects.
                        - The fourteen templates under `resources/defaults/templates/`, which today
                          leave circuit, country, grand prix, date and time unbounded.
                        - `docs/wip-specs/image_module_specification.md`, `README.md` and the image
                          module's how-to guide.

[2026-08-27 — v6.2.0 → v6.3.0: MINOR — the 3-second budget is stated of autocomplete, which cannot defer and must therefore answer empty rather than late]
  Version change    : 6.2.0 → 6.3.0
  Bump rationale    : MINOR. The acknowledgement rule named a remedy that does not exist on one
                      of the two paths it governs, and the gap was reached in production rather
                      than in theory.

                      "The bot MUST acknowledge any command within 3 seconds; long-running
                      operations MUST use Discord's deferred response mechanism" is sound for a
                      command. An **autocomplete** interaction carries the same three-second
                      budget, but Discord provides no deferral for one: there is no
                      `interaction.response.defer()` equivalent on that path. A reader following
                      the rule literally would reach for the escape hatch and find none.

                      This was not hypothetical. On 2026-08-25 an `/images test lineup`
                      autocomplete failed with `404 Unknown interaction` (error code 10062) after
                      a contended database delayed its reply past the budget. The traceback lay
                      entirely inside discord.py — the callback had built its choices correctly
                      and simply answered too late, into a token that had already expired.

                      MINOR rather than PATCH: this is not a wording correction. It places a new
                      obligation on a path the constitution did not previously govern — an
                      autocomplete MUST bound its own runtime, MUST prefer an empty answer to a
                      late one, and MUST NOT propagate its failure into the command it serves.
                      The project's own policy reserves MINOR for "materially expanded guidance",
                      which this is.

                      MINOR rather than MAJOR: no existing guarantee is narrowed or withdrawn.
                      The command-path rule is untouched, and nothing that satisfied v6.2.0 fails
                      under v6.3.0.

  Modified sections :
    - Bot Behavior Standards: a new bullet follows the 3-second acknowledgement rule, stating the
      budget of autocomplete, recording that no deferral exists there, and requiring latency to be
      removed at source instead. Carries three obligations: bound the runtime, answer empty rather
      than late, never break the parent command.
    - Governance footer: version and Last Amended date.

  Added sections    : None
  Removed sections  : None

  Not changed, and deliberately:
    - The 3-second command rule itself, which was correct as it stood. The amendment sits beside
      it rather than rewriting it.
    - Data & State Management's durability requirement. The same change adopted WAL journalling
      on both databases and deliberately kept `synchronous=FULL`, declining the faster
      `synchronous=NORMAL` because it leaves a window in which a power cut loses the most recent
      commits. That decision is strictly more conservative than the rule requires, so the rule
      needs no amendment.
    - Performance & Storage Considerations' "no additional caching layer is required at the
      current scale". The change memoises the operating system's IANA time-zone list — static
      data owned by the host, not league data — and deliberately declined to cache the 28
      circuits, which are database rows. The clause governs league data at scale and is not
      engaged. A clarification distinguishing the two was considered and judged unnecessary.
    - No template under `.specify/templates/` mentions the 3-second rule, deferral or
      autocomplete, so none needed updating. Checked: plan, spec, tasks, checklist, agent-file,
      constitution templates.

  Follow-up TODOs   : None. The implementing change is already in the tree and satisfies the new
                      rule: `src/utils/autocomplete.py` bounds every autocomplete and returns no
                      choices on overrun, and `src/utils/log_filters.py` records the unavoidable
                      residual race as a warning rather than an error.

[2026-08-25 — v6.1.0 → v6.2.0: MINOR — the closed-set clause is stated of a datum rather than of a class; the packaged directory is never the configured default; a notice may not claim a fallback where the module's own file was drawn]
  Version change    : 6.1.0 → 6.2.0
  Bump rationale    : MINOR, on three counts, none of which narrows a prior guarantee.

                      (1) Rule XIV.13's closed-set clause was stated of a **class** whose data are a
                      closed set the module defines, which reached `marker` and `weather` and
                      nothing else. `mystery.svg` sat outside it by accident of shape rather than
                      of principle: it is as much the module's own vocabulary as `lost.svg` is, but
                      it lives in the flag and track classes, whose other data are the countries and
                      circuits a league named. The clause is now stated of a **datum**, qualifying
                      in either of two ways — the class settles it where every datum the class can
                      be handed is the module's; the datum settles it where the module reserves a
                      filename inside a class that is otherwise the league's. `mystery` and `other`
                      are the two reserved filenames. The four-outcome table is untouched and gains
                      no fifth outcome.

                      The generalisation carries an explicit prohibition, which is what makes it
                      safe: a class whose data are values a league named MUST NOT be declared
                      closed-set as a whole, however many reserved filenames it carries. Without it
                      the obvious simplification — "the flag class is closed" — would hand a league
                      the module's file for a country it had simply not drawn yet. That no country
                      flag ships today makes the mistake invisible rather than harmless.

                      (2) The same rule asserted that the packaged directory and the configured
                      directory "are one and the same directory" where a league has not moved the
                      class. That is now false and is corrected: the seven asset classes default to
                      `resources/league/<class>`, the gitignored folder a league fills with its own
                      artwork and which an update cannot overwrite, while the packaged directory
                      remains `resources/defaults/<class>`. The two tiers are therefore always
                      distinct, and a league drops a file in and has it drawn without issuing a
                      command. The template directory is excepted in the rule itself: it has no
                      packaged tier, so pointing it at an empty folder would leave nothing to draw.

                      (3) The outcome table's requirement of the **same notice** on either fallback
                      tier stands, and is explicitly preserved as to the notice's kind. Its wording
                      is now constrained: where the packaged tier supplied the datum's own file
                      under the closed-set clause, nothing was substituted, and the notice may not
                      say a fallback was drawn.

                      MINOR rather than MAJOR because nothing that resolved before now fails, and
                      nothing that drew a specific file before now draws a fallback. The default
                      change binds newly created configurations alone; a server already configured
                      keeps the value it holds, whether it chose that value or inherited it.

                      MINOR rather than PATCH because (1) widens what a render can succeed with and
                      adds a prohibition that did not exist, and Rule XIV.4 gains a new obligation
                      as to how notices are reported.

  Modified sections :
    - Principle XIV, Rule 13: the closed-set bullet is restated of a datum and gains the two
      granularities and the prohibition on declaring a league's-own class closed; the packaged
      directory bullet loses its "one and the same directory" clause and gains the league-folder
      default and the template-directory exception; the outcome rules' cross-reference follows the
      restatement, and gains a bullet governing the notice's wording on a packaged exact hit.
    - Principle XIV, Rule 4: "Where each is reported" is unchanged and gains a companion, "How they
      are reported" — one grouped message per generation, repetitions counted, the subject named,
      and a link from a commanding command's output to the logged message.
    - Governance footer: version and Last Amended date.

  Added sections    : None. Rule XIV.4 gains a paragraph within an existing rule.
  Removed sections  : None.

  Not changed, and deliberately:
    - The four-outcome table, in either its rows or their order.
    - Resolution for every datum that is a league's own value, in every respect — including the
      countries of the flag class and the circuits of the track class, which the generalisation
      reaches only at the two reserved filenames.
    - Rule XIV.4's "Where each is reported": the log always, a commanding command's output
      additionally, never a channel the drivers read.
    - `resources/defaults/` continues to hold no league-specific artwork. `other.svg` is the
      module's own vocabulary, as `mystery.svg` and the direction markers already were.

  Follow-up TODOs   : None. `docs/wip-specs/image_module_specification.md`,
                      `docs/wip-specs/image_module_changes.md`, `README.md`, `resources/README.md`
                      and `docs/how-to/configuring-the-image-module.md` are brought into step by the
                      same change, per the close-out discipline in CLAUDE.md — tracked as part of
                      the change that requested this amendment, not as a constitution follow-up.
[2026-08-21 — v6.0.0 → v6.1.0: MINOR — a closed-set class's packaged directory is searched for the datum's own file, not only its fallback, whether or not a league has moved the directory]
  Version change    : 6.0.0 → 6.1.0
  Bump rationale    : MINOR. Rule XIV.13's closed-set bullet previously closed with "A league MAY
                      still point the class at a directory of its own and is then bound exactly as
                      any other league, its fallback covering what its own set omits" — treating a
                      closed-set class (marker, weather) exactly like an open-set one the instant a
                      league customised its directory. That contradicts the rationale stated two
                      sentences earlier in the same bullet: the league did not choose the closed-set
                      vocabulary and cannot be incomplete against it, a fact that does not change
                      because the league moved the directory. The sentence was an unreasoned
                      inconsistency inside one rule, not a deliberate design point being reversed.

                      The bullet now requires the packaged directory of a closed-set class to be
                      searched for the datum's own file — not merely its `fallback.svg` — ahead of
                      the packaged fallback, whether or not the league has pointed the class at a
                      directory of its own. The outcome table (four rows) is unchanged; this
                      refines the third row for closed-set classes alone and adds no fifth outcome.
                      No open-set class (track, team, flag, driver, tyre) is affected, and the
                      datum's own file in the packaged directory of an open-set class is still never
                      drawn for a league that did not supply it.

                      MINOR because this widens what a render can succeed with — a customised
                      closed-set directory now draws the module's own correct icon in a case that
                      previously drew a generic placeholder — and narrows no prior guarantee.
                      Nothing that resolved before now fails, and nothing that drew a specific file
                      before now draws a fallback.

  Modified sections :
    - Principle XIV, Rule 13: the closed-set bullet's closing sentence is replaced, and the
      general "packaged directory is consulted for a fallback and for nothing else" bullet gains a
      cross-reference naming the closed-set bullet as its sole exception.
    - Governance footer: version and Last Amended date.

  Added sections    : None.
  Removed sections  : None.

  Not changed, and deliberately:
    - The four-outcome table itself, and the `mystery.svg` rule the closed-set bullet already
      generalises from.
    - Resolution for the five open-set classes, in every respect.

  Follow-up TODOs   : None. `docs/wip-specs/image_module_specification.md`, `README.md`,
                      `resources/README.md` and `docs/how-to/configuring-the-image-module.md` are
                      brought into step by the same change, per the close-out discipline in
                      CLAUDE.md — tracked as part of the change that requested this amendment, not
                      as a constitution follow-up.
[2026-08-20 — v5.0.1 → v6.0.0: MAJOR — teams addressed by ordinal; capacity fixed by the template alone; the fallback gains a packaged tier]
  Version change    : 5.0.1 → 6.0.0
  Bump rationale    : MAJOR, on three independent counts, any one of which would carry it.

                      Rule XIV.11 loses the **key** discriminator entirely. A member of a
                      collection is discriminated by an ordinal and by nothing else, and the
                      keyed collection — of which the lineup's team block was the only instance
                      — is withdrawn. A lineup template authored to v5.0.1's XIV.11 is wrong
                      under this version and cannot be rendered by it.

                      Rule XIV.12 loses the **by the data** capacity. Three ways of fixing a
                      capacity become two, and the teams of a division and the seats of a team —
                      its only instances — pass to the template. A divergence that was fatal in
                      both directions is now fatal in one: a template declaring more than the
                      division fields is correct and removes the surplus in silence.

                      Principle IX loses the uniform-divisions invariant outright, and redefines
                      team-name validity: a name beginning with a digit is now admitted. A season
                      refused under v5.0.1 may be valid under this version, which is why the
                      redefinition is backward-incompatible in the permissive direction and still
                      MAJOR — the rule it replaces no longer holds.

                      Rule XIV.13 additionally gains a **fourth** resolution outcome and moves the
                      packaged directories to `resources/defaults/`. The outcome table growing a
                      row degrades nothing: every resolution that succeeded before succeeds
                      identically, and only a render that would have been abandoned now survives.
                      It is carried in this MAJOR rather than held back for a MINOR of its own,
                      being one feature with the two changes above.

  Modified sections :
    - Principle IX → "Team name validity": the normalised form is now a **filename**, not an
      identifier. The leading-letter requirement is withdrawn, with an explicit statement that a
      digit-leading name is admitted and why. The rationale is re-anchored from XIV.11's key to
      XIV.13's filename, and now cites every graphic that draws a badge rather than the lineup.
    - Principle IX → "Uniform divisions where a lineup graphic is drawn" is **replaced** by
      "Divisions MAY differ in composition", which states the permission and records the
      withdrawal. It was the only invariant of Principle IX gated on a module aspect; no gated
      invariant remains.
    - Principle XIV, Rule 11: the ordinal/key alternative becomes ordinal alone; the singleton's
      reserved name no longer speaks of keyed members; the two closing paragraphs on keyed
      collections and on keys as XML identifiers are replaced by a statement that an ordinal is a
      place in the layout, a record of the withdrawal and its cost, and the rule that a datum of
      the league reaches the module as a filename and in no other way.
    - Principle XIV, Rule 12: "exactly three ways" becomes "exactly two"; the by-the-data bullet is
      removed and the teams and seats it governed are named under by-the-template; the by-the-data
      divergence paragraph is replaced by a record of the withdrawal and by an elevated statement
      that a member the data hold but leave empty is drawn and never removed; the nested-ceiling
      paragraph is re-anchored, no longer being a species of a capacity that no longer exists.
    - Principle XIV, Rule 13: normalisation no longer produces a template key, and two bullets
      state that one rule serves every class and that the form names a file and never a field; the
      packaged directory is defined; the outcome table goes from three rows to four; three bullets
      state that the datum's own file is sought in the configured directory alone, that every other
      mention of "holds a fallback" means the two-tier check, and what a league no longer needs to
      supply; "no fourth outcome" becomes "no fifth".
    - Principle XIV narrative: template storage moves to `resources/defaults/templates/`.
    - Data & State Management → New Entities (v5.0.0), (v4.7.0), (v2.11.0): packaged paths
      restated under `resources/defaults/`, the inventory otherwise unchanged. Corrected for the
      same reason v5.0.1 corrected the test-command shape — an inventory naming a file that is not
      there has stopped being an inventory.
    - Governance footer: version and Last Amended date.

  Added sections    : None. Every change is a removal, a redefinition or a restatement within an
                      existing principle or rule.
  Removed sections  : None as sections. Two rules are withdrawn within their principles: the keyed
                      collection (XIV.11) and the by-the-data capacity (XIV.12).

  Not changed, and deliberately:
    - Sync Impact Report entries for earlier versions, and the `resources/...` paths inside them.
      They record what was true when written and are history, not inventory.
    - "Assets under `resources/` MUST be plain SVG" (Principle XIV) still holds as written:
      `resources/` remains the root of everything shipped, and the sentence binds the tree.
    - Rule XIV.11's ordinal-coincides-with-a-datum clause, and the ordinal-is-not-drawn clause. A
      lineup's team ordinal does not coincide with a datum and is not drawn, which the existing
      wording already covers without amendment.
    - The singleton collection itself (XIV.11) and the reserve block it describes. The reserve team
      remains a singleton, addressed by name, its seats fixed by the template.
    - Rule XIV.13's per-field absent-datum declaration, the two track-imagery classes, and the
      flag-keyed-by-country rules. None depends on a keyed collection.

  Follow-up TODOs   : None. This clears dependency D-001 of `specs/047-ordinal-teams-packaged-defaults`,
                      which named this amendment as a precondition of implementation.
[2026-08-19 — v5.0.0 → v5.0.1: PATCH — the test commands are subcommands, not parameter values]
  Version change    : 5.0.0 → 5.0.1
  Bump rationale    : PATCH. Two statements in the versioned entity inventory described the
                      `/images test` family as a parameter carrying choice values. That stopped
                      being true when 045 shipped on 2026-08-18, and v5.0.0 was last amended on
                      2026-08-17 — the day before — so the inventory has been describing a command
                      shape the bot no longer has. This corrects the wording and nothing else.

                      No Core Principle is added, removed or redefined, and no entity is added or
                      amended. Rule XIV is untouched. The project's own policy reserves MINOR for
                      "addition of a new principle, section, or materially expanded guidance", so a
                      `New Entities (v5.0.1)` block was deliberately **not** added: recording that
                      046 adds no entity would be a new section, and the correction does not need
                      one.

  Modified sections :
    - Data & State Management → New Entities (v4.8.0): "the `images test verdicts` value" becomes
      "the `images test verdict` subcommand". Two corrections in one: the form (a value became a
      subcommand at 045) and the name (045 named it `verdict`, singular, after the datum rather
      than after the `verdicts` aspect).
    - Data & State Management → New Entities (v4.7.0): "the four `images test weather-*` values"
      becomes "the four `images test weather-*` subcommands".
    - Both entries additionally record that 046 made the `division` and `round` parameters of
      every test subcommand optional, since a season-less server resolves neither.
    - Governance footer: version and Last Amended date.

  Added sections    : None
  Removed sections  : None

  Not changed, and deliberately:
    - Rule XIV.7's narrative at Principle XIV already said "the four `images test weather-*`
      **commands**", and was correct as it stood.
    - No template under `.specify/templates/` references the test command's shape, so none needed
      updating. Checked: plan, spec, tasks, checklist, agent-file, constitution templates.

  Follow-up TODOs   : None. This clears the amendment feature 045 identified as owed and did not
                      raise; it was carried in 045's spec under "Documentation impact" and in
                      046's plan under "Constitution Check".

[2026-08-17 — v4.8.0 → v5.0.0: MAJOR — track imagery split in two; the flag class rekeyed by country]
  Version change    : 4.8.0 → 5.0.0
  Bump rationale    : MAJOR, and the first since v4.0.0. Two changes to Rule 13 are backward
                      incompatible, and either alone would carry the bump.

                      **Rule 6 was extended after this entry was first written, and the version was
                      deliberately not bumped to 5.0.1.** Per-class aspect uniformity was found
                      while specifying 044: converting four templates' round headings from the track
                      class to the flag class means re-geometrying those slots, and Rule 6 was
                      silent on whether a class holds one aspect across templates. The author ruled
                      that v5.0.0 absorbs it, the branch being unmerged — the same reasoning v4.8.0
                      recorded for 043 ("one increment, one version ... the branch is not merged, so
                      nothing had ever read a version between this one and v4.7.0"), and the same
                      that 037 through 042 each took. **No reader has seen a v5.0.0 without the Rule
                      6 paragraphs**, so there is no version for a 5.0.1 to distinguish itself from.
                      The extension is a clarification in its own right and would have been PATCH
                      had it stood alone; it does not disturb the MAJOR reasoning below.

                      **Found in implementation: the country examples were wrong, and are corrected
                      here.** Building 044 showed that every illustration of a country name in this
                      document contradicted the data it illustrated. Migration 029 seeds
                      `tracks.country` as `United Kingdom` and `United States of America`; Rule 13,
                      Rule 6 and the Track entity each said `Great Britain` and `United States`.
                      Left standing, a British driver would have drawn `great_britain.svg` while the
                      British Grand Prix drew `united_kingdom.svg` — **two files for one country, the
                      exact duplication the rekey exists to remove**, reintroduced by the rekey's own
                      documentation. The **rule** was right throughout and is unchanged; only the
                      examples move, and the seed's spellings are authoritative because they are data
                      and the examples were prose. No version movement: same unmerged increment, same
                      reasoning as the Rule 6 note above. Seven sites corrected, the Track entity's
                      among them — it is where the wrong spelling originated and the correction is
                      worthless if the source of it is left in place.

                      **The flag class is rekeyed.** A driver's flag resolved by the nationality
                      adjective — `British` to `british.svg` — and now resolves by the country of
                      that nationality, `united_kingdom.svg`. A flag directory that was **complete
                      and conformant** under v4.8.0 is incomplete under v5.0.0: every file in it is
                      named wrongly, every driver draws the fallback, and a directory holding no
                      fallback makes the render fatal. That is the definition the versioning policy
                      gives MAJOR — a backward-incompatible redefinition — and it is met without
                      argument.

                      **Four image types lose the track class.** Standings (both slots), the
                      attendance sheet and the weather forecasts declared a track-class field and
                      now declare a flag-class one. A template authored against v4.8.0 still
                      *renders*, which was v4.0.0's test for MAJOR, but it renders a **different
                      picture** from the one its author drew it to hold, and the id naming the field
                      no longer names what is placed upon it. Rule 11's convention obliges the
                      rename, so the catalogues change too.

                      MINOR was considered and rejected. v4.8.0 rejected MAJOR three times on one
                      consistent test — that nothing conformant to the prior version becomes
                      non-conformant, Rule 7's inversion being a pure loosening. This amendment
                      fails that test in both directions at once: it **removes** the track class
                      from four types, and it **substitutes** one datum for another in a fifth. It
                      is neither a loosening nor an expansion, and reading it as one would leave a
                      league's flag directory silently broken under a version number that promised
                      it would not be.

                      A reader diffing this version is owed the plain warning: **an existing flag
                      directory must be renamed file by file, and four templates must be re-read
                      against their catalogues.** The bot is not in production, so no live league is
                      affected, but the rules are versioned rather than the deployments.
  Feature branch    : 044-track-imagery-split (created 2026-08-17 from main). The amendment precedes
                      the increment, as the scope guard requires; nothing is implemented on it yet.
                      Suggested next: /speckit-specify for the track imagery split.

  Session context   : The image module has a prototype implementation end to end, and every graphic
                      that draws a round drew a circuit map from `resources/tracks/`, one class and
                      one configured directory serving every site. The author raised that a league
                      wants a **flag in some places and a map in others**, which one class keyed one
                      way cannot express. Principle XIV was audited for what the split touches:
                      Rule 13 (classes, keying, fallbacks), Rule 11 (an id naming its class), Rule
                      10 (a catalogue naming a class per field) and Rule 3's literal-value paragraph
                      (a mystery round). Rules 10 and 3 needed no text; the substance sits in
                      Rule 13 with cross-references out to the rest.

  Author's rulings  : - Granularity           → **two optional fields, not a configuration toggle**.
                                              The choice is not a setting a league flips per graphic
                                              but two distinct elements a template declares. The map
                                              field is available to the **calendar and check-in
                                              kinds only**; every other type that used the track
                                              class reverts to the country flag. The author's
                                              reasoning, recorded as the Rule's rationale: a map
                                              reads where the round is the subject and is unreadable
                                              where the round is a column heading.
                      - The flag's source     → **reuse the nationality flag directory, rekeyed by
                                              country name**. Not a second directory. The
                                              consequence the author accepted explicitly: a table
                                              relating a driver's nationality to a country name must
                                              exist, so that one directory serves both a driver and
                                              a round. `utils/nationality_data.py` already holds
                                              every country name as a lookup key, so the table is an
                                              inversion of shipped data rather than new research.
                      - Circuits sharing a    → **acceptable, and intended**. Las Vegas, Miami and
                        country                the Circuit of the Americas all draw one
                                              `united_states_of_america.svg`. Ruled not a collision to be
                                              broken by keying on the circuit instead.
                      - A miss                → **the class's own fallback, and never the other
                                              class**. A flag that does not resolve draws
                                              `flags/fallback.svg`; a map that does not resolve
                                              draws `tracks/fallback.svg`. Rule 13's three-outcome
                                              table is left standing verbatim; the cross-class
                                              fall-through that would have made a fourth outcome was
                                              put to the author and declined.
                      - `flags/mystery.svg`   → **required, and ratified**. A mystery round conceals
                                              its track and thereby its country, and Rule 3's
                                              literal-value paragraph fills the field with the datum
                                              `Mystery` and resolves it by the ordinary slug rule.
                                              Once those graphics draw the flag class, that lookup
                                              lands in the flag directory, which holds no such file.
                                              Without it a concealed round draws the fallback and
                                              raises a notice against a league that has done nothing
                                              wrong. Derived from the four rulings above rather than
                                              stated, put to the author as such, and **confirmed**;
                                              it is a rule of this version on the same footing as
                                              the rest.
                      - Aspect uniformity     → **within a class, never across two**. Raised while
                                              specifying 044. A class holds one aspect on every
                                              template of every kind — a flag is 3:2 whether it
                                              stands for a driver or a round, a map is 1:1 — and the
                                              two classes are **not** brought into line with each
                                              other. A first reading that the two should share one
                                              aspect was put to the author and corrected. Rule 6 was
                                              silent on the point and now states it.

  Modified sections : - Principle XIV, Rule 6 — three paragraphs added after the aspect-authoring
                        paragraph: one class carries one aspect on every template and Layer 2
                        refuses a slot declared at another; the rationale that per-class files and
                        a non-padding generator are only reconcilable if the class is uniform; and
                        that two classes need not match each other, flags at 3:2 and maps at 1:1
                        deliberately differing. The aspect a class carries is left to the asset
                        documentation rather than fixed here.
                      - Principle XIV, Rule 13 — three subsections added before the Rationale: the
                        two classes and which types may declare the map; the flag class keyed by
                        country, with the nationality-to-country map and the `Other` carry-through;
                        `mystery.svg` reserved in the flag directory; and the per-class fallback.
                      - Principle XIV, Rule 6 and Rule 13, and the Track entity — the country
                        examples corrected to the spellings migration 029 seeds. Rules unchanged.
                      - Data & State Management — "New Entities (v5.0.0)" added, recording that no
                        database entity changes and that the nationality-to-country map is a
                        module-shipped constant.
                      - Governance — version line and last-amended date.

  Templates requiring updates:
                      ✅ .specify/templates/plan-template.md — Constitution Check is generic; no edit
                      ✅ .specify/templates/spec-template.md — no domain-specific reference; no edit
                      ✅ .specify/templates/tasks-template.md — phase structure unaffected; no edit

  Follow-up TODOs   : None deferred in the constitution. Downstream work is out of this command's
                      scope and is listed as Next Actions.

[2026-08-14 — v4.7.0 → v4.8.0: MINOR — the simplest graphic; Rule 7 inverted; the catalogues complete]
  Version change    : 4.7.0 → 4.8.0
  Bump rationale    : MINOR. **No rule is added.** Eight are expanded (3, 4, 5, 9, 10, 16, 17) or
                      **inverted** (7). Nothing conformant to v4.7.0 becomes non-conformant: Rule 7's
                      change is a pure loosening, and every other change either admits a form
                      previously unadmitted or states in full a contract Rule 5 had only summarised.

                      **This entry covers the whole of 043, implementation included.** Rules 5 and 9
                      were extended a second time once the type was built and two findings came back
                      from the raster; both are recorded below under "Found in implementation". One
                      increment, one version, as 037 through 042 each took — the branch is not merged,
                      so nothing had ever read a version between this one and v4.7.0.

                      MAJOR was considered **three times** and rejected each time.

                      For **Rule 7**, whose central constraint is not merely expanded but reversed in
                      direction — the correspondence with the text path was written as a ceiling on
                      what a graphic may say and is now a floor under what it must. What is deleted
                      is a *prohibition*, so nothing built against v4.7.0 can fail under v4.8.0; a
                      graphic conformant to the ceiling is conformant to its absence. The direct
                      precedent is v4.7.0's own correction of Principle IV — a NON-NEGOTIABLE
                      principle's paragraph rewritten, staying MINOR on the reasoning that widening
                      a permission breaks nothing built against the prohibition. This is that shape,
                      at a larger scale. A reader diffing this version is owed the warning all the
                      same, which is what this entry is for: **Rule 7 now means something different
                      from what it meant, and should be read again rather than recalled.**

                      For **Rule 5**, whose three new problems — a `shape-inside` naming a rectangle
                      the template does not declare, a wrapped field on which no `line-height`
                      resolves, and a rectangle declaring no usable width and height — make fatal what
                      was not. Rejected on evidence rather than argument: of the fifteen shipped
                      templates, `verdicts_template.svg` is the **only** one declaring `shape-inside`
                      at all, and both of its wrapped fields — `description` and `justification` —
                      declare a `line-height` and name a rectangle carrying an explicit 1104 × 156. No
                      template that rendered under v4.7.0 stops rendering under this one, which is the
                      test v4.0.0 fixed for MAJOR. Verdicts is genuinely the module's first wrapping
                      type.

                      For **Rule 9**, where Layer 3 goes from reserved to enforced. Ratifying a layer
                      is what XIV.9 was built to accommodate — "a layer MUST be ratified before it is
                      enforced" — so this is the rule operating, not changing. The templates it newly
                      refuses are the ones Rule 5 newly calls faulty, and the evidence above covers
                      them.

                      That this amendment adds no numbered rule is recorded rather than passed over,
                      as v4.7.0's was. The seventh type is the module's **simplest** graphic and its
                      hardest to place, and the two are one fact: with no collection, no ordinal and
                      no capacity, everything this Principle built to manage repetition falls away,
                      and what is left is the question it had never had to answer — what a graphic
                      *is*, as against what it contains.
  Feature branch    : 043-verdicts-image-generation (created 2026-08-14 from main)

  Session context   : 037 built the calendar, 038 the lineup, 039 the results, 040 the standings, 041
                      the two attendance graphics, 042 the six weather ones. This session begins the
                      seventh per-image-type utility — the **verdict**, one template serving a
                      post-race penalty, an appeal and an attendance sanction alike — and no other
                      type is in scope. Principle XIV was audited against
                      `docs/wip-specs/image_module_specification.md` § "Verdicts image generation" as
                      the six sessions before it audited their own. Five divergences were put to the
                      author; the rest were settled by the precedent v4.1.0 set, that where the
                      constitution stated a rule too narrowly the wip-spec wins.

                      The source module is **shipped**: `verdict_announcement_service.py` has posted
                      penalty, appeal and autosanction announcements since 026, and the attendance
                      module has composed the last of those since 041. Nothing here waits on the
                      steward module, and nothing here anticipates it.

  Author's rulings  : - The flag notice       → **verdicts inherits the suppression**. The results,
                                              standings and attendance sections each carry the
                                              sentence that a league switching nationality collection
                                              off draws no flags and is told nothing (Rule 4); the
                                              verdicts section omitted it. Ruled an oversight, not a
                                              distinction: same switch, same field, same reason. The
                                              wip-spec gains the sentence its three siblings carry.
                      - Rule 7 itself         → **a floor, not a ceiling**, and this ruling supersedes
                                              the two below it. The author's correction: the image
                                              path must carry **at least** the information of the text
                                              path and **may add to it**. The rule had said the
                                              opposite — that every image must correspond to
                                              information the bot can already express as text — and
                                              that ceiling was wrong rather than merely narrow.
                                              *Additive* was never a promise that a picture would say
                                              no more than a message; it was a promise that turning
                                              images on costs a league nothing. The prohibition on
                                              **deciding** now carries Rule 7 alone, which is where
                                              the weight always sat.

                                              Recorded plainly because of how it was arrived at: the
                                              two rulings below were each put to the author as a
                                              request for an **exemption**, and each was granted as
                                              one, before the author named the fault they shared. Both
                                              are now instances of the rule rather than exceptions to
                                              it, and neither needed a clause of its own. The audit
                                              asked twice for permission to step over a line and did
                                              not ask whether the line was drawn the right way round.
                      - Flag and badge        → **imagery that identifies is drawn freely**. Put to
                                              the author because the wip-spec says the graphic "adds
                                              to the textual announcement the flag of the driver and
                                              the badge of the team", and the penalty flow publishes
                                              neither as text — a breach of the ceiling that had been
                                              quietly true of the lineup, results, standings and
                                              attendance flags for five sessions. Ruled that an image
                                              depicting an **entity the graphic already names**
                                              obliges the text path to publish nothing, and answers to
                                              Rule 13 alone. Widening the test across modules was
                                              offered as an alternative and declined. Superseded in
                                              form by the ruling above and retained as an instance:
                                              an image standing for a **fact** rather than an entity
                                              is still a value read from the module that owns it.
                      - The verdict stage     → **a graphic may name its own kind**. Put to the author
                                              because `verdict_stage` is mandatory and the textual
                                              announcement carries no stage whatever: the penalty and
                                              appeal messages are identical in wording, verified
                                              against `verdict_announcement_service` and the 026
                                              message-format contract. Repairing the text path was
                                              offered first, per Rule 7's then-instruction that an
                                              inadequate text path is a defect and not a licence, and
                                              **declined**. No change to the textual announcement
                                              follows. Superseded in form by the ruling above: under
                                              a floor, a graphic naming its own kind needs no
                                              permission.
                      - The absent session    → **emptied, not labelled**. The wip-spec put
                                              "Attendance Sanction" on `session_name` *and* on
                                              `verdict_stage`, so a sanction verdict would carry the
                                              same two words twice under two headings. Ruled that the
                                              session field is **emptied** — its group removed where
                                              declared — and the label stands on the stage alone. The
                                              field stays **mandatory**: the template must declare it,
                                              and the data determine its value to be nothing, which
                                              Rule 3 already holds is determined. Applied to the
                                              wip-spec.
                      - The redraw declaration → **static, declared explicitly**. Offered as
                                              "ordinary, satisfied vacuously" — its message being
                                              never reposted nor edited — and the author took the
                                              stronger declaration. This obliged Rule 17 to admit a
                                              **second ground** for staticity, a verdict drawing a
                                              driver's display name being a value that plainly does
                                              change. See below.

  Settled by precedent (not put to the author, the wip-spec having stated the general form):
                      - The wrapping contract in full. The conventions section forward-references the
                        verdicts section for it ("wrapped and reduced as defined for the verdicts
                        graphic"), so what is written there is general and not the type's own. Rule 5
                        had summarised four of its clauses and omitted eight.
                      - The graphic displacing the whole announcement but the mention.
                      - A mention resolved in place inside free text a person wrote.
                      - The module placing no ceiling on the length of a steward's prose.
                      - One template serving three kinds; the type declaring no collection at all.
                      - The graphic adding no precondition to the finalisation of a review or the
                        enforcement of a sanction; no verdicts channel, no graphic; the pardon that is
                        no verdict and carries none.

  Found in implementation (not put to the author; both were caught by rasterising and looking at the
  PNG, per Rule 14, and neither was found by reading the rules):
                      - The third defect  → a `shape-inside` may name a rectangle that **exists** and
                                            still declares no usable width and height. Every check for
                                            the first two defects passes; the natural implementation
                                            degrades to one unwrapped line and reports nothing. That is
                                            a render which *succeeds and is wrong*, which is the worst
                                            outcome Rule 4 exists to prevent. Now the third bullet of
                                            Rule 5, enforced in the fill pipeline and in Layer 3.
                                            Surfaced as a verdict's justification running off the edge
                                            of the canvas with every check green.
                      - The font resolver → **no rule changed**, and it is recorded because that is the
                                            point. Rule 5 already required measurement against "the
                                            font family, weight, style and size the field declares",
                                            and the resolver honoured only the family — answering with
                                            a *condensed* face for a family whose normal face the
                                            rasteriser draws, and with whatever weight sorted first.
                                            Lines were measured narrower than drawn, the one direction
                                            "errs narrow" forbids. The rule was right; the code was
                                            brought to it, and the obligation is now pinned by a test
                                            comparing the measurement against the rasteriser's own
                                            reported width.

  Modified rules (Principle XIV):
    - **3. Every mandatory field MUST be resolved** — a **kind of record that has no such thing at
      all** empties the field, where the paragraph above it governs a thing the record *has* and
      withholds. A mystery round conceals a track it owns; an attendance sanction pertains to no
      session because none was run. The classification is untouched — the template must still declare
      a mandatory field, and the data determine its value to be nothing. The graphic MUST NOT write
      the kind's label into the slot of the absent thing where another field already names the kind;
      the text path may, a single-line heading having nowhere else to put it, and that is a difference
      in arrangement rather than in rendering.
    - **4. Problems and notices are distinct outcomes** — the problem list gains a wrapped field the
      template gives no room or no leading to lay out (Rule 5).
    - **5. Text bounds are declared by the template** — stated in full where it had been summarised.
      A single word wider than its room is broken within itself. A wrapped field's `shape-inside`
      names a rectangle that is the field's extent and is never drawn; a field declaring `inline-size`
      and `shape-inside` both is wrapped, not truncated. The **wrapping contract**: the author's own
      line breaks are honoured first and their blank lines counted against the budget; the line height
      in force is the `line-height` resolving on the field; reduction by half-pixel steps to a floor of
      half the declared size, with the leading scaled and the admissible line count recomputed, so a
      field set smaller holds *more lines*; each field reduced alone, the canvas never resized;
      `shape-inside` removed once the lines are laid out. **Three template defects are problems** and
      all three are structural under Rule 9: a `shape-inside` naming a rectangle the template does not
      declare, a wrapped field on which no `line-height` resolves, and a rectangle declaring no usable
      width and height. Each MUST be reported naming the field at fault and distinguishably from the
      other two, which is XIV.9's specific-attribution invariant applied within one layer; a paragraph
      records why the third looks redundant and is not. **Measurement** is against the declared face,
      against the substituted face with a notice where it is not installed, and MUST **err narrow**.
      And the module places **no ceiling** on free text.
    - **7. Image output is additive** — **inverted**, and read again rather than recalled. The
      correspondence with the text path was a **ceiling** ("every image MUST correspond to information
      the bot can already express as text") and is now a **floor**: a graphic carries at least what the
      posting it replaces carried, save what Rules 15 and 16 say a picture cannot carry, **and MAY
      carry more**. There is no matching ceiling, and a graphic may draw what the text path has never
      published anywhere.
      **What replaces it** is the prohibition that was doing the work all along, gathered from the old
      derived-presentation clause and promoted to the rule's centre: a graphic MUST NOT **decide**. A
      value requiring a rule — an ordering, a tie-break, an eligibility, a points award, a sanction —
      is the source module's and the graphic reads its result; a derivation lives with the data and
      never in the image utility; a second record of the same kind is read as persisted, not
      recomputed. A graphic may arrange, measure and depict; it may not settle.
      **A fallback may therefore say less than the graphic would have**, which is stated and accepted:
      the league is told everything it would have been told had images never existed, and not the
      surplus. An image type MUST NOT answer this by holding its surplus back.
      Also added, and unaffected by the inversion: **a graphic MAY displace all but what a picture
      cannot carry**, the verdict standing at the opposite pole from the check-in call which displaces
      nothing.
      Retained as **instances** rather than exceptions: imagery that identifies an entity the graphic
      already names, and a graphic naming its own kind. Both were drafted as exemptions from the
      ceiling and were left standing, much shortened, once the ceiling went.
    - **9. Template validity is a layered, extensible contract** — the layer list becomes a
      **ratification record** rather than a plan. Layer 1 mandatory; **Layer 2 ratified and in force**;
      **Layer 3 (Bounds declaration) ratified and in force**, checking Rule 5's three defects; **Layer
      4 (Trial render) not ratified**, and a report MUST keep saying it was not applied.
      The per-type ratification rule is **retained and explicitly not spent**: all fifteen catalogues
      are now specified, so no type is skipped in practice, but the rule binds the next type added and
      the skip-rather-than-pass behaviour MUST remain implemented and tested against a catalogue staged
      empty for the purpose. Written that way because the condition no longer arises on its own, and a
      behaviour nothing exercises is a behaviour that quietly rots.
    - **10. Every image type MUST declare a field catalogue** — two converses of the slot-selecting
      datum. **Several kinds MAY share one slot** where they differ only in the values of fields; an
      aspect gains a second slot only where the two would draw different *fields*. **An image type MAY
      declare no collection at all**, whereupon Rules 11 and 12 bind nothing in its catalogue and
      nothing else follows from the absence. Two types reach that — the mystery notice, which arrived
      at v4.7.0 without the rule being written, and the verdict. The wip-spec called the verdict the
      only one; it was written before the notice had a slot of its own, and is corrected in this same
      change.
    - **16. A graphic draws nothing a reader can act on** — **a mention standing inside a value is
      content, and is resolved in place**. It is the fixed rendering of the rule's first paragraph and
      not the markup-stripping of the paragraph before it: markup is an instruction the text path
      added, so finding it inside means the handover is wrong, while a mention is a value a person put
      there and no upstream repair could remove it without taking it out of the message too.
    - **17. A graphic is redrawn whenever what it draws changes** — a **second ground for staticity**.
      A type may be static because it draws nothing mutable (the check-in call) *or* because it draws
      a **record of an event** rather than a view of a state, its values fixed at the moment the event
      occurred. The test is not "can this datum ever change" but "can what this graphic *says* become
      false while its message stands"; for a graphic of a state the two coincide, for a graphic of an
      event they do not. A type taking this ground MUST say so and MUST be one whose corrections
      arrive as **new postings** — a penalty overturned on appeal is its own verdict beside the first,
      not an edit of it. The verdict is the second static type and the strongest case of the form:
      its message is never edited either, so no message id need be persisted at all.

  Added sections    : "New Entities (v4.8.0)" — **None**, recorded so it is not re-derived. No table
                      records a verdict's message and the image flow adds no column. `PenaltyRecord`,
                      `AppealRecord` and `DivisionResultsConfig.penalty_channel_id` are read as they
                      stand, as are the attendance module's two enforcements.
                      `translate_penalty` is the code Rule 7's one rendering obliges the graphic to
                      call, and the compact sanction rendering of a results graphic MUST NOT be
                      substituted for it. `utils/font_metrics.py` and the `fonttools` declaration
                      already exist and are read as they stand. No asset class is added and no file
                      is shipped: neither the flag nor the team-image vocabulary is the module's, so
                      Rule 13's closed-set clause does not arise.

                      Three rationale paragraphs close Principle XIV besides. One records that the
                      per-type specification is **complete** at fifteen catalogues, and that Layer 3
                      cost one class and one registry entry — the stable surface XIV.9's first
                      invariant was written to guarantee, verified rather than assumed. One records
                      that this increment's findings were caught by the raster and not by the rules,
                      and that two of them were failures to honour rules already stated. And a **note
                      on the steward module** closes it, at the
                      author's invitation and deliberately confined to two tests rather than any
                      prediction: a field added to the verdict catalogue is an amendment of its static
                      declaration and is reviewed as one; and a verdict amended in place rather than
                      superseded by a second verdict is no longer a record under Rule 17 and takes
                      Rule 8's delete-and-repost, with the persisted message id the type does not have
                      today. Nothing else about the steward module is written down here.

  Removed sections  : none. **Principle VI is corrected**, not removed: its one-sentence restatement
                      of the image module's additivity carried the same ceiling Rule 7 did ("every
                      image corresponds to information the bot can already express as text") and now
                      carries the floor, with the prohibition on deciding named beside it and Rule 7
                      cited as the authority. Two principles stating one rule is how a correction goes
                      half-applied, and the grep that found it is part of this audit rather than luck.

  Deferred          : TODO(PER_TYPE_ASSET_SENTENCES) — carried since v4.0.0 and **discharged in full
                      here**, verdicts having been the last type to carry it. Verified rather than
                      assumed: its flag sentence separates the absent nationality (field removed,
                      non-fatal error) from the absent file (deferred to the conventions), and its
                      team-image sentence defers to the results type. The TODO is **closed**.

  Templates confirmed aligned:
  ✅ .specify/templates/plan-template.md — Constitution Check is a generic gate; principle count is
     unchanged at I–XIV, so the Governance section's "I–XIV" stands
  ✅ .specify/templates/spec-template.md — generic structure; no domain-specific changes needed
  ✅ .specify/templates/tasks-template.md — phase structure aligns with updated principles
  ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale references

  Wip-spec corrections applied in this same change, that document being the source of truth for rules
  and the place a decision made in conversation must land:
    - § Verdicts, "Handling of mismatches" — gains the nationality-toggle sentence its three siblings
      carry.
    - § Verdicts, "Resolution of the data to be placed" — an attendance sanction empties `session_name`
      and removes its group, rather than carrying "Attendance Sanction" there.
    - § Conventions, "When a graphic is drawn again" — the verdict graphic is named as the second
      static graphic, and the record-of-an-event ground stated.

  Known divergence for the increment to repair (not a constitution matter):
    `svg_fill._DEFAULT_LINE_HEIGHT_RATIO` substitutes a leading of 1.2 where a wrapped field declares
    no `line-height`. Rule 5 now makes that a problem. The constant predates any wrapping type and no
    shipped template reaches it; it is an implementation task of 043.

  `README.md` is **not** changed here, and was checked rather than assumed. No verdict graphic is
  built, `image_catalogues.py` declares no verdict catalogue, and the README describes the bot as it
  is. It is updated within the increment, when the behaviour it documents exists.

  Follow-up TODOs   : None — all placeholders resolved, and the one long-standing TODO is closed.

[2026-08-13 — v4.6.0 → v4.7.0: MINOR — one aspect, six templates; the notice that is a forecast's absence]
  Version change    : 4.6.0 → 4.7.0
  Bump rationale    : MINOR. **No rule is added.** Seven are expanded (3, 7, 8, 10, 11, 12, 16) and
                      Principle IV's Mystery Rounds paragraph is corrected. Nothing is removed and
                      nothing conformant to v4.6.0 becomes non-conformant: every expansion states a
                      form the existing rule did not reach, and Principle IV's correction *permits* a
                      posting it had forbidden rather than forbidding one it had allowed. No template
                      that rendered under v4.6.0 stops rendering under this one, which is the test
                      v4.0.0 fixed for MAJOR.

                      MAJOR was considered for Principle IV, a NON-NEGOTIABLE principle whose Mystery
                      Rounds paragraph is rewritten, and rejected. The paragraph was stale rather than
                      wrong in intent: `mystery_notice_service.py` has posted that notice at the phase
                      1 horizon since the weather module shipped, and `forecast_messages` has recorded
                      it since migration 006. The correction records what the bot does and what the
                      author confirms it should do. Widening a prohibition's exception breaks nothing
                      built against the prohibition.

                      That this amendment adds no numbered rule is itself worth recording. The sixth
                      image type is the module's most divided aspect — six templates, three phases, two
                      round formats and a kind of round holding no forecast at all — and every question
                      it raised was answered by extending a rule Principle XIV already held. The fifth
                      needed a rule of its own; the sixth did not.
  Feature branch    : 042-weather-image-generation (created 2026-08-13 from main)

  Session context   : 037 built the calendar, 038 the lineup, 039 the results, 040 the standings, 041
                      the two attendance graphics. This session begins the sixth per-image-type
                      utility — the **weather** module's forecasts, all three phases and every variant
                      — and no other type is in scope. Principle XIV was audited against
                      `docs/wip-specs/image_module_specification.md` § "Weather image generation" as
                      the five sessions before it audited their own. Four divergences were put to the
                      author; the rest were settled by the precedent v4.1.0 set, that where the
                      constitution stated a rule too narrowly the wip-spec wins.

                      Weather is the first aspect drawn by **six** templates rather than one or two,
                      and the first whose template is chosen by a property of the **round** rather than
                      of the thing drawn. It is also the first aspect holding a **chain of postings**
                      across occasions — phase 2's message deleting phase 1's, phase 3's deleting
                      phase 2's — and the first to include a posting that is a forecast's *absence*.
                      Most of this amendment follows from those three facts.

  Author's rulings  : - Weather icons       → **the module ships all eight**. Sunny, Mixed and Rain,
                                            and Clear, Light Cloud, Overcast, Wet and Very Wet, are a
                                            closed set the module itself defines and no league chose,
                                            so Rule 13's closed-set clause applies unaltered and
                                            `resources/weather/` gains eight files beside the
                                            `fallback.svg` it already holds. This is the second class
                                            the module ships complete, after the markers.
                      - Sprint slot floor   → **three, not two**, the wip-spec's figure being an
                                            arithmetic slip. The author's instruction was to follow
                                            "whatever is the current functionality on the textual
                                            output"; `MAX_SLOTS[LONG_FEATURE_RACE]` is 3, and the
                                            Round Formats table of this document already said so. A
                                            sprint phase 3 template therefore declares at least three
                                            slots per session, as a plain one declares at least four.
                      - The mystery notice  → **it is the phase 1 posting of such a round**. The
                                            author corrected the question: the bot does not merely
                                            post "a message" for a mystery round, it posts the phase 1
                                            message, which states that the weather is not
                                            pre-generated. Phases 2 and 3 are silent for such a round
                                            on both pathways. Principle IV is corrected to say this,
                                            which is also what makes the mystery graphic legal under
                                            Rule 8's "no posting, no graphic".
                      - `session_<x>_slot_type`
                                            → **the name stands; no rename**. Put to the author on the
                                            ground that `slot` names both a session-level value and a
                                            numbered collection on one phase 3 template, and answered:
                                            the two meanings are of two different phases, a session
                                            holding one slot at phase 2 and one to four at phase 3.
                                            Rule 11 gains the general statement instead — a field of a
                                            member may begin with the name of a collection nested in
                                            that member, and the catalogue, not a parser, tells them
                                            apart.

  Settled by precedent (not put to the author, the wip-spec having stated the general form):
                      - The six forecasts being siblings — Rule 3 already named them.
                      - Redrawing on every occasion the textual forecast is posted — Rule 17's
                        ordinary form, the weather types being declared nothing else.
                      - The replacement produced before the original is destroyed; a transport failure
                        enqueuing the text form; a commanded posting refusing rather than falling back,
                        which is why the four `images test weather-*` commands report to their invoker
                        and post nothing.
                      - An icon standing where the textual forecast draws an emoji. The label is the
                        shared rendering of Rule 7 and the icon is an asset resolved from that same
                        datum by Rule 13's slug; no third divergence from the text path arises.
                      - Emptying rather than drawing a dash; notices to the log channel and never to a
                        forecast channel.

  Modified rules (Principle XIV):
    - **3. Every mandatory field MUST be resolved** — **a kind of record MAY have an image type of its
      own** rather than a defined literal in a shared one, where that kind's posting differs. A
      calendar draws a mystery round as a row among rows and takes the literal; the weather module
      posts a notice that no forecast is coming and takes a slot of its own.
    - **7. Image output is additive** — **the correspondence is with the text path, not with the
      message the graphic rides on**. A graphic MAY carry a value the module published in another
      message of the same flow: the likelihood of rain computed at phase 1 stands on the phase 2 and
      phase 3 graphics, where neither of those messages carries it.
    - **8. Images are attachments** — two additions. **A lifecycle MAY span occasions**, the chain by
      which each phase's posting deletes its predecessor's message passing to the image flow entire,
      ordering and test-mode suppression included. And **the manner of a message is not part of the
      chain**: a text message may be deleted by an occasion posted as a graphic and the reverse, a
      fallback substituting one message without forking the flow.
    - **10. Every image type MUST declare a field catalogue** — where an aspect holds several slots,
      the catalogue MUST name the **datum that selects** among them, and the selection MUST be a
      function of that datum alone. The format of the round chooses between the two slots of each of
      phases 2 and 3, and nothing else may enter that choice.
    - **11. Template ids follow a fixed convention** — a field of a member MAY bear a name **beginning
      with the name of a collection nested in that member**. `session_<x>_slot_type` and
      `session_<x>_slot_<y>_label` are told apart by the catalogue and never by parsing the id.
    - **12. Collection capacity** — a **third way a capacity is fixed: by the template slot**, where an
      aspect's slots each serve a known subset of the data and the shape that subset can demand is a
      constant of the module. The declaration is a **floor**: under-declaring is a structural problem
      refused at every one of Rule 9's three moments, over-declaring is removed silently at generation,
      and the floor is the maximum over the subset the slot serves.
    - **16. A graphic draws nothing a reader can act on** — **markup the message channel interprets is
      not content**. The emphasis a phase 3 summary carries in the forecast message is dropped, the
      graphic drawing the value the markup adorned and leaving distinction to the template's own
      typography.
    - **13. Asset resolution** — no rule changed; the weather vocabulary is named alongside the
      position-change markers as the second instance of the closed-set clause.
    - **Rationale** — extended for the amendment that added no rule, for why Rule 12's third capacity
      belongs inside Rule 12, and for correcting Principle IV rather than exempting the graphic.

  Added rules (Principle XIV): **none.**

  Modified principles:
    - **IV. Three-Phase Weather Pipeline (NON-NEGOTIABLE)** — the Mystery Rounds paragraph is
      corrected. It said no phase runs and no weather message is posted. It now says no draw is
      performed and no forecast computed; that a fixed notice carrying no forecast and no role mention
      is posted at the **phase 1 horizon**, standing in the place of the phase 1 forecast and recorded
      distinctly from one; and that nothing whatever is posted at the phase 2 and phase 3 horizons.

  Added sections:
    - **New Entities (v4.7.0)** — none, with the reasoning recorded, plus the eight shipped weather
      icons.

  Removed sections: none.

  Deferred          : TODO(PER_TYPE_ASSET_SENTENCES) — carried from v4.0.0 and **discharged for the
                      weather type** in this audit, verified rather than assumed. Its three asset
                      sentences — the track image, the two weather icons, and the icon resolution
                      under § "Handling of mismatches" — each defer to the conventions section in
                      full and bundle nothing. **Verdicts alone** now carries the TODO forward, and
                      it is the last type to do so.

                      The three wip-spec corrections the rulings imply — the sprint slot floor, the
                      eight shipped icons, and the mystery notice as the phase 1 posting — are applied
                      to `docs/wip-specs/image_module_specification.md` in this same change, that
                      document being the source of truth for rules and the place a decision made in
                      conversation must land.

                      `README.md` is **not** changed here, and was checked rather than assumed. No
                      weather graphic is built, `image_catalogues.py` declares no weather catalogue,
                      and the README describes the bot as it is. It is updated within the increment,
                      when the behaviour it documents exists.

[2026-08-13 — v4.5.0 → v4.6.0: MINOR — how long a picture is good for; the attendance audit]
  Version change    : 4.5.0 → 4.6.0
  Bump rationale    : MINOR. One rule is added (17) and Rules 3, 7, 8, 11 and 12 are expanded.
                      Nothing is removed and nothing conformant to v4.5.0 becomes non-conformant.
                      Rule 17's ordinary form is what every existing type already does, stated;
                      Rule 8's lifecycle is what the wip-spec already required of all six types,
                      hoisted; Rule 12's floor is opt-in per type and the four types declaring none
                      keep their behaviour entire. No template that rendered under v4.5.0 stops
                      rendering under this one, which is the test v4.0.0 fixed for MAJOR.

                      MAJOR was considered for Rule 3's widened sibling test, which makes a problem
                      of something v4.5.0 left unstated, and rejected. The relation it adds holds
                      only between the graphics of one source module, and the sole pair it newly
                      catches is the two attendance types — both first specified in this amendment.
                      No catalogue existing under v4.5.0 gains a sibling it did not have.
  Feature branch    : 041-attendance-image-generation (created 2026-08-13 from main)

  Session context   : 037 built the calendar, 038 the lineup, 039 the results, 040 the standings.
                      This session begins the fifth per-image-type utility — the **attendance**
                      module's two graphics, the sheet and the check-in call — and no other type is
                      in scope. Principle XIV was audited against
                      `docs/wip-specs/image_module_specification.md` § "Attendance image generation"
                      as the four sessions before it audited their own. Nine divergences were found;
                      three were put to the author, one of which they dismissed as not arising, and
                      the rest settled by the precedent v4.1.0 set, that where the constitution
                      stated a rule too narrowly the wip-spec wins.

                      Attendance is the first module whose **two graphics stand in different
                      relations to their text**. The sheet replaces the textual sheet as the tables
                      before it did. The check-in graphic replaces nothing: the embed, its roster,
                      its status indicators and its three buttons remain entire, and the picture is
                      added beside them. It is also the first graphic that **outlives its own
                      generation** — the call's message cannot be reposted, its buttons being armed
                      against it, so the embed is edited in place on every press and the attachment
                      rides through untouched. Most of this amendment follows from that one fact and
                      from the discipline it demands in exchange.

  Author's rulings  : - Static graphics     → **the image type declares itself static**. The licence
                                            is a declaration made in the type's specification and
                                            not a property the module derives from the catalogue,
                                            the author being trusted to have kept mutable values off
                                            the picture. A catalogue-checkable form was offered and
                                            declined. Rule 17 therefore states the obligation, names
                                            who holds it, and says plainly that the module cannot
                                            catch a breach of it — a stale picture under a current
                                            message reports nothing, and that is the price of the
                                            only lifecycle a message with buttons admits.
                      - Collection floor    → **declared per image type**. A division holding no
                                            driver refuses rather than drawing an empty sheet, and
                                            each type says for itself whether it has such a floor.
                                            The calendar and the attendance sheet declare one; the
                                            results, standings, weather and verdicts types declare
                                            none and keep the silent-removal behaviour in full. A
                                            universal floor over every principal collection was
                                            offered and declined.
                      - Collapsed causes    → **not a divergence; no rule added**. Put to the author
                                            on the ground that the sheet empties a round cell for six
                                            distinct causes it does not tell apart, and dismissed:
                                            the sheet lists the points a round conferred and never
                                            the reason, so no cause is collapsed and nothing is
                                            hidden. Rule 3's determined-empty already governs the
                                            cell and Rule 4 raises no notice for it. Recorded here
                                            so the question is not re-opened by the next audit.

  Settled by precedent (not put to the author, the wip-spec having stated the general form):
                      - The posting lifecycle in full — that an attachment cannot enter a posted
                        message, that an edit therefore becomes a delete-and-repost, that the
                        replacement is produced before the original is destroyed, and that a
                        transport failure enqueues the text form. The wip-spec states all four in
                        each of six image-type sections; the constitution stated none of them.
                      - The graphic adding no precondition, stated for attendance and for verdicts.
                      - No posting, no graphic.
                      - The sibling relation holding between the graphics of one source module.
                      - The ordinal that is a slot and not a datum.

  Modified rules (Principle XIV):
    - **3. Every mandatory field MUST be resolved** — the **sibling** relation is widened. Two image
      types are siblings where they draw one output aspect, **or** where they are the several graphics
      of one **source module**, whatever they draw. The attendance sheet and the check-in graphic share
      not one field and are siblings all the same: common content makes a swapped file plausible,
      common provenance makes it possible, and only the latter is the test. Types of two different
      modules remain unrelated.
    - **7. Image output is additive** — two additions. **Additive means adding no precondition
      either**: the generation and posting of a graphic MUST NOT prevent, delay or condition anything
      the source module would have done without it — a sanction enforced, attendance rows opened, a
      review finalised, the message itself posted. Rule 4 said a broken graphic costs at most one
      graphic; this says it costs at most a picture and never a consequence. And **a graphic that
      displaces nothing** is admitted as the purest case of the rule rather than an exception to it,
      its fallback being the message posted without the attachment and its toggle altering the textual
      flow in no respect.
    - **8. Images are attachments** — expanded from three lines into the posting lifecycle every image
      type inherits, all of it hoisted from statements the wip-spec repeats per type. **No posting, no
      graphic**; **an attachment cannot be introduced into a message already posted**, so an in-place
      edit becomes a delete-and-repost with the id persisted in place of the old; **the replacement is
      produced before the original is destroyed**, on the fallback path as firmly as on the ordinary
      one; and **a transport failure retries as text**, a retry queue being durable and outliving the
      state that filled it.
    - **11. Template ids follow a fixed convention** — the converse of the coinciding ordinal. Where an
      ordinal does **not** coincide with a datum it is a place in a layout and the graphic MUST NOT
      draw it. An attendance sheet is ordered by total and stands in no classification; two drivers
      level stand level, and a numbered row would publish a ranking the module never computed. A type
      MUST say which of the two its ordinal is, the answer being invisible in the template.
    - **12. Collection capacity** — a **floor**, per the author's ruling, with its declaration
      per-type, its rationale (zero is otherwise merely the extreme of "fewer data than slots", and
      what gets posted is not a graphic of an empty division but a graphic of nothing), and its
      checking moment (against concrete data, never approximated earlier under Rule 9).
    - **Rationale** — extended for Rules 8 and 17 as two halves of one question the module could not
      put off past its sixth image type, for why the licence is a declaration and where that puts the
      burden, and for the floor and the precondition clause as the two answers to having nothing to
      draw or nothing to add.

  Added rules (Principle XIV):
    - **17. A graphic is redrawn whenever what it draws changes, unless its type is declared static.**
      The ordinary form is what every type before the check-in call did by accident rather than by
      decision, the message having been reposted whenever anything changed. A type MAY instead be
      declared static — generated once, its message edited in place beneath it, the attachment
      surviving every edit — against the single obligation that **a static type MUST NOT draw a value
      that changes while its message stands**. The check-in graphic is the first, drawing the round,
      its sessions, its date and its lock moment, and drawing no driver, no team, no RSVP status and
      no roster.

  Added sections    : Data & State Management → **New Entities (v4.6.0)**: **None**, recorded with
                      its reasoning so it is not re-derived. Both types already hold the column their
                      lifecycle needs and the two lifecycles differ — the sheet replaces its message
                      through `AttendanceDivisionConfig.attendance_message_id` as the results and
                      lineup flows do, while the check-in graphic leaves `RsvpEmbedMessage.message_id`
                      entirely alone, which is the point of declaring the type static.
  Removed sections  : None.

  Consistency       : `docs/wip-specs/image_module_specification.md` was brought into step in the
                      same change window per the close-out discipline in CLAUDE.md. Nothing in it was
                      **contradicted** — every rule added above was read out of it, and its
                      § "Attendance image generation" is conformant to Principle XIV as amended. What
                      it lacked was the **general form** of rules it states only per type, which is
                      where the next audit would have had to re-derive them.

                      Added to § "Conventions of every graphic", each holding for every graphic and so
                      placed there once rather than repeated per type:
                        - § "When a graphic is drawn again" (new) — the ordinary redraw rule, the
                          static declaration the author ruled for, the obligation that a static
                          graphic carry no value that changes while its message stands, and the
                          statement that the declaration is made by the graphic and not derived from
                          its catalogue.
                        - § "The capacity of a collection" — the floor the author ruled for, its
                          per-graphic declaration and the moment it is checked; and the ordinal that
                          is a place in the layout alone, which shall not be drawn.
                        - § "Errors and the rejection of input" — a graphic never prevents, delays or
                          conditions the work of the module owning the posting.

                      § "Attendance image generation" now names the check-in graphic a static graphic
                      in the conventions' terms, the behaviour having been described there already
                      but never declared.

                      The posting lifecycle and the retry-as-text rule are hoisted into the
                      constitution but **not** into the wip-spec's conventions: the wip-spec already
                      states both in each of its six type sections, so no knowledge is missing and
                      nothing is stale. Consolidating those twelve sentences is a tidying task
                      available to a later session and is not a correction.

                      `README.md` is **not** changed here, and was checked rather than assumed. The
                      two attendance graphics are not built, and the README describes the bot as it
                      is; its statement that the remaining five toggles change only what
                      `/images config view` and `/season review` report was verified still true —
                      `image_standings_service` is reachable from `/images test` alone and is not
                      wired into the standings posting flow. The README is updated within the
                      increment, when the behaviour it documents exists.

  Deferred          : TODO(PER_TYPE_ASSET_SENTENCES) — carried from v4.0.0 and **discharged for the
                      attendance type** in this audit. Its flag sentence separates the absent
                      nationality (field removed, non-fatal error) from the absent file (deferred to
                      the conventions), and its team-image and track-image sentences defer to the
                      lineup and calendar types respectively. The **weather** and **verdicts**
                      sections still bundle the two and carry the TODO forward. Nothing is ambiguous
                      in force, the conventions section superseding any per-type statement that a
                      field is removed for want of a file.

[2026-08-13 — v4.4.0 → v4.5.0: MINOR — the graphic may arrange; the standings audit]
  Version change    : 4.4.0 → 4.5.0
  Bump rationale    : MINOR. Rules 2, 3, 7, 9, 10, 12, 13 and 16 of Principle XIV are expanded and
                      one entity is amended. Nothing is removed and nothing conformant to v4.4.0
                      becomes non-conformant. Every addition either admits a form that was
                      previously unadmitted (the optional collection, the discriminated column
                      group, the per-member nested capacity, the derived presentation) or places an
                      obligation on the module rather than on a template (the shipped marker
                      files). No template that rendered under v4.4.0 stops rendering under this one,
                      which is the test v4.0.0 fixed for MAJOR.

                      MAJOR was considered for the Rule 12 per-member clause, which withdraws a
                      refusal v4.4.0 required — a template over-declaring a nested collection
                      against a data-fixed capacity was refused and is now trimmed silently — and
                      rejected. The clause widens what is accepted rather than narrowing it, so no
                      artefact that worked ceases to; a template refused under v4.4.0 now renders,
                      which is the benign direction.
  Feature branch    : 040-standings-image-generation (created 2026-08-13 from main)

  Session context   : 037 built the calendar, 038 the lineup, 039 the results. This session begins
                      the fourth per-image-type utility — the **standings**, both championships —
                      and no other type is in scope. Principle XIV was audited against
                      `docs/wip-specs/image_module_specification.md` § "Standings image generation"
                      as the three sessions before it audited their own sections. Eight divergences
                      were found; three were put to the author and five settled by the precedent
                      v4.1.0 set, that where the constitution stated a rule too narrowly the
                      wip-spec wins.

                      Standings is the first image type that **draws a grid**. A results table is
                      one dimension of members; a standings graphic is a classification crossed
                      with a calendar, and a cell of it belongs to a row and to a round both. It is
                      also the first type whose graphic carries **columns the text path does not
                      have** — the gap to the leader, the previous position and the position change
                      — and the first whose two graphics are posted where the text path posts one
                      message. Most of this amendment follows from those three facts.

  Author's rulings  : - Derived columns     → **admit derived presentation**. Arithmetic over
                                            figures the text path already publishes decides nothing
                                            and admits no new datum; it is presentation, not
                                            computation, and Rule 7's exception list stays closed at
                                            two. Two conditions bind it: the derivation is written
                                            in the service owning the data and never in the image
                                            utility, and a value requiring a *rule* to reach — an
                                            ordering, a tie-break, an award — remains forbidden. The
                                            countback is the standings service's; the subtraction is
                                            the graphic's.
                      - Marker asset class  → **the module ships its own closed classes**. The three
                                            directions of a position change are the module's own
                                            vocabulary, not values a league supplies, so
                                            `resources/markers/` gains `gained.svg`, `lost.svg` and
                                            `unchanged.svg` beside its fallback. It is the rule
                                            `mystery.svg` already follows in the track class,
                                            generalised, and it stands apart from the prohibition on
                                            shipping league-specific artwork.
                      - Fallback grain      → **stated generally in Rule 7**. A fallback covers the
                                            failed graphic's scope alone and never re-posts what a
                                            surviving graphic carries. Where the text path's message
                                            is coarser than one graphic it MUST be able to emit the
                                            proper subset; that it cannot is a defect in the text
                                            path, not a licence to post the whole. Binds attendance
                                            and weather equally.

  Settled by precedent (not put to the author, the wip-spec having stated the general form):
                      - The optional collection; the discriminated column group and the grid's
                        one-parent problem; the nested collection whose data-fixed capacity varies
                        per containing member; one capacity governing several id families; and
                        Rule 16's split being non-exclusive. Each is a form Rules 2, 3, 12 and 16
                        did not admit because no image type before this one needed it.

  Modified rules (Principle XIV):
    - **2. Removable groups** — the group table gains a fifth form, the **column of a collection of
      columns** (`round_<z>_group`), which bears its collection's discriminator and is removed when
      the column does not exist rather than when its field is emptied for every member. It carries
      v4.4.0's column prohibition for a reason stated more sharply: a cell of a grid belongs to its
      row and to its column both, and a node of an SVG file has one parent. The cell lives under the
      **row's** group, the column group carries chrome alone, and a column's cells leave the graphic
      through Rule 12 rather than through containment. A block group MAY also stand inside a member
      and bear that member's discriminator (`row_<x>_position_change_group`).
    - **3. Every mandatory field MUST be resolved** — a **collection MAY be optional as a whole**,
      with every collection nested inside it and every field of them, a template declaring none of
      it drawing the graphic without that part entire. A field classified mandatory *within* such a
      collection is mandatory only where the template declares the collection at all: the number of
      a round must stand on every round a template draws, and a template drawing no round owes none.
      This is the scope of a classification narrowing, as v4.3.0's per-member variation was, and not
      a third classification.
    - **7. Image output is additive** — two additions. **A derived presentation is not a
      computation**, per the author's ruling, with the two conditions that bind it and the line
      between measuring and deciding. And **a fallback is at the grain of the graphic that failed**,
      which obliges the text path to emit a proper subset of its usual message where its natural
      grain is coarser than one graphic.
    - **9. Template validity** — the three moments are stated to be the moments a **template** is
      evaluated, and not a bound on Rule 12's capacity check, which fires at any command changing
      the data a template is measured against. A command seating a driver in a division names no
      template and draws no graphic, and is refused all the same.
    - **10. Field catalogue** — where a portion of a catalogue is optional as a unit (Rule 3), the
      catalogue MUST name the collection at which the portion begins, so a check can tell a part
      deliberately not drawn from a part left out by mistake.
    - **12. Collection capacity** — two additions. **A capacity fixed by the data MAY vary by
      containing member**: where the configured value bounding a nested collection belongs to the
      containing member — the cars of a round, bounded by the seats of the team on the row — one
      template serves every member and no declared count is right for all, so the declared members
      are a ceiling, over-declaration is trimmed silently per member, and the fatal test is against
      the data actually drawn. And **one capacity may govern several id families**: a round ordinal
      standing as `round_<z>_group`, `row_<x>_round_<z>_group` and
      `row_<x>_round_<z>_driver_<w>_group` is removed from all three by one decision.
    - **13. Asset resolution** — a class whose data are a **closed set the module itself defines**
      is shipped complete by the **module**, the league having nothing to be incomplete against. The
      general cover-or-fallback obligation is discharged by the module rather than the league; a
      league pointing the class at its own directory is bound as any other.
    - **16. A graphic draws nothing a reader can act on** — two corrections. **The split is not
      exclusive**: the rule governs what a picture cannot carry, and a plain label MAY be drawn both
      as message text and on the graphic, so that a picture forwarded away from its message still
      says which phase it stands after. v4.4.0's illustration read as though the two were
      alternatives; the results type it described already contradicted that, `result_status` being a
      mandatory field of both results templates. And the fixed rendering extends from a **person**
      to any **entity of the server**, a team being reached through the Discord role its standings
      record and falling back to the name of the role itself.
    - **Rationale** — extended for the derived presentation, and for the pairing of Rules 12 and 13:
      both admit a case where the module, and not the league, is the one who knows, which is Rule
      3's mystery round applied twice more.

  Added sections    : Data & State Management → **New Entities (v4.5.0)**:
                      **DriverStandingsSnapshot** amended with `constructor_standings_message_id`
                      beside the existing `standings_message_id`. The textual flow posts one message
                      carrying both championships and one column sufficed; the image flow posts two,
                      each of which must be replaceable without disturbing the other under Rule 4's
                      unit of failure and Rule 7's fallback grain. It is the only part of this image
                      type reaching outside the image module.
  Removed sections  : None.

  Consistency       : `docs/wip-specs/image_module_specification.md` was brought into step in the
                      same change window per the close-out discipline in CLAUDE.md. One of its
                      statements was found to be **self-contradictory** and is corrected; the rest
                      are additions, the three rulings having gone the wip-spec's way.

                      Corrected:
                        - § "The capacity of a collection" — "Where the capacity is fixed by the
                          data, a divergence in either direction is a fatal error" is contradicted
                          by § "Standings image generation", which removes cars declared in excess
                          of a team's configured seats silently. The general statement gains the
                          per-containing-member case, which is what Rule 12 now states.

                      Added, each being a rule that holds for every graphic and so placed in
                      § "Conventions of every graphic" once rather than repeated per type:
                        - § "The fallback image" — the module ships every file of a closed class it
                          defines itself, on the model of the `mystery.svg` sentence already in
                          § "A round of the mystery format".
                        - § "What a graphic works out" (new) — the shared-rendering rule, the
                          derived presentation the author admitted, where its code lives, and the
                          line at which a value requiring a rule stays forbidden.
                        - § "The name of a team" (new) — beside "The name of a person", the author's
                          ruling having raised the standings section's role-based chain into the
                          general rule for every graphic. The standings section now points at it
                          rather than restating it.
                        - § "Errors and the rejection of input" — the unit of failure is one
                          graphic, and a fallback covers the failed graphic's part alone.
                        - § "Removable groups" — the four group forms, and the column of a
                          collection of columns with the one-parent rule that governs its cells.

                      `README.md` and `resources/README.md` are deliberately **not** changed. Both
                      enumerate what a clone ships, and `resources/markers/` holds `fallback.svg`
                      alone today. The claim goes in when the files do, per the README's own rule
                      that it describes the bot as it is and not as it is planned. This is an
                      implementation task of the increment: `resources/markers/` gains `gained.svg`,
                      `lost.svg` and `unchanged.svg`, and both READMEs then name them beside
                      `tracks/mystery.svg` as shipped reserved files.

  Deferred          : TODO(PER_TYPE_ASSET_SENTENCES) — carried from v4.0.0 and **further discharged
                      here for the standings type**, whose flag, marker, team-image and track-image
                      sentences each already separate the absent datum from the absent file and
                      defer the latter to the conventions. The attendance, weather and verdicts
                      sections still bundle the two. Nothing is ambiguous in force; the sentences
                      want splitting as each of those types is built.

  Templates confirmed aligned: no change required. Principle count is unchanged at I–XIV — this
  amendment expands rules within Principle XIV — so plan-template.md's Constitution Check and the
  Governance section's "I–XIV" both stand.

[2026-08-12 — v4.3.0 → v4.4.0: MINOR — the graphic re-presents; the results audit]
  Version change    : 4.3.0 → 4.4.0
  Bump rationale    : MINOR. Rules 2, 3, 4, 7, 9, 10, 11 and 13 of Principle XIV are expanded and
                      Rule 16 is added. Nothing is removed and nothing conformant to v4.3.0 becomes
                      non-conformant: every group form v4.3.0 admitted is still admitted, the
                      absent-datum fallback of Rule 13 is opt-in per field and inert where no
                      catalogue declares it, and the two image types that carry a catalogue today
                      have no sibling and so meet no new refusal.

                      MAJOR was considered for the Rule 7 shared-rendering clause, which places a
                      new MUST on code already delivered, and rejected. The versioning policy
                      reserves MAJOR for the removal or backward-incompatible redefinition of a
                      principle, and v4.0.0 fixed the test: a template that rendered under the
                      prior version stops rendering under this one. No template does. The clause
                      obliges a utility to call the formatter the text path already calls; that is
                      materially expanded guidance, which is MINOR.
  Feature branch    : 039-results-image-generation (created 2026-08-12 from main)

  Session context   : 037 built the calendar and 038 the lineup. This session begins the third
                      per-image-type utility — the **results** graphic — and no other type is in
                      scope. Principle XIV was audited against
                      `docs/wip-specs/image_module_specification.md` § "Results image generation"
                      as 037 and 038 audited it against their own sections. Eight divergences were
                      found; three were put to the author and five settled by the precedent v4.1.0
                      set, that where the constitution stated a rule too narrowly the wip-spec wins.

                      Results is the first image type whose **aspect is drawn by two templates**.
                      `images template results-qualifying` and `images template results-race` fill
                      two slots under one `results` toggle, sharing every field but the columns of
                      their rows. It is also the first type whose graphic re-presents values the
                      text path already renders — lap times, gaps, intervals, penalties — rather
                      than composing its own. Most of this amendment follows from those two facts.

  Author's rulings  : - Absent tyre         → **neither of the two settlements offered**. The
                                            author observed that `resources/tyres/` ships a
                                            fallback and asked why it could not serve. It can, and
                                            it is the better answer: Rule 13 gains an opt-in
                                            per-field declaration under which an **absent datum**
                                            draws the class fallback and raises no notice. Nothing
                                            is emptied, so the notice question dissolves and
                                            v4.3.0's narrow configured-absence gate in Rule 4 is
                                            left exactly as it stood. It is per field and never per
                                            class, an unoccupied lineup seat drawing no portrait
                                            and no flag where a tyreless entry draws its fallback.
                      - Shared rendering    → **one code path, MUST**. A value the graphic and the
                                            text path both draw is produced by one formatting
                                            function, which the utility calls and does not restate.
                                            `src/utils/results_formatter.py` already holds
                                            `_ms_to_lap_time`, `_ms_to_gap` and the session label.
                      - Picture limits      → **stated once**, as Rule 16, with the author's own
                                            addition: where the element mentions a person, the
                                            fixed rendering the graphic draws is that person's
                                            **current display name on the server at the moment of
                                            generation**. The lineup's resolution chain already
                                            begins there; the ruling makes it the rule for every
                                            graphic rather than one type's convention.

  Settled by precedent (not put to the author, the wip-spec having stated the general form):
                      - Block groups and column groups; a determined-empty value; a sibling
                        catalogue's field in a template; the structural check that refuses at every
                        moment; and the ordinal that coincides with a datum. Each is a form Rules
                        2, 3, 9 and 11 did not admit because no image type before this one needed
                        it.

  Modified rules (Principle XIV):
    - **2. Removable groups** — the forms a group may take are tabulated. Beside the field group
      and the member group v4.3.0 knew, a group MAY wrap a **block** of fields that stand or fall
      together (`fastest_lap_group`) and a **column** — the same field across every member of a
      collection (`postrace_penalty_group`), removed only when that field is emptied for every
      member. A column group carries the chrome and never a member's cell, so the two forms cannot
      contend for one node. The catalogue names the removal condition in every case.
    - **3. Every mandatory field MUST be resolved** — three additions. A value the data
      **determine to be empty** is determined, and offends no mandatory field: a sanction field of
      a phase not yet closed, a seat nobody occupies, the gap of the entry that set the reference
      lap. Where the text path draws a dash for what does not apply the graphic empties the field
      instead, and an **image** field is removed rather than emptied, having nothing to empty. A
      field belonging to a **sibling** type's catalogue is a problem — the wrong file in the slot —
      while an id belonging to no catalogue is chrome and not the module's business.
    - **4. Problems and notices** — **the unit of failure is one graphic**. A problem abandons the
      render it was met in and that render alone; where one event draws several graphics, the
      failure of one may not prevent the others.
    - **7. Image output is additive** — gains **one rendering, two presentations**. A value both
      paths draw is produced by one and the same formatting code. The exception list is closed at
      two: Rule 15's zone and Rule 16's fixed renderings.
    - **9. Template validity** — a **structural** check is neither a stand-in check nor a real-data
      check. Made against the template alone, it is complete at all three moments and refuses at
      each. That a results template's rows cannot be counted against a classification which will
      not exist until the session is run does not stop them being counted.
    - **10. Field catalogue** — where an aspect is drawn by more than one image type, each type is
      its own entry keyed by the template slot it fills. Siblings MAY share the declaration of
      their common part and MUST stay separately addressable, so a report can name which is at
      fault.
    - **11. Template ids** — where an ordinal coincides with a datum the member draws, the field is
      filled **from the ordinal** and no reconciliation is attempted; the renumbering is the source
      module's and is persisted before the graphic is drawn.
    - **13. Asset resolution** — a catalogue MAY declare, per image field, that an **absent datum**
      draws the class's `fallback.svg` with no notice. Inert where the class holds no fallback, in
      which case an absent datum remains governed by Rule 3 and is never fatal for want of a file.

  Added rule (Principle XIV):
    - **16. A graphic draws nothing a reader can act on.** A mention, a link, a button and a live
      timestamp are per-reader or interactive elements a picture cannot carry. Each is resolved to
      a fixed rendering or left to the message text the image rides on — a results post keeps its
      heading and lifecycle label as text and gives the table alone to the graphic. Where the
      element mentions a person, the fixed rendering is that person's display name on the server at
      the moment of generation. Rule 15 becomes this rule applied to time.

  Added sections    : Data & State Management → **New Entities (v4.4.0)**, recording that the
                      results type introduces **no** new entity and why:
                      `SessionResult.results_message_id` already holds the message the image flow
                      replaces, and `fastest_lap_colour` and `tyre_directory` were delivered with
                      the configuration surface at 035/036.
  Removed sections  : None.

  Consistency       : One statement of `docs/wip-specs/image_module_specification.md` is
                      contradicted by this amendment and is corrected in the same change window per
                      the close-out discipline in CLAUDE.md:
                        - § "Results image generation" → Resolution of the data — "Where no tyre is
                          recorded for the entry the field shall be removed and no error reported"
                          becomes the tyre fallback drawn, still with no error.
                      One statement is generalised rather than corrected: the driver-name
                      resolution chain of § "Lineup image generation" is raised into § "Conventions
                      of every graphic", the author having ruled it the rule for every graphic.

  Deferred          : TODO(PER_TYPE_ASSET_SENTENCES) — carried from v4.0.0 and further discharged
                      here for the results type, whose flag and tyre sentences now separate the
                      absent datum from the absent file. The standings, attendance, weather and
                      verdicts sections still bundle the two. Nothing is ambiguous in force; the
                      sentences want splitting as each of those types is built.

  Templates confirmed aligned: no change required. Principle count is unchanged at I–XIV — this
  amendment adds a *rule* to Principle XIV, not a principle — so plan-template.md's Constitution
  Check and the Governance section's "I–XIV" both stand.

[2026-08-12 — v4.2.0 → v4.3.0: MINOR — a collection may be keyed by a name; the lineup audit]
  Version change    : 4.2.0 → 4.3.0
  Bump rationale    : MINOR. Rules 2, 3, 4, 9, 10, 11, 12 and 13 of Principle XIV are expanded
                      and Principle IX gains three invariants. Nothing is removed and no
                      artefact conformant to v4.2.0 becomes non-conformant: every ordinal id
                      remains an id, every catalogue stating a fixed capacity remains a
                      catalogue, and every template that rendered still renders.

                      MAJOR was considered for the Rule 4 suppression clause, which withdraws a
                      notice v4.2.0 required, and rejected. v4.0.0 took MAJOR because a template
                      that rendered under the prior version stopped rendering under it. Here the
                      drawn graphic is byte-for-byte what it was; only a line in a staff-read log
                      is not written. No template, catalogue or asset is invalidated, so the
                      versioning policy's "backward-incompatible redefinition" is not met.
  Feature branch    : 038-lineup-image-generation (created 2026-08-12 from main)

  Session context   : 037 built the calendar, the first per-image-type utility. This session
                      begins the second — the **driver lineup** — and no other type is in scope.
                      Principle XIV was audited against `docs/wip-specs/image_module_specification.md`
                      § "Lineup image generation" as 037 audited it against § "Calendar image
                      generation". Seven divergences were found. Four were settled by the
                      precedent v4.1.0 set — where the constitution stated a rule too narrowly,
                      the wip-spec wins — and three were put to the author.

                      The lineup is the first image type that is not a table. Its members are
                      addressed by the *name* of a team rather than by an ordinal, because a
                      team's block is hand-designed in that team's own livery and an ordinal
                      cannot say which team it is. That single fact is what most of this
                      amendment follows from.

  Author's rulings  : - Team name constraints → **unconditional**. `team add` and `team rename`
                                            validate the normalised name whether or not the image
                                            module is enabled. A name is cheapest to constrain at
                                            the moment it is set.
                      - Division uniformity  → **gated** on the module and the `lineup` aspect. It
                                            is a real restriction on how a league runs its season
                                            and a league drawing no lineup owes it nothing.
                      - Missing portrait     → **Rule 13 stands**. The wip-spec's "silent removal"
                                            is corrected: the fallback is drawn and a notice
                                            raised. v4.0.0 settled this class uniformly and at
                                            MAJOR cost; reopening it for one asset class would
                                            put a hole in a graphic.
                      - Configured absence   → **suppressed**. Where a league switched the datum
                                            off at its source, the emptied field raises no notice.

  Settled by precedent (not put to the author, the wip-spec having stated the general form):
                      - Collection keys, singletons, per-member classification, and capacity
                        fixed by the data rather than by the template. Each is a form Rules 10–12
                        did not admit because no image type before this one needed it.

  Modified rules (Principle XIV):
    - **2. Removable groups** — a group is ordinarily optional chrome, but an image type MAY
      declare one **mandatory**: the template must provide the block, and the data may none the
      less have nothing to put in it. `reserve_group` is the case — every division holds a reserve
      team and many field no reserve driver.
    - **3. Classification** — a classification MAY vary by member within a collection, declared by
      a rule and never by an enumeration. It is still exactly two classifications; the scope over
      which one is declared is what narrows.
    - **4. Configured absence raises no notice** — narrow, and justified per field in the
      catalogue. It requires a configuration switch that turns the datum off *at its source*, and
      does not extend to a value the league collects and merely happens not to hold. Nothing is
      degraded when the graphic draws exactly what the league configured.
    - **9. Template validity** — the warning/refusal split is generalised. A moment that can
      compare the template only against a **stand-in** for the data that will be drawn warns; a
      moment that holds the real data refuses. Two stand-ins are now named: the calendar's
      most-demanding division at season review, and the lineup's season-under-setup teams at the
      configuring command. The converse is stated for the first time and is what makes the
      lineup's season review a refusal rather than a warning.
    - **10. Field catalogue** — for a collection, the catalogue names its discriminator form
      (Rule 11) and how its capacity is fixed (Rule 12), not a bare number.
    - **11. Template ids** — a member is discriminated by an **ordinal or a key**, the catalogue
      fixing which, and the two are never mixed within one collection. A key is a datum normalised
      by Rule 13, so one datum yields one spelling in the id and in the filename alike. A
      collection MAY be a **singleton** bearing no discriminator (`reserve_name`), whose name is
      reserved against every keyed sibling. An image type MUST NOT choose a key where an ordinal
      would serve, the cost of a key being a template authored against one league's data.
    - **12. Collection capacity** — fixed in one of exactly two ways, named per collection by the
      catalogue. **By the template**: slots are the capacity, under-fill removes silently and
      overflow is fatal, as v4.1.0 had it. **By the data**: a configured value fixes it — the
      teams of a division, the seats of a team — and divergence in *either* direction is fatal,
      both sides being declared and knowable. A member the data hold but leave empty is not a
      divergence and is drawn empty.
    - **13. Asset resolution** — states that its normalisation is the same rule that produces a
      keyed id, so the two cannot drift apart.
    - **Rationale** — extended for keys, the two capacity kinds and configured absence.

  Modified principles :
    - **IX. Team & Division Structural Integrity** — three invariants added: **team name
      validity** (normalises non-empty, begins with a letter, unique in scope, not `reserve`;
      rejected at `team add` / `team rename`, failed at `season review`; only the *new* name of a
      rename is validated, and approved seasons are not re-validated), the **Reserve team at
      server scope**, and **uniform divisions where a lineup graphic is drawn** — the one of the
      three that is gated on the image module, per the author's ruling.

  Added sections    : Data & State Management → **New Entities (v4.3.0)**, recording that the
                      lineup introduces **no** new entity and why, `lineup_message_id` having been
                      added at v2.8.0 for exactly this purpose.
  Removed sections  : None.

  Consistency       : Two statements of `docs/wip-specs/image_module_specification.md` are
                      contradicted by this amendment and are corrected in the same change window
                      per the close-out discipline in CLAUDE.md:
                        - § "The capacity of a collection" — "its members bearing an ordinal" is
                          false of a keyed collection and of a singleton.
                        - § "Lineup image generation" → Test data — "the silent removal of the
                          driver image field" is settled against by the portrait ruling.

  Deferred          : TODO(PER_TYPE_ASSET_SENTENCES) — carried from v4.0.0 and further discharged
                      here for the lineup. The results, standings, attendance, weather and
                      verdicts sections still bundle "the datum is absent" with "no matching file
                      is found". Nothing is ambiguous in force; the sentences want splitting as
                      each of those types is built.

  Templates confirmed aligned: no change required. Principle count is unchanged at I–XIV — this
  amendment expands rules within Principle XIV and invariants within Principle IX — so
  plan-template.md's Constitution Check and the Governance section's "I–XIV" both stand.

[2026-08-12 — v4.1.0 → v4.2.0: MINOR — the Attendance module catches up to what was built]
  Version change    : 4.1.0 → 4.2.0
  Bump rationale    : MINOR. Materially expanded guidance in three Attendance module sections
                      and one entity. No Core Principle (I–XIV) is removed or redefined, so
                      MAJOR does not apply under the versioning policy. Above PATCH because the
                      reserve distribution priority doubles from three tiers to six and gains a
                      second tie-break, and the point distribution table gains two rules that
                      were nowhere stated.

  Session context   : This session repaired the suite's 22 standing failures. One of them,
                      `test_rsvp_service.py::test_tier1_team_gets_reserve_first`, was failing
                      because commits 4f69db5 and 9699aa2 had deliberately reordered reserve
                      allocation without the test, the wip-spec, or this document following.
                      The author ruled the code correct. Auditing this document against the
                      build then surfaced two further divergences that predate the session:
                      migrations 037 and 038 renamed the penalty columns and pardon types in
                      April and the constitution was never amended.

  Author's rulings  : - Reserve priority  → code. DECLINED outranks NO_RSVP; teams with no
                                            seated drivers lead; a served team steps aside.
                      - Standings tie-break → constitution and wip-spec, which already agreed:
                                            lowest-ranked team first. The code disagreed with
                                            both and was corrected in `rsvp_service.py`.
                      - Penalty naming    → migrations. The shipped schema is the fact.

  Modified sections : - Reserve Distribution — 3 tiers → 6; "number of ACCEPTED drivers already
                        assigned" tie-break withdrawn, being now expressed as tiers 1 and 5;
                        alphabetical team name added as the final tie-break; unranked-team
                        ordering stated.
                      - Attendance Point Distribution — ACCEPTED+absent split from
                        TENTATIVE/DECLINED+absent; allocated-reserve scoring stated; zero floor
                        stated; column names corrected.
                      - Attendance Pardon Workflow — pardon types NO_RSVP_ABSENT → ABSENT and
                        RSVP_ABSENT → NO_SHOW, per migrations 037 and 038; validation rules
                        restated to match.
                      - AttendanceConfig entity — `no_rsvp_absent_penalty` → `absent_penalty`,
                        `rsvp_absent_penalty` → `no_show_penalty`.
                      - AttendancePardon entity — `pardon_type` ENUM corrected to the shipped
                        NO_RSVP / ABSENT / NO_SHOW.

                      Historical Sync Impact Report entries below retain the old names, being
                      a record of what was true at those versions, and are left untouched.

  Added sections    : None.
  Removed sections  : None.
  Deferred TODOs    : None.

[2026-08-12 — v4.0.0 → v4.1.0: MINOR — the first per-type increment; the calendar audit]
  Version change    : 4.0.0 → 4.1.0
  Bump rationale    : MINOR. One rule added (15), one generalised (11), two expanded (6, 9), one
                      entity amended. Nothing is removed and nothing is redefined incompatibly:
                      a catalogue or template authored to v4.0.0 remains conformant under this
                      version, `row` being one collection name among several rather than a form
                      withdrawn.

                      MAJOR was drafted and rejected. The draft relaxed Rule 12 so that a
                      catalogue could declare **reported omission** for a collection — excess
                      members dropped with a notice rather than a refusal — which would have
                      forbidden behaviour v4.0.0 mandates. The author ruled against it: overflow
                      means one thing, more data than the template has slots for, and is fatal
                      wherever it occurs. Rule 12 therefore stands as v4.0.0 wrote it and the
                      bump falls to MINOR.
  Feature branch    : 037-calendar-image-generation (created 2026-08-12 from main)

  Session context   : 035 built the engine and the configuration surface; 036 settled the
                      module's cross-cutting conventions. This session begins the first
                      **per-image-type** generation utility — the **calendar** — and no other
                      type is in scope. Principle XIV was audited against
                      `docs/wip-specs/image_module_specification.md` § "Calendar image
                      generation", readable again after two sessions deny-listed, and seven
                      divergences were put to the author. This discharges the per-type half of
                      TODO(WIP_SPEC_RECONCILIATION), which v3.0.0 closed only against the
                      cross-cutting conventions the author had supplied directly. Rules 11 and 12
                      were written at v2.13.0 without sight of that document and expressly flagged
                      as at risk of contradicting it; the audit found one of the two did.

  Author's rulings  : Each divergence was decided individually rather than by a blanket rule that
                      the wip-spec or the constitution wins:
                        - Collection ids     → wip-spec. `round_<x>` is the general form the
                                               constitution had stated too narrowly.
                        - Overflow           → constitution, and extended. Fatal for every
                                               collection of every image type, not the calendar
                                               alone. Four wip-spec statements are corrected.
                        - Mystery round      → neither. See Rule 3 below.
                        - Message refresh    → wip-spec. User-visible mechanics belong there.
                        - Validity moments   → all three are right, and are tabulated here.
                        - Asset href, zone   → constitution. Both bind every image type.

  Generalised rule (Principle XIV):
    - **11. Template ids** — the repeating-collection form generalises from `row_<x>_<field>` to
      `<collection>_<x>_<field>`, the collection named by the thing it repeats and fixed by the
      image type's catalogue. `row` becomes one collection name among several rather than the
      only one; a calendar's members are rounds, a forecast's are sessions. Nesting is admitted
      explicitly, the wip-spec reaching three levels (`row_<x>_round_<z>_driver_<w>`). A gap in
      the numbering is named a fault of the template. Backward compatible: every v4.0.0 id is
      still a conformant id.

  Modified rules (Principle XIV):
    - **3. Every mandatory field MUST be resolved** — gains "a value the data does not hold
      literally is still a value". Where a record is of a kind carrying nothing — a round whose
      track is concealed until it is run — the image type **defines the value standing for that
      kind** and fills the field with it. A mystery round therefore places "Mystery GP" on
      `round_<x>_race_name` and "Mystery" on `round_<x>_country_name`, and resolves `mystery.svg`
      by the ordinary slug rule of Rule 13. No field is emptied and no exemption arises. An
      earlier draft added a conditional-exemption mechanism for this case; the author's ruling
      removed the need for it entirely, which is the better outcome — the kind *is* the datum.
    - **6. Assets** — an asset MUST be referenced by an href that is a **URI**; a bare filesystem
      path resolves to nothing and rasterises as a broken-image mark, invisible in a browser
      preview. Author-side transparent padding is stated explicitly, the generator's prohibition
      on padding being unchanged.
    - **9. Template validity** — the three moments at which validity is evaluated are tabulated:
      the command naming the template, season review, and immediately before a render. All three
      MUST read one and the same evaluation. A check is made at the earliest moment its data
      exists and repeated before the render, the data having possibly changed since. A check
      whose data is not yet available MUST NOT be approximated earlier: season review can compare
      a template only against the most demanding division of the season, so a divergence found
      there is a **warning**. Review reports; approval refuses.
    - **12. Collection capacity** — unchanged in substance, and strengthened in reach. The
      `<collection>_<x>_group` form follows Rule 11. A closing paragraph states that overflow is
      fatal for every collection of every image type, principal or incidental, so that the four
      wip-spec statements admitting a non-fatal omission are settled against.

  Added rule (Principle XIV):
    - **15. A graphic carries one time zone, named by configuration.** A text output renders an
      instant as a Discord timestamp every reader sees in their own zone; a picture cannot. Every
      date and time on a graphic is drawn in the single configured zone, identically for every
      reader, with the zone abbreviation appended wherever a time is drawn — never derived from a
      locale, the host machine or the viewer. Stated once so that no image type invents a
      per-reader scheme a picture cannot honour. It is a real reduction against the text path and
      the rule says so.

  Modified sections :
    - Principle XIV, Rationale — "Rules 10–14" becomes "Rules 10–15" and names the zone rule. The
      mandatory/optional paragraph gains a statement that there is no third classification and no
      exemption from either. Rule 14's list of cases where browser and rasteriser disagree gains
      unresolvable asset references, pairing with Rule 6.
    - Data & State Management → **New Entities (v4.1.0)**: **Division** amended with
      `calendar_message_id`, and `/division calendar sync` recorded. The textual calendar has been
      posted once and never replaced, so no id was held; an attachment cannot be introduced into a
      message already posted, so the image flow must know which message to replace. The mechanics
      of the replacement are user-visible and are left to the wip-spec per the author's ruling.

  Rejected from this amendment (recorded so the reasoning is not re-derived):
    - A per-collection **overflow treatment** admitting non-fatal omission. Overruled: fatal
      everywhere.
    - A **conditional exemption** mechanism on Rule 3. Made unnecessary by the mystery-round
      ruling.
    - A **replacement, not edit** clause on Rule 8. Overruled to the wip-spec as user-visible.

  Consistency       : Four statements of `docs/wip-specs/image_module_specification.md` are
                      contradicted by the overflow ruling and one by the mystery-round ruling.
                      They are corrected in that document in the same change window as this
                      amendment, per the close-out discipline in CLAUDE.md.

  Deferred          : TODO(PER_TYPE_ASSET_SENTENCES) — carried from v4.0.0 and now partly
                      discharged by the author's own edit at af76d87, which realigned the calendar
                      and lineup asset statements to Rule 13. The results, standings, attendance,
                      weather and verdicts sections still bundle "the datum is absent" with "no
                      matching file is found". Nothing is ambiguous in force, the Conventions
                      section governing them correctly; the sentences want splitting as each of
                      those types is built.

  Templates confirmed aligned: no change required. Principle count is unchanged at I–XIV — this
  amendment adds a *rule* to Principle XIV, not a principle — so plan-template.md's Constitution
  Check and the Governance section's "I–XIV" both stand.

[2026-08-12 — v3.0.0 → v4.0.0: MAJOR — assets are resolved apart from field classification]
  Version change    : 3.0.0 → 4.0.0
  Bump rationale    : MAJOR. Rule XIV.13 loses a branch and inverts a severity: a missing asset
                      in a class with no fallback was survivable for an optional field and is
                      now fatal for every field. A template authored against v3.0.0 — an
                      optional flag slot, no fallback in the flags directory, no file for one
                      nationality — rendered before this version and does not render after it.
                      That is a backward-incompatible redefinition, which the versioning policy
                      reserves MAJOR for.

                      MINOR was considered and rejected: this removes a branch rather than
                      expanding guidance. The practical blast radius is nil — the only code
                      written against v3.0.0's XIV.13 was written earlier in this same session
                      and is already updated — but semantic versioning describes the contract,
                      not the disruption.
  Feature branch    : 036-image-generation-conventions

  Session context   : Closing out 036 surfaced a three-way divergence. The implementation always
                      raised a notice on a fallback; v3.0.0 said the same; the wip-spec's
                      twenty-two per-type statements each set their own severity for a missing
                      asset, some non-fatal and some silent. Asked to settle it, the author
                      ruled that assets are resolved uniformly and apart from the
                      mandatory/optional distinction, which classifies template *fields* alone:

                        asset found                   → drawn
                        absent, class has a fallback  → fallback drawn, notice
                        absent, class has none        → fatal, generation abandoned

  Redefined rules (Principle XIV):
    - **13. Asset resolution** — retitled from "…with a per-directory fallback image" to
      "…and every class carries a fallback". Opens by separating asset from field in as many
      words, since conflating them is the error this amendment corrects. The fallback becomes
      an obligation (MUST cover every datum **or** hold `fallback.svg`) rather than a
      permission (MAY hold one). The classification-dependent branch is struck and replaced by
      a three-outcome table that holds whatever the receiving field's classification. A new
      clause distinguishes an **absent datum** — where no asset is sought at all, and Rule 3
      governs — from an absent **file**.

  Modified sections :
    - Principle XIV, Rule 3 — closing paragraph rewritten. Mandatory and optional are now stated
      to classify fields of the template "and nothing else", saying nothing about the assets
      placed upon them.
    - Principle XIV, Rule 4 — the problem list drops "and the field is mandatory" from the
      unresolved-asset entry. The notice list is unchanged: a fallback still raises one, and an
      optional *value* that cannot be determined still empties its field.
    - Principle XIV, Rationale — the paragraph arguing that aborting for one missing portrait
      would make the module useless is withdrawn; it argued the case this amendment overturns.
      Replaced with the field/asset split and why a single uniform asset rule is worth more to a
      template author than a proportionate one.

  Consistency       : `docs/wip-specs/image_module_specification.md` § "The fallback image" was
                      written to this rule in the same session and agrees. `src/utils/svg_fill.py`
                      no longer consults the catalogue during asset resolution, and its tests
                      assert the uniform outcome for mandatory, optional and absent catalogues
                      alike.

  Deferred          : TODO(PER_TYPE_ASSET_SENTENCES) — roughly twenty-two statements in the
                      wip-spec's per-image-type sections bundle "the datum is absent" together
                      with "no matching file is found" and assign one severity to both. Those
                      two cases now diverge. The Conventions section governs them correctly, so
                      nothing is ambiguous in force, but each sentence reads as though it still
                      decides the file case. They want splitting in the author's own voice.

  Templates confirmed aligned: no change required. Principle count is unchanged at I–XIV.

[2026-08-12 — v2.13.0 → v3.0.0: MAJOR — reconciliation with the author's specification]
  Version change    : 2.13.0 → 3.0.0
  Bump rationale    : MAJOR. Four rules of Principle XIV are redefined in ways that contradict
                      their previous text rather than extending it: XIV.2's addressing contract,
                      XIV.7's fallback behaviour, XIV.11's id convention and XIV.13's asset
                      resolution. A template authored to v2.13.0's XIV.11, or an asset named to
                      its XIV.13, is wrong under this version. The versioning policy reserves
                      MAJOR for backward-incompatible redefinition, and that is what this is,
                      notwithstanding that the practical blast radius is nil — nothing was built
                      against the two superseded rules, which were one session old.
  Feature branch    : 036-image-generation-conventions

  Session context   : v2.13.0 added Rules 10–14 from answers the author gave without sight of
                      `docs/wip-specs/image_module_specification.md`, which is deny-listed. The
                      deferred item TODO(WIP_SPEC_RECONCILIATION) warned that the risk was
                      contradiction rather than omission. The author then supplied the module's
                      cross-cutting conventions directly, specified as
                      `specs/036-image-generation-conventions/spec.md`, and two of the five
                      guesses proved wrong. This amendment discharges that TODO in full.

  Redefined rules (Principle XIV):
    - **2. Fields are addressed by `@id`** → "…with a layer label as fallback". The identifier
      stays normative, but where no node bears it and a layer carries the field's name as its
      label, that layer is the field; where both exist, the identifier wins. Templates are
      authored in graphical editors where the manager sets labels and never sees the generated
      identifier. The operation table is restated: **Truncate** and **Empty or remove** are now
      named operations, and **Vertical crop** is demoted from a general operation to a
      calendar-specific one. **Removable groups** (`<field>_group`) are defined here: the group
      goes wherever the field would be emptied or removed, the field itself untouched, and its
      removal never resizes the canvas.
    - **3. Every addressable field MUST be resolved** → "Every **mandatory** field…". Catalogues
      now classify each field mandatory or optional. An absent or undeterminable optional field
      is no longer a render failure; it is emptied, or its `_group` removed.
    - **7. Image output is additive** — the blanket "a failed render MUST fall back to the text
      output" is replaced by a split on who asked. An **uncommanded** posting (horizon, schedule,
      startup) falls back to text. A **commanded** posting does not: it is rejected, nothing is
      posted, and the caller is told what is at fault. Silently substituting text would deny the
      one person able to fix the template the chance to do so.
    - **11. Template ids follow a fixed convention** — the row form becomes `row_<x>_<field>`,
      indexed from 1, **unpadded**, with no per-collection prefix. v2.13.0's
      `standings_row_03_points` is withdrawn.
    - **13. Asset resolution** — two corrections. The slug separator is an **underscore**, not a
      hyphen (`red_bull_racing.svg`), and the fallback is a reserved `fallback.svg` per asset
      **directory**, not a placeholder declared per field by a template.

  Modified sections :
    - Principle XIV, Rule 4 — problem and notice lists restated against the mandatory/optional
      and fallback model. Two paragraphs added: where each outcome is reported (never a
      driver-read channel), and rejection of faulty input at the earliest moment it is
      detectable. The v2.13.0 note fixing the word "placeholder" is removed with the mechanism.
    - Principle XIV, Rule 10 — catalogues also carry the mandatory/optional classification and,
      for image fills, the asset class.
    - Principle XIV, Rule 12 — unused rows go by `row_<x>_group`; vertical crop is referenced as
      an image-type-specific mechanism. Overflow is rejected at the command that would cause it.
    - Principle XIV, Rationale — extended to cover the mandatory/optional split.
    - Data & State Management → New Entities (v2.11.0): **RenderNotice.notice_kind** —
      `ASSET_PLACEHOLDER_USED` (added in v2.13.0, never implemented) is replaced by
      `ASSET_FALLBACK_USED`, and `OPTIONAL_FIELD_EMPTIED` is added.

  Why the underscore matters: the normalisation now stated is the one
  `resources/poc/build_poc.py` already implements, whose docstring calls it "the spec's
  normalization". Every asset shipped under `resources/` is named that way. v2.13.0's hyphen was
  an invention of that session and would have required renaming the entire asset tree.

  Impact on delivered code (035): the layer-label fallback, the mandatory/optional split, the
  commanded/uncommanded fallback split and the `_group` convention are all **new behaviour not
  yet built**. Two delivered behaviours now contradict this version and must change:
  `utils/svg_document.py` surfaces the raw parser error where a named fault is now required, and
  `cogs/image_cog.py` writes the template filename before validating it where rejection with the
  configuration untouched is now required. Both are in scope for 036.

  Deferred          : none. TODO(WIP_SPEC_RECONCILIATION) from v2.13.0 is closed. The wip-spec
                      remains deny-listed, but its cross-cutting content has now been supplied
                      by the author directly and reconciled here.

  Templates confirmed aligned: no change required. Principle count is unchanged at I–XIV, which
  is what plan-template.md's Constitution Check reads.

[2026-08-12 — v2.12.1 → v2.13.0: MINOR — the rules the per-type generators will each need]
  Version change    : 2.12.1 → 2.13.0
  Bump rationale    : MINOR — five rules added to Principle XIV as materially expanded
                      guidance, plus one notice kind. No principle removed; no principle
                      redefined incompatibly. Principle count is still I–XIV.
  Feature branch    : 036-image-generation-conventions

  Session context   : The 035-image-module increment built the engine, the config surface
                      and `/images test`. This session audited Principle XIV against what
                      was built, before the first per-image-type generation utility is
                      written. Rules 1–9 were found present and honoured by the code. Four
                      decisions every future utility depends on were found ungoverned, and
                      were put to the author:

                        Q1 (field catalogue home)  → a code constant per image type, in one
                                                     shared declaration module. Not a
                                                     sidecar file, not per-call-site.
                        Q2 (collection overflow)   → under-fill crops or removes; over-fill
                                                     is a problem and falls back to text.
                                                     Not truncation, not continuation images.
                        Q3 (id convention)         → binding: snake_case, with a zero-padded
                                                     `_NN` index on repeating collections.
                        Q4 (asset resolution)      → documented slug, plus an optional
                                                     template-declared placeholder that turns
                                                     a miss into a notice rather than a
                                                     problem.

  Added rules (Principle XIV):
    - **10. Field catalogue as a code constant** — one entry per image type in a single
      shared module; the same object read by the fill pipeline (Rule 3) and validity Layer 2
      (Rule 9); no utility merges without one.
    - **11. Template id convention** — lowercase semantic snake_case; `<collection>_<NN>` and
      `<collection>_<NN>_<field>` for repeating members, contiguous from `01`;
      `crop_<collection>_<NN>` for the matching crop point. Catalogues express a collection
      as prefix + capacity, never as an enumerated id list.
    - **12. Declared capacity, fatal overflow** — the template states how many member slots a
      varying list has. Fewer data → remove or crop. More data → a problem naming count,
      capacity and template. No silent truncation, no continuation images.
    - **13. Slug asset resolution with optional placeholder** — deterministic lowercase,
      accent-folded, hyphen-collapsed slug + `.svg` inside the configured directory. A
      template may declare a per-field placeholder; a miss with a placeholder is a notice,
      a miss without one is a problem. Placeholders are bound by Rule 6.
    - **14. Verification is by PNG** — any check that a render is correct MUST be made against
      the rasterised PNG. A browser view of the filled SVG is not evidence; the two disagree
      on flowed text, font substitution and the crop.

  Modified sections :
    - Principle XIV, Rule 4 — the problem list now names "an asset that does not resolve and
      for which the template declares no placeholder" and "a collection larger than the
      template's declared capacity"; the notice list gains the placeholder substitution. A
      note fixes the word *placeholder* to one meaning (a missing **asset**'s substitute),
      since Rule 4 also forbids posting a stand-in for a whole render.
    - Principle XIV, Rationale — extended to cover Rules 10–14.
    - Data & State Management → New Entities (v2.11.0): **RenderNotice.notice_kind** gains
      `ASSET_PLACEHOLDER_USED`.

  Compatibility     : Backward compatible with what is built. `FillSpec.expected_fields` is
                      already optional and already the catalogue's shape; Rule 10 makes its
                      source authoritative rather than changing its type. Validity layers 2–4
                      remain reserved and unenforced — Rule 10 supplies the artefact Layer 2
                      has been waiting on, but does not ratify the layer.

  Deferred          : TODO(WIP_SPEC_RECONCILIATION) — `docs/wip-specs/image_module_specification.md`
                      is deny-listed in `.claude/settings.json` and was unreadable this session,
                      as it was during 035 specification and planning (see that plan's Open
                      Decisions). Rules 10–13 were derived from Principle XIV and the author's
                      answers, not from that document. If it already fixes an id convention, a
                      slug rule or a capacity policy, reconcile before the first utility is
                      written; the risk is contradiction, not merely omission.

  Templates confirmed aligned: no change required. plan-template.md's Constitution Check reads
  the principle list at runtime and the count is unchanged; spec-template.md and
  tasks-template.md carry no per-principle text.

[2026-08-11 — v2.12.0 → v2.12.1: PATCH — entity re-grained to match what was built]
  Version change    : 2.12.0 → 2.12.1
  Bump rationale    : PATCH — One entity renamed and re-grained to match the delivered
                      implementation. No principle added, removed or redefined; no
                      governance rule changed. The correction was anticipated in the
                      035-image-module plan's Complexity Tracking and is applied now that
                      the feature is built.
  Feature branch    : 035-image-module

  Modified sections :
    - Data & State Management → New Entities (v2.11.0): **ImageTypeState** renamed to
      **ImageAspectToggle** and re-grained from the image *type* (15 rows) to the output
      *aspect* (8 rows), with `enabled` defaulting to false rather than true.

  Why                : The command surface a league actually uses is the eight aspects of
                       `/images config toggle`. No command addresses an individual
                       template's toggle, so a 15-row table would have required fanning
                       one value out to as many as six rows (weather) and folding them
                       back for reporting, with no user-visible benefit. The default was
                       false, not true, because Principle X.1 requires a freshly
                       configured server to have every optional capability off.
                       `source_module` ceased to be a stored column: it never varies per
                       server, so it is a code constant per aspect.

  Governance         : Unchanged. Principle count is still I–XIV.

  Templates confirmed aligned: no change — this touches an entity definition only.

[2026-08-10 — v2.11.0 → v2.12.0: MINOR — validity contract and stale-proof config exception]
  Version change    : 2.11.0 → 2.12.0
  Bump rationale    : MINOR — Two materially expanded guidance additions, both arising from
                      clarifications answered by the author during the 035-image-module
                      specification session. No principle removed or incompatibly redefined.
  Feature branch    : 035-image-module

  Session context   : Three scope questions were put to the author while specifying the
                      image module. Two were answered with a decision; the third was
                      answered with an instruction to leave the matter open and govern it,
                      which is what this amendment does.
                        Q1 (increment size)     → config surface + `images test` only;
                                                  the output toggles are stored but inert
                                                  this increment. No governance impact.
                        Q2 (template validity)  → deliberately left open. Validity is to be
                                                  defined incrementally in later sessions and
                                                  wired into the same reporting surface. The
                                                  author asked that this be a keynote of the
                                                  constitution or the specification; it is
                                                  governed here and instantiated there.
                        Q3 (config on disable)  → retained. Requires an exception to
                                                  Principle X.6, added here.

  Modified principles :
    - X. Modular Feature Architecture, rule 6 (Module configuration isolation) — added the
        "configuration that cannot go stale" exception. A module whose configuration consists
        solely of values that remain true while it is disabled retains that configuration
        across a disable; only the module-enabled flag is cleared. Qualification is defined
        narrowly: a value qualifies only if it names nothing the bot owns or schedules — a
        filesystem path, a display preference, a colour, a format. Any value naming a Discord
        channel, role, message or scheduled job does not qualify. A module claiming the
        exception must enumerate its qualifying values in its feature specification. The
        Image generation module claims it for the whole of its configuration.
    - XIV. Image Generation Discipline — added rule 9, "Template validity is a layered,
        extensible contract". Validity is evaluated at configuration time, separately from
        rendering, as ordered named layers, cheapest first. Layer 1 (Resolution: file
        resolves, parses as SVG, declares a root canvas) is mandatory from the outset for
        every template. Deeper layers — field-catalogue conformance, field addressability,
        declared text bounds, trial render — are ratified per image type as that type's
        catalogue is specified, and must not be enforced against a type whose catalogue does
        not yet exist. Four rules bind the growth: stable surface (adding a layer changes
        neither the command surface, the three reported states, nor the report structure —
        only the set of reasons); specific attribution (name the individual template, never
        the group); declared depth (a report states which layers were applied); and no silent
        pass (a type checked only shallowly is not presented as fully valid).

  Added sections      : None — both changes are expansions of existing principles.
  Removed sections    : None

  Templates confirmed aligned:
      ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
      ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XIV.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no impact.
      ✅ .specify/templates/constitution-template.md — source template; no changes required.

  Governance          : Unchanged. Principle count is still I–XIV; the PR Constitution Check
                        line already reads "I–XIV" as of v2.11.0.

  Deferred TODOs (new at this version):
      - Each image type's field catalogue, and with it the deeper validity layers rule 9
        anticipates, must be ratified as that type is specified. Until then every template
        is checked to Layer 1 only and must be reported as such.
      - The per-aspect wiring of image output into the eight source-module posting paths is
        deferred to a later increment per Q1; the toggles ratified here are inert until then.

  Deferred TODOs (carried from v2.11.0):
      - README module documentation for the image module, deferred until there are commands
        to document. The README "Module Commands" section names only three modules and is
        already stale for the Attendance module ratified at v2.10.0; close both together.
      - Principle XIV was derived from the author's brief and from the committed proof of
        concept at resources/poc/ and resources/templates/. The working specification at
        docs/wip-specs/image_module_specification.md was not readable in these sessions
        (deny-listed in .claude/settings.json) and must be reconciled against.

[2026-08-10 — v2.10.1 → v2.11.0: MINOR — image generation module governance added]
  Version change    : 2.10.1 → 2.11.0
  Bump rationale    : MINOR — A new Core Principle (XIV) was added and three existing
                      principles were materially expanded to admit a new optional module.
                      No principle was removed or redefined incompatibly, so this is not
                      a MAJOR bump.
  Version stamp fix : The footer version line read 2.10.0 while the v2.10.1 report entry
                      above claimed 2.10.1. The v2.10.1 content change (Governance
                      "I–XII" → "I–XIII") had in fact been applied; only the stamp was
                      missed. The footer now reads 2.11.0, and the 2.10.1 → 2.11.0 chain
                      is recorded here so the history is continuous.
  Feature branch    : 035-image-module (created 2026-08-10 from main)
  Session intent    : Set up the initial configuration of a new image generation module.
                      The module accepts inputs from the four existing modules (results &
                      standings, weather, signups, attendance) and from the shared season /
                      division / round / team / driver concepts, and provides an alternative
                      output path: standardised SVG templates filled with data and
                      rasterised to PNG attachments.

  Modified principles :
    - VI. Incremental Scope Expansion — added in-scope domain 12 (image-based output
        generation). Replaced the "current output format is text-only" paragraph, which the
        addition of the module makes false, with the two-format additive statement.
    - VII. Output Channel Discipline — added the "Image attachments" clause: a generated
        image rides on the source module's message in the source module's registered
        channel; the image module registers no channel category of its own.
    - X. Modular Feature Architecture — added the Image generation module to the optional
        module list, classified as a *consumer* module (no channels, no messages of its
        own), with its enable/disable semantics against source modules stated.

  Added sections      :
    - Principle XIV: Image Generation Discipline (NON-NEGOTIABLE) — eight non-negotiable
        rules: templates are data not code; fields addressed by @id with a closed set of six
        fill operations; every addressable field must resolve; problems abort while notices
        are logged and survive; text bounds declared by the template (inline-size /
        shape-inside); assets aspect-authored and never padded by the generator; image
        output strictly additive with text fallback; images are attachments, not a channel.
    - Data & State Management → New Entities (v2.11.0): ImageConfig, ImageTypeState,
        RenderNotice. Render problems deliberately reuse the Principle V audit log rather
        than adding a fourth entity.

  Removed sections    : None

  Templates confirmed aligned:
      ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
      ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XIV.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no impact.
      ✅ .specify/templates/constitution-template.md — source template; no changes required.

  Deferred TODOs (new at this version):
      - README module documentation for the image module is deferred until the module has
        commands to document. The README "Module Commands" section currently names three
        modules (weather, signup, results) and is already stale with respect to the
        Attendance module ratified at v2.10.0; both gaps should be closed in the same pass.
      - Principle XIV was derived from the user's session brief and from the committed
        proof of concept at resources/poc/ and resources/templates/. The working
        specification at docs/wip-specs/image_module_specification.md was not readable in
        this session (deny-listed in .claude/settings.json), so the field catalogue,
        the `images test <type>` command surface, and the per-image-type inventory MUST be
        reconciled against that document before /speckit-specify is run.

  Deferred TODOs (carried from v2.10.0):
      - Exact command naming for appeal submission and review commands to be confirmed
        against the 026-penalty-posting-appeals implementation.
      - Whether the existing penalty wizard loose-text fields on DriverSessionResult
        (post_race_time_penalties, post_stewarding_total_time) have been fully superseded
        by PenaltyRecord rows — migration confirmation required.

[2026-04-03 — v2.10.0 → v2.10.1: PATCH — governance section reference corrected; attendance tracking branch initialised]
  Version change    : 2.10.0 → 2.10.1
  Bump rationale    : PATCH — Two non-semantic corrections:
                        1. Governance section's pull-request compliance line was stale,
                           referencing "Principles I–XII" after Principle XIII was added
                           at v2.10.0. Corrected to "I–XIII".
                        2. Session-initialisation entry for the attendance tracking sub-
                           increment (feature branch 033-attendance-tracking) added to
                           the Sync Impact Report.
  Feature branch    : 033-attendance-tracking (created 2026-04-03 from main)
  Session intent    : Implement the core attendance tracking features left out of scope
                      in 032-attendance-rsvp-checkin:
                        - Attendance recording hook (first SessionResult row accepted for
                          the round triggers DriverRoundAttendance.attended population).
                        - Attendance point distribution (post-penalty finalization hook;
                          deferred from RSVP sub-increment per Principle XIII).
                        - Attendance pardon workflow integrated into the penalty wizard
                          (NO_RSVP / NO_RSVP_ABSENT / RSVP_ABSENT modal, staged display, approval).
                        - Attendance sheet posting to the division's attendance channel
                          (descending points list with threshold footer).
                        - Autoreserve and autosack sanction enforcement after point
                          distribution (threshold evaluation, driver seat mutations,
                          audit log entries per Principle V).
                      All functionality is already governed by Principle XIII; no new
                      governance principle additions or amendments are required.
  Implementation status at session start:
      ✅ 031-attendance-module — fully merged to main (2026-04-03, PR #50).
         Covers all 30 module configuration tasks; 20/20 unit tests passing.
      🔄 032-attendance-rsvp-checkin — in progress (branch created 2026-04-03 from main);
         latest commit: feat(032): attendance RSVP check-in & reserve distribution.
         Covers: RSVP embed posting, driver button interactions, reserve extension window,
         reserve distribution at RSVP deadline, last-notice ping (FR-001–FR-030; US1–US5).
         Out of scope for that branch: attendance recording, point distribution, pardons,
         attendance sheet, autosanctions.
  Modified principles : None
  Added sections      : None
  Removed sections    : None
  Fixes               : Governance section line — "I–XII" corrected to "I–XIII".
  Templates confirmed aligned:
      ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
      ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XIII.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs (carried from v2.10.0):
      - Exact command naming for appeal submission and review commands to be confirmed
        against the 026-penalty-posting-appeals implementation.
      - Whether the existing penalty wizard loose-text fields on DriverSessionResult
        (post_race_time_penalties, post_stewarding_total_time) have been fully superseded
        by PenaltyRecord rows — migration confirmation required.

[2026-04-03 — Session reuse: Attendance module RSVP & check-in implementation — feature branch created]
  - Constitution reused as-is; no principle amendments required at session start.
  - Session intent: implement the RSVP check-in embed and button interactions;
    reserve distribution at the RSVP deadline; last-notice ping scheduling and sending;
    attendance recording from submitted round results (first SessionResult row hook);
    attendance point distribution (post-penalty finalization hook); attendance pardon
    workflow inside the penalty wizard; attendance sheet posting to the attendance channel;
    and automatic sanction enforcement (autoreserve and autosack).
    DriverRoundAttendance and AttendancePardon data entities to be introduced as part of
    this increment.
  - Feature branch: 032-attendance-rsvp-checkin (created 2026-04-03 from main).
  - Implementation status at session start:
      ✅ 031-attendance-module — fully merged to main (2026-04-03, PR #50); all tasks [x]
         complete. Covers: Attendance module enable/disable lifecycle (Results & Standings
         dependency gate, ACTIVE-season gate, cascading auto-disable on R&S disable);
         /division rsvp-channel and /division attendance-channel commands; season approval
         Gate 4 (both RSVP and attendance channels required per division); /attendance
         config timing commands (rsvp-notice, rsvp-last-notice, rsvp-deadline) with
         invariant enforcement; /attendance config penalty commands (no-rsvp-penalty,
         no-attend-penalty, no-show-penalty, autosack, autoreserve); season review
         attendance status and per-division channel display; full unit test suite (20
         tests passing).
  - All placeholder tokens remain resolved; constitution is fully resolved at v2.10.0.
  - No version bump required; Last Amended date remains 2026-04-03 (no content amendments).
  - All templates confirmed aligned with Principles I–XIII:
      ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
      ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XIII.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no impact.
  - Deferred TODOs (carried from v2.10.0):
      - Exact command naming for appeal submission and review commands to be confirmed
        against the 026-penalty-posting-appeals implementation.
      - Whether the existing penalty wizard loose-text fields on DriverSessionResult
        (post_race_time_penalties, post_stewarding_total_time) have been superseded by
        PenaltyRecord rows — migration confirmation required.
  - Pending: speckit.specify to define exact scope and task ordering for this sub-increment;
    constitution will be re-evaluated if any new governance requirements are identified.

[2026-04-03 — v2.9.0 → v2.10.0: Attendance module ratified — Principle XIII added]
  Version change    : 2.9.0 → 2.10.0
  Bump rationale    : MINOR — The Attendance module is formally ratified as a new optional
                      module. Driver check-in management and attendance tracking were
                      previously unaddressed; this amendment:
                        1. Adds "Driver attendance management" (check-in RSVP flow,
                           attendance point accumulation, reserve distribution, and automatic
                           sanction enforcement) to the formally in-scope domain list in
                           Principle VI as item 11.
                        2. Registers the Attendance module in Principle X's optional modules
                           list, noting its dependency on the Results & Standings module.
                        3. Introduces Principle XIII (Attendance & Check-in Integrity):
                           module dependency gate, season-lifecycle constraints, RSVP notice
                           timing invariants, reserve distribution rules, attendance point
                           accumulation and pardon mechanics, autosack/autoreserve sanction
                           automation, and channel discipline.
                        4. Defines new data entities: AttendanceConfig, AttendanceDivision-
                           Config, DriverRoundAttendance, AttendancePardon.
  Feature branch    : 031-attendance-module (created 2026-04-03 from main)
  Session intent    : Initial configuration of the Attendance module: governance
                      ratification only. Implementation (commands, scheduler jobs, DB
                      migrations, tests) to follow in dedicated sub-increments.
  Modified principles:
    - Principle VI (Incremental Scope Expansion) — item 11 (attendance management) added
      to in-scope; corresponding entry removed from planned future scope (was not listed
      there; no prior reference).
    - Principle X (Modular Feature Architecture) — Attendance module added to optional
      modules list with its dependency constraint.
  Added sections    :
    - Principle XIII: Attendance & Check-in Integrity (NEW)
    - Data & State Management: New Entities (v2.10.0) — AttendanceConfig,
      AttendanceDivisionConfig, DriverRoundAttendance, AttendancePardon.
  Removed sections  : None
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
    ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
    ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XIII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs (carried from prior sessions):
    - Exact command naming for appeal submission and review commands to be confirmed
      against the 026-penalty-posting-appeals implementation.
    - Whether the existing penalty wizard loose-text fields on DriverSessionResult
      (post_race_time_penalties, post_stewarding_total_time) have been superseded by
      PenaltyRecord rows — migration confirmation required.

[2026-04-03 — v2.8.0 → v2.9.0: Track entity formalised + track/tier stats preparation]
  Version change    : 2.8.0 → 2.9.0
  Bump rationale    : MINOR — The Track registry has been a de-facto bot entity since v1.0.0
                      (used for weather parameter resolution and round identification) but was
                      never formally defined as a governed data entity. This amendment:
                        1. Formally defines the Track entity in Data & State Management,
                           expanding its documented dataset to include canonical name, country,
                           circuit name, and weather defaults alongside the existing server-
                           override mechanism.
                        2. Notes that the Track entity is the authoritative lookup basis for
                           future track-based and tier-based statistics derivable from
                           SessionResult + Round data (Principle VI planned: season history
                           and statistics).
                      No new governance principle is required; the changes land entirely in
                      Data & State Management and are a natural extension of the existing
                      season/division lifecycle and future statistics roadmap.
  Feature branch    : 030-track-data-expansion (created 2026-04-03 from main)
  Session intent    : Expand the Track dataset (names, country, circuit identity) and enable
                      track-based/tier-based stats queries in preparation for future modules.
                      Minor finetuning to division commands to align with richer Track data.
                      README to be updated as needed.
  Modified principles: None
  Added sections    :
    - Data & State Management: New Entities (v2.9.0) — formal Track entity definition.
  Removed sections  : None
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
    ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
    ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs (carried from prior sessions):
    - Exact command naming for appeal submission and review commands to be confirmed
      against the 026-penalty-posting-appeals implementation.
    - Whether the existing penalty wizard loose-text fields on DriverSessionResult
      (post_race_time_penalties, post_stewarding_total_time) have been superseded by
      PenaltyRecord rows — migration confirmation required.
  Pending: speckit.specify to define exact scope of track entity expansion and division
    command finetuning; constitution will be re-evaluated if any new governance requirements
    are identified during that process.

[2026-04-02 — Session start: Results & Weather improvements — feature branch created]
  - Constitution footer corrected from v2.7.0 to v2.8.0. The body already contained
    v2.8.0 amendments (New Entities v2.8.0, Principle XI v2.8.0 notes) applied during the
    028-season-signup-flow session; the footer was inadvertently not updated at that time.
    Last Amended date updated to 2026-04-02 to reflect this correction.
  - Version bump rationale: no governance content changes this entry; footer is a
    PATCH-level correction restoring accurate versioning (body and footer now agree at v2.8.0).
  - Session intent: make targeted improvements to two existing optional modules:
      1. Results & Standings module — improvements to flexibility and error tolerance
         (e.g., ability to correct or amend initial submission mistakes more easily).
      2. Weather generation module — behavioral flexibility improvements
         (e.g., configurable or more tolerant pipeline behavior).
    Exact scope to be defined via speckit.specify before implementation begins.
  - Feature branch: 029-results-weather-improvements (created 2026-04-02 from main).
  - Implementation status at session start (post-028 merge):
      ✅ 028-season-signup-flow — fully merged to main (2026-04-02).
         Covers: signup close-timer scope narrowed to PENDING_SIGNUP_COMPLETION only;
         lineup_channel_id and calendar_channel_id moved to divisions table;
         lineup_message_id added to divisions; /division calendar-channel no longer
         module-gated.
  - All placeholder tokens remain resolved; constitution is fully resolved at v2.8.0.
  - No principle amendments required at session start; constitution will be re-evaluated
    and amended once the scope of each module improvement is formally defined.
  - All templates confirmed aligned with Principles I–XII:
      ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
      ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XII.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no impact.
  - Deferred TODOs (carried from v2.7.0):
      - Exact command naming for appeal submission and review commands to be confirmed
        against the 026-penalty-posting-appeals implementation.
      - Whether the existing penalty wizard loose-text fields on DriverSessionResult
        (post_race_time_penalties, post_stewarding_total_time) have been superseded by
        PenaltyRecord rows — migration confirmation required.
  - Pending: user to define exact scope of results and weather improvements; constitution
    will be re-evaluated and amended once any new governance requirements are identified.

[2025-07-01 — v2.7.0 → v2.8.0: Season-signup flow alignment — close-timer scope + channel ownership]
  Version change    : 2.7.0 → 2.8.0
  Bump rationale    : MINOR — Two targeted amendments to Principle XI (Signup Wizard Integrity):
                        1. Signup close timer scope narrowed: only PENDING_SIGNUP_COMPLETION
                           drivers are transitioned to NOT_SIGNED_UP on forced close. Drivers
                           in PENDING_ADMIN_APPROVAL or PENDING_DRIVER_CORRECTION retain their
                           state — their completed/reviewed submissions are preserved.
                        2. Lineup and calendar channel ownership moved to the `divisions` table.
                           `lineup_channel_id` migrated from `signup_division_config` to
                           `divisions`. New `calendar_channel_id` and `lineup_message_id`
                           columns added to `divisions`. `/division calendar-channel` is no
                           longer gated on the signup module.
  Feature branch    : 028-season-signup-flow (created 2025-07-01 from main)
  Modified principles:
    - Principle XI (Signup Wizard Integrity) — signup close timer clause amended (scope
      narrowed); lineup announcement channel clause amended (channel ownership + calendar
      channel added; module gate removed).
  Added sections    :
    - Data & State Management: Division entity amended — lineup_channel_id moved from
      SignupDivisionConfig, calendar_channel_id and lineup_message_id added.
    - Data & State Management: SignupDivisionConfig amended — lineup_channel_id dropped.
  Removed sections  : None
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
    ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
    ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs    : None.
[2026-03-30 — Session reuse: Signup module modifications + minor feature additions]
  - Constitution reused as-is; no principle amendments required at session start.
  - Session intent: modify existing signup wizard functionality and add minor features
    to the signup module. All proposed work falls within the already-ratified domains
    of Principle VI (item 5: signup wizard and driver onboarding) and Principle XI
    (Signup Wizard Integrity). Feature branch to be created from main after scope is
    confirmed.
  - Implementation status at session start (post-026 merge):
      ✅ 026-penalty-posting-appeals — fully merged to main (2026-03-30).
         Covers: PenaltyRecord and AppealRecord entities; penalty announcement channel
         (DivisionResultsConfig.penalty_channel_id); admin-driven appeals review wizard;
         penalty and appeal outcome posting; full test suite.
  - No placeholder tokens present; constitution is fully resolved at v2.7.0.
  - No version bump required; Last Amended date remains 2026-03-29 (no content amendments
    at session start).
  - All templates confirmed aligned with Principles I–XII:
      ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
      ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XII.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no impact.
  - Deferred TODOs (carried from v2.7.0):
      - Exact command naming for appeal submission and review commands to be defined in
        the feature specification.
      - Whether the existing penalty-wizard loose-text fields on DriverSessionResult
        (post_race_time_penalties, post_stewarding_total_time) have been fully superseded
        by PenaltyRecord rows — migration confirmation required.
  - Pending: user to confirm exact scope of signup modifications and new features;
    constitution will be re-evaluated and amended once any new governance requirements
    are identified.

[2026-03-29 — v2.6.0 → v2.7.0: Penalty posting channel + appeals workflow formalized]
  Version change    : 2.6.0 → 2.7.0
  Bump rationale    : MINOR — "Penalty and protest adjudication" promoted from planned
                      future scope to formally in-scope (Principle VI item 10). Principle
                      XII extended with two new subsections:
                        1. Penalty Announcements: penalties applied via the wizard MUST
                           be posted to a configured per-division penalty announcement
                           channel (module-introduced channel, fallback to results channel).
                        2. Penalty Appeals: a second review tier allowing interaction-role
                           members to appeal their own penalty; resolved by a tier-2 admin
                           via Uphold / Overturn; outcome posted to the same channel.
                      New data entities: PenaltyRecord, AppealRecord. DivisionResultsConfig
                      amended to add penalty_channel_id.
  Feature branch    : 026-penalty-posting-appeals (created 2026-03-29 from main)
  Modified principles:
    - Principle VI (Incremental Scope Expansion) — item 10 (penalty adjudication) added
      to in-scope; corresponding entry removed from planned future scope.
    - Principle XII (Race Results & Championship Integrity) — Amendment & Penalty section
      extended: Penalty Announcements and Penalty Appeals subsections added.
  Added sections    :
    - Data & State Management: New Entities (v2.7.0) — PenaltyRecord, AppealRecord;
      DivisionResultsConfig amendment note (penalty_channel_id).
  Removed sections  : None
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
    ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
    ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs    :
    - Exact command naming for appeal submission and review commands to be defined in
      the feature specification.
    - Whether appeals are driver-initiated only or also administratively triggered to be
      confirmed in the feature specification.
    - Whether a penalty announcement channel is required before the module may be enabled
      (or only before a penalty can be posted) to be defined in the feature specification.
  Follow-up TODOs   :
    - The existing penalty wizard in DriverSessionResult uses loose text fields
      (post_race_time_penalties, post_stewarding_total_time); these MUST be superseded
      by PenaltyRecord rows in the feature increment. Migration required.

[2026-03-27 — v2.5.0 → v2.6.0: Signup close timer, lineup announcements, module-config decoupling]
  Version change    : 2.5.0 → 2.6.0
  Bump rationale    : MINOR — Three governance additions:
                        1. Principle X (Enable atomicity): Clarified to decouple module
                           configuration (channels, roles, settings) from the module-enable
                           action. Enabling now atomically sets the module-enabled flag and
                           arms scheduled jobs only; all other configuration is handled via
                           dedicated commands independently of enable.
                        2. Principle XI (Signup Wizard Integrity):
                           (a) Signup close timer — optional close-at duration set when
                           signups are opened; fires automatically with the same cancellation
                           semantics as a manually confirmed close; re-armed on bot restart.
                           (b) Lineup announcement channel — optional per-division channel
                           for driver assignment change notices; module-introduced category
                           per Principle VII; not required for module activation.
                        3. Data & State Management: SignupConfiguration amended to add
                           close_at; new SignupDivisionConfig entity introduced.
  Feature branch    : 025-signup-expansion (created 2026-03-27 from main)
  Modified principles:
    - Principle X (Modular Feature Architecture) — Enable atomicity rule refined.
    - Principle XI (Signup Wizard Integrity) — close timer + lineup channel added.
  Added sections    :
    - Data & State Management: New Entities (v2.6.0) — SignupDivisionConfig entity;
      SignupConfiguration amendment note (close_at).
  Removed sections  : None
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
    ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
    ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs    :
    - Exact trigger semantics for lineup announcement posts (per-event vs. on-demand)
      to be defined in the feature specification.
    - Command naming for the new dedicated signup configuration commands (channel set,
      base-role set, signedup-role set) to be defined in the feature specification.
  Follow-up TODOs   :
    - The existing `/module enable signup` implementation accepts channel, base_role,
      and signedup_role parameters; these MUST be removed and replaced with dedicated
      configuration commands in the feature increment.

[2026-03-26 — v2.4.1 → v2.5.0: Season Archive paradigm — seasons persist on completion]
  Version change    : 2.4.1 → 2.5.0
  Bump rationale    : MINOR — New governance concept added: Season Archive, formalising that
                      completed seasons are retained permanently in an append-only,
                      server-scoped archive rather than wiped or discarded. This supersedes
                      the prior implicit ephemeral-season paradigm. Changes land in three
                      places:
                        1. Principle VI (Incremental Scope Expansion) — "Season history and
                           statistics" added to planned future scope as the consumer of the
                           archive.
                        2. Data & State Management — COMPLETED lifecycle state description
                           extended to reference archival.
                        3. New Season Archive section in Data & State Management, defining
                           append-only semantics, zero-to-many cardinality, full data
                           retention, and read-only access rules.
  Feature branch    : 024-season-archive (created 2026-03-26 from main)
  Modified principles:
    - Principle VI (Incremental Scope Expansion) — "Season history and statistics" added
      to planned future scope.
  Added sections    :
    - Data & State Management: Season Archive (new governance section)
    - Data & State Management: New Entities (v2.5.0) note
  Removed sections  : None
  Paradigm superseded:
    - Prior practice of wiping/discarding season data on completion is formally superseded.
      Completed Season records (and all related tables) are now permanently retained.
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md      — dynamic Constitution Check; no changes.
    ✅ .specify/templates/spec-template.md      — generic structure; no stale references.
    ✅ .specify/templates/tasks-template.md     — generic; aligns with I–XII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs    :
    - Concrete schema additions for archive indexing, migration tooling, and the
      stats-module query layer are deferred to the feature specification for the season
      persistence increment (speckit.specify to be called next).
  Follow-up TODOs   :
    - If a `reset` or "wipe season" command currently exists in the implementation it
      MUST be deprecated or removed as part of the season persistence feature increment;
      this will be enforced in the feature spec.

[2026-03-23 — Session reuse: Results & Standings specification & incremental verification — feature branch created]
  - Constitution reused as-is; no principle amendments required at session start.
  - Session intent: provide a fresh specification for the Results & Standings module and
    verify the existing implementation against it incrementally. Any conflicts between the
    current implementation and the specification provided in this session will be resolved
    in favour of the specification. Feature branch `022-results-standings-verification`
    created from main.
  - Existing implementation status at session start:
      ✅ specs/018-results-standings/  — fully merged to main; all tasks [X] complete.
      ✅ specs/019-results-submission-standings/  — fully merged to main; all tasks [X]
         complete.
      ✅ specs/020-results-standings-session/  — fully merged to main (session branch).
      ✅ specs/021-results-spec-alignment/  — fully merged to main; spec-alignment
         corrections applied (submission validation, penalty wizard two-step flow).
  - All placeholder tokens remain resolved; constitution is fully resolved at v2.4.1.
  - No version bump required (no content amendments at session start).
  - All templates confirmed aligned with Principles I–XII:
      ✅ .specify/templates/plan-template.md
      ✅ .specify/templates/spec-template.md
      ✅ .specify/templates/tasks-template.md
      ✅ .specify/templates/agent-file-template.md
      ✅ .specify/templates/checklist-template.md
  - Deferred TODOs: none.
  - Pending: user to provide new specification for this session; constitution will be
    re-evaluated and amended once the scope of new work is defined.

[2026-03-19 — Session reuse: Results & Standings continuation — feature branch created]
  - Constitution reused as-is; no principle amendments required.
  - Session intent: begin a new session for results & standings specification and
    incremental implementation verification. Feature branch `020-results-standings-session`
    created from main.
  - Existing implementation status at session start:
      ✅ specs/018-results-standings/  — fully merged to main; all tasks [X] complete.
         Covers: R&S module enable/disable lifecycle; weather-channel decoupling;
         /division weather-channel, /division results-channel, /division standings-channel
         commands; season-approval prerequisite gates (weather + R&S + points-config).
      ✅ specs/019-results-submission-standings/  — fully merged to main; all tasks [X]
         complete (T016 results_formatter.py confirmed present despite unchecked box).
         Covers: points-config store CRUD; season config attachment + snapshot; submission
         wizard with transient channel; results and standings posting; config view; penalty
         wizard; full session amendment; mid-season amendment mode; reserves visibility
         toggle; full unit + integration test suite (T028–T035).
  - No placeholder tokens present; constitution is fully resolved at v2.4.1.
  - No version bump required (no content amendments this session).
  - All templates confirmed aligned with Principles I–XII:
      ✅ .specify/templates/plan-template.md      — dynamic Constitution Check; no changes.
      ✅ .specify/templates/spec-template.md      — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md     — generic; aligns with I–XII.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no impact.
  - No stale agent-specific references detected.
  - Deferred TODOs: none.
  - Pending: user to provide new specification for this session; constitution will be
    re-evaluated and amended once the scope of new work is defined.

[2026-03-18 — v2.4.0 → v2.4.1: PATCH clarifications for Results & Standings module]
  Version change    : 2.4.0 → 2.4.1
  Bump rationale    : PATCH — Non-semantic clarifications to Principle XII covering three
                      gaps identified when cross-checking results_module_specification.md
                      against the constitution:
                        1. Endurance round session-type mapping for results not explicit.
                        2. Round-cancel constraint (fail if submission channel already open)
                           not stated.
                        3. Amendment-toggle disable constraint (cannot disable while
                           modified_flag is true) not stated.
  Modified principles:
    - Principle XII (Race Results & Championship Integrity):
        * Result Submission: added explicit session-type mapping for all four round
          formats (Normal, Sprint, Endurance, Mystery); Endurance Full Qualifying /
          Full Race → Feature Qualifying / Feature Race respectively.
        * Amendment & Penalty: added round-cancel-while-submission-open constraint;
          added amendment-toggle-off-while-modified constraint.
  Added sections    : None
  Removed sections  : None
  Resolved spec incoherencies (spec errors — to be corrected in feature spec, not here):
    1. results_module_specification.md §"Sprint Race and Feature Race" states that
       DNF/DNS/DSQ drivers "shall not be eligible to receive points" — this omits the
       constitution's explicit allowance that DNF drivers MAY still receive the fastest-lap
       bonus (provided the position limit is met). The feature spec MUST be updated to
       read: "DNF drivers are ineligible for finishing-position points but remain eligible
       for the fastest-lap bonus under the position-limit condition."
    2. results_module_specification.md §"Assigning channels to divisions" uses logical AND
       for the R&S approval gate ("module enabled AND not all channels configured AND no
       valid points config"), which would incorrectly allow approval when only channels or
       only the config prerequisite is missing. The constitution (Principle XII, Authorization
       & Module Gate) uses OR — each missing prerequisite independently blocks approval.
       The feature spec MUST be corrected to use OR semantics.
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md      — no changes needed.
    ✅ .specify/templates/spec-template.md      — no changes needed.
    ✅ .specify/templates/tasks-template.md     — no changes needed.
    ✅ .specify/templates/agent-file-template.md — no changes needed.
    ✅ .specify/templates/checklist-template.md  — no changes needed.
  Feature branch status:
    - `018-results-standings` already exists; foundational phases 1–8 implemented
      (module enable/disable, channel decoupling, channel assignment commands,
      season approval gates, and unit tests).
    - Remaining work: points configuration management, results submission wizard,
      standings computation, amendment/penalty flow, and all associated commands.
    - Next step: speckit.specify for the next increment within 018.
  No deferred TODOs remaining.

[2026-03-18 — v2.3.0 → v2.4.0: Results & Standings module formal specification]
  Version change    : 2.3.0 → 2.4.0
  Bump rationale    : MINOR — Principle XII (Race Results & Championship Integrity)
                      materially expanded and corrected. Principle X amended: race results
                      recording and championship standings moved from foundational to the new
                      optional Results & Standings module. Both previously deferred TODOs
                      (FASTEST_LAP_RULE and SCORING_TABLE_CUSTOMIZATION) resolved.
                      Data entities for v2.3.0 (RaceResult, ScoringTable) superseded by the
                      correct session-level and configuration-store schema (v2.4.0).
  Modified principles:
    - Principle X (Modular Feature Architecture) — "Race results recording and championship
      standings" removed from foundational modules; "Results & standings module" added as a
      new optional module.
    - Principle XII (Race Results & Championship Integrity) — full rewrite:
        * Corrected: no default scoring preset (zero-points default, not the F1 table).
        * Corrected: results are session-level, not round-level (sequential per-session
          submission per round).
        * Corrected: tiebreaking uses Feature Race finishes only (not a generic "most recent
          round" criterion).
        * Added: named multi-configuration points store (server-scope and season-scope).
        * Added: fastest-lap bonus mechanics (per session per config, position-limit).
        * Added: mid-season amendment flow (modification store, modified flag, approval gate).
        * Added: reserve driver standings visibility toggle per division.
        * Added: standings snapshot per round (points, per-position finish counts, first
          finish round).
        * Added: results channel and standings channel as module-introduced channel categories.
        * Added: season approval gate for the Results & Standings module.
  Added sections    : New Entities (v2.4.0)
  Removed sections  : None
  Resolved TODOs    :
    - TODO(FASTEST_LAP_RULE): Resolved — fastest-lap bonus points apply per session per
      named configuration; qualifying sessions are excluded; position-limit eligibility is
      configurable per session per configuration.
    - TODO(SCORING_TABLE_CUSTOMIZATION): Resolved — servers define fully custom named
      configurations; no F1 preset is provided; the default for any unspecified position
      is 0 points.
  Data entities     :
    - Superseded (v2.3.0): RaceResult, ScoringTable.
      *Reason: designed for a simplified single-round/single-table model inconsistent with
      the session-level multi-config schema mandated by the feature specification.*
    - SeasonAssignment (v2.3.0) amended: standings live-state fields (current_points,
      current_position, points_gap_to_leader) removed; authoritative standings state
      moved to DriverStandingsSnapshot (v2.4.0).
    - New (v2.4.0): PointsConfigStore, PointsConfigEntry, PointsConfigFastestLap,
      SeasonPointsLink, SeasonPointsStore, SeasonModificationStore, SeasonAmendmentState,
      SessionResult, DriverSessionResult, DriverStandingsSnapshot, TeamStandingsSnapshot,
      ResultsModuleConfig.
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md      — Constitution Check gate is dynamic; no
         hardcoded principle list; no changes needed.
    ✅ .specify/templates/spec-template.md      — generic; no stale references.
    ✅ .specify/templates/tasks-template.md     — generic; aligns with I–XII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Incoherencies resolved:
    - Principle X listed "Race results recording and championship standings" as foundational
      (cannot be disabled), but the feature specification requires an optional module.
      Corrected.
    - Principle XII stated the default scoring table is the standard F1 preset; the feature
      specification states the default is 0 points for all positions. Corrected.
    - Principle XII described round-level "atomic submission" (one operation per round);
      the feature specification requires session-level sequential submission. Corrected.
    - Principle XII's tiebreaker was "driver who places higher in most recent round"; the
      feature specification defines a detailed countback hierarchy restricted to Feature Race
      finishes. Corrected.
    - v2.3.0 RaceResult modelled results at round/driver level; the feature requires results
      at session/driver level with per-session config choices. Superseded.
    - v2.3.0 ScoringTable modelled a single server-level table; the feature requires named
      multi-config stores at both server and season scope. Superseded.
  Follow-up notes   :
    - The "results channel", "standings channel", and transient "round submission channel"
      per division are module-introduced channel categories and MUST be explicitly documented
      in the feature specification per Principle VII.
    - Division-level channel config (results channel, standings channel) MUST be specified
      in the feature spec (stored on Division or a new DivisionResultsConfig entity).
    - SeasonAssignment live-state fields already implemented code-side from v2.3.0 will
      require a migration to drop or ignore the removed columns.

[2026-03-12 — Session reuse: QoL changes and bugfixes]
  - Constitution reused as-is; no principle amendments required.
  - Session intent: quality-of-life improvements and bugfixes to existing features.
  - All placeholder tokens remain resolved; no bracket tokens present.
  - Version 2.3.0 confirmed; no bump warranted (patch-level corrections and refinements
    to existing implementation — no governance or principle changes).
  - Templates confirmed aligned with Principles I–XII:
      ✅ .specify/templates/plan-template.md      — Constitution Check gate is dynamic; no
           hardcoded principle list; no changes needed.
      ✅ .specify/templates/spec-template.md      — generic; no stale references.
      ✅ .specify/templates/tasks-template.md     — generic; aligns with I–XII.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no impact.
  - No stale agent-specific references detected.
  - No version bump required; Last Amended date remains 2026-03-11 (no content amendments).
  - Deferred TODOs (carried over):
      - TODO(FASTEST_LAP_RULE): pending project owner confirmation.
      - TODO(SCORING_TABLE_CUSTOMIZATION): pending project owner confirmation.

[2026-03-11 — v2.2.0 → v2.3.0: Race results & championship ratification + SeasonAssignment formalization]
  Version change    : 2.2.0 → 2.3.0
  Bump rationale    : MINOR — Principle XII (Race Results & Championship Integrity) added.
                      Race results recording and championship standings moved from "planned
                      future scope" to formally in-scope (Principle VI items 8–9). Both
                      added to foundational modules (Principle X). SeasonAssignment entity
                      formally defined, resolving the "normalized join table" gap present
                      since v2.0.0. RaceResult and ScoringTable entities added (v2.3.0).
                      Constitution title updated to reflect full-lifecycle mandate.
  Modified principles:
    - Principle VI (Incremental Scope Expansion) — items 8 (race results recording) and
      9 (championship standings) added to in-scope; both removed from planned future scope.
      Planned future scope now contains only penalty adjudication and financial/licensing.
    - Principle X (Modular Feature Architecture) — race results recording and championship
      standings added to the foundational modules list.
  Added sections    :
    - Principle XII: Race Results & Championship Integrity (NEW)
    - Data & State Management: SeasonAssignment, RaceResult, ScoringTable added as
      New Entities (v2.3.0). SeasonAssignment formally resolves the underdefined
      "normalized join table" referenced in DriverProfile since v2.0.0.
  Removed sections  : None
  Resolved TODOs    : None
  Deferred TODOs    :
    - TODO(FASTEST_LAP_RULE): Whether fastest-lap bonus points are available (and under
      what conditions) is a policy question pending confirmation from the project owner
      before the race results feature specification is written.
    - TODO(SCORING_TABLE_CUSTOMIZATION): Whether servers may define fully custom scoring
      tables or are restricted to the standard F1 preset must be confirmed before the race
      results feature specification is written.
  Other changes     :
    - Constitution title updated from "F1 League Weather Randomizer Bot Constitution" to
      "F1 League Bot Constitution" to reflect the bot's expanded scope mandate.
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md      — Constitution Check gate is dynamic; no
         hardcoded principle list; no changes needed.
    ✅ .specify/templates/spec-template.md      — generic; no stale references.
    ✅ .specify/templates/tasks-template.md     — generic; aligns with I–XII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Incoherencies resolved:
    - The "normalized join table" for DriverProfile season assignments (referenced since
      v2.0.0 but never formally structured) is now defined as SeasonAssignment, including
      all position and points fields required for standings computation.
  Pending follow-up:
    - README.md title ("F1 League Weather Randomizer Bot") should be updated to reflect
      the bot's expanded scope. Flagged for the next feature increment.

[2026-03-10 — v2.1.0 → v2.2.0: Signup wizard & driver placement ratification + BAN_STATE_NAMING resolution]
  Version change    : 2.1.0 → 2.2.0
  Bump rationale    : MINOR — Signup wizard and driver assignment/placement moved from
                      "planned future scope" to formally in-scope. New Principle XI
                      (Signup Wizard Integrity) added. Principle VI in-scope list expanded
                      to 7 items. Principle VIII materially expanded: all 9 driver states
                      enumerated with a transition table, Awaiting Correction Parameter
                      formalised as an explicit state, Season Banned duration mechanics
                      resolved (BAN_STATE_NAMING TODO closed).
  Modified principles:
    - Principle VI (Incremental Scope Expansion) — items 5 (signup wizard & driver onboarding)
      and 6 (driver assignment & placement) added to in-scope; corresponding entries removed
      from planned future scope; former item 5 (Modular feature architecture) renumbered to 7.
    - Principle VIII (Driver Profile Integrity) — all 9 driver states enumerated in a table;
      full permitted-transition table added; Awaiting Correction Parameter formalised;
      Season Banned ban_races_remaining mechanics specified; server-leave rule added;
      signup data clearing on Not Signed Up transition clarified.
  Added sections    :
    - Principle XI: Signup Wizard Integrity (NEW)
    - Data & State Management: SignupRecord, SignupWizardRecord, SignupConfiguration,
      and TimeSlot entities added as New Entities (v2.2.0).
  Removed sections  : None
  Resolved TODOs    :
    - TODO(BAN_STATE_NAMING): Resolved. "Season Banned" duration = total round count of the
      season in which the ban was issued, stored as ban_races_remaining INT on DriverProfile.
      Decrements by 1 for each round completion server-wide. Transitions automatically to
      Not Signed Up when the counter reaches 0.
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md      — dynamic Constitution Check; no hardcoded
         principle list; no changes needed.
    ✅ .specify/templates/spec-template.md      — generic; no stale references.
    ✅ .specify/templates/tasks-template.md     — generic; aligns with I–XI.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs    :
    - Race results recording, championship standings computation, penalty adjudication,
      and financial/licensing workflows remain pending formal ratification; each will be
      ratified as a dedicated feature increment per Principle VI.
    - The signup module specification MUST enumerate all new channel categories introduced
      (general signup channel, per-driver signup channels) and register them per Principle VII.
    - Lap time format edge cases (millisecond rounding vs. zero-padding, multi-track display
      ordering) are deferred to the signup feature specification for implementation detail.

[2026-03-07 — v2.0.0 → v2.1.0: Modular architecture ratification + full-league expansion vision]
  Version change    : 2.0.0 → 2.1.0
  Bump rationale    : MINOR — New Principle X added (Modular Feature Architecture). Principle VI
                      materially expanded to formally declare the incremental path toward full
                      league management and reclassify previously "out of scope" domains as
                      "planned future scope". Principle VII extended with a module-channel clause
                      to resolve a forward incoherency with the signup-wizard channel model.
  Modified principles:
    - Principle VI (Incremental Scope Expansion) — "Out of scope" list replaced with "Planned
      future scope" language; bot's strategic direction toward encompassing entire league business
      rules explicitly declared; ratification gate retained.
    - Principle VII (Output Channel Discipline) — Added clause permitting module-introduced
      channel categories when each is explicitly documented and registered with the same
      discipline as primary channels.
  Added sections    :
    - Principle X: Modular Feature Architecture (NEW)
  Removed sections  : None
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md      — Constitution Check gate is dynamic; no
         hardcoded principle list; no changes needed.
    ✅ .specify/templates/spec-template.md      — generic; no stale references.
    ✅ .specify/templates/tasks-template.md     — generic; aligns with I–X.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Resolved incoherencies:
    - Principle VII vs. signup-wizard channels: resolved by new module-channel clause in
      Principle VII. Per-driver signup channels and the general signup channel are module-
      introduced categories and must be documented in the signup module specification.
  Deferred TODOs    :
    - TODO(BAN_STATE_NAMING): league-functionality-specification.md describes the "Season Banned"
      driver state as lasting "for a number of races equal to the length of the season they were
      race banned for." This conflates race-ban severity with season-ban state naming. The
      specification must clarify whether (a) "Season Banned" covers the remainder of the active
      season regardless of offense, or (b) a separate "Race Banned" state is needed for
      timed-race bans. Resolution must be agreed before the ban-management feature is ratified.
    - Race results recording, championship standings computation, penalty adjudication, and
      financial/licensing workflows remain pending formal ratification; each will be ratified
      as a dedicated feature increment per Principle VI.
    - The signup module specification (feature 013 or later) MUST enumerate all new channel
      categories introduced and register them formally per Principle VII.

[2026-03-06 — v1.2.0 → v2.0.0: Formal scope expansion — driver profiles, teams, season management]
  Version change    : 1.2.0 → 2.0.0
  Bump rationale    : MAJOR — Principle VI backward-incompatibly redefined. The prior scope
                      restriction ("strictly limited to weather + schedule only") has been
                      replaced with an explicit incremental-expansion policy that formally
                      admits driver profile management, team management, and enhanced season
                      lifecycle tracking as ratified additions to the bot's mandate.
  Modified principles:
    - Principle V (Observability & Change Audit Trail) — extended to cover driver-state
      transitions and team mutations alongside weather/schedule changes.
    - Principle VI (Simplicity & Focused Scope → Incremental Scope Expansion) — scope gate
      redefined; still guards against uncontrolled expansion but now explicitly admits driver
      profile management, team management, and extended season lifecycle as in-scope domains.
    - Data & State Management — new entities (DriverProfile, TeamSeat) documented; season
      counter and division tier ordering rule added; performance and storage footprint note
      added per user request.
  Added sections    :
    - Principle VIII: Driver Profile Integrity (NEW)
    - Principle IX: Team & Division Structural Integrity (NEW)
  Removed sections  : None
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md      — Constitution Check gate is dynamic; no
         hardcoded principle list; no changes needed.
    ✅ .specify/templates/spec-template.md      — generic; no stale references.
    ✅ .specify/templates/tasks-template.md     — generic; aligns with I–IX.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs    : Race results recording, raw driver points calculation, and penalty
                      management remain explicitly out of scope pending future formal
                      ratification under Principle VI's incremental-expansion process.

[2026-03-05 — v1.1.0 → v1.2.0: UX streamlining command standards]
  Version change    : 1.1.0 → 1.2.0
  Bump rationale    : MINOR — materially expanded guidance on command naming and UX
                      requirements. Added explicit subcommand-group mandate, command
                      grouping rule, single-interaction preference rule, and
                      hyphenated-command migration requirement to Bot Behavior
                      Standards.
  Modified sections :
    - Bot Behavior Standards: command naming expanded from a one-line convention
      to a multi-rule standard. Hyphenated top-level commands disallowed for new
      features; migration required for existing ones. Command grouping requirement
      added. Single-interaction preference rule added.
  Added sections    : None
  Removed sections  : None
  Templates confirmed aligned (no structural changes required):
    ✅ .specify/templates/plan-template.md      — generic; no hardcoded principle list.
    ✅ .specify/templates/spec-template.md      — generic; no stale references.
    ✅ .specify/templates/tasks-template.md     — generic; aligns with I–VII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale refs.
    ✅ .specify/templates/checklist-template.md  — not impacted.
  Deferred TODOs    : None. All placeholders resolved.

[2026-03-05 — Bug fix: test mode mystery-round completion + permission]
  - Session intent: fix two bugs in the existing test-mode feature.
  - Constitution reused as-is; no principle amendments required.
  - Version 1.1.0 confirmed; no bump warranted (patch-level corrections to
    existing implementation — no governance or principle changes).

  Bug 1 — Mystery rounds incorrectly shown as "next round" in /season-status
    Root cause : `season_status` used `not (phase1_done AND phase2_done AND
                 phase3_done)` to find next pending round; mystery rounds have
                 all three permanently False → always reported as "next."
    Fix        : src/cogs/season_cog.py — added `r.format != RoundFormat.MYSTERY`
                 guard to the `next_round` generator expression.
    Principle  : IV (mystery rounds skip all phases), VI (focused output).

  Bug 2 — Season not ending after advancing all non-mystery phases via test mode
    Root cause : The "all phases done" early-return path in /test-mode advance
                 returned "nothing to advance" without attempting season end,
                 leaving the season active if the previous Phase-3 advance's
                 internal execute_season_end call was skipped (e.g. past-dates
                 fast-path cleared data before the cog's own check could run,
                 or a Discord API error aborted the call mid-execution).
    Fix        : src/cogs/test_mode_cog.py — replaced the bare followup.send
                 early return with a check: if an active season still exists
                 when the queue is empty, cancel any pending scheduled job and
                 call execute_season_end immediately; otherwise send the
                 "nothing to advance" message.
    Principle  : IV (season lifecycle), V (no silent state mutations).

  Bug 3 — Test-mode commands accessible only to server admins, not to
           interaction-role holders configured via /bot-init
    Root cause : app_commands.Group for /test-mode had no `default_permissions`
                 specified (discord.py MISSING sentinel), leaving Discord to use
                 any previously cached per-server permission that may have been
                 set to manage_guild from an earlier sync. Also missing
                 `guild_only=True`, meaning the group was technically usable in
                 DMs where `channel_guard`'s Member check would block all users.
    Fix        : src/cogs/test_mode_cog.py — added `guild_only=True` and
                 `default_permissions=None` to the Group definition.
                 `default_permissions=None` forces Discord to reset to
                 "no Discord-level restriction" on next tree sync, leaving
                 `channel_guard` (interaction_role_id check) as the sole gate,
                 which already satisfies Principle I Tier-1 access control.
    Principle  : I (interaction role gates all commands), VII (guild channel only).

  Bug 4 — Mystery round notice never fires during test-mode advance
    Root cause : APScheduler job `mystery_r{id}` fires on a real-time schedule;
                 in test mode the scheduler never runs, so Mystery round player-
                 facing notices were silently skipped. `get_next_pending_phase`
                 also filtered out Mystery rounds entirely, making them invisible
                 to the advance queue.
    Fix        : src/services/test_mode_service.py — widened query to include all
                 rounds; returns `PhaseEntry(phase_number=0)` sentinel when a
                 Mystery round has `phase1_done=0`; skips if `phase1_done=1`.
                 src/cogs/test_mode_cog.py — added `phase_number == 0` dispatch
                 block: calls `run_mystery_notice`, then sets `phase1_done=1` on
                 success. `phase1_done` reused as "notice sent" proxy; safe
                 because `all_phases_complete` and `build_review_summary` already
                 filter `format != 'MYSTERY'`.
    Principle  : IV (mystery rounds have no phases but still have a pre-pipeline
                 notice step), V (no silent skips of expected bot actions).

  Bug 5 — Reset raises FOREIGN KEY constraint failed when forecast_messages exists
    Root cause : `reset_service` deleted `sessions` and `phase_results` before
                 `rounds`, but omitted `forecast_messages` which has
                 `REFERENCES rounds(id)` with FK enforcement ON. Any reset after
                 Phase 1 had run violated the FK and aborted the transaction.
    Fix        : src/services/reset_service.py — added
                 `DELETE FROM forecast_messages WHERE round_id IN (...)`
                 after `phase_results` and before `rounds` in the FK-safe chain.
                 Regression test added: `test_reset_deletes_forecast_messages`.
    Principle  : III (reset must complete cleanly to allow a fresh season start),
                 V (no silent data integrity failures).

  Bug 6 — Advance logs use internal DB id instead of user-visible round number
    Root cause : Log lines in the advance command emitted `entry["round_id"]`
                 (the `rounds.id` primary key), which is meaningless to league
                 managers reading logs. `PhaseEntry` had no `round_number` field.
    Fix        : src/services/test_mode_service.py — added `round_number: int`
                 field to `PhaseEntry`; SELECT now includes `r.round_number`.
                 src/cogs/test_mode_cog.py — log line now emits
                 `round=<round_number>` and `id=<round_id>` for all paths.
    Principle  : V (observable, human-legible audit trail).

  Templates confirmed aligned (no changes needed):
    ✅ .specify/templates/plan-template.md
    ✅ .specify/templates/spec-template.md
    ✅ .specify/templates/tasks-template.md
    ✅ .specify/templates/agent-file-template.md
    ✅ .specify/templates/checklist-template.md
  Files modified:
    ✅ src/cogs/season_cog.py            — next_round mystery exclusion (Bug 1)
    ✅ src/cogs/test_mode_cog.py         — advance safety net + Group permissions
                                           + mystery notice dispatch + round_number log
                                           (Bugs 2, 3, 4, 6)
    ✅ src/services/test_mode_service.py — PhaseEntry.round_number + phase_number=0
                                           sentinel in get_next_pending_phase (Bugs 4, 6)
    ✅ src/services/reset_service.py     — forecast_messages FK-safe delete (Bug 5)
    ✅ tests/unit/test_test_mode_service.py — updated mystery tests (Bug 4)
    ✅ tests/unit/test_reset_service.py  — regression test for FK reset (Bug 5)
  No deferred TODOs. Last Amended date remains 2026-03-03 (no principle changes).

[2026-03-05 — Bug fix: visual output correction pass]
  - Constitution reused as-is; no principle amendments required for visual output bug fixes.
  - Session intent: identify and correct bugs in the bot's visual/message output on an
    already-existing SpecKit-driven codebase.
  - All placeholder tokens remain resolved; no bracket tokens present.
  - Version 1.1.0 consistent across all sections; no version bump warranted (no content
    amendments — reuse session only).
  - Templates confirmed aligned with Principles I–VII:
      ✅ .specify/templates/plan-template.md    — Constitution Check gate is dynamic; no
           hardcoded principle list; no changes needed.
      ✅ .specify/templates/spec-template.md    — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md   — phase structure generic; aligns with I–VII.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no issues.
  - No stale agent-specific references detected.
  - Last Amended date remains 2026-03-03 (no content amendments this session).
  - No deferred TODOs.

[2026-03-04 — New feature addition: constitution validation pass]
  - Constitution reused as-is; no new principles required for incremental feature work.
  - Session intent: validate constitution readiness before beginning a new SpecKit feature
    on an already-existing codebase.
  - All placeholder tokens remain resolved; no bracket tokens present.
  - Version 1.1.0 footer consistent with all sections.
  - Templates confirmed aligned:
      ✅ .specify/templates/plan-template.md    — Constitution Check gate is dynamic ("based
           on constitution file"), no hardcoded principle list; no changes needed.
      ✅ .specify/templates/spec-template.md    — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md   — phase structure generic; aligns with I–VII.
      ✅ .specify/templates/agent-file-template.md — all generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — not in scope for this pass; no issues.
  - No stale agent-specific references detected.
  - No version bump required; Last Amended date remains 2026-03-03 (no content amendments).
  - No deferred TODOs.

[2026-03-04 — Session reuse: behavior correction]
  - Constitution reused as-is from previous session (no principle amendments).
  - Session intent: identify and correct a bug / incorrect runtime behavior in the application.
  - All placeholder tokens remain resolved; no bracket tokens present.
  - Version 1.1.0 footer consistent with all sections.
  - Templates (plan, spec, tasks, agent-file) confirmed aligned with Principles I–VII.
  - No stale agent-specific references detected.
  - No version bump required; Last Amended date remains 2026-03-03.
  - No deferred TODOs.

[2026-03-03 — v1.0.0 → v1.1.0]
Version change    : 1.0.0 → 1.1.0
Modified principles:
  - Principle I (Trusted Configuration Authority) — split into two explicit access tiers:
      bot-interaction role (general commands) vs. trusted/config role (season management)
  - Principle IV (Deterministic & Auditable Weather Generation) — replaced generic seeding
      language with the concrete three-phase pipeline as a non-negotiable architectural
      constraint (Phase 1 T-5d, Phase 2 T-2d, Phase 3 T-2h), Mystery Round exception,
      and amendment invalidation semantics
  - Principle V (Observability & Change Audit Trail) — explicitly names the calculation
      log channel as the target for phase computation records
Added sections    :
  - Principle VII: Output Channel Discipline (new)
  - Bot Behavior Standards: round format taxonomy, weather slot counts, text-first note
  - Data & State Management: inter-phase state persistence, amendment invalidation clearing
Removed sections  : None

Templates requiring updates:
  ✅ .specify/templates/constitution-template.md — source template; no changes required
  ✅ .specify/templates/plan-template.md — Constitution Check gates now reference I–VII;
       template is generic enough; no structural edits needed
  ✅ .specify/templates/spec-template.md — generic structure; no domain-specific changes needed
  ✅ .specify/templates/tasks-template.md — phase structure aligns with updated principles
  ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale references
  (no files found in .specify/templates/commands/)

Follow-up TODOs   : None — all placeholders resolved
-->

# F1 League Bot Constitution

## Core Principles

### I. Trusted Configuration Authority

Two distinct access tiers MUST be maintained and configured independently:

1. **Interaction role**: A server-level Discord role that gates all bot commands. Only members
   holding this role may issue any command to the bot. Commands MUST be accepted only when
   sent in a single, administrator-configured interaction channel. Both the role and the
   channel are set during initial bot setup, separately from season configuration.

2. **Season/config authority**: A subset of interaction-role members (e.g., Race Director,
   Admin) who are additionally permitted to create or mutate season data — divisions, track
   schedules, race dates/times, round formats, and any amendments. This tier MUST also be
   explicitly configured; holding the general interaction role alone is insufficient.

The bot MUST reject out-of-channel commands silently (no response) and MUST reject
unauthorized configuration commands with a clear, actionable permission error.
No implicit super-user status exists for either tier.

**Rationale**: Separating "who can read weather" from "who can change the season" prevents
casual members from accidentally triggering configuration commands, while still allowing
the broader league membership to interact with the bot in controlled ways.

### II. Multi-Division Isolation

The bot MUST support multiple divisions (e.g., Pro, Am, Open) operating concurrently within
a single Discord server. Each division's calendar, weather outputs, and runtime state MUST be
stored and evaluated as a fully independent data domain. A command or mutation targeting
Division A MUST NOT read, write, or in any way affect Division B. Division identifiers MUST
be explicit in every configuration command and every output message.

**Rationale**: League servers routinely run tiered divisions in parallel. Cross-contamination
of schedules or weather seeds would undermine competitive fairness and create administrative
confusion.

### III. Resilient Schedule Management

The bot MUST accommodate mid-season plan changes at any point in an active season:

- **Track substitutions**: replace a scheduled circuit with another.
- **Postponements**: shift a race date and/or time forward without losing round identity.
- **Cancellations**: remove a round and resequence the calendar cleanly.

Each change MUST be applied atomically; partial updates are not permitted. The bot MUST
preserve the original schedule alongside the current one so the full amendment history is
recoverable. Re-generating weather after a schedule change MUST use a fresh, distinct seed
and MUST log the reason for re-generation.

**Rationale**: Real leagues face unavoidable logistical disruptions. The bot MUST absorb these
without requiring a full season reset or manual data repair.

### IV. Three-Phase Weather Pipeline (NON-NEGOTIABLE)

Weather generation for every non-Mystery round MUST follow exactly three sequential phases,
each triggered automatically at a fixed horizon before the scheduled round start time:

- **Phase 1 — Rain Probability** (T − 5 days): Compute `Rpc` from the track base factor and
  two independent random draws. Log all inputs and the result. Post a public probability
  message to the division's weather forecast channel.
- **Phase 2 — Session Type Draw** (T − 2 days): Use the `Rpc` value persisted in Phase 1 to
  populate a 1 000-entry weighted map of Rain / Mixed / Sunny slots; draw once per session
  in the round. Log inputs, weights, and draws. Post session-type forecasts to the division
  channel.
- **Phase 3 — Final Slot Generation** (T − 2 hours): Use the `Rpc` value and each session's
  Phase 2 type to build per-session weighted maps; draw `Nslots` times (randomly chosen
  within the session-type slot-count bounds). Log the full draw sequence. Post the final
  weather layout to the division channel.

**Mystery Rounds** are the sole exception. No draw is performed, no `Rpc` is computed, no phase
result is written, and nothing is logged to the calculation channel. In place of the three phases:

- At the **Phase 1 horizon** (T − 5 days) the bot MUST post a fixed notice to the division's
  forecast channel stating that the weather of the round is not pre-generated. It carries no
  forecast value and no division role mention, the conditions being unknown to every participant
  alike. It stands in the place of the Phase 1 forecast for such a round and MUST be recorded
  distinctly from one, no forecast having been computed.
- At the **Phase 2 and Phase 3 horizons** nothing whatever is posted.

The notice is the round's only weather output, and it is a posting the module makes rather than one
it withholds — which is what allows an image type to draw it (Principle XIV, Rule 8).

**Amendment invalidation**: If a round is amended (track change, postponement, format change)
after any phase has completed, ALL previously posted weather outputs for that round are
invalidated. The bot MUST immediately post an invalidation notice to the division channel and
re-execute whichever phases have already passed their horizon. Previously computed `Rpc`,
session-type draws, and slot draws MUST be discarded from active state but retained in the
audit log with an `INVALIDATED` status marker.

All random draws MUST be logged with the input state at the moment of drawing so any result
can be independently audited or challenged.

**Rationale**: A locked pipeline with defined horizons gives drivers predictable information
cadence and eliminates any window for post-hoc manipulation. The Mystery Round exception
preserves competitive surprise by design.

### V. Observability & Change Audit Trail

Every configuration mutation — season setup, track substitution, postponement, cancellation,
format change, trusted-role grant or revoke, driver-state transition, team assignment change,
and team definition add/modify/remove — MUST produce a timestamped audit log entry recording:
actor (Discord user ID and display name), division (where applicable), change type, previous
value, and new value.

All three weather phases MUST log their full computation to the designated calculation log
channel (configured separately from the division weather forecast channels): inputs, random
draws, intermediate values, and final outputs. Phase log entries MUST include the round
identifier, division, and UTC timestamp.

All mutations that affect a published schedule MUST post a human-readable confirmation to the
calculation log channel. The bot MUST NOT silently accept or silently discard any command.

**Rationale**: League administrators and drivers need an unambiguous, channel-visible record
of computations and changes, especially when disputing weather outcomes or schedule
alterations.

### VI. Incremental Scope Expansion

The bot is on a deliberate, incremental path toward encompassing the full business rules of
an F1 game league. Scope expands one formally ratified feature at a time. The following
domains are formally in-scope as of this version:

1. **Weather generation**: the three-phase pipeline (Principle IV) remains the core function,
   delivered as an optional module (Principle X).
2. **Season and division lifecycle**: setup, activation, completion, cancellation, round
   scheduling, and amendments.
3. **Driver profile management**: state machine enforcement, Discord User ID reassignment,
   and historical participation tracking.
4. **Team management**: configurable team definitions per division, seat assignment, and
   the Reserve team ruleset.
5. **Signup wizard and driver onboarding**: the multi-step signup flow, per-driver signup
   channels, admin approval pipeline, correction request cycle, signup configuration
   (nationality toggle, time type, time-proof image requirement, time slots), and driver
   onboarding from first button-press through placement eligibility.
6. **Driver assignment and placement**: assign/unassign/sack drivers to division-team seats;
   seeded placement queue; division-role grant and revocation.
7. **Modular feature architecture**: per-server enablement and disablement of optional
   capability modules (Principle X).
8. **Race results recording**: round-by-round result entry per division, outcome modifiers
   (DNF, DNS, DSQ), and result amendments with full audit trail.
9. **Championship standings computation and display**: points accumulation per driver per
   division, tiebreaking, and derivation of current and final standings.
10. **Penalty adjudication and appeals**: application of post-race penalties (time
    penalties, disqualifications) via a stewards workflow, posting of penalty decisions
    to a dedicated channel, and a second-level appeals process allowing interaction-role
    members to contest a penalty, resolved by a tier-2 admin.
11. **Driver attendance management**: round RSVP check-in flow, reserve distribution at
    the RSVP deadline, attendance tracking once round results are submitted, attendance
    point accumulation per driver, attendance pardon workflow inside the penalty wizard,
    and automatic sanction enforcement (autoreserve and autosack thresholds).
12. **Image-based output generation**: SVG template filling and PNG rasterisation for the
    output of information already produced by the results & standings, weather, signup, and
    attendance domains, plus the season, division, round, team, and driver concepts they
    share; the asset catalogue backing those templates; and per-server enablement of the
    image output path (Principle XIV).

The following domains are **planned future scope** — each will be formally ratified as an
independent feature increment before any implementation begins:

- **Season history and statistics**: aggregated career records and cross-season metrics
  derived from the Season Archive (see Data & State Management).
- Financial or licensing workflows.

Every proposed new command or data concern MUST be evaluated against the current scope
boundary before implementation begins. Features not falling within a ratified domain MUST
be rejected or deferred via the governance process below.

Output is available in two formats: text, which is always available, and images, which are
produced by the image generation module (Principle XIV). Image output is strictly additive —
every image carries at least what the posting it replaces carried, and no image path may
replace or degrade a text path. A graphic MAY carry more than the text path publishes; what
it MUST NOT do is decide anything the source module has not already settled. Principle XIV
Rule 7 governs, and is the authority on both halves.

**Rationale**: A controlled, documented expansion path allows the bot to grow toward full
league management without sacrificing reliability or auditability. Each increment is gated
behind formal ratification to prevent unplanned feature creep.

### VII. Output Channel Discipline

The bot MUST post messages to exactly the following categories of channel, and no others
unless explicitly permitted by an active module (see below):

1. **Per-division weather forecast channel** (one per division, configured at season setup):
   receives only Phase 1, Phase 2, Phase 3 public weather messages, and amendment
   invalidation notices for that division.
2. **Calculation log channel** (one per server, configured at bot setup): receives all phase
   computation logs, configuration mutation confirmations, and audit trail entries.

**Module-introduced channels**: Optional modules (Principle X) MAY register additional
channel categories (e.g., a general signup channel, per-driver signup channels). Each such
category MUST be explicitly documented in the module's feature specification, configured
via a dedicated module-setup command, and governed by the same discipline as primary
channels — no unregistered posting, no cross-channel noise.

**Image attachments**: A generated image (Principle XIV) is an attachment on the message its
source module posts, not a message of its own. It MUST be posted to the channel that module
already registered for that information. The image generation module registers no channel
category and MUST NOT post independently of a source module.

The bot MUST NOT post to any other channel, including the interaction channel where commands
are issued. Unsolicited messages in unregistered channels are not permitted.

**Rationale**: Keeping output in known, designated channels prevents noise in general server
channels and makes it trivial for drivers and admins to find the right information.

### VIII. Driver Profile Integrity

Every Discord user within a server is represented by at most one driver profile, keyed on
their Discord User ID in server scope. The following rules are non-negotiable:

- **State machine enforcement**: A driver's current state MUST only change via the transitions
  in the table below. Any transition not in the approved list MUST be rejected with a clear
  error. No code path may bypass the state machine to set state directly.

#### Driver States

| State | Meaning |
|-------|---------|
| Not Signed Up | Inactive; eligible to initiate signup. Default when no profile exists. |
| Pending Signup Completion | Wizard engaged; bot is collecting signup parameters. |
| Pending Admin Approval | All parameters collected; awaiting trusted-role review. |
| Awaiting Correction Parameter | Trusted user clicked "request changes"; selecting which field to re-collect (5-minute window). |
| Pending Driver Correction | Specific field flagged; driver must re-submit that field only. |
| Unassigned | Signup approved; not yet placed in any division-team seat. |
| Assigned | Placed in at least one division-team seat. |
| Season Banned | Banned for `ban_races_remaining` rounds (see Season Banned mechanics). Cannot sign up. |
| League Banned | Permanently banned. Cannot sign up until explicitly lifted by an administrator. |

#### Permitted Transitions

| From | To | Trigger / Condition |
|------|----|---------------------|
| Not Signed Up | Pending Signup Completion | Driver presses signup button (signups must be open) |
| Pending Signup Completion | Pending Admin Approval | Driver completes all wizard steps |
| Pending Signup Completion | Not Signed Up | Driver withdraws; or 24 h inactivity timeout |
| Pending Admin Approval | Awaiting Correction Parameter | Trusted user clicks "request changes" |
| Awaiting Correction Parameter | Pending Driver Correction | Trusted user selects field to correct |
| Awaiting Correction Parameter | Pending Admin Approval | 5-minute timeout with no field selected |
| Pending Driver Correction | Pending Admin Approval | Driver submits valid corrected field |
| Pending Driver Correction | Not Signed Up | Driver withdraws; or 24 h inactivity timeout |
| Pending Admin Approval | Unassigned | Trusted user approves signup |
| Pending Admin Approval | Not Signed Up | Trusted user rejects signup; or driver withdraws |
| Unassigned | Assigned | `/driver assign` places driver in their first seat |
| Assigned | Unassigned | `/driver unassign` removes driver's last seat assignment |
| Unassigned | Not Signed Up | `/driver sack` |
| Assigned | Not Signed Up | `/driver sack` |
| Any (except League Banned, Season Banned) | Season Banned | Ban command issued |
| Any (except League Banned) | League Banned | Ban command issued |
| Season Banned | Not Signed Up | `ban_races_remaining` decrements to 0 |
| League Banned | Not Signed Up | Administrator explicitly lifts ban |
| Not Signed Up | Unassigned | Test mode: admin direct-assign |
| Not Signed Up | Assigned | Test mode: admin direct-assign |

- **Season Banned mechanics**: When a Season Ban is issued, `ban_races_remaining` is set to
  the total round count of the active season at the time of issuance. This counter decrements
  by 1 for each round that completes anywhere within the server. When `ban_races_remaining`
  reaches 0, the driver automatically transitions to *Not Signed Up* under the same rules as
  any other transition to that state (immutability gate, deletion, signup-data clearing).
- **Signup data clearing**: On transition to *Not Signed Up* with `former_driver = true`, all
  signup record fields (collected parameters) MUST be nulled; the driver's signup channel
  reference is retained until the channel is pruned per Principle XI.
- **Immutability of former drivers**: Once `former_driver` is `true` (set on first round
  participation), the profile record MUST NOT be deleted — only modified. Deletion attempts
  MUST be rejected.
- **Deletion rule**: Transitioning to *Not Signed Up* with `former_driver = false` MUST delete
  the record atomically in the same transaction as the state change.
- **User ID reassignment**: Only a server administrator may change the Discord User ID.
  Both old and new IDs MUST be logged as an audit event (Principle V). Upon reassignment,
  the stored Discord username and server display name MUST be overwritten by those of the
  new account.
- **Test-mode overrides**: When test mode is active, administrators MAY directly set
  `former_driver` to `true` or `false`, and MAY assign *Not Signed Up* drivers directly to
  *Unassigned* or *Assigned*. All such overrides MUST produce audit log entries.
- **Absent profile semantics**: A Discord user with no database record is treated as
  *Not Signed Up*. The bot MUST NOT error or warn on absence — absence is the canonical
  default.
- **Server-leave rule**: If a user leaves the server while their driver profile exists, the
  profile record MUST be retained. Any active signup wizard is cancelled immediately and the
  signup channel deleted without delay.

**Rationale**: The driver profile is a long-lived, server-scoped identity record. Exhaustive
state enumeration and machine enforcement prevent data loss, support unambiguous auditability,
and provide a stable framework for all planned lifecycle extensions.

### IX. Team & Division Structural Integrity

Teams and division tiers carry structural invariants that MUST be enforced at every mutation
point:

- **Reserve team**: The Reserve team MUST always exist in every division and MUST NOT be
  removable, renameable, or otherwise modified by any user command. Its seat count is
  unlimited. It MUST likewise exist in the **server's team configuration**, and MUST be created
  whenever that configuration is read or written and none is present.
- **Team name validity**: A team name MUST normalise (Principle XIV.13) to a **filename** that is
  non-empty, is unique within its scope — the server for the server's team configuration, the
  division for the teams of a season — and is not `reserve`, which is reserved for the Reserve
  team. `team add` and `team rename` MUST reject a name failing any of these with
  a clear diagnostic, and `season review` MUST fail validation of a season any team of which
  fails them, naming every offending team. Of the two names `team rename` takes, only the **new**
  one is validated: the current name identifies a team that already exists, and validating it
  would leave a team named before this rule impossible to rename or to remove. The same holds for
  the name taken by `team remove`. Seasons already approved MUST NOT be re-validated against this
  rule, and no team may be renamed or removed by its introduction.

  A name **beginning with a digit is admitted**. The requirement that it begin with a letter held
  only while the normalised form had to serve as the `@id` of a node in an XML document, and is
  withdrawn together with that use of it (Principle XIV.11).

  This constraint binds **whether or not the Image generation module is enabled**. The normalised
  name is the **filename** under which every graphic drawing a team badge seeks that team's image
  (Principle XIV.13), and a name is cheapest to constrain at the one moment it is set; a league
  enabling the module later would otherwise hold names it cannot clean up without losing that
  team's history.
- **Configurable teams**: The standard ten constructor teams (Alpine, Aston Martin, Ferrari,
  Haas, McLaren, Mercedes, Racing Bulls, Red Bull, Sauber, Williams) each carry exactly 2 seats
  by default. A server administrator MAY add, modify, or remove configurable teams from the
  server-level default set at any time. Changes to the default set MAY be applied to all
  divisions of the current season ONLY during the `SETUP` lifecycle phase.
- **Division isolation**: A team definition or seat assignment in Division A MUST NOT affect
  Division B. Team data is partitioned per division, per season.
- **Divisions MAY differ in composition**: the divisions of a season MAY field different teams,
  and different numbers of seats in each. No image type places a requirement of uniformity upon a
  season. A lineup template is authored against a **count** of teams and of seats and never
  against a team list (Principle XIV.11), so one file serves divisions of unlike composition, a
  block beyond the teams of the division being removed in silence. The former invariant requiring
  the divisions of a season to field the same teams and the same seats — gated on the `lineup`
  aspect — is **withdrawn**, the template convention that forced it having been withdrawn with it.
- **Sequential tier ordering**: Before a season may be approved (transitioned from `SETUP` to
  `ACTIVE`), all configured divisions MUST have tier values that form a gapless sequence
  starting at 1 (e.g., 1, 2, 3 — not 1, 3). The bot MUST block season approval and return a
  clear diagnostic if this rule is violated. Divisions are stored and displayed in ascending
  tier order, with tier 1 representing the highest tier.
- **Tier as supplementary ID**: A division's tier MAY be used as a secondary identifier in
  commands and logs, but the division name remains the canonical label in all bot output.

**Rationale**: Structural invariants on teams and tiers prevent silent misconfiguration that
would compromise competitive fairness — a division with a gap in its tier sequence or a
missing Reserve team would produce ambiguous or incorrect league operations.

### X. Modular Feature Architecture

The bot is partitioned into foundational and optional modules. Module state MUST be persisted
per server and MUST survive bot restarts.

**Foundational modules** (always active, cannot be disabled):
- Division and round management
- Team management
- Driver profile management
- Season lifecycle management

**Optional modules** (disabled by default; enabled explicitly per server by a server
administrator via a dedicated `/module enable <name>` command — or its equivalent
structured subcommand):
- **Weather generation module**: arms the three-phase scheduler, registers weather channel
  configs, and processes the forecast pipeline (Principle IV).
- **Signup module**: manages the signup wizard flow, the general signup channel, per-driver
  signup channels, signup configuration (nationality toggle, time-type, time-image, time
  slots), and the driver onboarding state machine.
- **Results & standings module**: delivers the named points-configuration store, season
  attachment, session-by-session round result submission, standings computation, and results
  and standings channel posting (Principle XII).
- **Attendance module**: manages round RSVP notices and check-in embeds, attendance
  tracking per round, attendance point accumulation per driver, reserve distribution at
  the RSVP deadline, and automatic sanction enforcement (autoreserve and autosack)
  (Principle XIII). MUST NOT be enabled while the Results & Standings module is disabled;
  if the Results & Standings module is disabled while Attendance is active, the Attendance
  module is disabled automatically.
- **Image generation module**: renders SVG templates to PNG attachments for the output of
  the other modules and of the shared season, division, round, team and driver concepts;
  owns the template set and asset catalogue (Principle XIV). It is a *consumer* module: it
  registers no channels and originates no messages of its own. Enabling it switches the
  output path of every source module that has an image type defined; disabling it returns
  those modules to text output. It MAY be enabled independently of any source module, but
  image types belonging to a disabled source module MUST NOT be produced.
- Additional modules as ratified under Principle VI.

The following rules MUST hold for every optional module:

1. **Default-off**: A freshly configured server MUST have all optional modules disabled until
   explicitly enabled.
2. **Enable atomicity**: Enabling a module MUST atomically set the module-enabled flag and
   create or arm any associated scheduled jobs. Module-specific configuration (channels,
   roles, settings) MAY be set via dedicated commands independently of the enable action —
   before or after enabling. If any step of the enable operation itself fails, it MUST be
   rolled back and no partial state left. Any module function that depends on configuration
   not yet provided MUST validate those prerequisites before executing and return a clear,
   actionable error; the module-enabled flag alone does not guarantee all configuration is
   complete.
3. **Disable atomicity**: Disabling a module MUST atomically cancel all scheduled jobs
   associated with that module, delete or archive its channel/role configuration, and post
   a human-readable notice to the log channel. Historical data generated by the module
   (phase results, audit entries, signup records) MUST be retained; only live/scheduled
   artifacts are removed.
4. **Scheduling guard**: Scheduled jobs (e.g., weather phase timers) MUST only be created or
   re-armed when the relevant module is enabled. On bot restart, the bot MUST check module
   state before re-arming any job.
5. **Gate enforcement**: Any command or system action that belongs to an optional module MUST
   check the module-enabled flag before executing and return a clear, actionable error to
   the user if the module is disabled.
6. **Module configuration isolation**: Module-specific configuration is stored separately
   from core server configuration (Principle I). Disabling a module clears module config;
   re-enabling starts fresh unless a `--preserve-config` flag is explicitly supported and
   documented.

   **Exception — configuration that cannot go stale**: A module whose configuration consists
   solely of values that remain true while the module is disabled MUST retain that
   configuration across a disable, and disabling MUST clear only the module-enabled flag.
   A value qualifies only if it names nothing the bot owns or schedules — a filesystem path,
   a display preference, a colour, a format. Any value naming a Discord channel, role,
   message or scheduled job does NOT qualify and remains subject to the clearing rule above.
   A module claiming this exception MUST enumerate the qualifying values in its feature
   specification. The **Image generation module** claims it for the whole of its
   configuration: template and asset directories, template filenames, time zone, clock and
   date formats, the fastest-lap colour, and the per-aspect output toggles.

   **Rationale for the exception**: The clearing rule exists so that a re-enabled module
   cannot act on stale bindings to server objects that may have been deleted or reassigned
   while it was off. A filesystem path or a colour has no such binding — it is as true after
   a disable as before it, and discarding thirty such values punishes an administrator for
   toggling a module off to diagnose a problem.

**Rationale**: The bot's growth toward full league management requires a clean separation
between always-on infrastructure (divisions, drivers, teams) and capability modules that
server administrators opt into. Mandatory modules establish the data model that all other
modules build on; optional modules add functionality only when the server is ready for it.
The default-off policy prevents accidental activation of unintended features and keeps the
initial setup experience simple.

### XI. Signup Wizard Integrity

The signup wizard is the multi-step onboarding flow initiated when a driver presses the signup
button. It operates as a secondary state machine (wizard state) orthogonal to the driver
lifecycle state (Principle VIII). The following rules are non-negotiable:

- **Isolation**: Each driver has exactly one wizard state record. Concurrent wizards for
  different drivers MUST be fully isolated; one driver's wizard MUST NOT delay, influence, or
  share state with any other.
- **Channel lifecycle**:
  - On wizard start, the bot MUST create a private channel named `<username>-signup`, visible
    only to the driver, tier-2 admins, and server administrators.
  - The channel MUST be deleted after a 24-hour hold period following any terminal event
    (approval, rejection, withdrawal, or timeout cancellation). During the hold period the
    channel is read-only for the driver.
  - The channel MUST be deleted immediately (no hold) when the driver leaves the server.
  - If a driver with an existing signup channel re-presses the signup button, the old channel
    MUST be deleted immediately and a new one created.
  - Tier-2 admins and server administrators MAY write freely in any signup channel at any time.
- **Sequential collection (normal flow)**: In the normal wizard (Pending Signup Completion),
  parameter collection MUST follow the exact order specified in the feature specification.
  Each step MUST wait for a valid response before advancing.
- **Targeted correction flow**: In the correction wizard (Pending Driver Correction), the
  wizard MUST advance directly to the flagged parameter's collection state, collect only that
  parameter, then return to Unengaged and transition the driver to Pending Admin Approval.
  No other parameters are re-collected.
- **Inactivity timeout**: Remaining in Pending Signup Completion or Pending Driver Correction
  without wizard progress for 24 consecutive hours triggers cancellation: the driver
  transitions to Not Signed Up; the channel is frozen (read-only); a cancellation notice is
  posted; the channel is deleted 24 hours later.
- **Withdrawal**: A withdrawal button MUST be visible throughout the wizard while the driver is
  in Pending Signup Completion, Pending Admin Approval, or Pending Driver Correction. Pressing
  it transitions the driver to Not Signed Up immediately.
- **Signup data persistence**: Collected answers are stored as draft data during the wizard.
  On transition to Pending Admin Approval the complete record MUST be committed atomically.
  Draft data MUST be discarded on any transition to Not Signed Up.
- **Image proof validation (configurable)**: When `time_image_required` is enabled, every
  lap-time submission MUST include an attached image; text-only submissions MUST be rejected
  with a clear explanation. The requirement MUST be stated in the channel before each
  time-collection step.
- **Lap time format**: Accepted formats are `M:ss.mss` and `M:ss:mss`. The colon-separated
  variant MUST be normalised to dot-separated. Milliseconds MUST be zero-padded to 3 digits.
  Leading and trailing whitespace MUST be stripped.
- **Configuration snapshot**: Wizard-governing configuration (nationality toggle, time type,
  image requirement, time slots, signup tracks) is read once at wizard-start and cached per
  wizard instance. Configuration changes after a wizard starts MUST NOT affect that wizard.
- **Signup close timer (optional)**: When signups are opened, an optional close duration MAY
  be specified. If provided, it is resolved to an absolute UTC timestamp (`close_at`) and
  persisted on the server's signup configuration. When the timer fires, signups are closed
  automatically: only drivers in Pending Signup Completion are transitioned to Not Signed Up
  (applying the same cancellation semantics as a manually confirmed close with in-progress
  drivers); drivers in Pending Driver Correction or Pending Admin Approval retain their
  current state — their submitted records are preserved for admin review. The signup button
  is removed; and a "signups closed" notice is posted in the general signup channel. The
  timer is cleared when signups are closed manually before it fires. On bot restart, any
  active close timer MUST be re-armed.
  *(Amended v2.8.0: close-timer now preserves PENDING_ADMIN_APPROVAL and
  PENDING_DRIVER_CORRECTION drivers; only PENDING_SIGNUP_COMPLETION is cleared.)*
- **Lineup and calendar announcement channels**: Optional per-division Discord channels for
  lineup and calendar posts. Both `lineup_channel_id` and `calendar_channel_id` are stored
  on the `divisions` table (alongside `results_channel_id`, `standings_channel_id`, etc.)
  and are NOT scoped to the signup module. `/division calendar-channel` is available
  whenever a season exists, without requiring the signup module to be enabled. When
  `lineup_channel_id` is configured for a division, the bot MUST delete the previous lineup
  message (tracked via `lineup_message_id` on the `divisions` row) and post a fresh lineup
  message whenever a driver's assignment in that division changes (assign, unassign, or sack).
  When `calendar_channel_id` is configured, a calendar message is posted to that channel
  upon season approval. If neither channel is configured for a division, no messages are
  posted for that division.
  *(Amended v2.8.0: lineup channel ownership moved from signup_division_config to divisions;
  calendar_channel_id and lineup_message_id added to divisions; /division calendar-channel
  command is not module-gated.)*

**Rationale**: A strictly defined, isolated wizard removes ambiguity in the onboarding process,
protects in-progress signups from mid-flow configuration changes, ensures data integrity before
trusted-user review, and maintains a clean channel lifecycle for server hygiene.

### XII. Race Results & Championship Integrity

Race outcomes MUST be recorded, persisted, and computed with the same auditability as weather
generation. Results form the authoritative competitive history of the league. This principle
governs the **Results & Standings optional module** (Principle X).

#### Authorization & Module Gate

- Only tier-2 admins (season/config authority, Principle I) may submit, amend, or penalise
  result records.
- All commands in this module MUST check that the Results & Standings module is enabled before
  executing, and return a clear error if it is not (Principle X, rule 5).
- The module MAY NOT be enabled in the middle of an active season.
- A season in `SETUP` MAY NOT be approved if the module is enabled and any division lacks a
  configured results channel or standings channel, or if no points configuration exists that
  yields at least one non-zero position value for any session.

#### Points Configuration Store

- A server maintains a **server points config store**: a keyed set of named configurations.
  Each configuration defines, per session type, the points awarded per finishing position
  and optionally a fastest-lap bonus and a position eligibility limit for that bonus.
- The default for any position or bonus not explicitly configured is **0 points**. There is
  no preset.
- Valid session types for point awards are: Sprint Qualifying, Sprint Race, Feature
  Qualifying, Feature Race. Fastest-lap awards are valid only for Sprint Race and Feature
  Race; configuring fastest-lap for qualifying sessions MUST be rejected.
- Within a single configuration and session type, positions MUST be monotonically
  non-increasing in points (higher position ≥ lower position). Season approval MUST be
  blocked if any configuration attached to the season violates this rule.
- Named configurations from the server store are **attached** (weakly linked) to a season
  in `SETUP` to form that season's **season points store**. Attachment is a copy-on-approve
  action: on season approval the attached configurations' settings are snapshotted into the
  season points store and become independent of the server store.
- Modifications to the server store after a season is approved do NOT affect that season's
  store.

#### Result Submission

- Results are submitted **per session**, sequentially within a round, in the order: Sprint
  Qualifying → Sprint Race → Feature Qualifying → Feature Race (sprint-type sessions
  omitted for Normal/Endurance rounds). Each session's results MUST be validated and
  accepted before the next session's collection begins. Session-type mapping by round
  format:
    - **Normal**: Feature Qualifying, Feature Race.
    - **Sprint**: Sprint Qualifying, Sprint Race, Feature Qualifying, Feature Race.
    - **Endurance**: the Full Qualifying session maps to Feature Qualifying; the Full Race
      session maps to Feature Race for result-type and points-configuration purposes.
    - **Mystery**: no result sessions; result collection MUST NOT be triggered.
- The bot creates a transient submission channel adjacent to the division's results channel
  at the scheduled round start time, notifying tier-2 admins to enter results. This channel
  is a module-introduced channel category registered per Principle VII.
- Each session result row MUST carry: session type, round ID, division ID, driver Discord
  User ID, finishing position (1-indexed positive integer), team role, tyre (qualifying)
  or total race time and fastest lap (race), and an outcome modifier. Permitted modifiers:
  CLASSIFIED (eligible for points), DNF / DNS / DSQ (0 points, ineligible for fastest-lap
  bonus except as noted). A special CANCELLED result MAY be recorded for sessions not run.
- After a session's results are accepted, the bot presents one button per named seasonal
  configuration; the tier-2 admin MUST choose one. The chosen configuration name is
  persisted with the session result and used for all points calculations for that session.
- A CLASSIFIED driver is eligible for the chosen configuration's fastest-lap bonus if, and
  only if, their finishing position is at or above the configured position limit for that
  session (i.e., finishing_position ≤ fastest_lap_position_limit).
- DNF, DNS, and DSQ drivers are NEVER eligible for finishing-position points. DSQ and DNS
  drivers are NEVER eligible for fastest-lap points. DNF drivers are eligible for
  fastest-lap points provided the position limit condition is met.

#### Amendment & Penalty

- A tier-2 admin MAY amend any session's results entirely (full re-entry) or apply
  targeted time penalties or disqualifications per driver via a guided wizard. Each
  amendment or penalty MUST produce an audit log entry per Principle V.
- On amendment or penalty application, standings for the affected round and all subsequent
  rounds in that division MUST be recomputed and reposted atomically.
- If the transient round results submission channel has already been opened for a round,
  any request to cancel that round MUST be rejected with a clear error. The round MAY
  only be cancelled once all pending result sessions have been submitted or explicitly
  marked CANCELLED.
- The amendment-mode toggle MUST reject a request to disable (toggle off) while
  `modified_flag` is `true`. Tier-2 admins MUST first either approve (overwriting the
  season points store) or revert (discarding the modification store) before amendment
  mode may be disabled.
- Mid-season scoring table amendments follow a **modification store** workflow: a copy of
  the season points store is placed into a modification store; changes are applied there;
  only upon tier-2 admin approval does the modification store overwrite the season store.
  On approval, all affected results and standings MUST be reposted. A `modified_flag`
  (default false) tracks uncommitted changes; it is set on any modification and cleared
  on approval or revert.

#### Penalty Announcements

- When a penalty (time penalty or DSQ) is applied via the penalty wizard, the bot MUST
  post a penalty announcement notice to the division's configured **penalty announcement
  channel** (`penalty_channel_id` on `DivisionResultsConfig`).
- The announcement MUST include: the affected driver's display name, the round name, the
  session type, the penalty type and magnitude (e.g., "+5 seconds" or "DSQ"), a reason if
  provided by the tier-2 admin, and the Discord display name of the admin who applied it.
- If no penalty announcement channel is configured for a division, announcements fall back
  to that division's results channel.
- The penalty announcement channel is a module-introduced channel category governed by
  Principle VII. Configuring it is not required for module activation; if absent the
  fallback ensures the notice is always posted.

#### Penalty Appeals

- The appeals stage is fully admin-driven. After the penalty review is approved, a tier-2
  admin runs an appeals review wizard (mirroring the penalty review wizard) in the same
  transient submission channel. The admin stages corrections and approves them; no driver
  submission step exists in this increment.
- Appeals follow a two-outcome lifecycle: **Upheld** (correction applied to the result) or
  **Overturned** (no change; reserved for future use). Every correction staged and approved
  in this increment is stored as `UPHELD`. A `PENDING` state and driver-initiated appeal
  submission are explicitly deferred to a future stewarding module.
- The appeals review MUST produce an audit log entry per correction, including description
  and justification (Principle V).
- On approving corrections: the affected `DriverSessionResult` rows are updated; standings
  for the affected round and all subsequent rounds in that division MUST be recomputed and
  reposted atomically, consistent with the amendment recomputation rule above.
- On approving with no staged corrections: the round advances to `FINAL` with results
  identical to the `Post-Race Penalty Results` post. No result changes occur.
- Each applied correction MUST produce one announcement post to the division's configured
  verdicts channel (if accessible). Announcement skipped silently if channel is
  inaccessible; finalization is never blocked by an announcement failure.

#### Standings Computation

- **Driver standings**: all drivers who have participated in a division, ranked by (1)
  total points, (2) count of Feature Race wins, (3) count of Feature Race 2nd-place
  finishes, … (n) count of Feature Race nth-place finishes, (n+1) earliest round in which
  the highest diverging finish was first achieved. Only Feature Race sessions are
  authoritative for countback tiebreaking.
- **Team standings**: teams ranked by the same hierarchy applied to the aggregate points and
  Feature Race finishes of all drivers scoring under that team's banner in each session.
  A reserve driver's points and finishes accrue to whichever team they drove for in each
  individual session.
- A standings **snapshot** MUST be persisted after every round: per driver (and per team),
  the total points accumulated to that round and the per-position finish counts (and
  the round number on which each position was first obtained) MUST be stored. These
  snapshots form the authoritative historical record and allow reconstruction of standings
  at any point in the season.
- Reserve drivers' appearance in the public driver standings is governed by a per-division
  **reserves visibility toggle** (default on). When toggled off, reserve drivers still
  accrue points and are included in internal snapshots, but are excluded from posted output.
- On season completion, each driver's final points and position MUST be written atomically
  to their SeasonAssignment `final_points` and `final_position` fields as part of the
  season-end transaction.

**Rationale**: Accurate, immutable, session-level result records are the backbone of any
competitive league. A deterministic, auditable computation pipeline with named configurations
and snapshot-based standings history ensures results can always be reproduced from raw input
and legitimately contested.

### XIII. Attendance & Check-in Integrity

The Attendance module governs driver RSVP check-ins before each round and the resulting
attendance tracking and point accumulation. It operates as an optional module (Principle X)
and depends entirely on the Results & Standings module (Principle XII). The following rules
are non-negotiable.

#### Module Dependency & Lifecycle Gate

- The Attendance module MUST NOT be enabled while the Results & Standings module is
  disabled. Any attempt MUST be rejected with a clear error.
- If the Results & Standings module is disabled while the Attendance module is active,
  the Attendance module MUST be disabled automatically (applying the same disable atomicity
  rules of Principle X, rule 3).
- The Attendance module MUST NOT be enabled once a season is in the `ACTIVE` lifecycle
  state. It MAY only be enabled during `SETUP` or while no season exists.
- The Attendance module activation status MUST be displayed in the season review output
  alongside other module states.
- The Attendance module MUST function correctly with fake driver rosters created under
  test mode.

#### Season Validation Gates

- Before a season may be approved, if the Attendance module is enabled, every configured
  division MUST have both an RSVP channel and an attendance channel configured. Missing
  either channel for any division MUST block season approval with a clear diagnostic.
- Both channel IDs are stored in **AttendanceDivisionConfig** (one row per division per
  server). They are displayed in the season review alongside other division channels
  (results, standings, weather, etc.).

#### RSVP Timing Configuration

Three timing parameters govern the RSVP lifecycle and MUST satisfy the invariant
**notice_days × 24 > last_notice_hours > deadline_hours** at all times:

- `rsvp_notice_days` (default 5) — days before a round at which the RSVP embed is posted.
- `rsvp_last_notice_hours` (default 24) — hours before a round at which drivers who have
  not yet RSVP'd are directly notified. A value of 0 disables the last-notice ping entirely
  (the `> deadline_hours` comparison is skipped). Any non-zero value MUST be strictly
  greater than `rsvp_deadline_hours`.
- `rsvp_deadline_hours` (default 2) — hours before a round at which the RSVP choices
  become locked. A value of 0 means locking occurs at the scheduled round start time.

Any configuration command that would violate the invariant MUST be rejected with a clear
error. All three commands MUST be rejected if a season is currently `ACTIVE`.

#### RSVP Check-in Embed

At `rsvp_notice_days` days before a round, the bot MUST post an RSVP embed in the
division's configured RSVP channel. The embed MUST contain:

- **Title**: `Season <X> Round <X> — <canonical_name of track>` (or `Mystery` if the
  round type is Mystery and track identity is withheld).
- **Fields**: scheduled datetime as a dynamic Discord timestamp; location (canonical circuit
  name, or "Mystery" for Mystery rounds); event type (Normal / Sprint / Mystery / Endurance).
- **Driver roster**: a mini-list per team (including the Reserve team) showing each driver's
  display name alongside their current RSVP status indicator (empty brackets `()` if not
  yet responded; ✅ if accepted; ❓ if tentative; ❌ if declined).
- **Three action buttons** (horizontal): Accept (green ✅), Tentative (grey ❓), Decline
  (red ❌). Pressing any button updates the embed's status indicator for that driver
  atomically.

RSVP status MUST be persisted in **DriverRoundAttendance** rows and is authoritative for
all downstream operations.

#### RSVP Locking Rules

- **Full-time drivers**: RSVP choice locks at the `rsvp_deadline_hours` threshold. No
  changes are permitted after that point.
- **Reserve drivers**: RSVP choice locks at the scheduled round start time, but ONLY if
  they have accepted the check-in. A reserve who remains tentative or declined is
  locked out at round start regardless.
- After the RSVP deadline, the bot MUST process the reserve distribution (see below) and
  post a standby/assignment message to the division's RSVP channel.

#### Reserve Distribution

Once the RSVP deadline is reached, reserves who have confirmed `ACCEPTED` are distributed
to teams in the following priority order:

1. Teams with no full-time drivers seated at all.
2. Teams where at least one driver declined.
3. Teams where at least one driver failed to RSVP (no response).
4. Teams with a physically vacant seat while some full-time drivers are seated.
5. Teams that have already received a reserve this round. A team at tiers 1–4 MUST drop to
   this tier on receiving its first reserve, so that no team receives a second reserve while
   another team still needs one.
6. Teams where at least one driver is tentative.

Teams whose full-time drivers have all accepted and whose seats are all filled are NOT
candidates for distribution.

Within each priority tier, tie-breaking is applied in order:
1. Constructors' Championship position in that division (lowest-ranked team first). Teams
   with no standings snapshot yet — before the first round of a season, for instance — sort
   after every ranked team.
2. Alphabetical order of team name.

Reserves are picked in the order they confirmed `ACCEPTED` (earliest timestamp wins).
Every time a reserve changes their status back to `ACCEPTED`, their timestamp resets.

Reserves confirmed `ACCEPTED` who remain unplaced are classified as **on standby**. After
distribution is determined, the bot MUST post a message in the division's RSVP channel:
- Mentioning each assigned reserve by Discord user and stating which team they are
  racing for.
- Mentioning each standby reserve and informing them they are on standby and should be
  ready to substitute.

#### Attendance Recording

Once the initial round results are submitted (first `SessionResult` row accepted for the
round), the bot MUST populate attendance status in each `DriverRoundAttendance` row:
- A driver is counted as **attended** if they appear in any submitted session result for
  that round (any `DriverSessionResult` row for that round in that division), regardless
  of outcome modifier.
- Drivers seated in the Reserve team of that division for this round are excluded from
  attendance tracking.

Attendance points are NOT distributed at this stage; they are deferred to post-penalty
finalization (see below).

#### Attendance Pardon Workflow

A dedicated **Attendance Pardon** button MUST be available in the penalty wizard,
exclusively during the penalty review stage (NOT during the appeals stage). When pressed,
a modal form MUST request:

1. The Discord User ID of the driver being pardoned.
2. The type of attendance event being waived: NO_RSVP, ABSENT, or NO_SHOW.
3. A free-text justification (logged to the calculation log channel; never displayed
   elsewhere for privacy reasons).

Pardon validation rules:
- A NO_RSVP pardon requires the driver's RSVP status to be NO_RSVP.
- An ABSENT pardon requires the driver's RSVP status to be `NO_RSVP`, `TENTATIVE`, or
  `DECLINED`, and the driver to have not attended.
- A NO_SHOW pardon requires the driver to have been RSVP `ACCEPTED` and not attended.
- Each pardon waives only its matching penalty component.
- Multiple pardons for the same driver are permitted (e.g., both NO_RSVP and ABSENT can be
  waived to eliminate both penalties for a driver who did not RSVP and did not attend).

Staged attendance pardons MUST be displayed alongside staged penalties in the penalty
review summary.

After post-race penalties are approved, attendance pardons can no longer be applied for
that round.

#### Attendance Point Distribution

Attendance points are distributed once the post-race pen­alty results are finalised
(approved), to prevent erroneous automatic sanctions due to provisional result errors.
Points are awarded per driver per round as follows:

| RSVP status | Attended | Points gained |
|-------------|----------|---------------|
| NO_RSVP | Attended | `no_rsvp_penalty` |
| NO_RSVP | Did not attend | `no_rsvp_penalty` + `absent_penalty` |
| Any (ACCEPTED/TENTATIVE/DECLINED) | Attended | 0 |
| ACCEPTED | Did not attend | `no_show_penalty` |
| TENTATIVE or DECLINED | Did not attend | `absent_penalty` |

A reserve driver allocated into a full-time seat for the round (`assigned_team_id` set) who
RSVP'd `ACCEPTED` is scored on `no_show_penalty` alone. Reserves who were not allocated are
excluded from attendance scoring entirely.

Pardons waive the corresponding point award(s). A driver who receives a pardon for a given
event type does NOT accumulate points for that event. Net points per driver per round are
floored at zero; pardons never produce a negative total.

#### Attendance Sheet Posting

Once post-race penalties are approved and posted, the bot MUST post an updated attendance
sheet to the division's configured attendance channel. The post MUST:

- List all drivers in descending order of total accumulated attendance points (most first).
- Format each entry as `@mention — X attendance points`.
- Append the following footer at the end:
  > Drivers who reach `<autoreserve_threshold>` points will be moved to reserve.
  > Drivers who reach `<autosack_threshold>` points will be removed from all driving roles
  > in all divisions.
  If either threshold is disabled (value 0 / null), the corresponding sentence MUST be
  omitted.

#### Automatic Sanction Enforcement (Autoreserve & Autosack)

After attendance points are distributed, the bot MUST evaluate each driver's total:

- **Autoreserve** (`autoreserve_threshold`, default disabled): if a full-time driver's
  total attendance points meet or exceed this threshold, the bot MUST unassign them from
  their current team seat and assign them to the Reserve team of the same division,
  producing an audit log entry (Principle V). This action MUST NOT be applied to drivers
  already seated in the Reserve team.
- **Autosack** (`autosack_threshold`, default disabled): if a driver's total attendance
  points meet or exceed this threshold, the bot MUST remove them from all team seats
  across all divisions (equivalent to `/driver sack`), producing an audit log entry per
  division affected (Principle V). Autosack supersedes autoreserve when both thresholds
  are met simultaneously.

Both thresholds are evaluated in a single pass after point distribution. A threshold value
of 0 or null means the corresponding sanction is disabled.

**Rationale**: Reliable attendance management is essential for competitive fairness in a
multi-division league. A governed RSVP workflow with clear locking semantics, a proper
reserve distribution protocol, and an auditable point accumulation pipeline give league
admins a transparent mechanism to enforce attendance requirements without ad-hoc manual
intervention. Deferring attendance point distribution to post-penalty finalization prevents
incorrect automatic sanctions from provisional result errors.

### XIV. Image Generation Discipline (NON-NEGOTIABLE)

Image output is produced by filling a static SVG template with data and rasterising the
filled SVG to PNG. The following rules are non-negotiable:

**1. Templates are data, not code.**

Every image type MUST be backed by an SVG template file stored under `resources/defaults/templates/`.
Templates MUST NOT be generated, assembled, or emitted by application code. A template's
declared `width` and `height` are authoritative: the renderer MUST read the canvas from the
template root and MUST NOT assume a fixed canvas for any image type. Changing an image's
layout, colour, or dimensions MUST be achievable by editing the template alone.

**2. Fields are addressed by `@id`, with a layer label as fallback.**

A field is addressed by the `@id` of a node, and the identifier is normative. Where a template
declares no node bearing a field's identifier but declares a layer whose label is the name of
that field, the labelled layer MUST be taken for that field. Where both exist and are not the
same node, the node bearing the identifier MUST win.

The label fallback exists because templates are hand-authored in graphical SVG editors, where a
league manager sets a layer's label and never sees the identifier the editor generated. A
contract reachable only by hand-editing XML is a contract that will not be honoured.

The module MUST support exactly these fill operations, and no others:

| Operation | Target | Effect |
|-----------|--------|--------|
| Text fill | `<text>` / `<tspan>` | Replaces the element's text content |
| Image fill | element | Rewrites `xlink:href` to an asset path |
| Recolour | element | Merges a `fill:` declaration into the element's inline `style`, from a palette the template's stylesheet declares where the data decide it |
| Text fit | `<text>` carrying a declared box | Breaks the string into `<tspan>` lines within the box's line budget, reducing the field's size until it fits. Nothing is ever cut |
| Empty or remove | element, or its `_group` wrapper | Clears the text, or deletes the node and its subtree |
| Vertical crop | the root, at a declared crop point | Carries the footer group up and shortens what spans the cut, then rewrites the root `height` and `viewBox` to the crop point's `y` |

**The vertical crop.** Any image type whose capacity is fixed by the template (Rule 12) MAY
declare a crop point per member of its repeating collection, and the render MUST cut the canvas
at the crop point of the **last member the data fill**. This is what lets a classification drawn
for twenty drivers stop at row twenty rather than carry thirty rows of empty canvas, and a
calendar of eight rounds end after the eighth.

A crop point is a **valueless** field (Rule 3): geometry the render reads, never text it writes.
Its absence from a template that claims one is a fault of the template; its never carrying a
value is not.

Where the template declares a **footer group** — a band of static chrome standing beneath the
repeating collection, such as a caption naming the graphic — the crop MUST carry that group up
by the difference between the declared canvas height and the crop point, and MUST do so *before*
rewriting the height and `viewBox`. A graphic drawn short therefore keeps the band beneath its
rows instead of losing it off the bottom, and a template declaring no footer group is cropped
exactly as one always was. It follows that the crop point of the **last member the template
declares** is expected to sit at the declared canvas height: a template whose last crop point
does not still draws every smaller division correctly and MUST NOT be refused, but the
divergence MUST be reported as a notice (Rule 4).

An element **spanning** the crop point MUST have its lower end carried up by that same
difference, so that it keeps the distance from the foot of the canvas at which it was drawn. A
rule ruled down the repeating collection — the separator between the round columns of a standings
grid or an attendance sheet — therefore stops above the footer band of a graphic drawn short
exactly as it does of one drawn whole, instead of running on to the cut and through the band the
crop has just carried up. An element already reaching the foot of the canvas reaches the new
foot, which is what rewriting the height alone did to it. Without this the crop keeps only half
its promise: a graphic drawn short is meant to be the graphic drawn whole with the empty members
taken out, and it is not, in the one place a league looks.

This MUST be applied to a line, a rectangle, and a path drawing one straight vertical rule and
nothing else, those being the forms in which a template rules one. A path drawing anything more
MUST be left as it stands, its shape not being one whose lower end can be moved without reading
it. A transform that purely translates MUST be followed, an editor placing its artwork inside a
positioned layer as a matter of course; an element under a scale, a rotation or a matrix MUST be
left as it stands, its own coordinates not being those of the canvas. An element within the
footer group MUST be left as it stands, that group being carried up whole, and so MUST an element
within a definition — a clipping path, a mask, a symbol, a gradient — whose geometry is borrowed
by whatever refers to it and is not drawn where it stands. An element left as it stands is
clipped by the shortened canvas, as every spanning element was before this rule.

Declaring crop points is OPTIONAL for every image type but the calendar, which requires them. A
template declaring none MUST render at its full declared height, which is what keeps a league's
existing hand-authored template working unchanged.

Recolour MUST be merged into the existing inline `style` rather than written as a
presentation attribute or as a `style` replacement, so that template-declared styling on the
same element survives. Recolour MUST NOT count as addressing a field: a recoloured field that
carries a value MUST still be filled.

A **valueless** field (Rule 3) is exempt from that obligation, being drawn by its colour alone.
It MUST be neither filled nor asked for a value, and its absence from the values a generation
determines MUST NOT be read as unresolved — exactly as a vertical crop point is neither filled
nor asked for one. Its absence from a template that claims it remains a fault of the template.

Where the colour a recolour applies is decided by the **data**, the palette MUST be read from
the template's own stylesheet by documented class names, and MUST NOT be held in bot
configuration. A kind for which the template names no rule MUST NOT be painted, and the field
MUST be left as the template drew it. A league states the appearance of its graphics in the
template and nowhere else, which is also what lets a paint the module has no vocabulary for —
a gradient, a pattern — be used without the module gaining one.

**Removable groups.** Any field, mandatory or optional, MAY be wrapped in a group named for that
field followed by `_group`. Where such a group is declared, it MUST be removed in its entirety
wherever the rules would have the field emptied or removed, and the field itself MUST be left
untouched; where none is declared, the field alone is emptied or removed. The group exists so
that the static chrome standing around a field — a label, a separator, a card, a plate — leaves
the graphic together with the value it introduces. Removing a group MUST NOT resize the canvas:
a block that may be removed belongs where its removal is survivable.

A group is ordinarily optional, being chrome around a field. An image type MAY instead declare a
group **mandatory** in its catalogue (Rule 10), where the block it wraps is one the template must
provide and the data may none the less have nothing to put in it — a reserve team every division
holds and many divisions never field. Such a group is a field of the template for the purposes of
Rule 3: its absence from the template fails the render, while its removal when the data are empty
is the ordinary behaviour of a group and is not a failure.

**What a group may wrap.** A group wraps one of exactly these, and the image type's catalogue
(Rule 10) names which and states the condition on which it is removed:

| Form | Named | Removed when |
|---|---|---|
| One field | `<field>_group` | the rules would have that field emptied or removed |
| A member of a collection | `<collection>_<x>_group` (Rule 11) | that member is not drawn |
| A **block** of fields standing or falling together | for the block (`fastest_lap_group`) | the condition the block depends on does not hold |
| A **column** — the same field across every member | `<field>_group`, bearing no discriminator | that field is emptied for **every** member, and never while one member holds a value |
| A **column of a collection of columns** | `<collection>_<z>_group` (Rule 11) | that column is not drawn at all |

A column group wraps the static chrome of a column — its heading, its rule, its plate — and MUST
NOT contain any member's cell: a cell belongs to the member it stands on and leaves the graphic
with that member's group. The two forms therefore never contend for the same node. A template
declaring no column group carries its heading over an emptied column, which is a template's choice
and not a fault.

Where the columns of a grid are themselves a **collection** — the rounds of a season drawn across a
classification — the column group bears that collection's discriminator and is removed when the
column does not exist, rather than when its field is emptied for every member. This is the column
form at a second dimension and carries the same prohibition, for the same reason stated more
sharply: a cell of such a grid belongs to its row and to its column both, and a node of an SVG file
has one parent. The cell therefore lives under the **row's** group, the column group carries the
heading and the chrome alone, and the removal of a column reaches its cells through the rule of
Rule 12 rather than through containment.

A block group MAY stand inside a member and bear that member's discriminator
(`row_<x>_position_change_group`), the block form and Rule 11's nesting composing as any two forms
do.

**3. Every mandatory field MUST be resolved.**

An image type's field catalogue (Rule 10) classifies each field **mandatory** or **optional**.

- A render MUST fail if a mandatory field is absent from the template, or if its value cannot
  be determined from the data.
- An optional field absent from the template is not a failure. An optional field whose value
  cannot be determined is not a failure either: the field is emptied, or its `_group` removed.
- A render MUST fail if the data supplies a field the template does not declare.
- A field taken off the canvas by a group removal or a vertical crop is not unresolved.
- A value the data **determine to be empty** is determined. A mandatory field is offended only
  where its value cannot be determined, never where the data determine it to be nothing: a
  sanction field of a phase not yet closed, a seat no driver occupies, the gap of the entry that
  set the reference lap. Such a field is drawn empty, is not a failure, and raises no notice —
  Rule 4 reserving its notice for a value that could not be determined at all.
- Where the text path draws a placeholder for a value that does not apply — a dash — the graphic
  **empties** the field rather than drawing the placeholder, unless the image type names a field
  it draws otherwise. An **image** field has nothing to empty: emptying one means removing it, or
  drawing the class's fallback where the catalogue declares that (Rule 13).

Mandatory and optional classify **fields of the template**, and nothing else: whether the
template must declare the field, and whether its value must be determinable. They say nothing
about the *assets* placed upon fields. An asset that resolves to no file is governed by Rule 13
alone, whatever the classification of the field receiving it.

**A classification MAY vary by member within a collection**, where the catalogue says so and says
it by a rule rather than by an enumeration. The first member of a collection whose length no
configuration bounds may be mandatory and every member beyond it optional, so that the template is
obliged to declare the block at all without being obliged to declare a fixed number of it — the
first reserve seat of a lineup is of this kind. This is still exactly two classifications; it is
the *scope* over which one is declared that narrows.

**A collection MAY be optional as a whole.** A catalogue MAY declare a named collection — together
with every collection nested inside it and every field of them — optional **as a unit**, a template
declaring none of it drawing the graphic without that part entire. The results grid of a standings
graphic is of this kind: a template declaring no round draws a classification alone, and is not
faulty for it. A field the catalogue classifies **mandatory within** such a collection is mandatory
only where the template declares the collection at all — the number of a round MUST stand on every
round a template draws, and a template drawing no round owes none. This is the *scope* narrowing
again and not a third classification: the field is mandatory, over the members that exist.

The catalogue MUST name the collection at which the optional portion begins (Rule 10). A portion so
declared is all-or-nothing only in the sense that its mandatory fields bind once any member of it is
declared; the optional fields within it remain individually optional as they would anywhere else.

**A sibling's field is a problem.** A template declaring a field belonging to a **sibling** type's
catalogue is a problem, detected at the moment the template is named. It is the wrong file in that
slot, and rendering it would draw one session's columns under another's headings. An id belonging to
**no** catalogue is not a problem and is not the module's business: a hand-authored SVG carries
identifiers on every node it holds, and only the ones a catalogue claims are fields.

Two image types are siblings where **either** holds:

- they draw one **output aspect** — qualifying and race results, driver and constructor standings,
  the six forecasts;
- they are the several graphics of one **source module**, whatever they draw. The attendance sheet
  and the check-in graphic have not one field in common — the sheet draws a record of a season and
  the call draws a round about to be run — and are siblings all the same.

The second is not the first weakened. The fault the rule catches is a file in the wrong slot, and
the files a league is likeliest to swap are the ones it authors in one sitting and configures with
two adjacent commands. Common content is what makes a swap *plausible*; common provenance is what
makes it *possible*, and only the latter is the test. Types of two different modules remain
unrelated: a calendar template declaring a lineup's field states nothing about a calendar, and the
sentence above governs it.

**A value the data does not hold literally is still a value.** Where a round, a session or an
entry is of a kind for which the underlying record carries nothing — a round whose track is
deliberately concealed until it is run — the image type MUST define the value that stands for
that kind, and that value is what fills the field. It is filled, not exempted: the text is the
literal the type defines, and an asset is resolved from it by the ordinary slug rule of Rule 13.
No field is emptied and no exemption arises, because there is nothing missing — the kind *is*
the datum.

**A kind of record that has no such thing at all empties the field.** The paragraph above governs a
thing the record **has** and withholds — a track concealed until the round is run. Where the kind has
**no such thing** — an attendance sanction, which pertains to no session because none was run against
it — there is no value to define, and the field is **emptied**, its `_group` removed where one is
declared. The field's classification is untouched: a mandatory field of this sort must still be
declared by the template, and the data determine its value to be nothing, which this Rule already
holds is determined.

The graphic MUST NOT fill such a field with a label naming the kind. Where another field of the same
graphic already names the kind — a verdict's stage reading "Attendance Sanction" — writing that label
into the slot of the absent thing says it twice, the second time under a heading it does not answer.
The text path MAY none the less carry the label there, a single-line heading having no other place to
put it; that is a difference in what the two **arrange**, not in what they say, and Rule 7's one
rendering is untouched.

**A kind of record MAY instead have an image type of its own.** Where the graphic's principal
collection has no members at all for that kind — a round of the mystery format, which runs no session
and for which no forecast is computed — the module MAY give the kind its **own template slot**,
drawing what its own posting says, rather than a defined literal in a slot shared with every other
kind.

Which of the two an image type takes is decided by whether the **posting** differs. A calendar and an
attendance sheet draw a mystery round as a row among rows, so they take the literal. The weather
module posts a notice that no forecast is coming, which shares with a forecast only its heading fields,
so it takes a slot of its own. The paragraph above governs the first and this one the second, and
neither admits an exemption: a type of its own is an image type like any other and draws every field
of its catalogue in full.

**4. Problems and notices are distinct outcomes.**

- A **problem** is a disagreement between template and data (an unresolved mandatory field, an
  unknown field, a missing template, an asset that resolves to no file in a class carrying no
  fallback, a collection larger than the template's declared capacity, a wrapped field the template
  gives no room or no leading to lay out (Rule 5), rasteriser failure). A
  problem MUST abort the render; no partial image may be posted.
- A **notice** is a non-fatal degradation the render survives (a substituted font, a wrapped
  field reduced to its size floor and cut, a single-line field cut to its declared
  `inline-size`, a fallback image standing in for an asset that resolved to no file, an optional
  field emptied because its value could not be determined). Notices MUST NOT abort the render.

**Configured absence raises no notice.** Where an optional value is absent because the league
switched off the collection of that value at its source — not because a gap was left in data the
league does collect — the field is emptied or its group removed and **no notice is raised**.
Nothing has degraded: the graphic is drawing exactly what the league configured it to draw, and a
notice would report a setting back to the person who chose it, once per member, on every render.

The suppression is narrow and MUST be justified per field in the image type's catalogue. It
requires a configuration switch that turns the datum off at its source — a lineup draws no flags
at all when a league has switched nationality collection off, and that is a legitimate graphic. It
does **not** extend to a value the league collects and merely happens not to hold for one member,
which is an ordinary emptied optional field and raises its notice.

**Where each is reported.** A notice MUST be reported to the calculation log channel
(Principle V), and additionally alongside the output of the command where a command triggered
the generation. No problem and no notice may ever be reported in a channel the drivers of the
league read.

**How they are reported.** The notices of one generation MUST be reported in one message and not
in one message apiece, grouped by kind, with notices identical to one another counted rather than
repeated. A generation degrades in the same manner as many times as it draws the field that
degrades, and a line for each buries the one notice that differs among those that do not. A notice
standing alone MUST name the field it was met upon; one repeated names how many times it was met in
place of the fields. The message MUST name what was being drawn, the kind of template alone not
distinguishing one posting from another in a log holding many. Where a command triggered the
generation, its output MUST carry a link to that logged message, which is therefore written before
the output is composed; a log channel that cannot be written to costs the link and nothing else.
This governs presentation only: the notices of a generation are grouped for the message and
not merged, the render having met each one of them.

**Rejection at the earliest moment.** A problem traceable to something a user configured or
commanded MUST reject that input wherever the module is in a position to detect it: a command
naming a template that carries one is rejected and the configuration left as it stood; a season
review that meets one fails validation of the season, naming what is at fault; a command that
would carry a division past what its templates can draw is rejected and its change not applied;
a command that triggers a failing generation is rejected and nothing posted in consequence.

**The unit of failure is one graphic.** A problem abandons the render it was met in, and that
render alone. Where one event produces several graphics — the sessions of a round, the two
championships of a standings posting, the divisions of a season — the failure of one MUST NOT
prevent the others from being generated and posted, and each answers for itself under Rule 7,
falling back to text or rejecting the command that asked for it.

**5. Text bounds are declared by the template.**

Every field that may receive a value of a length the league does not control — a Discord display
name, a circuit, a country, a grand prix, a date — MUST declare a **box**. Overflow MUST NOT be
silently clipped by the rasteriser, and a field declaring no box is drawn as a single unbounded line
that will run across whatever stands beside it.

A box is declared in one of two ways, and where a field declares both, the rectangle wins:

- **In CSS.** The width is the field's `inline-size`; the height is its `max-lines` multiplied by
  the `line-height` resolving upon it; and the box is positioned so that its vertical centre is the
  field's declared `y`. A field taking a single line therefore sits exactly where the template drew
  it, and one taking two grows half a line either side of that same point. No node is added to the
  template to say so.
- **By rectangle.** The field declares `shape-inside` naming a rectangle of the template, which is
  the extent of the field. That rectangle carries neither fill nor stroke and is never itself drawn.

**`max-lines` is the field's line budget.** Where it is declared it IS the budget, whichever way the
box was declared. Where it is absent, a field declaring `shape-inside` takes the lines its rectangle
admits — its height divided by the line height in force — and a field declaring neither takes one
line.

**The fitting contract**, which every image type inherits:

- The text is broken **first at the line breaks its author entered**, and each piece so obtained
  broken again at word boundaries into lines no wider than the box. A break the author entered
  begins a line of the field; a run of them leaves the blank lines between, and each blank line counts
  against the field's budget as a line of text does. Prose written in paragraphs is drawn in
  paragraphs.
- A word wider than the box MUST be broken **within itself** rather than allowed to run across what
  stands beside it.
- Each line carries the horizontal coordinate and the anchoring the field declares, and each line
  after the first is offset from the one above it by the **line height in force** — the `line-height`
  resolving upon the field, whether declared on it or inherited by it.
- Where the text set at the template-declared size occupies more lines than the budget, the field's
  size is set down by half-pixel steps and the text broken again, until it fits.
- **The text is never cut.** No field is truncated and no ellipsis is drawn. Whatever the league
  entered is drawn in full, however small the field must become to hold it.
- Below **half** the template-declared size a field is no longer the size its designer intended, and
  a notice MUST be raised naming it. That floor **stops nothing**: the reduction continues past it
  until the text fits. A hard stop of one pixel guards against a box of no usable width, which no
  reduction could satisfy.
- Line height MUST scale with the reduced size. Where the budget is the rectangle's rather than a
  declared `max-lines`, the admissible count MUST be recomputed at the reduced leading. A field set
  smaller therefore holds **more lines**, rather than the same number more widely spaced, and the
  reduction can win room where otherwise it could only narrow lines it was already limited to.
- The lines are **centred vertically within the box**, so that a field taking one line and a field
  taking two sit alike and a template reads as one design whether or not a value happened to wrap.
  Fields declaring `shape-inside` are the exception and are laid from the top of their rectangle:
  those carry prose — a steward's description, a steward's justification — and prose floating in the
  middle of a box it does not fill reads as a mistake rather than as a design.
- Each field is fitted **on its own**. The canvas is not resized, and no other field follows the
  size of the field reduced.
- `shape-inside` MUST be removed from the field once its lines are laid out, the rasteriser otherwise
  re-flowing text the module has already set.

**Why the cut was withdrawn.** A truncated value is a wrong value drawn confidently: "Autodromo Enzo
e Dino Ferra…" names no circuit, and a league reading it cannot tell whether its data or its template
is at fault. A reduced value is the right one, merely smaller, and the notice tells the league which
field is under pressure. The module would rather draw a league's own words small than draw most of
them.

**Three template defects are problems** (Rule 4), and all three are **structural** under Rule 9 —
read off the template alone, needing no data — so each is complete at every one of the three moments
and refuses at each with the severity that moment carries. Each MUST be reported naming the field at
fault and distinguishably from the other two:

- a `shape-inside` naming a rectangle the template does not declare;
- a field upon which no `line-height` resolves, where one is needed to fix its budget or its
  leading. A default leading substituted here would silently decide how much of a league's prose is
  drawn, which is the template's decision and not the module's;
- a box of **no usable width**, or a `max-lines` that is not a positive whole number. Either is
  named, and exists, and still gives the text nowhere to go: there is no measure to wrap against, or
  no budget to count lines from.

The third is the one a reader would think redundant and is the one that cost most to find. A named
rectangle with no extent is not an absent rectangle — every check for the first passes — and the
natural implementation degrades to a single unwrapped line, which draws a steward's prose straight
across the graphic and **reports nothing**. That is the worst outcome the whole of Rule 4 exists to
prevent: not a render that fails, but one that succeeds and is wrong. A defect that cannot be
distinguished from soundness by any check the module makes is exactly the kind that has to be named
explicitly.

**Measurement.** A text's width is measured against the font family, weight, style and size the field
declares. Where that font is not installed on the machine, the measurement MUST be made against the
face the rasteriser would substitute and a notice raised naming the field and the font. The
measurement need not agree exactly with the width the rasteriser draws, which applies kerning and
shaping it need not; it MUST **err narrow**, so that a line the measurement admits is a line the
canvas holds.

**The module places no ceiling on free text.** Where a field carries prose a person wrote — a
steward's description, a steward's justification — no length limit is imposed at its source, and the
answer to a long one is the reduction above and nothing else. It is for the league to declare a
rectangle the longest such text its people write will fit at a size worth reading, and a template
giving unbounded prose a single unwrapped line is relying on it staying short.

**6. Assets are aspect-authored, never padded by the generator.**

Assets under `resources/` MUST be plain SVG with no `clipPath` and no filter, and MUST
be authored at exactly the aspect ratio of the slot they fill, padded with transparent margins
by their author where the subject does not fill that aspect. The generator MUST NOT pad or
letterbox an asset. A league supplying its own assets is bound by the same requirement, and
the module MUST document it wherever asset upload is offered.

**A gradient is permitted**, the prohibition on one having been withdrawn in v7.4.0. It was
asserted here from the first and never once justified — unlike the aspect requirement in the
same sentence, which carries the Rationale below — while the module's own templates depended on
gradients throughout. The hazard it might have guarded against was tested on 2026-08-31 and does
not exist in this pipeline: two assets whose gradients carry the *same* identifier render
independently, the rasteriser drawing each referenced file as its own document. `clipPath` and
filter are untouched, as is the requirement that an asset carry no text.

**One class carries one aspect throughout a single template, and every non-stretching slot of
that class on that template MUST carry it** (relaxed v7.10.0). A template declaring a slot at an
aspect its class does not carry on that template is invalid, and Layer 2 MUST refuse it, naming
the offending field and reporting it as a fault of shape rather than as a missing field.

**The aspect itself is the league's to choose, and this Constitution names no number.** The
reference MUST be read from the template being validated — the aspect the greater part of that
class's slots on it declare — and never from a table in the module. A template drawing every flag
slot at 2:1 is valid; one drawing most of them at 2:1 and one at 1:1 is not. Until v7.10.0 the
module asserted a number and refused the first of those as readily as the second.

**Agreement between templates MUST NOT be required, and MUST NOT be checked.** A class drawn by
several templates MAY be drawn at a different aspect on each, and a league doing so has one file
letterboxed wherever the aspects differ, unremarked. Requiring agreement would refuse the first
template of any re-shaping — the others still disagreeing with it — so no league could move a
class off the aspect it began with. The consequence MUST be documented to leagues rather than
refused.

**A slot of a stretching class MAY instead declare that it stretches**, in which case the rule
above does not bind that slot (v7.5.0, narrowed v7.9.0). It declares this by being authored
`preserveAspectRatio="none"`, so the asset is drawn to the box the template gives it rather than
fitted inside one. The letterboxing the one-aspect rule exists to prevent therefore cannot arise,
which is the whole of why the exemption is sound; the generator still MUST NOT pad. What follows
is that the artwork is the league's to draw for a shape that varies, and the module MUST say so
wherever it documents the data drawn into such slots.

**Two enumerations MUST exist, and both are closed** (v7.10.0, widened v7.11.0). The first names
the classes **held to one aspect** within a template; the second names the classes **whose slots
may declare that they stretch**. A class MUST NOT be named by both, which would be
self-contradictory, and a test MUST refuse that.

**A class MAY be named by neither, and only where it is declared shapeless with its ground**
(v7.11.0). Under v7.10.0 every class had to be named by exactly one, on the reasoning that a class
in neither would be checked against nothing at all. That reasoning holds against a class falling
out by *omission*; it does not hold against one deliberately placed outside both. The requirement
is therefore not that every class be enumerated, but that no class leave the check unremarked: the
exemption MUST be **declared**, and the test MUST refuse an **undeclared** omission exactly as it
formerly refused any omission.

The declaration MUST be written in two places — beside the code the exemption governs, and beside
the test that would otherwise catch it — each carrying the ground. One place would let a later
edit lift the exemption into effect while the test still described the old state; two mean a class
can escape the check only by someone recording why, never by being forgotten.

A class MUST NOT be admitted to the stretching enumeration because one template found its aspect
inconvenient — only where the data it draws genuinely have no shape of their own, the box being
decided by the template rather than by the subject. A class MUST NOT be left out of the first
enumeration for that reason either. It is left out on one of exactly two grounds, both stated
below and neither of them convenience: that the class serves slots of several shapes at once and
no one aspect could serve them, so there is nothing for its slots to agree on; or that its artwork
is supplied per datum rather than per class, so slots of differing shape are each answered by a
file of their own.

**A class qualifies for that exemption on one ground and no other** (v7.11.0): that a league
supplies **one file per datum** of the class rather than one file for the class, so that slots of
differing shape are each answered by artwork of their own. The one-aspect rule exists because a
single file is drawn into every slot of its class and is letterboxed wherever a slot disagrees;
where the artwork is per-datum, that reasoning has nothing to bite on and two slots of differing
shape are each drawn correctly.

This MUST NOT be read as licence to exempt a class whose aspect a template merely found
inconvenient. It is not enough that a template author would prefer two shapes; the class must be
one whose data each carry their own file, so that both shapes are genuinely served.

**The `division_logo` class is held to no aspect on that ground** (v7.11.0). A league draws one
logo per division, so two logo slots on one template are answered by two files of that division's
own and neither is letterboxed.

**The `marker` class is held to no aspect at all** (v7.10.0), on a different ground, which is why
the two MUST NOT be collapsed into one rule. It draws the square markers of a change of standing
position and the stretching marks of a standings result cell and of an attendance total out of one
directory, and no one aspect serves all three. A marker slot at an aspect its fellows do not share
MUST be permitted, and is letterboxed. This is a widening of
v7.9.0, under which the class carried an aspect its non-stretching slots were held to; the cost —
that a movement marker drawn to the wrong box is now distorted with nothing reported — is
accepted, one directory serving three shapes admitting no better answer.

**A slot outside the stretching enumeration authored `preserveAspectRatio="none"` MUST be refused
on that declaration alone** (v7.9.0, made a standalone obligation v7.10.0). Layer 2 MUST discharge
this check independently of any aspect comparison and MUST NOT rely on the comparison to catch it,
naming the offending field whatever aspect the slot declares beside it. The reliance was sound
only while the aspect came from a fixed table: with the reference read from the template, a
template declaring *every* slot of a class stretching agrees with itself, is passed over by the
comparison, and would be told nothing.

**The exemption is claimed by the slot and never by the class** (v7.5.0, qualified v7.9.0 and
v7.10.0). Every class MUST be named by exactly one of the two enumerations above, and a class
present in the asset directory table but absent from both — or present in both — is an omission a
test MUST refuse. Within a stretching class the declaration
`preserveAspectRatio="none"` remains the sole mechanism by which the check is skipped, and it is
made where the fact lives — in the template, by the author who chose the box — so it cannot be
forgotten in the way an absent aspect could, and it cannot silently disarm the check for slots of
the same class that do not stretch. Membership of the enumeration is **permission to claim** the
exemption and never the exemption itself: a slot of a stretching class that does not declare it is
held to its class's aspect exactly as any other slot is.

A single class MAY therefore serve slots of differing shape provided it is one that stretches and
every such slot declares that it stretches, and MAY do so alongside slots of its own aspect that
do not. This is what allows one closed-set class to carry both artwork with a fixed shape and
artwork drawn into a box the template decides.

**Rationale**: a league authors **one file per datum of a class**, and the rule above forbids the
generator to pad. A class serving slots of two aspects would therefore letterbox that one file
wherever it did not match, and no artwork a league could supply would answer it — the same
`united_kingdom.svg` cannot be correct in a 3:2 slot and a 1:1 one at once.

**What that argument compels is agreement, and never a number** (corrected v7.10.0). It is
indifferent to which aspect the slots of a class settle on, requiring only that they settle on
one; a league drawing every flag slot at 2:1 satisfies it exactly as one drawing them all at 3:2
does. Until v7.10.0 the module asserted a number anyway, binding a template a league had authored
itself against artwork the league had authored to match it. It is also indifferent to what a
*second* template does, one file being letterboxed only among the slots of one graphic — which is
why agreement between templates is left unchecked. Per-slot authoring
(the paragraph above) and per-class files are only reconcilable if the class is uniform, so this
is that rule carried to its conclusion rather than a second one. A stretching slot escapes the
argument entirely rather than weakening it: the file it draws is not fitted to a shape at all,
so there is no shape for it to be wrong about.

The enumeration exists because that escape is only available to data that genuinely have no shape.
Left open to every class, the declaration became a way for a template to opt out of a check that
was protecting it: a driver portrait slot authored at any ratio and told to stretch drew every
face in a league distorted, and was refused nothing, because the slot's own claim was taken as
sufficient. A class that carries an aspect means it, and the slot does not get to say otherwise —
a subject with a shape of its own cannot be drawn to a box that disagrees, however the box is
declared.

**A class held to no aspect MUST STILL NOT declare that it stretches** (v7.11.0). The stretching
enumeration remains exclusive to `marker`, and a slot of a shapeless class authored
`preserveAspectRatio="none"` MUST be refused as any other slot outside that enumeration is.
Freedom from one aspect is freedom to choose the box, not licence to distort what goes in it: the
artwork still letterboxes, and a league's crest squashed to a box it does not fit is exactly the
outcome Rule 6 exists to prevent.

**The artwork the module itself supplies carries a fixed aspect per class, which a league cannot
alter** (v7.10.0). That artwork stands in for any datum a league has not drawn, so a league that
re-shapes a class keeps receiving it at the shape it was authored at. Where such a file is drawn
into a slot of another aspect it is stretched, and the module MUST raise a notice naming both
aspects and saying that supplying the league's own file for that class answers it. The aspects the
packaged artwork carries are a fact about that artwork and MUST NOT be read as a rule binding a
league's templates.

**Two classes need not match each other, and the flag and track classes deliberately do not.**
A country flag is drawn at 3:2 and a circuit map at 1:1, wherever each appears. A template drawing
both — which only the calendar and the check-in graphic may (Rule 13) — places two slots of
differing shape, and that is the business of whoever authors it. The constraint is *within* a
class, never *across* two.

The aspect a class carries is not fixed by this Principle. It belongs to the asset documentation a
league reads, and changing one is a change to every template declaring that class's slots.

An asset MUST be referenced by an href that is a **URI**, and that URI MUST be **absolute**. A bare
filesystem path is not a URI at all, and a *relative* reference is the more dangerous of the two
because it looks as though it works: the rasteriser reads the filled document out of a working
directory of its own and resolves the reference against **that** directory, so a path every check
upstream confirmed — the module having resolved it against the project root — reaches the rasteriser
as a file that is not there. A relative reference MUST therefore be resolved against the project
root before it is placed on a field.

**The module MUST refuse to produce a graphic that links an image absent from the host**, naming the
element and the file it sought. This binds an `<image>` a league authored into its own template as
much as one the module placed: the former receives no asset and is read by no other check, so
nothing else in the pipeline would ever look at it. The refusal is required because the rasteriser
reports **nothing whatever** — Inkscape 1.4 exits 0 with an empty stderr for an href it cannot
follow, and draws a PNG byte-identical to one drawn from an href naming a file that never existed.

Until 2026-08-31 no render-time check caught any of this, and it was the single most likely way for
a correct-looking SVG to rasterise wrongly. That is why Rule 14 exists, and Rule 14 stands unchanged
for the cases still uncaught by any check: flowed text, substituted fonts, and the crop.

**7. Image output is additive.**

Image generation MUST NOT replace or alter any existing text output path, which MUST remain
functional when the module is disabled or a render fails.

**A graphic carries at least what the posting it replaces carried, and MAY carry more.** This is what
*additive* means, and it is a **floor** and not a ceiling. Turning a league's images on MUST NOT cost
that league anything its text posting told them — save what Rules 15 and 16 say a picture cannot
carry, which is the closed list of two this Principle admits and the one place a graphic is permitted
to say less.

**There is no matching ceiling.** A graphic MAY draw what the text path has never published anywhere:
a flag beside a name it prints, the stage at which a verdict was issued, the gap between two totals no
table has a column for. A picture has room a message has not, and denying it the use of that room
would buy a reader nothing while costing them the part of the graphic that made it worth drawing.

What a failed render does next depends on who asked for the posting:

- A posting **no user commanded** — one reached at a horizon, at a schedule, or at startup —
  MUST fall back to the text output rather than producing no output at all.
- A posting a user **commanded** MUST NOT fall back. The command is rejected, nothing is posted,
  and the caller is told what is at fault and invited to correct it.

A user standing at the keyboard is the one person able to fix the template; silently posting
text in place of the graphic they asked for would deny them the chance and hide the defect until
it next fires unattended. The reverse holds for a scheduled posting, where there is nobody to
tell and the league still needs its information.

**A fallback may therefore say less than the graphic would have.** Where the substitute is posted,
whatever the graphic would have added beyond the text path's own vocabulary is simply not said. This
is accepted, and it is why a fallback is a fallback rather than an equivalent: the league is told
everything it would have been told had images never existed — the floor above, in full — and is not
told the surplus a working render would have drawn. An image type MUST NOT answer this by holding its
surplus back. Levelling every graphic down to what a message can say would forfeit the whole reason to
draw one, to spare a reader a difference they only ever meet when something has already gone wrong.

**Additive means adding no precondition either.** The generation and the posting of a graphic MUST
NOT prevent, delay or condition anything the source module would have done without it. The
enforcement of an autoreserve or an autosack sanction, the opening of a round's attendance rows, the
finalisation of a review, the posting of the message the graphic rides on — each MUST complete
exactly as it would with the module disabled, and a render that fails MUST find that work already
done. A graphic is downstream of every state change it depicts and is never upstream of one.

The rule needs stating because the natural way to write a posting is to build the message and send
it whole, which quietly makes a rasteriser the gate on a league's sanctions. Rule 4's unit of failure
says a broken graphic costs at most one graphic; this says it costs at most a *picture*, and never a
consequence.

**A graphic that displaces nothing.** An image type MAY add a graphic to a posting from which it
takes no part — the check-in call keeping its role mention, its embed, its roster, its status
indicators and its three buttons entire, and the graphic restating the embed's heading rather than
relieving it of anything. Such a type is the purest case of this rule and not an exception to it.
Two consequences follow: its fallback is the message posted **without the attachment**, nothing else
changing, there being no text to restore that was never given up; and its toggle alters the textual
flow in no respect whatever, an image carrying no button.

**And a graphic MAY displace all but what a picture cannot carry.** The verdict graphic stands at the
opposite pole from the check-in call: the announcement's heading, its driver line, its sanction, its
description and its justification all move onto the canvas, and the message it rides on keeps the
**mention alone**. Rule 16 decides what must stay behind — a mention being a thing a picture cannot
carry — and the paragraphs above decide what is restored when a render fails, which is the
announcement entire. Displacing nothing and displacing all but the unpicturable are the two ends of
one rule and not two rules, and an image type states for itself where between them it falls.

**A fallback is at the grain of the graphic that failed.** Where one event draws several graphics
(Rule 4) and the text path's natural message is **coarser** than one graphic — one standings message
carrying both championships where the graphic posts two — the text substitute MUST cover the failed
graphic's scope and that scope alone, and MUST NOT re-post what the surviving graphics already
carry. The text path MUST therefore be able to emit the proper subset. Where it cannot, that is a
defect in the text path to be repaired, and never a licence to post the whole: a league that got its
constructor standings as a picture and its driver standings as text has been told each thing once,
which is the point of falling back at all.

**One rendering, two presentations.** Where the graphic draws a value the text path also draws — a
lap time, a gap, an interval, a time penalty, a points total, a session label — the two MUST be
produced by one and the same formatting code, which the utility calls and MUST NOT restate. A
change to how the text path renders such a value is a change to the graphic by the same stroke, and
no image type may hold a private rendering of a value that is not private to it. The graphic is a
second presentation of one output, not a second output: a value derived at generation — a qualifying
gap worked out from two recorded laps — is derived by the code that derives it for the table.

The exception is a value a rule of this Principle requires the graphic to draw *differently*. That
list is closed at two: the zone of Rule 15, and the fixed renderings of Rule 16.

**The floor is measured against the source module's output entire**, never against the single message
the graphic is attached to. A graphic MAY therefore gather onto one canvas what the text path said
across three messages: the likelihood of rain computed and posted at phase 1 stands on the phase 2 and
phase 3 graphics, where neither of those messages carries it. Gathering is **arranging**, which is the
whole of what a second presentation does.

**What a graphic MUST NOT do is decide.** With no ceiling on subject matter, this is the prohibition
carrying the weight, and it falls on *computation* rather than on information:

- **A value requiring a rule is the source module's, and the graphic reads its result.** An ordering,
  a tie-break, an eligibility, a points award, a sanction — anything **decided** rather than measured
  or read — MUST NOT be worked out for a graphic. The countback separating two entries level on points
  is not the graphic's to apply; the gap between their totals is the graphic's to subtract.
- **A derivation lives with the data, not with the graphic.** Where a graphic draws a value the text
  path has no column for — the difference between two points totals, the distance between two recorded
  positions, the direction of that distance — the working out MUST be written in the service of the
  module owning the figures, never in the image utility. The text path can then take up the column
  whenever it wants it, without a second implementation, and no image type holds a private rendering
  of a value that is not private to it.
- **A second record of the same kind is read, not recomputed.** Where such a derivation reads the
  standings of the round preceding the one drawn, it reads them as the source module persisted them.
  The graphic compares two facts; it does not re-establish either.

Together these are the whole of the constraint. A graphic may **arrange** anything the bot holds, may
**measure** across it, and may **depict** it in whatever medium its template gives it; what it may not
do is **settle** anything. The line runs between a picture that presents a league's season and a
picture that quietly becomes a second authority on it, and it falls exactly where a rule would have to
be applied to reach the value.

**Two consequences worth naming**, each of which reads as an exception only if the ceiling is imagined
back into place:

- **Imagery that identifies.** A flag, a badge, a livery, a portrait — an image standing for an entity
  the graphic already names — is drawn freely, and obliges the text path to publish no column it has
  never had. It settles nothing; it depicts what the text already named. An image standing for a
  **fact** rather than an entity — a tyre compound, a weather condition, the direction of a change of
  standing position — is a value like any other and is read from the module that owns it. A datum's
  medium never changes what it is.
- **A graphic naming its own kind.** The stage at which a verdict was issued, the phase a forecast
  stands at, the point of a lifecycle a table was drawn at — a graphic MAY write down what kind of
  posting it is, whether or not the message ever said so in words. A picture is separable from the
  channel that gave it its context: saved, forwarded, or read a season later it carries only what is
  drawn on it, and a verdict unable to say whether it was a penalty or the appeal that overturned one
  is a picture of nothing in particular.

**8. Images are attachments, not a new channel category.**

Generated PNGs MUST be posted as attachments on the message the source module would have
posted anyway, to that module's already-registered channel (Principle VII). The image module
MUST NOT register channel categories of its own.

**No posting, no graphic.** The module never creates a posting occasion. Where the source module
would post nothing — no channel configured for the division, the channel inaccessible, a round
recorded as cancelled distributing nothing — nothing is generated and nothing is posted, whatever
the image type's toggle says. The toggle decides how a posting is dressed, never whether it happens.

**An attachment cannot be introduced into a message already posted.** This is a fact of the
service, and the lifecycle every image type inherits follows from it. Where the text flow **edits**
its message in place and the graphic must change with it, the image flow MUST instead delete that
message and post a new one, persisting the id of the new message in the place of the old, so that at
most one such message stands at any moment — a property of the text flow the image flow inherits and
MUST NOT relax. Where the graphic need not change, Rule 17 governs and the message is edited in place
beneath it.

**The replacement is produced before the original is destroyed.** The previous message MUST NOT be
deleted until the message replacing it has been produced successfully — the graphic, or the text a
fallback substituted for it. The ordering is the whole of what stops a failed render from leaving a
league with nothing where its standings, its lineup or its sheet had been, and it MUST hold on the
fallback path as firmly as on the ordinary one, that being the path on which something has already
gone wrong.

**A lifecycle MAY span occasions, and the image flow inherits it whole.** Where the text flow deletes
the message of a **previous occasion** as it posts the next — the phase 2 forecast deleting the phase 1
message, the phase 3 forecast deleting the phase 2 — that chain is a property of the source module and
passes to the image flow unaltered. The ordering above binds each link of it: the message being
superseded is deleted only once the message superseding it has been produced. Any suppression of
deletions the text flow observes, a test mode among them, is observed by the image flow identically.

**The manner of a message is not part of the chain.** A message posted as text MAY be deleted by an
occasion posted as a graphic, and a message carrying a graphic by an occasion that fell back to text.
A fallback (Rule 7) substitutes one message; it does not fork the flow. Each occasion consults the
state the source module records — which message stands, and for which occasion — and never the manner
in which that message was drawn. Without this, one failed render would strand a league's chain of
postings for the remainder of its round, and the failure of a single graphic would cost far more than
Rule 4 allows it to.

**A transport failure retries as text.** Where the **posting** fails for a reason of the service
rather than of the generation, what is enqueued for retry is the **text form** — the textual
calendar, lineup, table, standings, sheet, forecast or announcement, or the message carrying no
attachment where the graphic displaced nothing. A generated image MUST NOT be enqueued. A retry queue
is durable and outlives the state that filled it; a picture drawn an hour ago and posted now is a
picture of a season that has moved on, where the text is composed at the moment it is finally sent.
The render is repeatable from the data at the next occasion that calls for it.

**9. Template validity is a layered, extensible contract.**

A template's validity is evaluated separately from any render. The checks that constitute
validity MUST be organised as ordered, independently named layers, cheapest first. The set of
layers is deliberately open: it grows as each image type is formally specified, and a layer MUST
be ratified before it is enforced.

**Three moments, one evaluation.** Validity is evaluated at exactly three moments, which differ
only in the data available to them and MUST read one and the same evaluation:

| Moment | Data available | Effect of a fault |
|---|---|---|
| The command naming the template | The template, and the league's configuration as it then stands | The command is rejected, the configuration left as it stood — unless the fault was found only against a stand-in, when it is a warning and the command succeeds |
| Season review | The template and the season's divisions | Named in the review; the season's **approval** is refused while it stands |
| Immediately before a render | The template and the concrete data | The render fails per Rule 4 |

A check MUST be made at the earliest moment its data exists, and MUST be repeated before the
render, because the data may have changed since the template was configured. A check whose data
is not yet available MUST NOT be approximated at an earlier moment.

**Stand-ins warn; the real data refuse.** Where a moment can compare the template only against a
**stand-in** for the data that will actually be drawn, a divergence found there is a **warning**
and never a refusal:

- season review can compare a calendar template only against the *most demanding* division of the
  season, the division actually drawn being decided later;
- the command naming a lineup template has no division to check against at all, and compares
  against the teams of the season under setup, or against the server's team configuration where
  there is no season.

The converse binds equally, and is what stops the rule becoming a licence to warn. Where a moment
**does** hold the data that will be drawn, a divergence there refuses. A lineup template is
compared at season review against every division of the season, and a divergence fails validation
of that season: season review is the last moment at which a league is told its season is sound,
and a warning there would let it approve a season every lineup of which then falls back to text.

**A structural check is neither, and refuses everywhere.** A check made against the **template
alone** — that it declares a member of a collection at all, that its numbering is contiguous from
1, that every mandatory field of a member stands on the members it declares — reads no data, so it
stands in for nothing and is complete at every one of the three moments. It refuses at each, with
the severity that moment carries. Only a check that needs data the moment does not hold is deferred
to the render: the entries of a session do not exist until the session is run and MUST NOT be
approximated earlier, but the rows of the template that will draw them can be counted and inspected
the moment that template is named.

Season review reports; approval refuses. The review commits nothing that could be refused, so
naming every faulty template with its own reason is the whole of its job, and the approval is
where the season is stopped alongside the prerequisites of every other module.

The three moments are the moments at which a **template** is evaluated, and the list is closed for
that purpose. They do not bound Rule 12's capacity check, which is evaluated against a template
wherever a command would change the data measured against it — a command seating a driver in a
division is neither naming a template nor drawing a graphic, and is refused all the same where the
assignment would carry the division past the rows its configured template declares (Rule 4,
rejection at the earliest moment).

- **Layer 1 — Resolution** is mandatory from the outset and applies to every template: the file
  resolves within the configured directory, parses as well-formed SVG, and declares a root
  `width` and `height` (Rule 1).
- **Layer 2 — Catalogue conformance** is **ratified and in force**: the template declares every
  field its catalogue makes mandatory (Rule 3), carries no field belonging to a **sibling**
  catalogue, and can be counted where its capacity is fixed by the template (Rule 12).
- **Layer 3 — Bounds declaration** is **ratified and in force**: every wrapped field
  the template declares can actually be laid out, against the three defects of Rule 5.
- **Layer 4 — Trial render** is **not ratified** and MUST NOT be enforced. A report MUST continue to
  state that it was not applied rather than presenting a template as fully valid (invariant 4).

**A deeper layer is ratified per image type**, as that type's field catalogue is specified, and MUST
NOT be enforced against an image type whose catalogue does not yet exist. **All fifteen
catalogues are specified**, so no type is skipped by Layers 2 and 3 in practice. The rule stands
none the less and is not spent: it binds the next type added, and the skip-rather-than-pass behaviour
it requires MUST remain implemented and tested against a catalogue staged empty for the purpose. A
condition that no longer arises on its own is not a condition that has stopped mattering — it is one
whose next occurrence will be a new image type, written by someone reading this.

The following MUST hold as layers are added:

1. **Stable surface**: adding a layer MUST NOT change the configuration command surface, the
   three reported states (enabled / disabled / enabled-but-invalid), or the structure of a
   validity report. Only the set of reasons a template can be reported invalid may grow.
2. **Specific attribution**: every layer MUST name the individual template at fault and give a
   reason distinguishable from every other layer's failure. A report naming a group of
   templates rather than the one at fault does not satisfy this rule.
3. **Declared depth**: a validity report MUST state which layers were applied. A template that
   has passed only Layer 1 MUST NOT be presented as though it had passed a deeper check.
4. **No silent pass**: an image type for which a deeper layer is not yet ratified MUST be
   reported as checked to the depth currently available, not as fully valid.

**10. Every image type MUST declare a field catalogue, as a code constant.**

An image type's field catalogue is the authoritative list of the `@id` values its render
addresses, split by the operation each id receives and classified **mandatory** or **optional**
(Rule 3). Where a field is an image fill, the catalogue MUST also name its asset class
(Rule 13). It MUST be declared as a code constant in a single shared declaration module, one
entry per image type. It MUST NOT be assembled inline by the utility that renders the type,
supplied per call site, or stored as a sidecar file beside the template.

For a repeating collection the catalogue declares the collection's name, its **discriminator
form** (Rule 11) and the **rule by which its capacity is fixed** (Rule 12) — never a bare number
and never an enumeration of its members' ids. Where a portion of the catalogue is optional as a unit
(Rule 3), the catalogue MUST name the collection at which that portion begins, so that the check of a
template can tell a part deliberately not drawn from a part left out by mistake.

- A generation utility MUST NOT be merged for an image type whose catalogue is not declared.
- The catalogue is the *same object* consulted by the fill pipeline (Rule 3) and by validity
  Layer 2 (Rule 9). Two lists that could disagree are not a catalogue.
- Adding an image type MUST be one catalogue entry plus one utility. It MUST NOT require a
  change to the fill pipeline, the validity registry, or the report renderer.

Where an output **aspect** is drawn by more than one image type, each type is its own catalogue
entry, keyed by the template slot it fills: `results_qualifying_template` and
`results_race_template` are two entries and not one entry carrying a branch. Sibling catalogues MAY
share the declaration of the part they hold in common, and MUST remain separately addressable, each
naming its own fields in full — so that a template can be checked against the one catalogue its
slot answers to (Rule 3), and a report can say which of the siblings is at fault (Rule 9, specific
attribution). The aspect is what a league toggles; the catalogue belongs to the template.

**The catalogue MUST name the datum that selects the slot.** Where an aspect holds several slots, the
choice among them MUST be a function of **that datum alone**: the kind of the session for the two
results slots, the championship for the two standings slots, the **format of the round** for the two
slots of each of weather's phases 2 and 3. Nothing else may enter the choice — not a configuration
beyond the one naming the templates, not a count of the data actually present, and not a fall back to
the other slot when the one selected is unconfigured or invalid. A selection reading any of those
would put a template a league authored for one case under the data of another, which is precisely the
fault Rule 3's sibling test exists to catch, arrived at by the module's own hand instead of by a
misplaced file.

**Several kinds MAY share one slot.** The converse holds and is the ordinary case: where the kinds of
a thing differ only in the **values of fields** — the three kinds of verdict, told apart by the stage
and the session drawn upon them — they are one image type, one template slot and one catalogue, and
the kind is a datum the graphic **draws** rather than a slot it **chooses**. An aspect gains a second
slot only where the two would draw different **fields**, which is what obliged results, standings and
weather to take a slot each and what leaves verdicts with one.

**An image type MAY declare no collection at all.** Where a graphic draws one subject and no list of
anything — one decision, upon one driver, at one round — its catalogue declares fields alone, and
Rules 11 and 12 bind nothing in it: no discriminator, no capacity, no floor. Nothing else follows from
the absence. A type declaring no collection is exempt from no other rule of this Principle, and the
statement is here only so that a checker reading a catalogue with no collection in it knows it is
reading a complete one.

Two types reach this, from opposite directions: the **mystery notice**, which says a forecast is not
coming and so has almost nothing to draw, and the **verdict**, whose subject is one decision upon one
driver. That the first arrived without the rule being written is the reason for writing it — a
catalogue of fields alone is a complete catalogue and not an unfinished one, and only a statement here
tells a reader which.

**11. Template ids follow a fixed convention.**

Because ids are the contract (Rule 2) and templates are hand-authored, the convention is binding
on both the author and the code:

- Ids are lowercase `snake_case`, semantic rather than positional (`driver_name`, never
  `text_47`).
- A field belonging to member *x* of a repeating collection MUST be named
  `<collection>_<x>_<field>`, and the member itself `<collection>_<x>`. The collection is named
  by the thing it repeats and is fixed by the image type's catalogue — `round_<x>_date` on a
  calendar, `session_<x>_slot_<y>` on a forecast. `row` is the collection name for a table whose
  members are the rows of a classification, and carries no privilege beyond that.
- A member is discriminated by an **ordinal**, and by nothing else. An ordinal is written
  plainly, **without padding**: `row_1_position`, `row_10_position`. Numbering starts at 1 and
  MUST be contiguous; a gap is a fault of the template. **No collection is discriminated by a
  datum of the league**: a template MUST be authorable in ignorance of the teams, the drivers and
  the tracks of any one league, and one file MUST serve every league alike.
- Where an image type's ordinal **coincides with a datum** the member draws — a classification row
  whose ordinal is the finishing position placed upon it — the field carrying that datum is filled
  **from the ordinal**, and no reconciliation between the two is attempted. The order, and any
  renumbering the source module performs upon it, are settled and persisted before the graphic is
  drawn; a utility comparing the two could only disagree with data that are already the fact.
- Where it **does not** coincide with a datum, the ordinal is a place in a layout and nothing more,
  and the graphic MUST NOT draw it. The rows of an attendance sheet are ordered by total accrued and
  stand in no classification: two drivers level on totals stand level, and a template numbering its
  rows would publish a ranking the module never computed and cannot defend. An image type MUST state
  which of the two its ordinal is, the answer being invisible in the template — an ordered list of
  rows looks the same either way, and only the catalogue can say whether the first of them is *first*
  or merely at the top.
- A collection MAY be a **singleton**: one member, named, bearing no discriminator at all
  (`reserve_name`, `reserve_driver_<y>_name`). A singleton's name is reserved — no member of a
  sibling collection may bear it, and no datum of the league may normalise to it (Principle IX).
- Collections MAY nest, each level contributing its own name and discriminator in the order of
  containment (`row_<x>_round_<z>_driver_<w>`, `team_<x>_driver_<y>_flag`).
- A removable group is the field's name followed by `_group` (Rule 2), which for a whole member
  is `<collection>_<x>_group`.
- A field of a member MAY bear a name **beginning with the name of a collection nested in that same
  member**. `session_<x>_slot_type` is a field of session *x*, carrying the type of weather drawn for
  it; `session_<x>_slot_<y>_label` is a field of slot *y* of that session. The two are told apart by
  the **catalogue** (Rule 10) and never by parsing the id. Reading a structure out of an identifier is
  convenient and is not the contract: the catalogue declares which names are collections and which are
  fields, a checker consults it, and a name a parser would find ambiguous is unambiguous to the one
  list both the fill pipeline and validity Layer 2 read.

**An ordinal is a place in the layout and never a member in itself.** Which datum is drawn at a
given ordinal is resolved from the data at the moment of generation and MUST be recorded in no
template. The lineup's team block is the case this rule was hardest won on: a block's ordinal is
the position the team holds in the division being drawn, the same ordinal MAY stand for a
different team in another division and for a differently named team in another league, and
nothing of the block save the name and the badge varies with the team.

The **keyed** collection — a member discriminated by a datum of the league normalised by the rule
of Rule 13, of which `team_red_bull_name` was the shape and the lineup's team block the only
instance — is **withdrawn**. It existed so that a member could be hand-designed as itself, and
the cost was that such a template was authored against one league's data rather than against a
shape: no file shipped with the bot could serve a league whose teams it did not know, and every
division of a season was forced into the same composition to keep one file serving them all. That
cost is not worth a livery.

A datum of the league therefore reaches the image module as a **filename** and in no other way,
and is bound by what a filename admits rather than by what the `@id` of a node in an XML document
admits. Constraining that datum remains the business of the module that owns it (Principle IX).

**12. Collection capacity is declared by the template; overflow is a problem.**

Any image type drawing a list whose length varies by league MUST state in its catalogue **how that
list's capacity is fixed**. A capacity is fixed in one of exactly two ways, and the catalogue names
which for every collection it declares:

- **By the template.** The member slots the template declares are the capacity, and the data are
  measured against them. The rounds of a calendar, the rows of a classification, the **teams of a
  division**, the **seats of a team** and the seats of a reserve team are all of this kind.
- **By the template slot.** Where an aspect holds several slots (Rule 10) and each serves a **known
  subset** of the data, the shape that subset can demand is a **constant of the module** — of the game
  the league plays, not of the league — and the catalogue states per slot the least a template filling
  it must declare. A weather phase 3 template of the sprint slot serves rounds of four sessions, the
  longest of which allows three weather slots; one of the plain slot serves rounds of two sessions, the
  longest of which allows four.

Where the capacity is fixed **by the template**:

- **Fewer data than slots**: the unused members MUST be removed — by their
  `<collection>_<x>_group` where one is declared (Rule 2), or, where the template declares crop
  points, by cutting the canvas at the corresponding crop point (Rule 2). Members taken off the
  canvas this way are not unresolved fields (Rule 3), and no notice arises.
- **More data than slots**: a **problem** (Rule 4), rejected at the earliest moment it can be
  detected — including the command that would grow the division past the capacity, which is
  refused with its change unapplied. The problem MUST report the data count, the declared
  capacity, and the template at fault.

The **by the data** capacity — a configured value fixing the count, the template obliged to
declare exactly those members, and a divergence in either direction fatal — is **withdrawn**. The
teams of a division and the seats configured for a team were its only instances, and both are now
fixed by the template. A capacity MUST NOT be fixed by a configured value at the top level of a
collection; the nested ceiling below is the one place a configured value still bounds anything.

**A member the data hold but leave empty is not an unused member.** A team that has recruited
nobody, a seat nobody occupies, is **drawn** — its text emptied and its image fields removed per
Rule 3 — and is never removed as a surplus slot is. Only a slot the data reach no member at all
for is removed. The distinction is the whole of what separates *fewer data than slots* from *data
that are themselves empty*, and an image type MUST NOT conflate them.

Where the capacity is fixed **by the template slot**, the declaration is a **floor**:

- **Fewer members than the floor**: a **problem** (Rule 4), naming the slot, the count the template
  declares and the count required of it. It is a **structural** check under Rule 9 — read off the
  template and a constant of the module, needing no data at all — so it is complete at every one of the
  three moments and refuses at each with the severity that moment carries. A league is told when it
  names the template, or at season review, and not at the horizon of a phase it can no longer post.
- **More members than the floor**: not a divergence. The surplus is removed at generation exactly as a
  template-fixed capacity's unused members are — silently, by group, raising no notice — because the
  floor is the greatest the slot can ever demand and every lesser case reaches it by removal. A template
  author sizing a row for the floor should expect its last cells to be absent on most rounds.
- The floor is the **maximum over the subset the slot serves**, taken per collection: the greatest
  number of members any served case holds, and, for a nested collection, the greatest capacity any
  member of any served case allows. A floor set to anything less would admit a template that cannot
  draw a round the league has already scheduled.

This is a third way a capacity is fixed and not a softening of the other two. The **data drawn** remain
the fatal test at generation, as they are for a capacity fixed by the template: a round holding more
sessions than its template declares, or a session drawn more weather slots than the template declares
for it, is a problem however the floor was satisfied.

**A nested collection MAY be bounded by a configured value of its containing member.** Where a
collection nests inside a member and a configured value belonging to *that member* bounds it — the
cars of a round of a constructors grid, bounded by the seats configured for the team on the row —
one template must serve every member, and no single declared count can be right for all of them.
There the members the template declares are a **ceiling**, not a count: those beyond the containing
member's configured value are removed silently, per member, and over-declaration is not a
divergence. The fatal test is against the **data actually drawn** — the drivers who drove that
team's cars in that round — exactly as it is for an ordinary capacity fixed by the template.

This is the sole survival of a configured value bounding a collection, and it is not a
reintroduction of the withdrawn by-the-data capacity: the ceiling is never compared against the
template in the other direction, and a template declaring more than any member can use is correct.
The catalogue MUST say which of its nested collections are of this kind; one whose bound is the
same for every member is an ordinary template-fixed capacity and is measured as one.

**One capacity may govern several id families.** Where an ordinal is drawn both as chrome and upon
every member of another collection — a round of a results grid standing as `round_<z>_group`, as
`row_<x>_round_<z>_group` on every row, and as `row_<x>_round_<z>_driver_<w>_group` on every car of
every row — the capacity decision on that ordinal applies to **every** family bearing it, and its
removal takes them all. This is how a column's cells leave the graphic, containment being unable to
carry them (Rule 2). Where the template declares no group for one of those families, every field of
that family bearing the ordinal is removed one by one instead.

**A collection MAY have a floor, and the floor is declared.** An image type MAY name a collection
below whose emptiness the graphic has no subject — the rounds of a calendar, the drivers of an
attendance sheet — and drawing that graphic against empty data is then a **problem** (Rule 4),
rejected at the earliest moment it can be detected and naming the division that holds nothing.

Without this, zero is merely the extreme of *fewer data than slots*: every member would be removed in
silence and a heading posted over an empty canvas, which is not a graphic of an empty division but a
graphic of nothing at all.

The floor is declared **per image type** and MUST NOT be inferred. A classification of no entries and
a round of no session are not fatal for any type that has not named them so, and the four types that
name none keep the ordinary silent-removal behaviour in full. A floor is a statement about the
**subject** of a graphic rather than about its template, so it is checked against the concrete data at
generation and at any command that would empty the collection; the moments before that hold no data to
check it against, and MUST NOT approximate it (Rule 9).

Overflow MUST NOT be silently truncated, and MUST NOT be spilled into continuation images.

This holds for **every** collection of every image type, principal or incidental. A graphic that
omits a driver, a round or an entry without saying so is worse than no graphic, and a graphic
that says so while quietly drawing short still leaves a league reading a picture that is not
their season. Overflow means one thing throughout — more data than the template has slots for —
and it is fatal wherever it occurs.

**13. Asset resolution is by normalised slug, and every class carries a fallback.**

**An asset is not a field.** A field is a place in the template; an asset is a file placed upon
one. Mandatory and optional classify **fields** (Rule 3) and nothing else. The rules of this
Rule govern the resolution of an asset and are **NOT** qualified by the classification of the
field receiving it. Conflating the two is the mistake this paragraph exists to prevent.

An asset fill (Rule 2) resolves a datum to a file inside the directory configured for that
asset class. Resolution MUST be deterministic and documented:

- The filename is the datum **normalised**, with the `.svg` extension. Normalisation is: trim,
  lowercase, decompose and strip diacritics, replace every run of characters that is neither a
  letter nor a digit with a single **underscore**, and drop leading and trailing underscores.
  `Red Bull Racing` resolves to `red_bull_racing.svg`; `São Paulo` to `sao_paulo.svg`.
- **One rule serves every class**: a team name, a country, a track, a tyre compound and a
  condition of weather are all normalised by it. A second normalisation rule would be a second way
  for two spellings of one datum to disagree.
- The normalised form names a **file** and never a **field** of a template, every field of every
  collection being addressed by an ordinal (Rule 11). It is therefore bound by what a filename
  admits, and not by what the `@id` of a node in an XML document admits.
- Each image type's catalogue MUST name the asset class for each of its image fields. A
  utility MUST NOT construct a path from anything but the configured directory and the slug.
- Every asset directory MUST cover each datum of its class a league can present it with, or
  resolve to a generic **fallback image** under the reserved name `fallback.svg` that covers those
  it does not, drawn under the two-tier resolution below.
- The **packaged directory** of a class is the directory shipped with the module for it —
  `resources/defaults/<class>` — and carries that class's own `fallback.svg`. It is distinct from
  the directory a league has configured for the class, which a league is free to point elsewhere.
  The two MUST NOT be the same directory. The configured directory of each of the eight asset
  classes defaults to `resources/league/<class>` — the folder a league fills with its own artwork,
  which an update to the bot MUST NOT overwrite — so the two tiers stand apart whether or not a
  league has configured anything, and a league that places a file there has it drawn without
  issuing any command. The template directory is not of this kind and does not default thus: it has
  no packaged tier, a template being sought in the configured directory alone.
- Where a datum is **not a value a league supplies** but part of a **closed set the module itself
  defines**, the **module** MUST ship a file for it in the **packaged directory** of its class,
  beside that class's `fallback.svg`. The obligation of the bullet above is discharged by the module
  rather than by the league, because the league has nothing to supply: it did not choose the
  vocabulary and cannot be incomplete against it. A league MAY still point the class at a directory
  of its own; doing so does not make the vocabulary the league's, so such a datum missing from that
  directory MUST still resolve to the packaged directory's own matching file, drawn in preference to
  the packaged `fallback.svg`. This is the one respect in which the packaged directory is searched
  for the datum's own file and not merely for its fallback — see the exception carried in the
  outcome rules below.

  A datum qualifies in **either of two ways**, which are one rule at two granularities and not two
  rules. What qualifies a datum is that it is the module's own vocabulary; the granularity follows
  from the class it sits in:

  - **The class settles it**, where every datum the class can be handed is the module's own: the
    three directions of a change of standing position (`gained`, `lost`, `unchanged`); the three
    types of weather a session may be drawn (`sunny`, `mixed`, `rain`) together with the five
    concrete weathers a slot may carry (`clear`, `light_cloud`, `overcast`, `wet`, `very_wet`); and
    the five tyre compounds a session may be run on (`soft`, `medium`, `hard`, `intermediate`,
    `wet`).
  - **The datum settles it**, where the class's other data are the league's own but the module
    reserves a filename within it: `mystery`, for a round concealed until it is run, and `other`,
    for a driver who stated no nationality in particular. Both are reserved in the flag class, and
    `mystery` in the track class besides.

  A class whose data are values a league named — the countries of the flag class, the circuits of
  the track class, and likewise team and driver — MUST NOT be declared closed-set as a whole,
  however many reserved filenames it carries. A league supplying most of its country flags would
  otherwise be handed the module's file for the remainder, under a name the league chose and for
  artwork it did not; that a file happens not to ship under such a name today is an accident of what
  is packaged and not a rule that can be relied upon.

  This is the rule `mystery.svg` already followed in the track class, generalised. It stands apart
  from `resources/defaults/` holding no league-specific artwork: a direction marker is the module's own
  vocabulary drawn in the module's own terms, and shipping none of it would put the class's fallback
  on every row of every graphic — three identical arrows and a notice apiece, which is not a
  degradation a league can act on but a picture the module never had to draw wrong.

Resolution has exactly **four** outcomes, and no others:

| Outcome | Result |
|---|---|
| The normalised file is found in the **configured** directory | Placed upon the field |
| Not found, the **configured** directory holds `fallback.svg` | Fallback placed upon the field; **notice** raised naming the field and the datum that had no file of its own |
| Not found, the configured directory holds none, the **packaged** directory of the class holds one | That fallback placed upon the field; the **same notice** raised |
| Not found, and neither directory holds a fallback | **Problem**. The render is abandoned |

- The datum's **own file** is sought in the **configured directory alone**. The packaged directory
  is consulted for a **fallback** and for nothing else; a file of the datum's own name sitting in
  the packaged directory MUST NOT be drawn for a league that did not supply it. This governs every
  datum that is a league's own value. The closed-set bullet above states the sole exception: for a
  datum that is the module's own vocabulary — whether because its whole class is, or because it is
  a reserved filename within a class that is not — the packaged directory is searched for the
  datum's own file, ahead of its `fallback.svg`, whether or not the league has pointed the class at
  a directory of its own.
- Where that exception applies and the packaged directory supplied the datum's **own file**, the
  notice raised MUST NOT state that a fallback was drawn. Its **kind** is unchanged, the row above
  requiring the same notice on either tier; its wording MUST say that the module's own file was
  drawn, nothing having been substituted. A league told otherwise would go looking for artwork it
  was never expected to supply.
- **A class MAY be declared one whose fallback stands for the absence of artwork** rather than for
  artwork that should have been supplied, whereupon the fallback is drawn and **no notice whatever
  is raised** — neither that a fallback was drawn, nor that a packaged file was drawn off the shape
  of its slot (v7.11.0). The two rows of the table above that raise a notice do not raise one for
  such a class; every other part of the resolution is unchanged, the file still being drawn and the
  field still resolving.
    - A class qualifies only where it is drawn **solely** where a league's own template declares its
      field — the module shipping no template that declares one — so that a league meets the class
      at all only by asking for it. A league that has asked for it and drawn artwork for none of its
      data is then in the **ordinary** state of the class rather than an incomplete one, and a
      notice on every graphic it posts would name nothing it could act upon: the only remedy
      available would be to supply the very file the module already ships.
    - Such a class MUST ship a fallback with **nothing drawn upon it**, carrying a size and no mark.
      The silence is defensible only because what is drawn in the absence of artwork is nothing; a
      visible placeholder drawn silently would be worse than either reporting it or drawing nothing.
    - The cost MUST be accepted knowingly and documented to leagues: a **misnamed file is silent
      too**, as is a datum renamed after its artwork was drawn. The artwork simply does not appear
      and nothing says why.
    - `division_logo` is such a class and is at present the only one. The declaration is made for
      the class as a whole, where the silence of an **absent datum** below is declared field by
      field; the two are distinct, that one governing a datum never supplied and this one a datum
      supplied for which the league has drawn nothing.
- Wherever else this constitution speaks of a directory **holding**, or not holding, a fallback, it
  means this two-tier check taken as a whole and not the configured directory alone.
- A league whose configured directory carries no `fallback.svg` of its own therefore no longer
  needs to place one there for the class to survive an incomplete asset set; the packaged fallback
  answers a miss the configured directory cannot.

- These outcomes hold whatever the receiving field's classification. A missing asset is never
  survived by emptying the field.
- A fallback image is bound by Rule 6 exactly as any other asset: plain SVG, authored at the
  slot's aspect ratio, never padded by the generator.
- An **absent datum** is a different matter entirely: where there is no value to look an asset
  up by, no asset is sought, and the field is handled by its classification under Rule 3 —
  removed where the field is optional, fatal where it is mandatory.
- A catalogue MAY instead declare, **per image field**, that an absent datum draws the class's
  `fallback.svg`. Where it does, the fallback is drawn and **no notice** is raised: it stands for
  the absence itself rather than for a file that should have existed, and nothing has degraded.
  Where the class holds no fallback the declaration is inert and the bullet above governs; an
  absent datum is never fatal for want of a file.

  The declaration is per **field** and never per class, because one class serves fields that answer
  absence differently. A qualifying entry for which no tyre was recorded draws the tyre fallback,
  the submission of a session not obliging one. A configured seat that no driver occupies must draw
  no portrait and no flag at all, a fallback there being a ghost driver.

**Track imagery is two classes, and a round is drawn by two distinct optional fields.**

A round's imagery is never one class serving both purposes:

- The **track** class holds circuit maps, resolved by the normalised name of the track, in the
  directory `images config track-image-directory` names.
- The **flag** class holds country flags, in the directory `images config flag-directory` names.
  It is the same directory a driver's nationality draws from. There is one flag directory and
  no second one.

**Only the calendar and the check-in graphic MAY declare a field of the track class.** Every
other image type that draws imagery for a round draws the flag class and nothing else. A
catalogue (Rule 10) declaring a track-class field for any other type is invalid and Layer 2
MUST refuse it. The two are separate optional fields, so a calendar or check-in template MAY
declare either, both, or neither, each removable on its own terms under Rule 3.

An id MUST name the class it draws (Rule 11). Where a type's single round-imagery field becomes
flag-class, its catalogue MUST rename it to the `_flag` form a driver's flag already uses; an id
reading `track_image` upon which a country flag is drawn is precisely the disagreement Rule 11
exists to prevent.

**Rationale**: a map earns its place where the graphic's subject is the round itself — a calendar
naming where a season goes, a check-in asking a driver to attend one race. A standings table, an
attendance sheet and a forecast draw the round as a *column heading*, at a size no circuit outline
survives, and a flag is what reads there.

**The flag class is keyed by the country, for every field that draws from it.**

- A round's flag resolves from the `country` of its Track, normalised: `United Kingdom` yields
  `united_kingdom.svg`.
- A driver's flag resolves from the **country of their nationality**, not from the nationality
  itself. The module MUST ship a **total** map from each canonical nationality adjective to its
  country name — `British` to `United Kingdom` — and the flag resolves from that country
  normalised.
- A canonical nationality absent from that map is a defect in a module-shipped constant, not a
  render-time outcome. It MUST be caught by a test over the map's totality, never by a fallback
  drawn at render.
- The value standing for **no stated nationality** is not a country and gains none: `Other` is
  carried through unchanged and resolves `other.svg`.

One directory, one spelling, one file per country. Several circuits in one country resolve to the
same flag — Las Vegas, Miami and the Circuit of the Americas each draw
`united_states_of_america.svg` — and
that is the intended result, not a collision to be broken.

**Rationale**: keying the class on the country is what lets one directory serve a driver and a
round at once. Keyed on the adjective it could serve only the driver, and a round would need a
second directory holding the same flags under different names — one authoring job done twice, and
two places for a league's set to be incomplete.

**`mystery.svg` is reserved in the flag directory as it is in the track directory.** A round of
the mystery format conceals its track and thereby its country, and the datum `Mystery` fills its
flag field by the ordinary rule of Rule 3's literal-value paragraph. The module MUST ship
`resources/defaults/flags/mystery.svg` beside `resources/defaults/tracks/mystery.svg`. Without it, a concealed round
draws the flag fallback and raises a notice against a league that has done nothing wrong.

**A miss is answered by the class's own fallback and never by the other class.** The three
outcomes above hold per class: a flag that does not resolve draws `flags/fallback.svg`, a map that
does not resolve draws `tracks/fallback.svg`, and neither is ever substituted for the other.
Drawing a map where a flag was asked for would silently put imagery on a graphic that its league
did not choose, and the table above admits no fifth outcome. The packaged tier of a class answers
only for **that** class.

**Rationale**: the fallback is per asset *class*, not per template field, because the gap it
answers belongs to the directory — a nationality with no flag drawn for it — and not to any one
graphic. Making the miss fatal when even that is absent is what stops the module drawing a card
with a hole in it: one generic file per directory is a trivial thing to supply, and a league
that has supplied neither it nor the asset has an incomplete set rather than a graphic the bot
should quietly degrade.

**14. A generated image is verified as a PNG.**

Any check that a template or a utility produces the intended graphic — during development,
in a test, or in a validity trial render (Rule 9) — MUST be performed against the rasterised
PNG. Inspecting the filled SVG in a browser does not satisfy this rule and MUST NOT be
offered as evidence that a render is correct. The two disagree in exactly the cases that
matter: flowed text, substituted fonts, unresolvable asset references, and the crop.

**15. A graphic carries one time zone, named by configuration.**

A text output renders an instant as a Discord timestamp, which every reader sees in their own
zone. A graphic is a picture and cannot: it MUST render every date and time in the single zone
the league configures, identically for every reader, and MUST append that zone's abbreviation
wherever it draws a time. Date and time formatting on a graphic MUST come from the module's
configured formats and zone, and MUST NOT be derived from a locale, from the host machine, or
from the viewer.

This is a genuine reduction against the text path, and is the one respect in which the graphic
tells a reader less than the message it rides on. It is stated here so that every image type
carrying a time answers it the same way, and so that no type invents a per-reader scheme that a
picture cannot honour.

**16. A graphic draws nothing a reader can act on.**

A mention, a link, a button and a live timestamp are interactive or per-reader elements of the text
path. A picture carries none of them. Each MUST be either resolved to a **fixed rendering** the
graphic draws, or left to the message text the image rides on — and the message keeps whatever it
must: a results post keeps its heading and its lifecycle label as text and gives the table to the
graphic.

**The split is not exclusive.** This rule governs elements a picture *cannot carry*. A plain text
label is not one of them, and a value MAY be drawn both in the message text and on the graphic where
the image type says so: a results or standings post keeps its lifecycle label as message text *and*
draws it on the graphic, so that a picture saved or forwarded away from the message it rode on still
says which phase it stands after. Nothing is resolved away here and nothing is lost; the rule is
about what the graphic must give up, not about what the message must keep to itself.

Where the element names an **entity of the server** — a person, or a team reached through a Discord
role — the fixed rendering is that entity's **name on the server at the moment of generation**. For a
person that is their display name; for a team it is the name the league gave the team holding the
role, falling back to the name of the role itself. An image type MUST state the chain by which a name
is reached where the first is unavailable, and MUST reach the same name wherever it draws that
entity, so that one driver is not two names on one graphic.

**Markup the message channel interprets is not content.** Bold, italic, underline, strike-through, a
code span, a block quote — anything that is an instruction to the channel rather than a value — is
dropped by the graphic. The graphic draws the value the markup adorned and leaves the distinguishing
to the template's own typography, which is where an SVG says such things. The emphasis a phase 3
summary carries in the forecast message is of this kind, and so is every bolded label and quoted line
of every message an image rides on.

This is not a divergence from Rule 7's one rendering. The markup was never part of the value: the
shared formatting code produces the value, and applying channel markup to it is something the text
path does afterwards, to the message rather than to the datum. An image type that finds itself
stripping markup **inside** a value it was handed has been given the wrong thing, and the repair is in
the code that handed it over.

**A mention standing inside a value is content, and is resolved in place.** Where a person — or a
module composing prose on a person's behalf — writes a mention **into** free text the graphic draws,
that mention is part of what was written. It MUST be resolved to the entity's fixed rendering above,
in the position it stands, and the text around it drawn as it was written. The justification the
attendance module composes for a sacking is built around such a mention, and reaches the canvas
carrying the name alone.

This is the fixed rendering of the first paragraph and not the markup-stripping of the one before it,
and the distinction is worth holding. Markup is an instruction the text path added to a value, so
finding it inside means the handover is wrong. A mention is a value a person put there, and no repair
upstream could take it out without taking it out of the **message** too — which is the one place it
belongs, the message being where a reader can act on it.

Rule 15 is this rule applied to time: the timestamp every reader saw in their own zone becomes the
one configured zone drawn for all. Stating the general form once is what stops each image type
inventing its own answer to a question every picture asks.

**17. A graphic is redrawn whenever what it draws changes, unless its type is declared static.**

The ordinary form: a graphic is generated anew, and its posting replaced under Rule 8, on every
occasion the text flow reposts or edits what it draws. A picture of a state is worth only its
currency, and a graphic left standing over changed data says something false with the full authority
of a rendered image.

An image type MAY instead be declared **static**: generated once, at the moment its message is first
posted, and never again while that message stands. The message beneath a static graphic is **edited
in place** as the text flow always edited it, and the attachment survives every edit untouched —
Rule 8's delete-and-repost does not arise, because nothing about the picture has changed.

The declaration is the image type's own and MUST be stated in that type's specification alongside its
catalogue. It carries one obligation, which is the whole of what makes it safe:

> **A static type MUST NOT draw a value that changes while its message stands.**

The check-in graphic is the first such type. It draws the division, the round, its sessions, its date
and the moment its check-in locks, and draws deliberately no driver, no team, no RSVP status, no
attendance point and no roster: everything the presses of its three buttons alter lives in the embed,
which is edited, and stays off the picture, which is not. An image type that finds it needs such a
value is simply not static, and takes Rule 8's delete-and-repost like every other.

**A type MAY instead be static because it draws a record rather than a state.** The obligation admits
two ways of being satisfied. The check-in graphic satisfies it by **drawing nothing mutable**. A
**verdict** satisfies it differently: every value it draws was fixed at the moment the decision was
taken, and a later change of the world does not falsify it. A driver renaming their Discord account
does not make the verdict wrong — the graphic records the name under which the decision was issued,
which is what a record is for.

The test is therefore not "can this datum ever change" but "can what this graphic **says** become
false while its message stands". For a graphic of a **state** the two questions are the same. For a
graphic of an **event** they are not: the event is over, its facts are settled, and only a change to
the event itself could falsify the picture. A verdict admits none — a penalty overturned on appeal is
announced as **its own verdict**, a second posting standing beside the first, and the first remains a
true record of what was decided when it was decided.

A type taking this ground MUST say so, and MUST be one whose corrections arrive as **new postings**
rather than as edits to old ones. Where a correction would instead amend the posting that stands, the
type is drawing a state after all and is not static.

The verdict is the second static type, and the strongest case of the form rather than a third form:
its message is never edited either, so Rule 8's delete-and-repost does not arise for it in any shape
and no message id need be persisted for it at all.

The obligation is held by the author of the type and not by a check the module runs — a field's
mutability is a fact about the module that owns the datum, not a property visible in the catalogue.
Adding a field to a static type's catalogue is therefore an amendment of its static declaration and
MUST be reviewed as one. The cost of getting it wrong is a picture that goes quietly stale beneath a
message that stays current, which no error will ever report.

**Rationale**: Separating layout (the template) from data (the fill) is what allows a league
to restyle its graphics without a code change and what keeps the rendering code independent
of the number of image types. Failing loudly on a template/data disagreement while surviving
a font substitution is the distinction that makes the module safe to run unattended: the
first is a defect that would post a wrong graphic, the second is a cosmetic degradation a
league can act on at leisure. Requiring the text path to remain authoritative ensures that
adding graphics never reduces what the bot can tell a league.

Rules 10–17 exist because the module is about to grow one generation utility per image type,
written across many sessions. A catalogue that is a shared constant, an id convention the
code can construct rather than be told, a capacity whose source the catalogue names, one time
zone rule, one answer to what a picture cannot draw, one answer to when a picture is redrawn, and a
single slug rule are what let fifteen
utilities be fifteen small entries rather than
fifteen private conventions — and are what make validity Layer 2 ratifiable at all, since a check can only be
written against a declaration that exists. Verifying through the rasteriser rather than the
browser is in this list for the same reason: it is a rule that costs nothing to hold from the
first utility and is expensive to retrofit once graphics have been signed off on the wrong
evidence.

The mandatory/optional split running through Rules 3, 10 and 13 is what keeps the failure
behaviour proportionate — but it applies to **fields**, and only to fields. Mandatory says the
graphic is meaningless without this value; optional says the graphic is merely plainer without
it. Drawing a standings table with a blank where the points should be would be worse than
drawing nothing, while a sanctions block that never appears costs a reader nothing.

There is no third classification, and no exemption from either. Where a kind of record holds no
literal value — a round whose track is concealed until it is run — the image type defines the
value that stands for that kind and fills the field with it (Rule 3). Treating that as a missing
mandatory value would have made a deliberate feature of a league's season look like a defect in
its template, and would have put a hole in a graphic the league expects to be drawn whole.

The same reasoning is why a **configured** absence raises no notice (Rule 4). A notice exists to
tell a league something went less well than it might have. A league that switched a datum off has
already been told, by itself; reporting it back once per member on every render would bury the
notices that mean something under the one notice that cannot.

A collection is discriminated by an ordinal (Rule 11), and its capacity is fixed by the template
(Rule 12). Both answer the same question, and now answer it the same way: **a graphic is a shape
the bot knows, and never one league's particulars**. A table of results is all shape — any ten
drivers fill it — and a lineup is now no different: its blocks are places, filled from whichever
teams the division being drawn holds.

This was not always so. A lineup's blocks were once keyed by team name so that each could be
drawn in that team's own livery, and the teams of a division once fixed the capacity of the
collection holding them. The price was a template authored against one league's data: no file
shipped with the bot could draw a league whose teams it did not know, every league had to author
its own lineup and re-author it on every rename, and the divisions of a season were forced into
one composition to keep a single file serving them all. A livery is not worth that, and both the
key and the data-fixed capacity are withdrawn. What distinguishes one block from another is now
the name and the badge alone, resolved from the division at the moment of generation.

Assets are governed separately (Rule 13), and uniformly. A league whose asset set does not cover
every value it will present need supply nothing at all: the packaged directory of the class
answers the miss with its own `fallback.svg`, and the league may still drop one of its own into
its configured directory to override what is drawn. Only a league that has pointed a class at a
directory of its own **and** stripped the packaged tier of a fallback has an incomplete set with
no answer, and the module says so rather than drawing a card with a hole in it. One rule for every asset class, with no branch on
the field receiving it, is the whole of what makes that predictable to a template author. The one
declaration a catalogue may make against that rule — that an **absent** datum draws the fallback —
is not a branch on the field's classification but a statement about the datum: some absences are a
state worth depicting, and where a league has already supplied the file that depicts them, drawing
it beats leaving a gap in a column and reporting the gap on every row.

A graphic is a picture, and Rules 15 and 16 are the two places this document admits what that
costs. Everything a reader could click, hover or resolve in their own zone is flattened or left
behind in the message text. The compensation is Rule 7's shared rendering: what the graphic *does*
draw is drawn by the code the text path drew it with, so the two can differ in what they can carry
without ever differing in what they say.

That compensation is also why Rule 7 is a **floor and not a ceiling**. For six image types the rule
read as though a graphic could draw only what the bot already said in words, and the reading was
wrong in a way that only became visible once a type wanted a flag beside a name it was already
printing. *Additive* was never a promise that the picture would say no more than the message; it was a
promise that turning images on would cost a league nothing. Those are different sentences, and only
the second is worth defending — the first would have obliged a league to add a nationality column to
a text table before its picture could carry a flag, which buys a reader nothing and costs them a
column.

What replaced the ceiling is the prohibition that was doing the real work all along: a graphic may not
**decide**. A gap between two points totals both already printed is not a fact the graphic invented; a
countback separating two entries level on those totals is, and stays on the far side of the line,
because that is where a graphic could start disagreeing with its own league. Arranging, measuring and
depicting are safe in a way that settling never is, and the condition attached to a derivation —
that the subtraction is written where the standings are computed — is what keeps even the safe case
from quietly becoming the image module's private arithmetic.

The cost is stated rather than hidden: a fallback now says **less** than the graphic would have. That
is the right trade. A reader meets the fallback only when something has already gone wrong, and
levelling every graphic down to what a message can say, permanently, to spare them that difference
would forfeit the whole reason to draw one.

Rules 12 and 13 each gain a case where the bot, and not the league, is the one who knows. A nested
collection whose bound differs from member to member cannot be refused for over-declaring, because
one template draws every member and the author cannot satisfy all of their bounds at once; the
declaration becomes a ceiling and the data drawn become the test. An asset class whose vocabulary the
module wrote is not a set a league can be incomplete against, so the module ships it. Both are the
same instinct as Rule 3's mystery round: where the module knows the answer, the module supplies it
rather than reporting the gap.

Rules 8 and 17 are the two halves of one question the module could not put off past its sixth image
type: how long is a picture good for. Every graphic before the check-in call answered it the same way
by accident rather than by decision — the message was reposted whenever anything changed, so the
picture could not go stale. The check-in call cannot be reposted, its buttons being armed against the
message that carries them and its roster changing with every press, and it forces the answer to be
said out loud. A graphic is redrawn when what it draws changes; a graphic that is never redrawn must
draw nothing that changes. Both rules are the same sentence read from its two ends, and stating them
together is what stops a future type from taking the convenient half.

That the licence is a **declaration** rather than a property the module derives is a deliberate
choice of where to put the burden. A catalogue lists fields, and no inspection of it reveals which of
them the next button press will alter; that knowledge lives in the module owning the datum and
arrives with the person specifying the type. Making it a declaration puts the judgement where the
knowledge is and makes it reviewable in the one document a reviewer reads. It also means the module
cannot catch a mistake here, which is stated plainly rather than papered over: a stale picture under
a current message is the one failure in this Principle that reports nothing, and it is the price of
the only lifecycle a message with buttons on it admits.

Rule 12's floor and Rule 7's precondition clause are both cases of the module being asked what it
owes when there is nothing to draw or nothing to add. It owes a refusal in the first case and
silence in the second, and the difference is who is waiting: a league asking for a sheet of a
division with no drivers has made a mistake worth naming, while a league whose sanction is being
enforced is owed that sanction whether or not a picture of it can be drawn.

The sixth image type added **no rule**, and that is worth recording rather than passing over. Weather
is the module's most divided aspect — six templates, three phases, two round formats, and a kind of
round that holds no forecast at all — and every question it raised was answered by widening a rule this
Principle already held. A capacity gained a third way of being fixed, a lifecycle gained a second
dimension, a catalogue gained the datum that chooses it, and an id convention gained the sentence
saying the catalogue outranks a parser. The fifth type needed a rule of its own; the sixth did not. A
framework whose sixth instance extends its rules rather than adding to them has found its joints.

Rule 12's third capacity is the addition that most deserved a rule of its own and correctly did not get
one, because it answers Rule 12's own question — how much of a graphic is a shape the bot knows —
for the case where the shape is known but **varies by which template is being asked for**. A league
authoring for the sprint slot is authoring against a constant of the game it plays, not against its own
configuration and not against a canvas of its own choosing. The module therefore knows the number, can
state it, and can refuse a template falling short of it before a single round is run. That the same
declaration is a floor upward and a ceiling nowhere is what lets one template serve a short qualifying
of two slots and a long feature race of three without either being a fault.

Principle IV's Mystery paragraph had been stale for as long as the module has posted its notice, and
the image type is what made the staleness matter. Rule 8 draws no graphic where the source module
posts nothing, so a principle forbidding any weather message for a mystery round would have made the
mystery notice graphic unspecifiable — a picture of a posting the constitution said did not exist.
Correcting the principle rather than exempting the graphic is the right order of repair. A rule that
contradicts the shipped bot is a defect in the rule, and two documents disagreeing about whether a
message is posted is worse than one document saying plainly that it is.

The seventh image type is the module's **simplest** graphic and its **hardest** one to place, and the
two facts are the same fact. A verdict draws one decision upon one driver: no collection, no ordinal,
no capacity, no floor, one template for all three of its kinds. Everything this Principle had built to
manage repetition falls away, and what is left standing is the part it had never had to answer —
what a graphic **is**, as against what it contains. Six types had told the module how to arrange many
things on a canvas. The seventh asked what a picture of a single decision owes, and three rules had to
say more than they had said.

Rule 7 was **inverted**, and the seventh type is what exposed it. Two of its questions — may a verdict
draw a flag the announcement never prints, may it name the stage it was issued at — were each put as
a request for an exemption, and each was granted as one before the author named the actual fault: the
rule had been written as a ceiling on what a graphic may say, when what *additive* protects is a
**floor** under what it must. A picture that says more than a message costs a league nothing. A
picture that says less costs them exactly what images were turned on to add. Only the second was ever
worth a rule, and the two exemptions dissolve into instances the moment the rule points the right way.

That leaves the prohibition on **deciding** carrying Rule 7 by itself, which is where it belongs and
where it was always doing the work. A graphic may arrange, measure and depict; it may not settle. The
countback stays forbidden, the derivation still lives with the data, and a second record of the same
kind is still read rather than recomputed — none of which needed the ceiling to hold, and all of which
the ceiling was obscuring.

Rule 17's second ground is the one that mattered most to get right. Staticity had been justified once,
by a graphic that draws nothing mutable, and a verdict draws a driver's name — a value that changes.
Reading the obligation as "no mutable datum" would have forced every verdict into delete-and-repost,
persisting a message id for a message nothing will ever edit, to keep a record current with a world
that has moved past it. The obligation is about **truth**, not about mutability: a record of an event
cannot go stale, because it was never a claim about now. Saying so is what lets an event-shaped
graphic be static honestly, and the condition attached — that corrections arrive as new postings —
is exactly the property that makes the claim hold.

Rule 5 was thin for six types because none of them wrapped. A results table draws figures, a calendar
draws dates, a forecast draws icons; every one of them knew roughly how long its values were. A
verdict draws **prose a person wrote**, of no length anybody controls, and the wrapping contract had
to be stated in full the moment a type existed to exercise it. That it is stated as a general contract
rather than as the verdict's own is deliberate: it is the steward module's graphics, and any later
type carrying free text, that would otherwise each invent an answer.

**The per-type specification is complete, and Rule 9 is what that completeness cost.** With the
verdict, all fifteen catalogues are written and Layers 2 and 3 apply to every one of them. The module
has arrived where v4.0.0 pointed it: a template is checked against its own fields, and against its own
text bounds, at the moment a league names the file. What is worth recording is that the layered design
paid off in the direction it was built for — Layer 3 was added as one class and one registry entry,
and changed no command, no state and no report shape, which is exactly the stable surface XIV.9's
first invariant demanded and the reason that invariant was written before any deeper layer existed.

The third defect of Rule 5 is the one this Principle nearly missed, and how it surfaced is worth
recording. It was not found by reading the rules: it was found by rasterising a verdict and looking at
the PNG, where a steward's justification ran off the edge of the canvas with every check passing and
nothing reported. Rule 14 exists for exactly this, and it earned its place again. So did the
obligation that a measurement **err narrow** — the same render exposed a font resolver that was
answering with a *condensed* face for a family whose normal face the rasteriser would draw, and with
whatever weight sorted first for a family with several. Neither was a rule that needed writing; both
were rules this document already stated and code that did not honour them. That is the ordinary way a
NON-NEGOTIABLE principle is broken, and only the raster showed it.

**A note on the steward module.** The verdict type is specified against the penalty and appeal flow as
it stands, and the steward module — the next feature of consequence — is expected to change what a
verdict records and therefore what its catalogue declares. Nothing here pre-empts that, and no rule
above is written in anticipation of it. Two tests are stated so that the change is taken deliberately
rather than discovered:

- A field added to the verdict catalogue is an amendment of its **static declaration** (Rule 17) and
  MUST be reviewed as one, the question being whether the new value was settled at the moment the
  decision was taken.
- A verdict **amended in place** rather than superseded by a second verdict is no longer a record
  under Rule 17, and takes Rule 8's delete-and-repost like any other graphic — with a persisted
  message id, which the type deliberately does not have today.

Neither is a prediction of what the steward module will do. Both are what it will have to answer.

## Bot Behavior Standards

All Discord slash commands MUST follow the `/domain action` subcommand-group convention — a
top-level slash command group (`/domain`) with named action subcommands. Hyphenated top-level
commands (e.g. `/season-setup`, `/round-add`) are NOT permitted for new features. Any existing
hyphenated command MUST be migrated to the subcommand-group form (e.g. `/season setup`,
`/round add`) in the same change window as any UX-streamlining work targeting that domain.

- **Command grouping**: Commands that share an operational domain (season lifecycle, track
  configuration, round amendments) MUST be registered under a single command group so that
  Discord's autocomplete surfaces all related actions together. Lone top-level commands for
  domain-specific actions are not acceptable for new features.
- **Single-interaction preference**: Every command MUST be completable in a single Discord
  interaction where technically feasible. Multi-step wizard flows are permitted ONLY when
  Discord's API cannot accommodate all required inputs in one command (e.g., more than
  25 parameters); in such cases, each step MUST provide clear inline guidance on what the
  user must do next.
- Commands that mutate persistent state MUST present an ephemeral confirm/cancel prompt before
  executing, except where the change is trivially reversible within the same interaction.
- Configuration command responses MUST be ephemeral (visible only to the invoking user).
  Weather generation results MUST be posted publicly per Principle VII.
- The bot MUST acknowledge any command within 3 seconds; long-running operations MUST use
  Discord's deferred response mechanism to avoid timeout failures.
- **Autocomplete carries the same 3-second budget and has no deferral.** Discord offers no
  equivalent of a deferred response for an autocomplete interaction, so the remedy named
  above does not exist on that path: its latency MUST be removed at source rather than
  deferred around. An autocomplete callback MUST bound its own runtime, and MUST answer with
  no choices rather than answer late. Answering late reaches an interaction token that has
  already expired, which the league sees as a failed command; answering empty costs a
  keystroke. An autocomplete MUST NOT propagate a failure into the command it serves.
- Error messages MUST identify the specific problem and suggest a corrective action. Generic
  "something went wrong" messages are not acceptable.
- The bot MUST validate all inputs before executing any command; invalid inputs MUST be
  rejected with feedback before any state is modified.

### Round Formats

Four round formats are defined. Session composition and weather slot capacities are fixed per
format and MUST NOT be altered at runtime:

| Format | Sessions | Slot capacities |
|--------|----------|-----------------|
| Normal | Short Qualifying, Long Race | Qual: 2 · Race: 3 |
| Sprint | Short Sprint Qual, Long Sprint Race, Short Feature Qual, Long Feature Race | SQ: 2 · SR: 1 · FQ: 2 · FR: 3 |
| Mystery | (none — all phases skipped) | — |
| Endurance | Full Qualifying, Full Race | Qual: 3 · Race: 4 |

Session types and their maximum weather slot counts are the authoritative values used by
Phase 3 when determining `Nslots`. No session may have fewer than 1 slot (or 2 if determined
mixed by Phase 2).

## Data & State Management

- All season data (divisions, rounds, tracks, dates, weather results, audit log) MUST be
  persisted to durable storage. In-memory state alone is not acceptable.
- Each season MUST carry an explicit lifecycle state: `SETUP` → `ACTIVE` → `COMPLETED`.
  - In `SETUP`: divisions, tracks, schedules, and round formats may be freely configured.
  - In `ACTIVE`: amendments (track substitutions, postponements, format changes, cancellations)
    are permitted; wholesale reconfiguration of the base schedule is not.
  - In `COMPLETED`: the season is finalised and moved into the Season Archive (see below).
    All data associated with the season — divisions, rounds, results, standings, driver
    assignments, and the full audit trail — is retained permanently and becomes fully
    immutable. No mutations are permitted. The archived record forms the authoritative
    historical basis for future statistics and reporting features (Principle VI).
### Season Archive

A server maintains a **Season Archive**: a persistent, append-only collection of all
completed seasons for that server. The following rules are non-negotiable:

- **Append-only**: When a season transitions to `COMPLETED`, the season record and all
  associated data are added to the archive atomically as the final step of the season-end
  transaction. A season already in the archive MUST NOT be deleted, overwritten, or mutated
  by any user command or automated system process.
- **Zero-to-many cardinality**: A server's archive MAY contain zero or more completed
  seasons. An empty archive is the canonical initial state for a newly configured server.
- **Full data retention**: Every archived season retains all associated records: division
  configurations, round schedules and amendment history, weather phase outputs, session
  results and driver results, standings snapshots, driver and team seasonal assignments,
  points configuration snapshots, and the full audit trail. No associated data is discarded
  on season completion.
- **Read-only access**: Archived season data MAY be read by any command or module with
  appropriate authorisation. No write path targets archived records outside of the single
  append operation triggered by season completion.
- **Future statistics foundation**: The Season Archive is the authoritative data source for
  all planned season history and statistics features (Principle VI). Any implementation
  consuming archived data MUST treat the archive as immutable and MUST NOT rely on derived
  or cached state not persisted at completion time.

The archive is constituted by the existing `Season` records (and all related tables) in the
`COMPLETED` lifecycle state. Concrete schema additions for archive indexing, querying, or
migration from the prior ephemeral-season model are deferred to the feature specification
for the season persistence increment.

- **Inter-phase state**: The `Rpc` value computed in Phase 1 MUST be persisted against its
  round and division and remain available until Phase 3 completes or the round is cancelled.
  Phase 2 session-type draws MUST similarly be persisted per session until Phase 3 consumes
  them. In-memory caching of these values is permitted only as a read-through layer; the
  durable store is always authoritative.
- **Amendment invalidation**: When a round amendment triggers phase invalidation (Principle IV),
  the bot MUST atomically: (a) mark existing phase outputs `INVALIDATED` in the audit log,
  (b) clear active phase state for that round, and (c) re-execute all phases whose time
  horizons have already passed. This MUST happen in a single transaction; a partial update
  is not permitted.
- Data schemas MUST be versioned. Migrations MUST be applied automatically on bot startup with
  a clear log of which migrations ran.
- A full data export of any division's season (schedule, amendments, weather log, phase
  computation records, audit trail) MUST be available to trusted users on demand.

### New Entities (v2.0.0)

**DriverProfile** (server-scoped, one row per Discord user per server):
- `discord_user_id` (TEXT, PK within server) — canonical key; may be updated by admin only.
- `current_state` (ENUM) — enforced by state machine (Principle VIII).
- `former_driver` (BOOLEAN, default false) — immutability gate (Principle VIII).
- `ban_counts` (race_bans INT, season_bans INT, league_bans INT) — accumulated ban history.
- Current and historical season assignment data linked via a normalized join table,
  avoiding redundant column-per-division patterns.

**TeamSeat** (per division, per season):
- Tracks which driver (if any) occupies each seat of each team in each division.
- Reserve team rows are auto-created on division creation; configurable team rows follow
  the server-level default set unless overridden during `SETUP`.

**Season counter** (server-scoped scalar):
- A single integer per server recording the highest completed-or-cancelled season number.
  Defaults to 0. Incremented on season cancellation or completion. New seasons display
  this value + 1 as their number.

### Performance & Storage Considerations

The bot is designed for small-to-medium Discord servers (tens to low hundreds of concurrent
drivers per server). The projected storage growth per season per division is modest:

- **DriverProfile rows**: O(number of ever-signed-up drivers) — expected dozens to low hundreds
  per server; each row is <1 KB.
- **TeamSeat rows**: one row per seat per team per division per season; with 10 standard teams
  × 2 seats + Reserve = ~21 rows per division per season.
- **Audit log rows**: one entry per mutation event; expected hundreds per season; small.
- **Phase result rows**: unchanged from v1.x; 3 rows per round per division.

No bulk computation, aggregation queries, or full-table scans are expected in hot paths.
All primary access patterns are single-row lookups by surrogate key or short-range scans
by (server_id, season_id, division_id). Standard SQLite indexes on these columns are
sufficient; no additional caching layer is required at the current scale. If the server
population grows beyond ~500 concurrent drivers, migrating the backing store from SQLite
to a client-server RDBMS (e.g., PostgreSQL) should be evaluated.

- **SignupRecord rows**: one active record per signed-up or pending driver; cleared on
  transition to Not Signed Up; expected O(active_drivers) ≤ hundreds per server; each
  row is <2 KB (lap times stored as compact JSON strings).
- **SignupWizardRecord rows**: one per driver with any wizard history; tiny; same order of
  magnitude as DriverProfile.
- **TimeSlot rows**: expected single digits to low tens per server; negligible.

### New Entities (v2.2.0)

**SignupRecord** (per driver per server — at most one active record per driver):
- Stores the committed signup submission: `discord_username` (TEXT), `display_name` (TEXT),
  `nationality` (TEXT — ISO flag code or "other"), `platform` (ENUM: Steam/EA/Xbox/
  Playstation), `platform_id` (TEXT), `availability_slots` (JSON array of TimeSlot IDs),
  `driver_type` (ENUM: FULL_TIME/RESERVE), `preferred_teams` (JSON ordered list of ≤3 team
  IDs, or null for no preference), `preferred_teammate` (TEXT, nullable), `lap_times`
  (JSON map of track_id → normalised time string), `notes` (TEXT ≤50 chars, nullable).
- Linked 1-to-1 with DriverProfile. Fields nulled on transition to Not Signed Up when
  `former_driver = true`; record deleted with DriverProfile when `former_driver = false`.

**SignupWizardRecord** (per driver per server):
- `wizard_state` (ENUM) — current wizard step; full enumeration defined in the signup
  feature specification.
- `signup_channel_id` (TEXT, nullable) — Discord channel ID; retained through the 24-hour
  hold period after wizard completion (Principle XI).
- `partial_answers` (JSON, nullable) — draft answers in progress; cleared atomically on
  reaching Pending Admin Approval or on any transition to Not Signed Up.
- Created lazily on first wizard engagement; linked 1-to-1 with DriverProfile.

**SignupConfiguration** (per server, owned by the signup module):
- `nationality_required` (BOOLEAN, default true).
- `time_type` (ENUM: TIME_TRIAL/SHORT_QUALIFICATION, default TIME_TRIAL).
- `time_image_required` (BOOLEAN, default true).
- `signups_open` (BOOLEAN, default false).
- `signup_tracks` (JSON array of track IDs, nullable — empty means no tracks shown).
- `general_signup_channel_id` (TEXT, nullable).
- `base_role_id` (TEXT, nullable) — Discord role that can see and use the signup channel.
- `signedup_role_id` (TEXT, nullable) — Discord role granted on signup approval.
- `close_at` (TEXT, nullable) — ISO 8601 UTC timestamp; set when signups are opened with
  an optional close duration; cleared on manual or automatic close; re-armed on bot restart
  if non-null (Principle XI, signup close timer).

**TimeSlot** (per server):
- `slot_id` (INTEGER, server-scoped auto-increment PK).
- `day_of_week` (ENUM: Monday–Sunday).
- `time_of_day` (TEXT, HH:MM 24-hour).
- IDs are stable; removing a slot does not renumber remaining slots.

### New Entities (v2.3.0)

**SeasonAssignment** (per driver, per season, per division — formally specifies the
"normalized join table" referenced in DriverProfile since v2.0.0):
- `driver_id` (TEXT, FK → DriverProfile within server scope)
- `season_id` (INTEGER, FK → Season)
- `division_id` (INTEGER, FK → Division)
- `team_seat_id` (INTEGER, FK → TeamSeat, nullable — null until `/driver assign` runs)
- `is_historical` (BOOLEAN, default false — set to `true` on season completion)
- `final_points` (INTEGER, nullable — written atomically on season completion)
- `final_position` (INTEGER, nullable — written atomically on season completion)
- Rows are created on first `/driver assign` for a season, or on admin direct-assign in
  test mode.

*Note: `current_points`, `current_position`, and `points_gap_to_leader` fields previously
defined here (v2.3.0 draft) are superseded; authoritative live standings state is now
held in DriverStandingsSnapshot (v2.4.0).*

*Note: RaceResult and ScoringTable entities previously defined here (v2.3.0 draft) are
superseded by the session-level schema in v2.4.0 below.*

### New Entities (v2.4.0)

**PointsConfigStore** (per server — the server-level named configuration store):
- `config_id` (TEXT, server-scoped — user-supplied name/ID, e.g. "100%", "50%")
- `server_id` (TEXT, FK → Server)
- One row per named configuration per server. Deleting a config from the store does not
  automatically detach it from a season in SETUP.

**PointsConfigEntry** (per server config, per session type, per finishing position):
- `config_id` (TEXT, FK → PointsConfigStore)
- `server_id` (TEXT)
- `session_type` (ENUM: SPRINT_QUALIFYING / SPRINT_RACE / FEATURE_QUALIFYING / FEATURE_RACE)
- `position` (INTEGER, 1-indexed)
- `points` (INTEGER, default 0)
- Uniquely keyed on (server_id, config_id, session_type, position).

**PointsConfigFastestLap** (per server config, per race session type):
- `config_id` (TEXT, FK → PointsConfigStore)
- `server_id` (TEXT)
- `session_type` (ENUM: SPRINT_RACE / FEATURE_RACE only)
- `fl_points` (INTEGER, default 0)
- `fl_position_limit` (INTEGER, nullable — null means no limit; otherwise driver must finish
  at or above this position to be eligible)
- Uniquely keyed on (server_id, config_id, session_type).

**SeasonPointsLink** (attachment record — weak link between server config and a season in
SETUP; discarded on approval after snapshot copied to SeasonPointsStore):
- `server_id` (TEXT)
- `season_id` (INTEGER, FK → Season)
- `config_id` (TEXT, FK → PointsConfigStore)
- Uniquely keyed on (server_id, season_id, config_id).

**SeasonPointsStore** (season-scoped snapshot of PointsConfigEntry rows — created on season
approval from the attached SeasonPointsLinks; completely independent of server store):
- Mirrors the schema of PointsConfigEntry with an added `season_id` column.
- Immutable after creation unless the mid-season amendment flow produces an approved
  replacement (at which point existing rows are replaced atomically).

**SeasonAmendmentState** (per server — tracks mid-season points amendment lifecycle):
- `server_id` (TEXT, PK)
- `season_id` (INTEGER, FK → Season)
- `amendment_active` (BOOLEAN, default false — true when `results amend toggle` has
  enabled amendment mode)
- `modified_flag` (BOOLEAN, default false — true once any modification is made to the
  modification store since the last revert or approval)

**SeasonModificationStore** (working copy of SeasonPointsStore during mid-season amendment;
mirrors SeasonPointsStore schema with an added `season_id` and `is_modification` flag;
cleared on successful amendment approval or explicit revert).

**ResultsModuleConfig** (per server — module-introduced configuration for the Results &
Standings module):
- `server_id` (TEXT, PK)
- `module_enabled` (BOOLEAN, default false)
- Per-division result and standings channel IDs are stored on a **DivisionResultsConfig**
  record (per division, per server):
  - `division_id` (INTEGER, FK → Division)
  - `results_channel_id` (TEXT, nullable)
  - `standings_channel_id` (TEXT, nullable)
  - `reserves_in_standings` (BOOLEAN, default true — the reserves visibility toggle)

**SessionResult** (per session, per round, per division — top-level result container):
- `session_result_id` (INTEGER PK, server-scoped auto-increment)
- `round_id` (INTEGER, FK → Round)
- `division_id` (INTEGER, FK → Division)
- `session_type` (ENUM: SPRINT_QUALIFYING / SPRINT_RACE / FEATURE_QUALIFYING / FEATURE_RACE)
- `status` (ENUM: ACTIVE / CANCELLED — CANCELLED when the special "CANCELLED" input is used)
- `applied_config_id` (TEXT, nullable — name of the seasonal config chosen for this session;
  null if CANCELLED)
- `submitted_by` (TEXT — Discord User ID of submitting tier-2 admin)
- `submitted_at` (TEXT — UTC ISO 8601 timestamp)

**DriverSessionResult** (per driver, per SessionResult):
- `driver_session_result_id` (INTEGER PK, server-scoped auto-increment)
- `session_result_id` (INTEGER, FK → SessionResult)
- `driver_id` (TEXT, FK → DriverProfile within server scope)
- `team_id` (INTEGER, FK → Team — the team the driver represented in this session)
- `finishing_position` (INTEGER, 1-indexed; null for CANCELLED sessions)
- `outcome_modifier` (ENUM: CLASSIFIED / DNF / DNS / DSQ)
- `tyre` (TEXT, nullable — qualifying sessions only; one of the five compounds a session may
  be run on, or null where the submission recorded none)
- `best_lap` (TEXT, nullable — lap time string or DNS/DNF/DSQ marker; qualifying sessions)
- `gap` (TEXT, nullable — qualifying sessions)
- `total_time` (TEXT, nullable — race sessions)
- `fastest_lap` (TEXT, nullable — race sessions)
- `time_penalties` (TEXT, nullable — race sessions; raw input value)
- `post_stewarding_total_time` (TEXT, nullable — reserved for post-stewarding corrections)
- `post_race_time_penalties` (TEXT, nullable — reserved for post-race penalty records)
- `points_awarded` (INTEGER, computed — 0 if outcome_modifier ≠ CLASSIFIED or session
  CANCELLED; otherwise sum of position points + fastest-lap bonus if eligible)
- `has_fastest_lap` (BOOLEAN, default false)
- `status` (ENUM: ACTIVE / SUPERSEDED, default ACTIVE)
- `superseded_at` (TEXT, nullable)
- `supersession_reason` (TEXT, nullable)

**DriverStandingsSnapshot** (per driver, per round, per division — standings state after
that round's results are finalised):
- `snapshot_id` (INTEGER PK, server-scoped auto-increment)
- `round_id` (INTEGER, FK → Round)
- `division_id` (INTEGER, FK → Division)
- `driver_id` (TEXT, FK → DriverProfile within server scope)
- `total_points` (INTEGER)
- `position` (INTEGER — driver's rank in the division at this round)
- `position_finish_counts` (TEXT — JSON map: position integer → finish count integer)
- `position_first_round` (TEXT — JSON map: position integer → round number integer,
  recording the first round in which this driver obtained each finishing position)

**TeamStandingsSnapshot** (per team, per round, per division — mirrors DriverStandingsSnapshot
for team-level aggregates):
- `snapshot_id` (INTEGER PK, server-scoped auto-increment)
- `round_id` (INTEGER, FK → Round)
- `division_id` (INTEGER, FK → Division)
- `team_id` (INTEGER, FK → Team)
- `total_points` (INTEGER)
- `position` (INTEGER)
- `position_finish_counts` (TEXT — JSON map)
- `position_first_round` (TEXT — JSON map)

### New Entities (v2.7.0)

**PenaltyRecord** (per `DriverSessionResult` — one row per applied penalty):
- `penalty_id` (INTEGER PK, server-scoped auto-increment)
- `driver_session_result_id` (INTEGER, FK → DriverSessionResult)
- `penalty_type` (ENUM: TIME_PENALTY / DSQ)
- `time_seconds` (INTEGER, nullable — magnitude in seconds; null for DSQ)
- `reason` (TEXT, nullable — free-text reason supplied by the tier-2 admin)
- `applied_by` (TEXT — Discord User ID of the tier-2 admin who applied the penalty)
- `applied_at` (TEXT — UTC ISO 8601 timestamp)
- `voided` (BOOLEAN, default false — set to true when an AppealRecord with status
  OVERTURNED is resolved against this penalty)
- `announcement_channel_id` (TEXT, nullable — the channel ID where the penalty notice
  was posted; retained to enable the appeal outcome follow-up post to the same channel)
- Replaces the loose `post_race_time_penalties` and `post_stewarding_total_time` fields
  on DriverSessionResult; those fields are retained for backwards compatibility during
  migration but are superseded by PenaltyRecord rows.

**AppealRecord** (per `PenaltyRecord` — at most one per penalty lifetime):
- `appeal_id` (INTEGER PK, server-scoped auto-increment)
- `penalty_id` (INTEGER, FK → PenaltyRecord)
- `status` (ENUM: PENDING / UPHELD / OVERTURNED, default PENDING)
- `submitted_by` (TEXT — Discord User ID of the driver submitting the appeal)
- `submitted_at` (TEXT — UTC ISO 8601 timestamp)
- `reviewed_by` (TEXT, nullable — Discord User ID of the reviewing tier-2 admin)
- `reviewed_at` (TEXT, nullable — UTC ISO 8601 timestamp)
- `review_reason` (TEXT, nullable — free-text outcome reason supplied by the reviewer)
- Uniquely keyed on `penalty_id`; a second appeal row for the same penalty MUST be
  rejected at the data layer.

*Amendment to DivisionResultsConfig (v2.4.0 entity, updated v2.7.0)*:
- `penalty_channel_id` (TEXT, nullable) added — when set, penalty announcements and
  appeal outcomes for this division are posted to this channel; if null, the bot falls
  back to `results_channel_id`.

### New Entities (v5.0.0)

**No database entity is added, and none is amended.** Track imagery is a resolution change, and it
is recorded here so that it is not re-derived as a schema one.

- `Track.country` is read as it stands. It has been an entity field since v2.9.0 and is now the
  datum a round's flag resolves by; nothing about the registry changes.
- The **nationality-to-country map** is a module-shipped constant, not a table. It belongs beside
  `utils/nationality_data.py`, whose `NATIONALITY_LOOKUP` already carries every country name the
  map needs as a key of its own — the obligation is to state the correspondence in the opposite
  direction, adjective to country, and to leave no canonical adjective out of it. Its totality is
  a unit-test obligation (Rule 13).
- **No asset class is added and no directory is configured.** Both `track_image_directory` and
  `flag_directory` are part of the configuration surface delivered at 035 and are read as they
  stand. The change is which class a field draws from and which datum keys the flag, not what a
  league configures.
- `resources/defaults/flags/mystery.svg` is a new packaged file under a reserved name, beside
  `resources/defaults/tracks/mystery.svg` (v4.6.0; relocated under `resources/defaults/` at v6.0.0). It ships no league-specific artwork and is bound by
  Rule 6 as any other asset.

### New Entities (v4.8.0)

**None.** The verdict image type introduces no entity and amends none, and the absence is recorded
here so that it is not re-derived.

- A verdict is posted once and is never edited, replaced or deleted, and **no message id is persisted
  for one**. This is Rule 17's static form at its strongest, and it needs no state whatever: no table
  records a verdict's message, and the image flow adds no column to any that exists.
- `PenaltyRecord`, `AppealRecord` and `DivisionResultsConfig.penalty_channel_id` (all v2.7.0) are read
  as they stand. So are the attendance module's autosack and autoreserve enforcements, whose
  announcements are verdicts of the third kind.
- `verdict_announcement_service.translate_penalty` is the descriptive rendering of a sanction, and is
  the code Rule 7's one rendering obliges the graphic to call. The **compact** rendering a results
  graphic places in a sanction column is a second presentation of the same datum and MUST NOT be
  substituted for it.
- `utils/font_metrics.py` and the `fonttools` declaration in `requirements.txt` are the third
  dependency the module was always specified to need — the means by which a text's width is measured
  (Rule 5) — and are read as they stand. Inkscape remains the one dependency no package declaration
  installs.
- The `verdicts_template` slot, the `verdicts` aspect and its toggle, the `images test verdict`
  subcommand, the flag directory and the team image directory are all part of the configuration
  surface delivered at 035 and 036, and are read as they stand. The test command was a parameter
  carrying choice values when this entry was written; 045 made each value a subcommand of its own,
  and 046 made the `division` and `round` parameters of every one of them optional.
- No asset class is added. The verdict draws a flag and a team image, both of classes already
  configured, and ships no file of its own: neither vocabulary is the module's, so Rule 13's
  closed-set clause does not arise.

### New Entities (v4.7.0)

**None.** The six weather image types introduce no entity and amend none, and the absence is recorded
here so that it is not re-derived.

- `forecast_messages` already keys a posted message by round, division and phase, and already admits
  phase `0` for the mystery notice (migration 006). Each phase's message is therefore separately
  addressable and separately replaceable, which is all the chain of Rule 8 needs; the image flow reads
  and writes exactly the rows the textual flow reads and writes, and no column is added.
- `Session` already carries `phase2_slot_type` and `phase3_slots` — the type drawn for a session and
  the sequence drawn within it — which are what the phase 2 and phase 3 graphics place.
- `MAX_SLOTS` and `SESSIONS_BY_FORMAT` in `models/session.py` are the constants Rule 12's third
  capacity reads to compute a slot's floor: four sessions of at most three slots for the sprint slot,
  two sessions of at most four for the plain one. They are read as they stand and MUST NOT be restated
  in the image module, a second copy being a second thing to get wrong.
- The six `weather_*_template` slots, the `weather` aspect and its toggle, the weather icon directory
  and the four `images test weather-*` subcommands are all part of the configuration surface delivered
  at 035 and 036, and are read as they stand. They were choice values of one test command when this
  entry was written; 045 made each a subcommand of its own, and 046 made the `division` and `round`
  parameters of every one of them optional.

**Shipped assets.** `resources/defaults/weather/` gains the eight files of the module's own weather vocabulary —
`sunny.svg`, `mixed.svg`, `rain.svg`, `clear.svg`, `light_cloud.svg`, `overcast.svg`, `wet.svg` and
`very_wet.svg` — beside the `fallback.svg` it already holds, per Rule 13's closed-set clause and the
author's ruling. This is the second class the module ships complete, after the markers, and the
directory remains league-overridable on exactly the ordinary terms.

The likelihood of rain, the session type draw and the slot draw are governed by Principle IV and are
read as the weather service computed and persisted them. The graphic computes none of them and derives
nothing: weather is the first image type to reach Rule 7's shared-rendering obligation without needing
its derived-presentation clause at all.

### New Entities (v4.6.0)

**None.** The two attendance image types introduce no entity and amend none, and the absence is
recorded here so that it is not re-derived. Each already has the column its lifecycle needs, and the
two lifecycles differ:

- `AttendanceDivisionConfig.attendance_message_id` holds the message carrying a division's sheet. The
  image flow deletes that message and persists the id of its replacement in the same column, an
  attachment being impossible to introduce into a message already posted (Rule 8), exactly as the
  results and lineup flows already do with theirs.
- `RsvpEmbedMessage.message_id` holds the message carrying a check-in call, and the image flow leaves
  it entirely alone. The call is never deleted and reposted while it stands; the embed is edited in
  place and the attachment rides through untouched (Rule 17). The graphic reaches this entity not at
  all, which is the point of declaring the type static.

The `attendance` and `rsvp` aspects, their `attendance_template` and `rsvp_template` slots, and the
flag, team-image and track-image directories are values of the configuration surface delivered at 035
and 036 and are read as they stand. `ASPECT_SOURCE_MODULE` already maps both aspects to the attendance
module, which is the relation Rule 3's widened sibling test reads. The attendance points, the pardons,
the autoreserve and autosack thresholds and the sanctions a sheet draws are the ones Principle XIII
already governs, and the graphic computes none of them; the check-in deadline is the one derived
value, subtracted in the attendance service under Rule 7's derived-presentation clause and never in
the image utility.

### New Entities (v4.5.0)

**DriverStandingsSnapshot** amended — `constructor_standings_message_id` (TEXT, nullable) added
beside the existing `standings_message_id`, and set upon the row of the top-ranked driver as that
column already is.

The textual flow posts one message carrying both championships, so one column sufficed. The image
flow posts two, the driver standings first and the constructor standings after, and each MUST be
deletable and replaceable without disturbing the other — Rule 4's unit of failure being one graphic,
and Rule 7's fallback being at that same grain. A single column could not name two messages. Both
columns are written on every posting of the standings, textual or graphic, so that the two flows
agree on which message is which; the textual flow leaves the second null.

This is the only part of the standings image type reaching outside the image module. The
classification, the position, the points and the countback that separates entries level on them are
governed by Principle XII and read as the standings service records them; the gap, the previous
position and the position change are derived under Rule 7's derived-presentation clause, in that same
service and not in the image utility. The marker, flag, team-image and track-image directories are
values of the configuration surface delivered at 035 and 036 and are read as they stand.

### New Entities (v4.4.0)

**None.** The results image type introduces no entity and amends none, and the absence is recorded
here so that it is not re-derived. `SessionResult.results_message_id` already holds the message
carrying a session's table; the image flow deletes that message and persists the id of its
replacement in the same column, an attachment being impossible to introduce into a message already
posted. The fastest-lap colour, the tyre directory and the flag directory are values of the
configuration surface delivered at 035 and 036 and are read as they stand. The classification, the
sanctions and the points a graphic draws are the ones Principle XII already governs, and the
graphic computes none of them (Principle XIV.7).

### New Entities (v4.3.0)

**None.** The lineup image type introduces no entity and amends none, and the absence is recorded
here so that it is not re-derived. The lineup message of a division has been replaced rather than
edited since long before the image module existed, and `Division.lineup_message_id` was added at
v2.8.0 for exactly that purpose; the image flow persists the id of its replacement in the same
column. The teams, the seats and the drivers occupying them are already governed by Principle IX
and read as they stand. The team-name invariant added to Principle IX at this version constrains a
value already stored and adds no column.

### New Entities (v4.1.0)

**Division** amended — `calendar_message_id` (TEXT, nullable) added, holding the id of the
message carrying the division's calendar in its `calendar_channel_id` channel. The textual
calendar has been posted once and never replaced, so no id was held. An attachment cannot be
introduced into a message already posted, so the image flow replaces that message rather than
editing it and must know which message to replace. It sits beside the `lineup_message_id` added
at v2.8.0 and is written on every posting of the calendar, textual or graphic, so that the two
flows agree on which message is the calendar.

`/division calendar sync` deletes that message and posts the calendar anew, persisting the id of
the replacement. It stands beside `/division calendar-channel` and is gated on neither the image
module nor any other: it refreshes whichever form of the calendar the server's configuration
calls for, and is refused only where the division has no calendar channel configured. The
mechanics of the replacement are user-visible and are specified in
`docs/wip-specs/image_module_specification.md`, not here.

### New Entities (v2.11.0)

**ImageConfig** (per server, owned by the Image generation module):
- `server_id` (TEXT, PK)
- `module_enabled` (BOOLEAN, default false)
- `asset_root` (TEXT, nullable) — filesystem root for league-supplied assets; null means the
  packaged defaults under `resources/defaults/` are used for every asset class.

**ImageAspectToggle** (per server, per output aspect — eight rows per server):
- `server_id` (TEXT)
- `aspect` (TEXT) — one of `calendar`, `lineup`, `results`, `standings`, `attendance`,
  `rsvp`, `weather`, `verdicts`.
- `enabled` (BOOLEAN, default false) — whether this aspect is drawn as an image when the
  module is enabled and its source module is enabled. Allows a league to keep text output
  for individual aspects.
- Uniquely keyed on (server_id, aspect).

The aspect is the unit a league toggles; the templates backing it are an implementation
detail of what the aspect draws. The mapping from aspect to template is a code constant,
not a table: eight aspects cover fifteen templates (weather alone accounts for six), and
no command addresses an individual template's toggle. `source_module` is likewise a
constant per aspect rather than a stored column, since it never varies per server.

Render *notices* (Principle XIV.4) are not persisted as their own entity. A notice is carried
on the outcome of the render that raised it and reported where XIV.4 requires — the calculation
log channel always, and the output of a commanding command additionally. Those destinations are
the whole of the obligation; the log channel is the durable record, and a log channel that cannot
be written to is itself reported in the interaction channel rather than passing silently.

Render *problems* (Principle XIV.4) are not persisted as their own entity either: a problem aborts
the render, falls back to text output, and is recorded in the existing audit log
(Principle V) alongside the source module's own output entry.

### New Entities (v2.10.0)

**AttendanceConfig** (per server, owned by the Attendance module):
- `server_id` (TEXT, PK)
- `module_enabled` (BOOLEAN, default false)
- `rsvp_notice_days` (INTEGER, default 5) — days before a round for RSVP embed posting.
- `rsvp_last_notice_hours` (INTEGER, default 1) — hours before round for un-RSVP'd ping;
  0 disables the last-notice ping.
- `rsvp_deadline_hours` (INTEGER, default 2) — hours before round when RSVP choices lock;
  0 means choices lock at round start time.
- `no_rsvp_penalty` (INTEGER, default 1) — attendance points per no-RSVP event.
- `absent_penalty` (INTEGER, default 1) — attendance points when a `NO_RSVP`, `TENTATIVE`, or
  `DECLINED` driver did not attend. Added on top of `no_rsvp_penalty` for a driver who
  neither RSVP'd nor attended.
- `no_show_penalty` (INTEGER, default 1) — attendance points when a driver RSVP'd `ACCEPTED`
  and did not attend.
- `autoreserve_threshold` (INTEGER, nullable — null means disabled) — total attendance
  points at which a full-time driver is automatically moved to Reserve.
- `autosack_threshold` (INTEGER, nullable — null means disabled) — total attendance points
  at which a driver is automatically removed from all team seats in all divisions.

**AttendanceDivisionConfig** (per server, per division, owned by the Attendance module):
- `server_id` (TEXT)
- `division_id` (INTEGER, FK → Division)
- `rsvp_channel_id` (TEXT, nullable) — channel for RSVP embeds and reserve distribution
  notices. Required before season approval when module is enabled.
- `attendance_channel_id` (TEXT, nullable) — channel for post-round attendance sheet posts.
  Required before season approval when module is enabled.
- Uniquely keyed on (server_id, division_id).

**DriverRoundAttendance** (per driver, per round, per division — one row per driver per
round while the Attendance module is enabled):
- `attendance_id` (INTEGER PK, server-scoped auto-increment)
- `round_id` (INTEGER, FK → Round)
- `division_id` (INTEGER, FK → Division)
- `driver_id` (TEXT, FK → DriverProfile within server scope)
- `rsvp_status` (ENUM: ACCEPTED / TENTATIVE / DECLINED / NO_RSVP, default NO_RSVP)
- `rsvp_timestamp` (TEXT, nullable — UTC ISO 8601; last time driver set status to
  ACCEPTED; reset each time driver returns to ACCEPTED)
- `rsvp_locked` (BOOLEAN, default false — set true at deadline or round start per locking
  rules in Principle XIII)
- `attended` (BOOLEAN, nullable — null until initial round results are submitted; true if
  driver appears in any DriverSessionResult for this round and division)
- `points_awarded` (INTEGER, nullable — null until post-race penalties are finalized;
  net points after pardons applied)
- `total_points_after` (INTEGER, nullable — cumulative attendance points for this driver
  in this division after this round's distribution)

**AttendancePardon** (per driver, per round, per attendance event type):
- `pardon_id` (INTEGER PK, server-scoped auto-increment)
- `attendance_id` (INTEGER, FK → DriverRoundAttendance)
- `pardon_type` (ENUM: NO_RSVP / ABSENT / NO_SHOW)
- `justification` (TEXT, nullable — logged to calculation log channel only; never
  displayed in public-facing output)
- `applied_by` (TEXT — Discord User ID of the tier-2 admin who applied the pardon)
- `applied_at` (TEXT — UTC ISO 8601 timestamp)
- Uniquely keyed on (attendance_id, pardon_type) — at most one pardon per event type
  per driver per round.

### New Entities (v2.9.0)

**Track** (bot-packaged static registry — 27 circuits as of this version):

The Track registry is the authoritative lookup table for all circuit data used across
rounds, weather generation, and future statistics. Each entry is bot-packaged and
immutable at the registry level; individual weather parameters may be overridden
per server via the `track_rpc_params` DB table (`/track config`).

Fields per track entry:

- `track_id` (TEXT — zero-padded two-digit string, e.g. `"01"`, `"27"`; stable PK within
  the registry; referenced by rounds and by autocomplete commands).
- `canonical_name` (TEXT — the short display name used in all bot output, e.g.
  `"United Kingdom"`, `"Las Vegas"`).
- `country` (TEXT — the country or territory in which the circuit is located, e.g.
  `"United Kingdom"`, `"United States of America"` — the spellings migration 029 seeds, and
  the datum a round's flag resolves by since v5.0.0).
- `circuit_name` (TEXT — the formal circuit/venue name, e.g. `"Silverstone Circuit"`,
  `"Las Vegas Strip Circuit"`).
- `mu_default` (REAL — bot-packaged mean rain probability; fractional 0–1).
- `sigma_default` (REAL — bot-packaged Beta dispersion; fractional 0–1).

The effective `(mu, sigma)` pair resolved at Phase 1 is: the server override stored in
`track_rpc_params` if present; otherwise `(mu_default, sigma_default)`.

**Track-based and tier-based statistics** (future module preparation):

Track-based stats (e.g., a driver's finishing positions or points scored at a specific
circuit) are derivable by joining `DriverSessionResult` → `SessionResult` → `Round`
→ `Track`. Tier-based stats (e.g., aggregated performance within a specific division tier)
are derivable by further joining via `Division.tier`. No additional entity is introduced
at this governance layer; the `Track` entity formalisation and the existing `Division.tier`
column are the authoritative structural prerequisites for these queries in the planned
"Season history and statistics" module (Principle VI).

### New Entities (v2.8.0)

*Amendment to Division (v1.0 entity, updated v2.8.0)*:
- `lineup_channel_id` (INTEGER, nullable) added to `divisions` — moved from
  `SignupDivisionConfig.lineup_channel_id`. When set, the bot deletes the previous lineup
  message and posts a fresh one to this channel on driver assignment changes in this division
  (Principle XI). Existing `lineup_channel_id` data is migrated from `signup_division_config`
  in migration 027.
- `calendar_channel_id` (INTEGER, nullable) added to `divisions` — when set, a calendar
  message listing all rounds is posted to this channel upon season approval (Principle XI).
- `lineup_message_id` (INTEGER, nullable) added to `divisions` — stores the Discord message
  ID of the most recently posted lineup message for this division (Principle XI, FR-014).
  Persisted to survive bot restarts.

*Amendment to SignupDivisionConfig (v2.6.0 entity, updated v2.8.0)*:
- `lineup_channel_id` removed — migrated to `divisions.lineup_channel_id` (migration 027).
- Remaining columns: `id`, `server_id`, `division_id`, `UNIQUE(server_id, division_id)`.
- The table is retained as an existence record for signup module per-division registrations.

### New Entities (v2.6.0)

**SignupDivisionConfig** (per server, per division — owned by the signup module):
- `server_id` (TEXT)
- `division_id` (INTEGER, FK → Division)
- `lineup_channel_id` (TEXT, nullable) — *removed v2.8.0; migrated to divisions table* (Principle XI).
- Uniquely keyed on (server_id, division_id). Created lazily on first per-division signup
  configuration; if absent, no lineup notices are posted for that division.

*Amendment to SignupConfiguration (v2.2.0 entity, updated v2.6.0)*:
- `close_at` (TEXT, nullable) added — see SignupConfiguration definition above.

### New Entities (v2.5.0)

No new database schema entities are introduced at this governance layer. The Season Archive
is a governance concept formalising that `Season` records (and all their associated data
— Division, Round, SessionResult, DriverStandingsSnapshot, SeasonAssignment, etc.) in the
`COMPLETED` state are permanently retained. Concrete schema additions (e.g., archive
indexing tables, a dedicated stats-query layer, or migration scaffolding to clear any prior
ephemeral-deletion logic) are scoped to the season persistence feature specification.

## Governance

This constitution supersedes all other development practices and conventions for this project.
Amendments require:

1. A documented rationale for the proposed change.
2. A version bump per the semantic versioning policy below.
3. Updates to all affected templates and runtime guidance files before the amendment is merged.

**Versioning policy**:

- **MAJOR**: Removal or backward-incompatible redefinition of a Core Principle.
- **MINOR**: Addition of a new principle, section, or materially expanded guidance.
- **PATCH**: Clarifications, wording improvements, or non-semantic refinements.

All pull requests MUST include a Constitution Check confirming compliance with Principles I–XIV
before merge. Any deliberate violation of a principle MUST be documented in the plan's
Complexity Tracking table with a justification for why the simpler compliant path is
insufficient.

**Version**: 7.11.0 | **Ratified**: 2026-03-03 | **Last Amended**: 2026-09-02
