# FuelSignal Data Sources

## Official Sources Used

### 1. NSW FuelCheck

- **Provider**: NSW Government (Data.NSW)
- **URL**: https://data.nsw.gov.au/data/dataset/fuel-check
- **Access Method**: CKAN API for metadata + resource download
- **Data Format**: JSON/CSV
- **Update Frequency**: Daily
- **Coverage**: All retail fuel stations in NSW
- **Key Fields**: station_code, station_name, brand, lat/lon, fuel_type, price, timestamp
- **Notes**: 
  - Primary source for station-level retail prices
  - Historical data available via CKAN bulk resources
  - Real-time API may require registration

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
