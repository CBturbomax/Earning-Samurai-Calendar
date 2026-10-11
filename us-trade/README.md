# US exports / imports

Census **seasonally adjusted**, nominal USD, goods, End-use classification. World totals only. This is deliberately not presented as HS, NSA, country-specific, quantity, or company trade data.

Source: https://www.census.gov/foreign-trade/statistics/historical/seas.html
Public exports_enduse.xlsx and imports_enduse.xlsx supply monthly DATE/ENDUSE/DESCRIPTION/VALUE rows. Values are USD (not USD millions). 72 months retained; 60 displayed with preceding-year comparison. Codes are flow-specific, not interchangeable HS codes. Categories are individually displayed, no aggregate total inferred.

`python us-trade/scripts/collect.py` refreshes both flows atomically after validation. `--source-dir DIR` reads downloaded official workbooks for reproducible offline ingestion. Failures keep trade.json and set status.json. Daily Actions at 01:47 UTC.

`python -m unittest discover -s us-trade/tests -v`
`node us-trade/tests/metrics.mjs`

Company sources are linked per catalog entry; impact notes are conditional analysis, not established statistical correlation or recommendations. Company trade shares are null until the numerator, denominator, scope and period can be supported. Headquarters location does not establish manufacturing origin.

Current limitations: no partner-country breakdown; LNG-only, AI-server-only, transformer-only, semiconductor-equipment-only and Korea-cosmetics-only values cannot be isolated in these public End-use files. Detailed HS expansion requires Census API access. Do not substitute broad categories under narrow labels.

## UI consistency
US views reuse the Japan export tracker stylesheet and its monthly/seasonal chart design: item panels, company impact notes, 4 KPIs, horizontally scrolling monthly tables, CSV, categories and favorites. The only source-specific restrictions are world-only End-use/SA values and no quantity or unit-price controls. Import/export switch resets the category so a filter unique to the previous flow cannot hide every item.

`node us-trade/tests/japan-layout.mjs` and `node us-trade/tests/app.mjs` verify charts, source units, both flows and controls.
