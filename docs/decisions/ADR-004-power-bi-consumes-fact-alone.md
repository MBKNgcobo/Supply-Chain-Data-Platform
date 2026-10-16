# ADR-004: Power BI consumes the fact alone

| Field | Value |
| --- | --- |
| Status | Accepted (design-level; gates still blocked per ADR-003) |
| Date | 2026-10-14 |
| Relates to | SPEC-001 §2 (business questions), §10 (Power BI scope line), §11 Q1, §12; ADR-002 decision 3; ADR-003 decisions 1–2 |
| Scope | `docs/gold-warehouse.md`; consumption guidance only — no model SQL, no contract, no exposure changes |

## Context

ADR-003 built `fct_sku_performance` denormalized with `dim_product` alongside,
and explicitly deferred the strict-star alternative because "no consumer
existed" to justify it. It also deferred a `dim_product` contract until "a
consumer other than the fact's relationships test appears". The design session
of 2026-10-14 selected the business questions (SPEC-001 §2 verbatim) and, with
them, the first real consumption pattern for the Gold layer: a Power BI report.
Two facts forced a decision the earlier ADR had parked:

1. Power BI must be *told* which tables to import. "Fact + dimension related on
   `sku`" is the textbook default and what a report author will build if not
   told otherwise — a join that returns only labels the fact already carries.
2. Importing `dim_product` would make it a *consumed dataset*, which under
   ADR-003's own rule obligates writing a contract for it — a maintained
   approval record with an owner that does not yet exist (SPEC-001 Q1,
   UNASSIGNED).

## Decision

**1. Power BI imports `fct_sku_performance` alone** — single table, zero
relationships, Import mode. The star requirement lives *in the warehouse*
(fact + `dim_product` pair, agreement enforced by the `relationships` test);
the report does not need to replicate a join that produces no new information.

**2. `dim_product` is therefore not consumed, and its contract stays
deferred** under ADR-003's unchanged rule. It remains documented as the
dimension dataset (`docs/gold-warehouse.md` §2.2) and as the fact's referent —
documentation is not consumption.

**3. `dim_supplier` / `dim_carrier` remain deferred** with recorded build
triggers (a consumer needing entity attributes the fact cannot carry; or a
second fact needing a conformed join) — SPEC-001 §4's proposals, unchanged in
intent by ADR-002 decision 3.

## Consequences

- The report has no relationships to drift, refresh, or resolve ambiguously;
  single-table import is the least fragile thing Power BI can consume.
- Group-by questions (supplier, carrier, mode, product type) are answered on
  the fact; no slicer ever sees a dimension table with different labels than
  the fact, because there is only one copy of each label in play.
- `dim_product` has no consumer beyond a test, so the ADR-003 contract gate
  legitimately stays closed — a fact a future reader would otherwise have to
  re-derive from "why does an unconsumed dimension exist?".
- If a report later needs dimension attributes that don't exist, the fix is
  additive: import `dim_product` (or the new dimensions), relate on `sku`, and
  that day the contract obligation fires.

## Rejected alternatives

- **Import fact + `dim_product`, relate on `sku`, write the contract now.**
  Rejected: the join returns only labels the fact already carries, and the
  contract would exist to justify a relationship rather than to protect a
  consumer. Spends an approval record (owner still UNASSIGNED) for zero
  analytical gain.
- **Strict star — strip the six labels from the fact so dimensions are the
  only labels.** Rejected: already rejected in ADR-003; this session re-opened
  that rejection against the now-selected business questions and the now-named
  Power BI consumer, and the denormalized shape still wins for single-table
  consumption. The contract would also break.
- **Tell the report author to decide.** Rejected: the entry point *is* the
  design decision; leaving it implicit means the default (join everything
  visible) wins by inertia.

## Verification

Static, 2026-10-14, documentation-only — this ADR changes no SQL and no
machine-readable contract:

- Every claim above is cross-referenced to ADR-002/003 and SPEC-001 sections.
- The consumption guide stating this decision exists at
  `docs/gold-warehouse.md` §3.1, and §4.4 records the smallest next step.
- GLOSSARY.md terms *Direct aggregation* and *Withheld measure* added to
  support §3's vocabulary; no other glossary term was changed.

Not verified: anything against a live Power BI import or a populated warehouse
— same standing gap as ADR-003 (no warehouse this project owns, no source CSV
in `data/`).
