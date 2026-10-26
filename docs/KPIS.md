# Executive KPIs and departmental dashboards

Two things are defined here: the small set of measures the platform is run on,
and the six departmental views that carry the detail beneath them. Definitions
are written out because an undefined KPI is a fight waiting to happen — every
one of these has at least two defensible readings, and the point of writing them
down is that everyone argues about the same number.

## The comparability problem, and how it is solved

The platform firm bills 4.8x what the smallest add-on bills. That fact says
nothing about whether either is well run, so no measure in these dashboards is
an absolute amount. Everything is one of three shapes:

- **a rate** — realisation, collection, margin, SLA attainment
- **a per-unit figure** — revenue, cost and margin per household or per advisor
- **basis points on AUM** — the three yields

That makes a $400m firm and a $1.7bn firm directly comparable.

**Mix is checked rather than assumed.** The obvious objection to comparing yields
is client mix: tiered schedules charge fewer basis points on larger households,
so a firm with wealthier clients looks cheaper without being worse. On this
estate that objection does not hold — average household AUM is $1.18m, $1.26m
and $1.21m across the three firms, near enough identical. `aum_per_household`
is on the profitability view precisely so the reader can check that for
themselves rather than take it on trust. When a firm with genuinely different
client sizes is folded in, that column is where it will show up.

## The three yields, and why there are three

This is the spine of the whole design.

| Yield | Definition | What it tells you |
|---|---|---|
| **Schedule** | expected fees ÷ AUM | How the book is *priced* |
| **Realised** | invoiced ÷ AUM | What the firm actually *billed* |
| **Collected** | cash received ÷ AUM | What the firm actually *kept* |

All three are annualised: quarterly amounts multiplied by four. Stated because
an un-annualised basis-point figure looks like a plausible advisory fee and is
wrong by a factor of four.

The two gaps between them are the two things a firm can get wrong, and they have
different owners:

- **Schedule to realised** is billing execution. Owned by billing operations.
- **Realised to collected** is credit control. Owned by the controller.

Reading only realised yield ranks Calloway Brooks last and invites the
conclusion that it is cheap. It is not: it is the most expensive book on the
platform (84.6bps against 80.5bps) and it invoices 78.5% of what it is owed.
That is a billing problem with a completely different fix from a pricing one,
and no single-yield dashboard can tell them apart.

## The executive set

Eight measures. Each maps to a promise the platform makes.

| KPI | Definition | Direction | Maps to |
|---|---|---|---|
| **Realised yield** | invoiced ÷ AUM, annualised, in bps | higher | one model of the platform |
| **Realisation rate** | invoiced ÷ schedule entitlement | higher | unbilled revenue |
| **Collection rate** | cash ÷ invoiced | higher | leaked revenue |
| **Loaded margin** | (billed − all attributed cost) ÷ billed | higher | margin, fully loaded |
| **Leakage** | total leakage ÷ schedule entitlement | lower | unbilled and leaked revenue |
| **SLA attainment** | 1 − (breached ÷ events) | higher | onboarding and service SLAs |
| **Largest advisor book** | biggest advisor's share of their firm's book | lower | key-person risk |
| **Cost to income** | attributed cost ÷ invoiced | lower | margin, fully loaded |

Every KPI carries a `direction`, and the dashboard colours moves by whether
they are *good* rather than by their sign. A console that paints every downward
move red is wrong roughly half the time, and readers learn to ignore the colour
instead of reading the metric.

Rank 1 always means best. For a lower-is-better metric that is the smallest
value, which is asserted in the test suite because a leaderboard whose first
place is worst is a bug readers blame themselves for.

## The peer benchmark

Each firm's figure is shown against the **platform**, which is computed from
totals — sum of billed over sum of AUM — never as the mean of the firms' rates.
With firms of different size those two differ, and the unweighted one is simply
wrong. The test suite asserts both that the benchmark equals the weighted figure
and that the two are far enough apart on this estate for the test to be capable
of failing.

Concentration measures have no platform aggregate: advisor shares are shares of
*their own firm's* book, and summing them across firms is not a number. Those
tiles lead with the worst firm instead, labelled as such.

## The six departments

Each view belongs to a group that owns a set of decisions.

| # | View | Owner | The question it answers |
|---|---|---|---|
| 01 | Executive | Managing partner, board | Which firm is well run, on measures that ignore size |
| 02 | Finance and billing | Controller, billing ops | Where does the entitlement go, and who owns each gap |
| 03 | Profitability | Finance, firm principals | What does a household cost to serve, and which ones lose money |
| 04 | Service operations | Head of operations | What is late, what is stuck, and at what load |
| 05 | Advisory and book | Head of advice, corp dev | Who holds the book and what walks if they leave |
| 06 | Data and assurance | Platform team | Can the other five views be defended |

### Choices worth knowing about

**Distributions, not just averages.** The profitability view shows household
margin quartiles and a loss-making count beside every mean. A firm can carry a
healthy average margin while a quarter of its book costs more to serve than it
bills, and only the quartiles show it.

**Open events count as breached.** A service event still open past its target is
a breach now, not a pending item. Counting only closed events hides the worst
backlog, because the tickets nobody has touched never close.

**Effective-dated book assignments.** An advisor who has left still shows the
book they held. The risk being measured is what walks out of the door; a report
that silently reassigns their households to whoever inherited them shows no risk
at all.

**Over-billing is an explicit step.** The yield bridge has a green step for
invoicing above the assigned schedule. It is small, but a waterfall with an
unexplained residual reads as precision while being wrong, and billing a client
above their schedule is a refund exposure rather than a windfall. The mart
assertion that the bridge must close is what forced this to become visible.

## Where the numbers come from

Every figure resolves through row-grain lineage to the canonical rows behind it,
then to the raw payloads, then to the file the client sent and its SHA-256. The
marts are `mart.firm_scorecard`, `mart.yield_bridge`, `mart.firm_kpi`,
`mart.household_economics`, `mart.household_distribution` and
`mart.producer_scorecard`; each is asserted after every build, and the
assertions are listed in `fracture.marts.runner.ASSERTIONS`.

The dashboards read current state. The board pack reads a pinned system time and
reissues byte-identically; these do not, and are not a substitute for it.

---

# The professional services pack

The same design constraint, restated for a practice. A four-partner firm and a
forty-partner firm cannot be compared on fees, so every measure below is a
rate, a per-hour figure, a day count or a percentage.

## Five stages, four gaps

Wealth has three yields and two gaps. A practice has five stages and four,
because it carries inventory that wealth does not and prices the work
separately from recording it.

| Stage | Definition |
| --- | --- |
| Recorded at card | Every recorded hour × the rate-card rate for the grade that recorded it |
| Billable at card | As above, for hours the timekeeper marked chargeable |
| Entitled | What the engagement may bill: billable × card × (1 − discount) for time and materials; the apportioned agreed fee for fixed, capped or retainer work |
| Billed | What the invoice actually went out at |
| Collected | What arrived |

| Gap | Owner | What it means |
| --- | --- | --- |
| Write-off at entry | Whoever approved the work | Hours recorded that never became billable: out of scope, goodwill, training, admin, rework. Happens in the timesheet, not on an invoice, which is why most practices never see it |
| Pricing | Whoever agreed the fee | Discount against the card, or a fixed fee that differs from the time it consumed. Signed both ways: a fixed fee above its time value is a gain, and that is the profitable fixed-fee engagement |
| Realisation | The engagement partner | What the bill went out at against what could have been billed |
| Collection | Finance | What has not arrived |

A single "realisation" percentage collapses the middle two. That is the common
reporting failure: it sends the engagement partner to look at a number that was
actually set when the fee was agreed, or sends the pricing conversation to
someone who under-billed a correctly priced engagement.

**The bridge must close.** `mart.realisation_bridge` publishes ten rows per
practice and period and an assertion checks that the opening balance plus every
signed step equals the closing balance. The first version of the model failed
it: the opening balance was billable hours at card, which is already net of
write-off, so write-off was subtracted twice. A waterfall with an unaccounted
residual reads as precision while being wrong.

Over-billing is reported as its own positive step rather than netted into the
residual, for the same reason it is in the wealth pack: an engagement billed
above its entitlement is a refund exposure, not a windfall.

## The executive measures

All on `mart.firm_scorecard`, one row per practice and period.

| Measure | Definition | Direction |
| --- | --- | --- |
| `card_rate_per_hour` | Recorded value ÷ recorded hours | — |
| `entitled_per_hour` | Entitlement ÷ recorded hours | higher |
| `billed_per_hour` | Billed ÷ recorded hours | higher |
| `collected_per_hour` | Collected ÷ recorded hours | higher |
| `writeoff_rate` | 1 − billable value ÷ recorded value | lower |
| `pricing_rate` | Entitlement ÷ billable value at full card | higher |
| `realisation_rate` | Billed ÷ entitlement | higher |
| `collection_rate` | Collected ÷ billed | higher |
| `utilisation` | Billable hours ÷ the timekeeper's own monthly target | higher |
| `entry_realisation` | Billable hours ÷ recorded hours | higher |
| `wip_days` | Unbilled entitlement ÷ the period's daily entitlement rate | lower |
| `debtor_days` | Invoice-value-weighted days outstanding | lower |
| `lockup_days` | WIP days + debtor days | lower |
| `margin_pct` | (Billed − fully loaded cost) ÷ billed | higher |
| `fee_per_head` | Billed ÷ timekeeper count | higher |

**Every per-hour figure uses recorded hours, not billable hours.** An hour
written off at entry still consumed the practice's capacity, and a comparison
about capacity that excludes it flatters whoever writes off the most.

**Utilisation is measured against each timekeeper's own target**, apportioned
monthly, not against a calendar year. A part-time or newly joined person
measured against a calendar year reads as underperforming, which makes the
number useless for the conversation it exists to support. The scorecard once
divided an already-monthly target by twelve again, inflating utilisation
twelvefold; the assertion that utilisation must be a rate caught it.

**Debtor days are weighted by invoice value**, not averaged across invoices.
One large old invoice beside twenty small fresh ones is a lockup problem that
an unweighted mean hides. Overdue is measured against each client's agreed
terms, not a firm-wide default, because the firm-wide default is the number
everyone quotes and nobody has agreed to.

**Margin is on what was billed**, not on entitlement. An engagement that failed
to bill its entitlement has a realisation problem and a margin problem, and
conflating them loses the first. Cost is fully loaded: labour at each
timekeeper's loaded cost rate, plus directly attributable costs, plus pooled
overhead allocated on recorded hours. Allocated on hours rather than on
revenue, because revenue-weighted allocation flatters the engagements that are
already the most profitable. `mart.cost_allocation_check` asserts every pooled
pound lands on exactly one engagement.

## The six views

| View | Owner | What it answers |
| --- | --- | --- |
| Executive | Managing partner, board | Which practice, on nine normalised measures |
| Realisation | Engagement partners | Where billable time went, and which engagements are thinnest |
| People and capacity | Head of people, resourcing | Who is full, who is yielding, and the difference |
| Cash and lockup | Finance | Which leg of lockup, and whose it is |
| Pipeline and delivery | Managing partner, business development | The weighted forward book, and what is past its date |
| Data and assurance | Platform team | Whether the other five can be defended |

The capacity view plots billable utilisation against realised yield over fully
loaded cost. Below 1.0x, an hour of that person's time loses money at the rates
it was sold at, and being busy makes that worse rather than better — which is
the one reading utilisation alone cannot produce.

Pipeline weighting is a flat published factor per stage, stated in the mart
rather than tuned. A factor fitted to past conversion would be a model
producing a figure with financial consequence, which the platform does not
permit.
