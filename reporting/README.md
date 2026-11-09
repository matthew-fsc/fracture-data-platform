# Reporting

Service line: the monthly operating pack on the retainer, and the operating
visibility pack sold with an M&A roll-up.

Pack definitions are SQL in `packs/`. `fracture pack build` pins them to a
system time, hashes the figures, and renders one HTML file. `fracture pack
verify` rebuilds at that same system time and compares the hash. A difference
is a defect, not a refresh.

| File | Section |
|---|---|
| `packs/010_platform_model.sql` | What the platform is looking at |
| `packs/020_revenue_margin.sql` | Revenue and margin |
| `packs/030_unbilled_leakage.sql` | Unbilled and leakage |
| `packs/040_service_sla.sql` | Service levels |
| `packs/050_concentration.sql` | Concentration |
| `packs/060_assurance.sql` | Whether the pack is allowed to be issued |

The wealth and services marts that feed those sections live with their packs
under `src/fracture/packs/`. Departmental definitions are in `docs/KPIS.md`.
Measures that compare firms are rates, per-unit figures or basis points.
Absolute dollars make a large firm look healthy and a small one look broken.

Every figure drills to canonical rows, raw payloads and the artifact hash.
`fracture drill` is that walk. A figure that does not open is not issued.

Dashboards (`fracture dashboards`) are the operating console for the same
numbers. The pack is the document a board, a lender or a buyer receives.
