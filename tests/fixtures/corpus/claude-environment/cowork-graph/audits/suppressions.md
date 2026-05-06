---
suppressions: []
---

# Audit Suppressions

Hand-curated allowlist of audit findings that are intentional. Each entry
suppresses one finding by `rule` + `key`. Add a `reason` and the date you added it.

To suppress a finding: identify its rule name and key fields from the audit
report, write an entry above. Re-run the audit to confirm it disappears from
the count.
