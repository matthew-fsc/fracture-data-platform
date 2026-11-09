# Assessment

Service line: architecture assessment (fixed fee, one to two weeks), and the
systems and data diligence that opens an M&A fold-in.

## Entry point

Fracture OS renders findings with `assessment.render_findings(engagement, findings)`
and lists the templates with `assessment.templates()`. The computed coverage
assessment is:

```
fracture assess <systems> --pack wealth --json
```

`assessment.assess_command(sources, pack)` returns that argv. From the repo root,
`PYTHONPATH=. python -m assessment engagement.yml findings.yml` prints the report.

The written deliverable is the four templates in `templates/`. The coverage
numbers inside it are computed. `fracture assess <sources> --pack wealth` (or
`--pack services`) prices every canonical entity a named system does not
cover, and `fracture estimate` is the same figure for a fold-in. A gap the
estimator cannot see is a gap that gets written down as manual, not omitted.

`render_findings.py` turns a findings file into the report. It copies the
statement and the evidence pointer. It has no field for an amount, so it
cannot become a place where a figure is recalculated.

```
python assessment/render_findings.py engagement.yml findings.yml
```
