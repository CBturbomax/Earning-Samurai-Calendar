# Expanded trade discovery

The discovery panels screen source classifications independently of the curated
company catalog. They do not claim to cover every national tariff line.

- Japan: every reported HS4 and HS6 code in the full national export CSVs.
  HS4 parents and HS6 children overlap; their counts and amounts must not be summed.
  Confidential chapter totals are excluded. Codes absent from either HS2017 or
  HS2022, changed descriptions, and zero-only histories are excluded. Identical
  descriptions cannot rule out every classification scope change.
- US: every five-digit End-use category in Census's seasonally adjusted monthly
  export and import workbooks, separately. These are not individual HS tariff lines.
- Signal: latest month equals/exceeds the maximum of 60 complete published months,
  or latest three-month average is at least 95% of the highest contiguous three-month
  average within the same window. This is a five-year high, not an all-time high.
- Japan values are thousand JPY; US values are USD. Monetary highs may reflect prices,
  currency changes and, for Japan, seasonality. They are not necessarily volume highs.

Each collector atomically replaces discovery.json only after validation, rejects
publication regressions and writes discovery-status.json independently of the
curated collector. Existing daily workflows run both collectors and preserve the
last valid snapshot on failure. The UI displays discovery failures separately.

Company files are editorial product associations with primary-source references.
They do not establish the named company's share of customs totals, shipment origin
for every product, or earnings sensitivity. Unknown associations stay explicit.
Discovery updates do not require a company mapping or Korean translation to exist.

Run:

    python -m unittest discover -s japan-exports/tests -v
    python -m unittest discover -s us-trade/tests -v
    node trade-shared/discovery.test.mjs
