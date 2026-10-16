# Glossary

Canonical terms for the Supply Chain Data Platform. Definitions only — no
implementation details. If code and this file disagree, the disagreement is a
defect to resolve, not a detail to ignore.

## Batch run

One execution of a pipeline against one source file, identified by its own
run-generated identity. Re-feeding the same file is a new batch run of the
same source batch; the two are never confused.

## Bronze

The raw landing stage: source data exactly as delivered, nothing corrected,
nothing typed, nothing dropped. Truth is always recoverable from here.

## Cleaned

Source data made consistent without being modelled: whitespace trimmed,
"empty" and "missing" unified, values cast to their documented types. Cleaning
never joins, aggregates, or derives — that is modelling.

## Current state

The most recent successful view of a business key's values. When several loads
contain the same key, the newest one is current; older values remain
recoverable from Bronze but are not visible in the current-state datasets.

## Data product

A published dataset with a defined grain, documented columns, quality checks,
and a named consumer. Not every table in the warehouse is a data product.

## Dimension

A dataset of labels and descriptors — product categories, suppliers, carriers —
identified by a key and used to give measures their context. Answers "what is
this row about", never "how much".

## Direct aggregation

A figure produced from one column alone — a total, a count, or a mean —
reported exactly as that column is delivered, with no other column combined
into it and no meaning added beyond the aggregation itself.

## Exposure

A declared consumer of a published dataset — a report or an analysis team,
named so that changing the dataset reveals who the change would break.

## Fact

A dataset of measures at a stated grain: one row per thing measured, carrying
its key and its numbers. Never contains a decision about what the numbers
mean; that lives with the dataset's definition.

## Grain

The exact meaning of one row — what one row is a fact about. Stated explicitly
for every dataset; two datasets with different grains are never silently
interchangeable.

## Mart (Gold)

The published layer: business-facing datasets consumed by reports and analysts.
A mart is a commitment — changes to one are breaking changes for its consumers.

## Measure

A single published column through a single aggregation function — a sum, a
count, an average — with the aggregation stated. Direct by construction; it
adds no meaning beyond the column it aggregates.

## Metric

A named figure in the metric catalogue, defined by its measure, its formula,
its aggregation grain, and its limitations. Supported only when every
definition it depends on is decided; otherwise withheld, never guessed.

## Provisional PII

A column suspected of holding personal data. Retained upstream so nothing is
silently dropped, but excluded from every published dataset until it is
classified and owned.

## Publish gate

The conditions that must all hold before a Gold dataset may be consumed —
approved spec, completed profiling, passing reconciliation, assigned owner.
Code can be complete while the gate is still closed; that is by design.

## Reconciliation

Proof that what goes in at a stage boundary is what comes out — the same loads
in, the same rows out — whether measured between pipeline stages or between a
published dataset and the layer beneath it. A dataset or stage that cannot be
reconciled is not publishable.

## Reject

A row the validator refuses to load, kept with its original payload, its
position in the file, and the reason it failed, so nothing disappears
silently. A rejected row never reaches a published dataset.

## SKU

Stock keeping unit: the business key of the product data. One SKU, one row in
the current-state datasets.

## Source batch

One delivery of the source file, identified by the hash of its contents. Every
row carries the batch it came from, so any published figure traces back to a
specific delivery. Distinct from the batch run that delivered it: the run is
the execution, the source batch is the data.

## Staging

The faithful typed projection of a single source: casts, trims, and
current-state selection — nothing modelled. One source, one staging dataset.

## Withheld measure

A figure deliberately not published because a definition it depends on is
undecided. Published instead as a named gap carrying the decision that blocks
it — never under a guessed definition, however plausible.

