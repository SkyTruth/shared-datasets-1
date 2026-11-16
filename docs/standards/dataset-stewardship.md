# Dataset stewardship and review dates

Every active asset names a person with a GitHub handle or email in
`admission.steward`. The organization in `owner` remains the dataset owner;
the steward is the contact responsible for maintenance decisions. Preserve an
existing named steward when changing unrelated metadata.

Daily and monthly assets have automated freshness checks, allowing two and
38 days respectively since a successful publication or verified unchanged-source
check-in. Every other active cadence, including annual, manual and static,
requires a `review_by: YYYY-MM-DD` in asset frontmatter. Manual assets use annual
reviews by default. An optional review date can also be set on scheduled assets.

The review deadline is **not** a claim that source data has been checked or is
current. On review, the named steward assesses upstream revisions, source and
license availability, intended consumers and continued usefulness. They record
the outcome in the reviewed metadata change and choose the next deadline. A
historical snapshot can remain active and unchanged after that review.

The initial assignment names `@jonaraphael` for assets without an existing named
contact, by explicit maintainer instruction on 2026-11-15. Manual and annual
assets have an initial deadline of 2027-11-15. That establishes the maintenance
schedule; it does not claim a new source-data review or refresh.

`catalog_docs.py` rejects missing contacts, missing required horizons and invalid
dates. It emits `steward` and `review_by` into the generated catalog CSV and asset
summaries; the web catalog displays them. Regeneration preserves dates exactly.
The daily operational safety report flags a review starting the day after its
deadline, names the steward, and uses the existing health-change notification
route. Repeated identical findings retain the existing notification policy.
Missing contacts or dates are also findings if they reach the safety reader.
Inactive assets keep their metadata and releases but do not generate overdue
maintenance findings.

After a real review, edit the asset doc and regenerate with:

```bash
uv run python scripts/catalog_docs.py generate
uv run python scripts/catalog_docs.py check
```

Do not advance review dates automatically, infer a review from a metadata rewrite,
or republish unchanged data to make a deadline appear satisfied.
