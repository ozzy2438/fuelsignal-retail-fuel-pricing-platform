# FuelSignal Decision Rights

## Pricing Decision Framework

The FuelSignal platform provides **decision support** for fuel pricing.
The final pricing decision ALWAYS remains with a human decision-maker.

## Decision Hierarchy

| Signal Output | Suggested Action | Human Decision Required |
|--------------|-----------------|------------------------|
| Jump probability >70% | LEAD (raise before market) | Yes — confirm timing and magnitude |
| Jump probability 30-70% | HOLD (maintain position) | Yes — assess competitive context |
| Jump probability <30% | FOLLOW (match reductions) | Yes — validate margin acceptability |

## What the System Does

- Estimates probability of market price jump within 48 hours
- Provides competitor positioning context
- Calculates indicative margin vs wholesale cost
- Surfaces relevant features (cycle position, competitor prices)

## What the System Does NOT Do

- Make pricing decisions autonomously
- Override human judgment
- Account for factors outside the data (e.g., brand strategy, promotions)
- Guarantee accuracy (all probabilities are estimates with uncertainty)

## Accountability

- The pricing analyst/manager retains full accountability for pricing decisions
- Model recommendations include confidence intervals (when model is built)
- All recommendations are logged for post-hoc review
- False positive/negative rates will be reported from backtest (when executed)
