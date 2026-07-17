# FuelSignal Assumptions and Limitations

## Key Assumptions

1. **Price cycles exist and are detectable** — Based on ACCC published research confirming regular cycles in Australian capital city markets

2. **5 cpl threshold for jump detection** — A price increase of ≥5 cpl in a single day is classified as a "jump". This threshold is configurable but has not been formally validated

3. **5km competitor radius** — Stations within 5km are considered direct competitors. This is a simplification; actual competition depends on traffic patterns, road networks, and brand loyalty

4. **Sydney TGP applies to all NSW** — Terminal gate prices from Sydney are used for margin calculation. Regional stations may have different wholesale costs

5. **Fuel type normalization is complete** — The mapping table covers known variations but may miss new product names

6. **Public holidays affect demand uniformly** — Binary feature; doesn't account for regional differences or holiday proximity effects

## Known Limitations

1. **No volume data** — Public sources don't include litres sold per station
2. **No cost data** — Operating costs, transport, franchise fees are unknown
3. **Indicative margin only** — Retail minus TGP is not true profit margin
4. **Single state** — NSW only; inter-state dynamics not captured
5. **API availability** — FuelCheck API may have rate limits or require registration
6. **HTML parsing fragility** — AIP TGP page structure may change without notice
7. **Databricks Free Edition** — Some features (Unity Catalog, Jobs) may be limited
8. **No real-time capability** — Designed for daily batch processing
9. **Walk-forward backtest not yet executed** — No performance claims can be made
10. **Human-in-the-loop required** — Recommendations are decision support only

## Portfolio Project Disclaimer

This is a public portfolio demonstration project. It does NOT represent:
- A deployed production system
- An actual client engagement
- Validated model performance
- Real business outcomes or revenue impact

Any future performance metrics will be clearly labelled as backtest results
and will not be presented as production outcomes.
