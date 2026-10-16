-- Business rule: order_quantities must be non-negative. Rule documented in
-- docs/data-dictionary.md ("Order Quantities") and asserted in the contract as
-- quality.checks: non_negative (docs/contracts/datasets/
-- gold-fct_sku_performance.contract.yaml).
--
-- WHAT IT PROVES: no gold row publishes a negative ordered quantity. A quantity
-- is a count of products ordered, so its SIGN is unambiguous — unlike the
-- defect_rates RANGE (0-1 vs 0-100, SPEC-001 Q7), where either assertion would
-- encode a guess. The withheld-never-guessed rule does not apply here because
-- nothing is undecided: no encoding, no bounds, only direction.
--
-- SEVERITY ERROR, per the settled policy (docs/testing/dbt-test-plan.md):
-- error-severity tests bind the publish gate and fail `dbt build`. Nulls pass:
-- the source legitimately delivers blank quantities, and a missing value stays
-- observable downstream (ADR-001 §2) rather than violating this rule.
--
-- FAILS WHEN: it returns one or more rows.
--
-- PASSES TRIVIALLY ON EMPTY TABLES — the standing caveat from ADR-001: a test
-- over an empty warehouse proves nothing until bronze is populated.

{{ config(severity = 'error') }}

select
    sku,
    order_quantities

from {{ ref('fct_sku_performance') }}

where order_quantities is not null
  and order_quantities < 0