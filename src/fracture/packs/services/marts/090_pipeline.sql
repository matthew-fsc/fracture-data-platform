-- Pipeline and the book by stage.
--
-- A consulting firm's forward view is engagements by stage, weighted. Not a
-- forecast: a weighting applied to an agreed fee is arithmetic, and the stage
-- is recorded by a human in the system of record. Nothing here is modelled.
--
-- depends_on: canon.engagement, canon.client, mart.engagement_margin
drop table if exists mart.pipeline;
create table mart.pipeline as
with engagements as (
  select distinct on (firm_id, engagement_id)
         firm_id, engagement_id, client_id, name, fee_basis, agreed_fee, stage,
         responsible_producer_id, started_on, due_on, closed_on
    from canon.engagement
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, engagement_id, recorded_at desc, canon_id desc
),
clients as (
  select distinct on (firm_id, client_id)
         firm_id, client_id, name, industry
    from canon.client
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, client_id, recorded_at desc, canon_id desc
),
delivered as (
  select firm_id, engagement_id,
         sum(billed_amount)    as billed_to_date,
         sum(margin_amount)    as margin_to_date,
         sum(recorded_hours)   as hours_to_date,
         max(period_end)       as last_period
    from mart.engagement_margin
   group by 1, 2
)
select e.firm_id,
       e.engagement_id,
       e.client_id,
       c.name                                      as client_name,
       c.industry,
       e.name                                       as engagement_name,
       e.fee_basis,
       e.stage,
       e.responsible_producer_id,
       e.agreed_fee,
       e.started_on,
       e.due_on,
       coalesce(d.billed_to_date, 0)                as billed_to_date,
       coalesce(d.margin_to_date, 0)                as margin_to_date,
       coalesce(d.hours_to_date, 0)                 as hours_to_date,
       -- Unbilled balance on a priced engagement. Positive means fee still to
       -- invoice; negative means the engagement has billed beyond its agreed
       -- fee, which needs a change order rather than a note.
       case when e.agreed_fee is not null
            then round(e.agreed_fee - coalesce(d.billed_to_date, 0), 2)
       end                                          as fee_remaining,
       -- Stage weighting, applied to the agreed fee. Flat per stage and stated
       -- here rather than tuned, because a weighting fitted to past conversion
       -- is a model producing a figure with financial consequence.
       case e.stage
         when 'pipeline'  then 0.25
         when 'active'    then 1.00
         when 'delivered' then 1.00
         when 'closed'    then 1.00
         else 0.00
       end                                          as stage_weight,
       round(
         coalesce(e.agreed_fee, 0)
           * case e.stage
               when 'pipeline' then 0.25
               when 'active' then 1.00
               when 'delivered' then 1.00
               when 'closed' then 1.00
               else 0.00
             end, 2)                                as weighted_fee,
       -- Overdue against the agreed due date, measured at the period end the
       -- marts were built for rather than against the wall clock, so the
       -- figure reproduces.
       case when e.due_on is not null and e.stage in ('active','pipeline')
            then greatest((%(system_time)s::date - e.due_on), 0)
       end                                          as days_past_due
  from engagements e
  left join clients c on c.firm_id = e.firm_id and c.client_id = e.client_id
  left join delivered d on d.firm_id = e.firm_id and d.engagement_id = e.engagement_id;

create index on mart.pipeline (firm_id, stage);
