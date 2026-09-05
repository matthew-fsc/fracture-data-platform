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
