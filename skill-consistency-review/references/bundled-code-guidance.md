# Bundling code for consistency — advisory guidance

This is the framing for the "Improving consistency through bundled code" section of a
findings document. It is a SUGGESTION, not a hard rule, and it is offered only where it
would genuinely help. Tailor it to where the skill under review actually is.

## The evidence behind the suggestion

Across skills reviewed with this method, run-to-run consistency correlates strongly with
how much of the artifact assembly is pinned in bundled code/templates/fixed queries
versus left to the model to redo each run. The parts that stayed identical across runs
were the ones backed by a fixed artifact (a builder script, a template, a query pack, a
terminology lexicon). The parts that drifted were the ones the model re-decided each run
(free-hand HTML assembly, ad-hoc query adaptation, number/label formatting, which
sections to include). Divergence appeared exactly at the un-pinned steps.

So this is not an abstract best practice — for these skills it is the single best
predictor of consistency observed.

## The principle

Pin the deterministic transforms in bundled code; reserve the model for genuine judgment.

- Good candidates to pin in code/templates/SQL: math and rollups, number/label
  formatting, table column sets, section/tile composition, date-window arithmetic,
  empty-state strings, and parameterized queries (including channel/marketplace
  variants).
- Keep with the model: scope decisions, the narrative/headline finding, which
  competitors/categories/items matter, threshold choices — genuine judgment.

## Two caveats (so this stays a suggestion, not dogma)

1. Bundled code only helps if it is robust on the real input space. Bundled code that
   faults on a common case (an empty optional table, a vendor account, a missing field)
   forces each run to hand-patch it, and different patches produce different output — the
   bundled code becomes a divergence amplifier, not a fix. Bundle the code AND make it
   handle the edge cases deterministically.
2. Do not codify genuine judgment. Forcing analytical judgment into rigid code usually
   makes the output worse; that is where the model should stay in the loop.

Corollary: a bundled artifact that is WRONG (a query with a bad column name, a schema doc
that lists a column that does not exist) is worse than none, because it is trusted —
only runs that independently re-verify will catch it, which is itself nondeterministic.
Verify bundled queries/docs against the live source once, at authoring time.

## Tailor the suggestion

- No builder / free-hand assembly -> suggest introducing a small deterministic build step
  (a script + a data-shape contract) that owns the mechanical rendering.
- Already has a builder that works -> suggest extending its boundary upstream to cover the
  last hand-authored bits (labels, trimming rules, rounding), rather than rebuilding.
- Has a query pack but hand-adapts variants -> suggest shipping the explicit variants
  (e.g. a Vendor version of each Seller query) so they are substituted, not improvised.
- Push the deterministic boundary as far upstream as is reasonable: the more the bundled
  code owns (query execution and result-shaping, not just final rendering), the less is
  left to vary between runs.
