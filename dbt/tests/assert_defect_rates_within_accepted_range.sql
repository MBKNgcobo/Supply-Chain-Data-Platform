-- Contract quality check: accepted_range for defect_rates.
--
-- BOUNDS 0-100 DELIBERATELY COVER BOTH CANDIDATE ENCODINGS. SPEC-001 Q7 has
-- not decided whether defect_rates is a 0-1 fraction or a 0-100 percentage.
-- Asserting either would encode the guess; asserting the superset catches what
-- is unambiguously garbage (negatives, absurd outliers) under either encoding
-- without committing to one.
--
-- SEVERITY WARN, per the settled policy: code-complete but semantically open
-- checks record what they see without failing the build. This test is promoted
-- to error severity when Q7's range question is answered.
--
-- FAILS WHEN: it returns one or more rows (nulls pass: missing values stay
-- observable downstream, per ADR-001 §2).

{{ config(severity = 'warn') }}

select
    sku,
    defect_rates

from {{ ref('fct_sku_performance') }}

where defect_rates is not null
  and (defect_rates < 0 or defect_rates > 100)
