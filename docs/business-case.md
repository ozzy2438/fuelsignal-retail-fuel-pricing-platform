# FuelSignal Business Case

## Problem Statement

Australian retail fuel markets exhibit **predictable price cycles** characterised by:
- **Sharp upward jumps** (often 20-40 cpl overnight)
- **Gradual decline** over 7-14 days
- **Geographic clustering** where nearby stations move together

### Commercial Impact

For a hypothetical retailer operating ~40 stations in NSW:

1. **Timing risk**: Being caught below cost when the market jumps means immediate margin loss
2. **Competitive risk**: Pricing too high during trough periods loses volume to nearby competitors
3. **Decision frequency**: Pricing decisions are made daily, requiring timely data

### Decision Framework

| Signal | Action | Rationale |
|--------|--------|-----------|
| Jump probability HIGH (>70%) | **LEAD** | Raise price before competitors to protect margin |
| Jump probability MEDIUM | **HOLD** | Maintain current position, monitor |
| Jump probability LOW, at trough | **FOLLOW** | Match competitor reductions to maintain volume |

## Portfolio Project Scope

This project demonstrates:
1. End-to-end Lakehouse data engineering
2. Feature engineering for time-series forecasting
3. Geospatial competitor analysis
4. SQL window function expertise
5. Production-quality code and documentation

## Explicit Limitations

- **No real volume data** — public sources don't include litres sold
- **No real P&L impact** — cannot calculate actual revenue/margin outcomes
- **No model results yet** — backtest must produce numbers before any claims
- **Human-in-the-loop** — recommendations are decision support, not automation
- **NSW only** — single-state scope for this implementation
