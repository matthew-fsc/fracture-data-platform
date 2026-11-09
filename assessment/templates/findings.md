# Findings

Copy this shape into `findings.yml` and render it with
`python assessment/render_findings.py engagement.yml findings.yml`.

A finding states what is wrong, points at the evidence, and says what to do.
The evidence is a pack figure id, a drill path, an artifact hash, or a
reconciliation check name. The statement may describe the figure. The file
does not carry a second copy of the number for the renderer to do arithmetic
on.

```yaml
# engagement.yml
client: "Client legal name"
pack: wealth
period: "2026-01-01 to 2026-03-31"
```

```yaml
# findings.yml
- id: F1
  severity: high
  owner: "billing operations"
  statement: "Invoiced fees do not reproduce from the custodian control total."
  evidence: "recon check aum_total, pack figure mart.unbilled"
  figure_ref: "mart.unbilled|<firm>|<household>"
  recommendation: "Resolve the unmatched accounts before the pack is issued."
```

Severity is high, medium or low. Owner is the person who can close it.
Recommendation is a next action, not a redesigned fee schedule computed in
this document.
