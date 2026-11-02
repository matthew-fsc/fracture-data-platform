-- The comparable KPI set, long format, with a peer benchmark and a rank.
--
-- Long rather than wide on purpose: the scorecard the dashboard renders is
-- generic, so adding a KPI is a row here rather than a column somewhere and a
-- template change everywhere. Each row carries what a reader needs to judge the
-- number without knowing the business: its unit, whether higher is better, the
-- platform-wide figure to compare against, and where the firm ranks.
--
-- `direction` matters more than it looks. A dashboard that colours every
-- downward move red is wrong half the time, and readers stop trusting the
-- colour rather than reading the metric.
--
-- depends_on: mart.firm_scorecard
drop table if exists mart.firm_kpi;
create table mart.firm_kpi as
with latest as (select max(period_end) as period_end from mart.firm_scorecard),
current_scorecard as (
  select s.* from mart.firm_scorecard s join latest l on l.period_end = s.period_end
),
prior as (
  select s.firm_id, s.period_end, s.actual_yield_bps, s.realization_rate,
         s.collection_rate, s.loaded_margin_pct, s.leakage_rate, s.sla_attainment,
         s.cost_income_ratio, s.revenue_per_producer, s.aum_per_household,
         s.households_per_producer, s.top_producer_share, s.total_aum,
         row_number() over (partition by s.firm_id order by s.period_end desc) as recency
    from mart.firm_scorecard s
   where s.period_end < (select period_end from latest)
),
previous_scorecard as (select * from prior where recency = 1),
-- The consolidated line is computed from the firms rather than averaged across
-- them: an unweighted mean of three firms' yields is not the platform's yield.
consolidated as (
  select (select period_end from latest)                              as period_end,
         sum(total_aum)                                               as total_aum,
         sum(household_count)                                         as household_count,
         sum(active_producers)                                        as active_producers,
         sum(expected_amount)                                         as expected_amount,
         sum(billed_amount)                                           as billed_amount,
         sum(collected_amount)                                        as collected_amount,
         sum(loaded_margin)                                           as loaded_margin,
         sum(cost_to_serve)                                           as cost_to_serve,
         sum(leak_total)                                              as leak_total,
         sum(sla_events)                                              as sla_events,
         sum(sla_breaches)                                            as sla_breaches
    from current_scorecard
),
peer as (
  select period_end,
         round(expected_amount * 4 / nullif(total_aum, 0) * 10000, 2)  as schedule_yield_bps,
         round(billed_amount * 4 / nullif(total_aum, 0) * 10000, 2)    as actual_yield_bps,
         round(collected_amount * 4 / nullif(total_aum, 0) * 10000, 2) as collected_yield_bps,
         round(billed_amount / nullif(expected_amount, 0), 6)          as realization_rate,
         round(collected_amount / nullif(billed_amount, 0), 6)         as collection_rate,
         round(leak_total / nullif(expected_amount, 0), 6)             as leakage_rate,
         round(loaded_margin / nullif(billed_amount, 0), 6)            as loaded_margin_pct,
         round(cost_to_serve / nullif(billed_amount, 0), 6)            as cost_income_ratio,
         round(total_aum / nullif(household_count, 0), 2)              as aum_per_household,
         round(billed_amount / nullif(household_count, 0), 2)          as revenue_per_household,
         round(cost_to_serve / nullif(household_count, 0), 2)          as cost_per_household,
         round(household_count::numeric / nullif(active_producers, 0), 2)
                                                                       as households_per_producer,
         round(billed_amount / nullif(active_producers, 0), 2)         as revenue_per_producer,
         round(1 - sla_breaches::numeric / nullif(sla_events, 0), 6)   as sla_attainment
    from consolidated
),
-- (kpi, department, label, unit, direction, sort) with the firm value, the prior
-- value and the platform value, unpivoted.
-- Current joined to prior first: a LATERAL cannot see a join that comes after
-- it, and the prior-period value is needed inside the unpivot.
joined as (
  select c.*,
         p.actual_yield_bps        as prior_actual_yield_bps,
         p.realization_rate        as prior_realization_rate,
         p.collection_rate         as prior_collection_rate,
         p.loaded_margin_pct       as prior_loaded_margin_pct,
         p.leakage_rate            as prior_leakage_rate,
         p.sla_attainment          as prior_sla_attainment,
         p.cost_income_ratio       as prior_cost_income_ratio,
         p.revenue_per_producer    as prior_revenue_per_producer,
         p.aum_per_household       as prior_aum_per_household,
         p.households_per_producer as prior_households_per_producer,
         p.top_producer_share      as prior_top_producer_share
    from current_scorecard c
    left join previous_scorecard p on p.firm_id = c.firm_id
),
metrics as (
  select c.firm_id, c.period_end, m.*
    from joined c
    cross join lateral (values
      ('schedule_yield_bps',      'finance',        'Schedule yield',            'bps',   'neutral',       10,
        c.schedule_yield_bps,      null::numeric,                 (select schedule_yield_bps from peer)),
      ('actual_yield_bps',        'executive',      'Realised yield',            'bps',   'higher_better', 20,
        c.actual_yield_bps,        c.prior_actual_yield_bps,            (select actual_yield_bps from peer)),
      ('collected_yield_bps',     'finance',        'Collected yield',           'bps',   'higher_better', 30,
        c.collected_yield_bps,     null::numeric,                 (select collected_yield_bps from peer)),
      ('realization_rate',        'executive',      'Realisation rate',          'ratio', 'higher_better', 40,
        c.realization_rate,        c.prior_realization_rate,            (select realization_rate from peer)),
      ('collection_rate',         'executive',      'Collection rate',           'ratio', 'higher_better', 50,
        c.collection_rate,         c.prior_collection_rate,             (select collection_rate from peer)),
      ('leakage_rate',            'executive',      'Leakage',                   'ratio', 'lower_better',  60,
        c.leakage_rate,            c.prior_leakage_rate,                (select leakage_rate from peer)),
      ('loaded_margin_pct',       'executive',      'Loaded margin',             'ratio', 'higher_better', 70,
        c.loaded_margin_pct,       c.prior_loaded_margin_pct,           (select loaded_margin_pct from peer)),
      ('cost_income_ratio',       'profitability',  'Cost to income',            'ratio', 'lower_better',  80,
        c.cost_income_ratio,       c.prior_cost_income_ratio,           (select cost_income_ratio from peer)),
      ('revenue_per_household',   'profitability',  'Revenue per household',     'usd',   'higher_better', 90,
        c.revenue_per_household,   null::numeric,                 (select revenue_per_household from peer)),
      ('cost_per_household',      'profitability',  'Cost to serve a household', 'usd',   'lower_better',  100,
        c.cost_per_household,      null::numeric,                 (select cost_per_household from peer)),
      ('margin_per_household',    'profitability',  'Margin per household',      'usd',   'higher_better', 110,
        c.margin_per_household,    null::numeric,                 null::numeric),
      ('aum_per_household',       'profitability',  'AUM per household',         'usd',   'neutral',       120,
        c.aum_per_household,       c.prior_aum_per_household,           (select aum_per_household from peer)),
      ('households_per_producer', 'operations',     'Households per advisor',    'count', 'neutral',       130,
        c.households_per_producer, c.prior_households_per_producer,     (select households_per_producer from peer)),
      ('revenue_per_producer',    'operations',     'Revenue per advisor',       'usd',   'higher_better', 140,
        c.revenue_per_producer,    c.prior_revenue_per_producer,        (select revenue_per_producer from peer)),
      ('sla_attainment',          'executive',      'SLA attainment',            'ratio', 'higher_better', 150,
        c.sla_attainment,          c.prior_sla_attainment,              (select sla_attainment from peer)),
      ('sla_open',                'operations',     'Open past target',          'count', 'lower_better',  160,
        c.sla_open::numeric,       null::numeric,                 null::numeric),
      ('cycle_p90_hours',         'operations',     'Cycle time, p90',           'hours', 'lower_better',  170,
        c.cycle_p90_hours,         null::numeric,                 null::numeric),
      ('top_producer_share',      'executive',      'Largest advisor book',      'ratio', 'lower_better',  180,
        c.top_producer_share,      c.prior_top_producer_share,          null::numeric),
      ('top10_client_revenue_share', 'advisory',    'Top ten clients',           'ratio', 'lower_better',  190,
        c.top10_client_revenue_share, null::numeric,              null::numeric),
      ('departed_book_share',     'advisory',       'Book held by leavers',      'ratio', 'lower_better',  200,
        c.departed_book_share,     null::numeric,                 null::numeric)
    ) as m(kpi, department, label, unit, direction, sort_order, value, prior_value, peer_value)
)
select firm_id,
       period_end,
       kpi,
       department,
       label,
       unit,
       direction,
       sort_order,
       value,
       prior_value,
       case when prior_value is null or prior_value = 0 then null
            else round((value - prior_value) / abs(prior_value), 6) end as change_pct,
       peer_value,
       case when peer_value is null or peer_value = 0 then null
            else round((value - peer_value) / abs(peer_value), 6) end   as variance_to_peer,
       -- Rank respects direction, so rank 1 always means best rather than
       -- largest. A leaderboard where first place is worst is a bug readers
       -- blame themselves for.
       case direction
         when 'lower_better' then rank() over (partition by kpi order by value asc nulls last)
         when 'higher_better' then rank() over (partition by kpi order by value desc nulls last)
         else null
       end                                                              as firm_rank,
       count(*) over (partition by kpi)                                 as firms_ranked
  from metrics;

create index on mart.firm_kpi (department, sort_order);
create index on mart.firm_kpi (firm_id, kpi);
