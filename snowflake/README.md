# Snowflake

Service line: platform implementation, the warehouse decision.

The operating platform in this repository is Postgres 16, one database per
tenant. `docs/spec-v1.md` records why a separate warehouse was set aside for
the retainer: the cost floor of a warehouse per tenant exceeds the retainer
margin at the data volume these engagements move. `docs/OPEN_DECISIONS.md`
leaves the Postgres host (Neon or RDS) open and does not reopen the warehouse.

`fracturesystems-snowflake-eval` was not readable from this workspace. No
benchmark, script or score from it is in this directory, and none was written
to stand in for it. An engagement that already runs Snowflake gets that fact
in the assessment inventory. It does not get an evaluation this repo does not
contain.

This module is not a merge of the evaluation repository. That repository is
not safe to archive.
