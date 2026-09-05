-- Billable AUM by household and month-end.
--
-- `billable` comes from the portfolio system, not the custodian: the custodian
-- has no view of the advisory agreement. An account flagged non-billable is
-- excluded here, which is why expected revenue does not silently include
-- held-away assets.
--
-- depends_on: canon.balance_snapshot, canon.account
drop table if exists mart.household_aum;
create table mart.household_aum as
with accounts as (
  select distinct on (firm_id, account_id)
         firm_id, account_id, household_id, billable, closed_on
    from canon.account
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, account_id, recorded_at desc
),
balances as (
  select distinct on (firm_id, account_id, as_of_date)
         firm_id, account_id, as_of_date, market_value, billable_value
    from canon.balance_snapshot
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, account_id, as_of_date, recorded_at desc
)
select b.firm_id,
       a.household_id,
       b.as_of_date,
       sum(b.market_value)                                          as total_value,
       -- Coalesced: a household whose every account is non-billable has a
       -- billable basis of zero, not an unknown one. Leaving it null lets it
       -- vanish out of downstream sums without anything looking wrong.
       coalesce(sum(b.market_value) filter (where a.billable), 0)   as billable_value,
       count(*)                                                     as account_count,
       count(*) filter (where not a.billable)                       as non_billable_accounts,
       array_agg(distinct b.account_id order by b.account_id)       as account_ids
  from balances b
  join accounts a on a.firm_id = b.firm_id and a.account_id = b.account_id
 where a.household_id is not null
 group by b.firm_id, a.household_id, b.as_of_date;

create index on mart.household_aum (firm_id, as_of_date);
create index on mart.household_aum (firm_id, household_id, as_of_date);
