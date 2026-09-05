# The open decisions in spec section 17

Where the build takes a position, it says so and says what it costs to change.
Where it does not, it says what would settle it.

## 1. Neon versus RDS

**Position: kept open, deliberately, and cheap to settle.**

The abstraction is one Terraform variable (`db_host_backend`) and one control
plane column (`tenant.db_host`). Nothing above that layer knows which is in use:
the platform assembles its DSN from the registry at call time, and the only
Postgres feature the platform relies on beyond plain SQL is `daterange` and
partial indexes, both of which are core.

What would settle it: the first PE security questionnaire. Neon's branching is
worth real money for per-tenant dev and point-in-time investigation, and
database-per-tenant is its intended pattern. But if the questionnaire asks for a
named VPC, a private endpoint and a shared-responsibility document, RDS answers
in a paragraph and Neon answers in a call. Start on Neon for phases 0 and 1; the
migration is a `pg_dump`/`pg_restore` per tenant and a registry update.

## 2. Is the tenant boundary the acquirer or the fund?

**Position: the acquirer, as spec section 1.2 assumes. Reversible upward, not
downward.**

The registry models `tenant → tenant_firm`, one platform firm and N add-ons. A
sponsor running three unrelated platforms gets three tenants, three databases,
three KMS keys, and no consolidated view across them.

Adding a level above the tenant later is additive: a `sponsor` table in the
control plane and a cross-tenant reporting path that reads issued packs rather
than tenant data. Going the other way — splitting one tenant into three after
loading — is a data migration per firm, so the default is the finer grain.

The thing that would change the answer: a sponsor who wants one board pack across
three unrelated platforms. That report cannot be built from tenant databases
without breaking the isolation the architecture is priced on, so it would have to
be built from *packs*, which is a different product.

## 3. Read API

**Position: not built. Packs and drill-through only.**

`fracture.pack.drill.resolve` is the drill-through path and it is deliberately a
library call plus a CLI command, not an HTTP endpoint. The moment there is an
API, isolation has to hold at an application layer too — every request needs a
tenant claim, every query needs that claim bound, and one missing `WHERE` is the
cross-tenant leak the database-per-tenant model exists to make impossible.

If a client asks for programmatic access, the cheapest safe answer is a scheduled
export into their own storage, using the same per-tenant credential path. The
next cheapest is a read API where the tenant claim selects the *database
connection*, not a filter — which keeps the guarantee where it is.

## 4. Raw retention

**Position: seven years, per-tenant override, object-locked.**

`raw_retention_days` defaults to 2557 in the tenant module, with S3 object lock
in GOVERNANCE mode on the raw prefix and COMPLIANCE mode on the audit bucket.
Indefinite is the right answer for the audit story and the wrong answer for a
breach; seven years matches the outer edge of what an adviser is likely to be
asked for.

The override exists because a client whose own policy is shorter will ask, and
"we hold your data forever" is a bad sentence in a security questionnaire.

## 5. dbt versus SQLMesh

**Position: neither is the executor today. See `docs/DEVIATIONS.md`.**

The mart models are parameterised SQL run by a small runner, because the binding
requirement — every mart must pin the pack's `system_time` — is not something
either tool gives, and a mart that reads `canon` without it silently
double-counts restated rows. The runner refuses such SQL outright.

What would move this: per-tenant environment management becoming the primary
source of operational pain, which is the spec's own trigger. At that point
SQLMesh's restatement semantics are worth more than dbt's hiring pool, because
restatement is the thing this platform already models by hand.
