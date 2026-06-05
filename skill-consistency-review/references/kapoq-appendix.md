# Kapoq appendix

Conventions for reviewing Kapoq MCP skills specifically. The core methodology in `SKILL.md`
is platform-agnostic; this file holds the Kapoq specifics you need when the skill under
review queries Kapoq data.

## Seller vs Vendor

- Many skills ship Seller-default queries; reviewing one against a Vendor brand surfaces
  whether the Vendor adaptation is pinned or improvised (a common divergence source).

## Frozen-input pattern for Kapoq dashboard skills

Freeze the human decisions — brand, connector/tenant, exact account, date window, theme,
thresholds — and let each run do its own discovery (channel detection, table
availability, freshness), query execution, and build. That isolates the skill's
determinism from legitimate input variance. Use exact account strings verbatim (watch for
double spaces and apostrophes; escape `'` as `''` in SQL).
