# Data profiling

Service line: platform implementation, the first look at a file a client hands
over (CRM export, custodian file, general ledger).

`fracture_profiling` reads CSV and JSON and reports row counts, null counts,
distinct counts and an inferred type per column. The report has no cell
values. Columns whose names look like an account number, a tax identifier or
a secret are marked sensitive and are not typed from their contents.

This module was written in this repository. `fracturesystems-data-profiling`
could not be read; see `docs/PROVENANCE.md`. Do not treat this as a merge of
that repository.

```
PYTHONPATH=data-profiling python -c "from fracture_profiling import profile_tree; print(profile_tree('extracts/'))"
```
