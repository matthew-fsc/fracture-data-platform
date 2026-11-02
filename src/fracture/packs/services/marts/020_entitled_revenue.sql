-- What the rate card entitles the firm to bill.
--
-- This is the figure nobody else can produce, and it is the whole reason this
-- pack exists. A practice management system reports what was invoiced. It
-- cannot report what *should* have been invoiced, because that requires
-- recomputing billable hours against the rate card in force on the work date,
-- net of the client's agreed discount. Read back out of the billing system,
-- entitlement and invoiced are the same number by construction and the gap
-- between them is invisible.
--
-- Fixed-fee work is entitled to its agreed fee, apportioned across the months
-- the engagement is live, not to hours times a rate. Mixing the two is how a
-- fixed-fee overrun gets reported as a realisation problem.
--
-- depends_on: mart.time_recorded, canon.rate_card_line, canon.rate_card_assignment, canon.engagement
drop table if exists mart.entitled_revenue;
create table mart.entitled_revenue as
with engagements as (
  select distinct on (firm_id, engagement_id)
         firm_id, engagement_id, client_id, fee_basis, agreed_fee, stage,
         responsible_producer_id, started_on, due_on, closed_on
    from canon.engagement
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, engagement_id, recorded_at desc, canon_id desc
),
assignments as (
  select distinct on (firm_id, engagement_id, effective_from)
         firm_id, engagement_id, rate_card_id, discount_pct, effective_from, effective_to
    from canon.rate_card_assignment
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, engagement_id, effective_from, recorded_at desc, canon_id desc
),
card_lines as (
  select distinct on (firm_id, rate_card_id, grade)
         firm_id, rate_card_id, grade, charge_rate
    from canon.rate_card_line
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, rate_card_id, grade, recorded_at desc, canon_id desc
),
-- The card in force for the engagement at the end of each period. Effective
-- dating means a repriced engagement keeps last quarter's entitlement at last
-- quarter's rates, which is what makes a prior period still reproduce.
in_force as (
  select distinct on (t.firm_id, t.engagement_id, t.period_end)
         t.firm_id, t.engagement_id, t.period_end, a.rate_card_id, a.discount_pct
    from mart.time_recorded t
    join assignments a
      on a.firm_id = t.firm_id
     and a.engagement_id = t.engagement_id
     and a.effective_from <= t.period_end
     and (a.effective_to is null or a.effective_to >= t.period_end)
   order by t.firm_id, t.engagement_id, t.period_end, a.effective_from desc,
            a.rate_card_id
),
time_priced as (
  select t.firm_id,
         t.engagement_id,
         t.period_end,
         sum(t.billable_hours * l.charge_rate * (1 - f.discount_pct)) as entitled_time_amount,
         sum(t.billable_hours * l.charge_rate)                        as gross_card_amount,
         -- Every recorded hour at card rate, written off or not. The opening
         -- balance of the realisation bridge: a bridge that starts at billable
         -- hours has already lost the write-off step before it begins.
         sum(t.recorded_hours * l.charge_rate)                        as gross_recorded_amount,
         sum(t.billable_hours)                                        as billable_hours,
         sum(t.recorded_hours)                                        as recorded_hours,
         sum(t.cost_amount)                                           as cost_amount
    from mart.time_recorded t
    join in_force f
      on f.firm_id = t.firm_id and f.engagement_id = t.engagement_id
     and f.period_end = t.period_end
    join card_lines l
      on l.firm_id = t.firm_id and l.rate_card_id = f.rate_card_id and l.grade = t.grade
   group by 1, 2, 3
),
-- A fixed, capped or retainer engagement is entitled to its agreed fee spread
-- over the periods it was live in. Straight-line rather than percentage of
-- completion: the firm has no reliable completion estimate, and a made-up one
-- would be a model computing a figure with financial consequence.
months_live as (
  select p.firm_id,
         p.engagement_id,
         count(*) over (partition by p.firm_id, p.engagement_id) as live_months,
         p.period_end
    from (select distinct firm_id, engagement_id, period_end from mart.time_recorded) p
),
fixed_priced as (
  select m.firm_id,
         m.engagement_id,
         m.period_end,
         round(e.agreed_fee / nullif(m.live_months, 0), 2) as entitled_fixed_amount
    from months_live m
    join engagements e on e.firm_id = m.firm_id and e.engagement_id = m.engagement_id
   where e.fee_basis in ('fixed','capped','retainer')
     and e.agreed_fee is not null
)
select t.firm_id,
       t.engagement_id,
       e.client_id,
       t.period_end,
       e.fee_basis,
       e.stage,
       e.responsible_producer_id,
       t.recorded_hours,
       t.billable_hours,
       t.cost_amount,
       -- What the card alone would charge, before the client's discount. Kept
       -- so a thin margin can be traced to discounting rather than to delivery.
       round(t.gross_card_amount, 2)                            as gross_card_amount,
       round(t.gross_recorded_amount, 2)                        as gross_recorded_amount,
       -- Entitlement. Hours times card for T&M; the apportioned agreed fee
       -- otherwise. A capped engagement is entitled to the lower of the two,
       -- which is the cap doing its job.
       round(
         case
           when e.fee_basis = 'time_and_materials' then t.entitled_time_amount
           when e.fee_basis = 'capped' then least(
             t.entitled_time_amount, coalesce(f.entitled_fixed_amount, t.entitled_time_amount)
           )
           else coalesce(f.entitled_fixed_amount, 0)
         end, 2)                                                as entitled_amount,
       -- For fixed work, what the same hours would have earned on the card.
       -- The gap is the overrun, in money, in the month it happened, rather
       -- than at the end of the engagement.
       round(t.entitled_time_amount, 2)                         as time_value_amount
  from time_priced t
  join engagements e on e.firm_id = t.firm_id and e.engagement_id = t.engagement_id
  left join fixed_priced f
    on f.firm_id = t.firm_id and f.engagement_id = t.engagement_id
   and f.period_end = t.period_end;

create index on mart.entitled_revenue (firm_id, period_end);
create index on mart.entitled_revenue (firm_id, engagement_id, period_end);
