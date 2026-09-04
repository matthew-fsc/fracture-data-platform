-- Tenant database: schema layout (spec section 6.4)
--   raw -> stg -> canon -> mart -> pack, with lineage and ai alongside.

create extension if not exists "pgcrypto";

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
