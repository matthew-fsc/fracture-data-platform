-- Tenant database: schema layout (spec section 6.4)
--   raw -> stg -> canon -> mart -> pack, with lineage and ai alongside.
--
-- Every script under this directory is applied by the tenant's `owner` role, so
-- nothing here may require superuser. gen_random_uuid() is built into Postgres
-- 13+, which is why pgcrypto is not installed: it is an untrusted extension and
-- requiring it would force migrations to run as superuser, leaving `owner`
-- owning none of the objects it is supposed to migrate.

create schema if not exists raw;
create schema if not exists stg;
create schema if not exists canon;
create schema if not exists mart;
create schema if not exists pack;
create schema if not exists lineage;
create schema if not exists ai;
create schema if not exists recon;

comment on schema raw is 'Append-only landing. Never updated, never deleted outside retention.';
comment on schema canon is 'Source-agnostic canonical entities, bitemporal.';
comment on schema lineage is 'Row-grain lineage. Every mart figure opens to the records behind it.';
