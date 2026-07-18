# FuelSignal Data Sources

## Official Sources Used

### 1. NSW FuelCheck (historical bulk archive)

- **Provider**: NSW Government (Data.NSW)
- **URL**: https://data.nsw.gov.au/data/dataset/fuel-check
- **Access Method**: CKAN `package_show` API to resolve monthly resource URLs, then direct
  download (CSV or XLSX depending on the month - format is detected from the URL, not
  trusted from CKAN's declared `format` field, which is occasionally wrong)
- **Data Format**: CSV or XLSX (18 consecutive monthly resources, Jan 2025 - Jun 2026, see
  `config/sources.yml` → `sources.nsw_fuelcheck.historical_resources`)
- **Update Frequency**: Monthly archive (not real-time)
- **Coverage**: All retail fuel stations in NSW
- **Key Fields**: ServiceStationName, Address, Suburb, Postcode, Brand, FuelCode,
  PriceUpdatedDate, Price
- **Confirmed limitation (verified 2026-07-18)**: this archive has **never** carried station
  coordinates, in any monthly resource from Aug 2016 through Jun 2026 (checked the oldest
  and newest available files directly). There is no official station code either - only
  free-text name/address/suburb/postcode/brand. This is why a second source
  (`nsw_fuelcheck_api_reference`, below) is required to obtain coordinates at all.
- **Known pitfall (fixed 2026-07-18)**: `pandas.read_excel` parses `PriceUpdatedDate` as a
  real `datetime64` column for XLSX-sourced months, and `pandas.to_json` silently serializes
  `datetime64` columns as epoch-millisecond integers rather than ISO strings. Left
  unhandled, this broke `try_cast(... AS TIMESTAMP)` for ~76% of ingested rows (every
  XLSX-sourced month) with no error - the rows just silently failed timestamp validation.
  `scripts/run_ingestion_pipeline.py::parse_fuelcheck_month` now explicitly normalizes
  `PriceUpdatedDate` to an ISO string for both CSV and XLSX sources before staging.

### 1b. NSW FuelCheck Live Reference Data API (station coordinates)

- **Provider**: NSW Government - Department of Customer Service (OneGov), via the
  api.nsw.gov.au developer portal (Fuel API product)
- **Landing page**: https://api.nsw.gov.au/Product/Index/22
- **Status of the old public endpoint**: `api.onegov.nsw.gov.au/FuelCheckApp/v1|v2` is
  **retired** - confirmed live via direct request on 2026-07-18 (HTTP 404). Any
  documentation or tutorial referencing this path is out of date.
- **Confirmed live flow (2026-07-18, tested end-to-end against production)**:
  1. Register an application at https://api.nsw.gov.au and subscribe to the Fuel API
     product to obtain a client key/secret (`FUELCHECK_API_KEY` / `FUELCHECK_API_SECRET`).
  2. `GET https://api.onegov.nsw.gov.au/oauth/client_credential/accesstoken?grant_type=client_credentials`
     with an HTTP Basic `Authorization` header. **Must be GET** - the commonly-documented
     POST form returns `HTTP 200` but silently echoes the request body back instead of
     issuing a token (confirmed by sending four different POST body/auth combinations and
     observing the response body exactly match the request body each time).
  3. `GET https://api.onegov.nsw.gov.au/FuelCheckRefData/v2/fuel/lovs` with
     `Authorization: Bearer <token>`, `apikey`, `transactionid` (a UUID), and
     `requesttimestamp` (`dd/MM/yyyy HH:mm:ss`) headers. Omitting `transactionid` /
     `requesttimestamp` fails with a `HeadersError`.
- **Data Format**: JSON. Response contains `stations.items[]` (station `code`, `name`,
  `brand`, free-text `address`, `location.{latitude,longitude}`, `state`), plus
  `brands.items[]` and `fueltypes.items[]` lookup lists (not currently used).
- **Coverage**: current live snapshot only - 3323 stations as of 2026-07-18, 0 missing
  coordinates, 0 duplicate station codes, all `state = "NSW"`. No historical depth.
- **Crosswalk outcome against the 18-month bulk archive (live run 2026-07-18)**: 2180
  stations matched 1:1 on normalized address+postcode (`match_method =
  exact_address_postcode`), 918 official stations had no bulk-archive counterpart yet
  (`reference_only_no_bulk_match`) - `silver_station_master` totals 3098 coordinate-bearing
  stations. 451 keys were ambiguous (quarantined, not guessed) and 144 bulk-archive
  stations had no official counterpart at all (no coordinates available for them).
- **Credentials**: `FUELCHECK_API_KEY`, `FUELCHECK_API_SECRET` (and optionally a
  pre-built `FUELCHECK_AUTHORIZATION_HEADER`) via environment variables / `.env` -
  never committed, never logged.

### 2. AIP Terminal Gate Prices

- **Provider**: Australian Institute of Petroleum
- **URL**: https://www.aip.com.au/pricing/terminal-gate-prices
- **Access Method**: HTTP GET + HTML table extraction
- **Data Format**: HTML tables on web page
- **Update Frequency**: Daily
- **Coverage**: All Australian capital cities
- **Key Fields**: date, terminal/city, product, price_cpl
- **Notes**:
  - Represents wholesale cost basis
  - Retail price minus TGP = indicative gross margin
  - Page structure may change; parser needs maintenance
  - The single downloaded workbook already contains **daily data back to 2004-01-01**
    (verified live 2026-07-18: `silver_terminal_gate_prices` spans 2004-01-01 to
    2026-07-17, 5882 distinct dates) - no separate historical backfill is needed for
    this source; the existing monthly re-download already captures full history.

### 3. ACCC Petrol Price Cycles

- **Provider**: Australian Competition & Consumer Commission
- **URL**: https://www.accc.gov.au/consumers/petrol-and-fuel/petrol-price-cycles
- **Access Method**: Reference documentation
- **Usage**: Methodology and business context
- **Notes**:
  - Defines what constitutes a "price cycle"
  - Published quarterly monitoring reports
  - Validates our cycle detection approach

### 4. NSW Public Holidays

- **Provider**: NSW Government (Industrial Relations)
- **URL**: https://www.industrialrelations.nsw.gov.au/public-holidays/public-holidays-in-nsw
- **Alternative**: https://data.gov.au/data/dataset/australian-holidays
- **Access Method**: API or curated list
- **Data Format**: JSON or static calendar
- **Key Fields**: date, holiday_name, state, is_national
- **Notes**:
  - Used as feature in pricing models
  - Holidays affect fuel demand patterns
  - Both state and national holidays included

## Sources NOT Used

- No Kaggle datasets
- No unofficial GitHub scrapers
- No proprietary/commercial data
- No web scraping of unofficial mirrors
