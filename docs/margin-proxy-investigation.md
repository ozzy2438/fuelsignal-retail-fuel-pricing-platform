# Margin Proxy Investigation for E10, P95, P98, PDL (Week 2 Phase 5)

Investigated live 2026-07-18 against the Databricks workspace, per the explicit
instruction: *"Investigate an alternative wholesale-cost or conservative margin
proxy for E10/P95/P98/PDL, but do not activate it until it is documented, validated
and backtested."*

> **Status: investigated, documented, NOT activated.** No code in this repository
> uses a proxy TGP for these four fuel types. `src/fuelsignal/policy/pricing_policy.py`
> and `config/pricing_policy.yml` are unchanged by this document - FOLLOW remains
> `disabled_unsafe` for E10/P95/P98/PDL exactly as before. This document exists to
> make the investigation, and the reason it stops short of activation, auditable.

## 1. Why a proxy is even worth investigating

`docs/pricing-policy.md` SS5 established that `tgp_cpl` (from the AIP TGP source) is
100% populated for DL and U91 and 100% null for E10, P95, P98, and PDL - a Phase 1
data-source limitation, not a bug. Without TGP, the pricing policy's margin guardrail
has nothing to act on for four of six fuel types, and their FOLLOW recommendations
stay `disabled_unsafe` indefinitely unless either (a) a real TGP source for these
fuel types is found, or (b) a defensible proxy is constructed from what *is*
available.

## 2. Candidate proxy design

Retail price spreads between fuel types are directly observable in
`gold_price_jump_labels.market_median_price_cpl`. The candidate proxy is:

```
proxy_tgp[fuel] = tgp[benchmark] + median_historical_retail_spread[fuel, benchmark]
                  - conservative_safety_buffer
```

Benchmark choice matters: petrol-family fuels (E10, P95, P98) should be compared
against U91 (the other benchmark petrol grade with real TGP); the diesel-family
product (PDL) should be compared against DL, not U91 - diesel and petrol wholesale
markets do not move together, and the data below confirms this concretely.

## 3. Live spread statistics (2025-01-01 to 2026-06-30, 489 market-days)

### 3a. Petrol grades vs. U91 (same-day retail market-median spread)

| Fuel | Mean spread (cpl) | Std dev (cpl) | P10 | P90 | Monthly range across 18 months |
|---|---|---|---|---|---|
| E10 | -2.27 | 1.69 | -4.0 | -0.8 | -3.44 to -1.39 (~2.1 cpl) |
| P95 | +17.09 | 2.46 | +15.0 | +20.0 | +15.0 to +20.3 (~5.3 cpl) |
| P98 | +24.19 | 1.81 | +22.3 | +26.0 | +23.2 to +25.5 (~2.3 cpl) |

### 3b. PDL vs. two different benchmarks - confirms diesel must use DL, not U91

| Comparison | Mean spread (cpl) | Std dev (cpl) | P10 | P90 |
|---|---|---|---|---|
| PDL vs. U91 (petrol) | +16.54 | **21.49** | -3.0 | +46.0 |
| PDL vs. DL (diesel) | +2.89 | **5.70** | -0.6 | +8.0 |

PDL-vs-U91's standard deviation (21.5 cpl) is more than 3x PDL-vs-DL's (5.7 cpl) -
petrol and diesel wholesale markets clearly do not track each other closely enough
for a cross-family proxy to be defensible. Any PDL proxy must be built from DL, not
U91.

## 4. Why this does not clear the bar to activate

Three independent problems, each sufficient on its own to withhold activation:

1. **No ground truth to validate against.** There is no historical TGP series for
   E10, P95, P98, or PDL anywhere in the Gold or Silver layers - none was ever
   ingested, because none is published in a form this project's data sources cover
   (Phase 1). "Validated and backtested" as the task requires means comparing a
   proxy's output against real held-out values; that is structurally impossible
   here. The best this investigation can do is check the proxy's *internal
   consistency* (is the retail spread stable?) - which is a necessary but nowhere
   near sufficient condition for the proxy to be *accurate*.
2. **A retail spread is not a wholesale spread.** `market_median_price_cpl` already
   embeds each fuel type's own competitive retail margin decisions, which are not
   guaranteed to move in lockstep with the wholesale (TGP) spread between grades -
   retailers may price premium grades at a different margin than U91 for reasons
   unrelated to wholesale cost (brand positioning, demand elasticity, local
   competition). Using the retail spread as a wholesale proxy conflates these two
   different things.
3. **The spread drifts by several cpl over 18 months** (E10: ~2.1 cpl range, P95:
   ~5.3 cpl range even after excluding the unstable PDL-vs-U91 case) - a
   static/fixed offset would introduce an uncharacterized and time-varying error
   into a guardrail whose entire purpose is precision at the 1-2 cpl scale
   (`min_margin_guardrail_cpl` is 2.0 cpl - see docs/pricing-policy.md SS8). An
   error of similar magnitude to the guardrail itself defeats the guardrail's
   purpose.

## 5. Conclusion and path to activation

**Do not activate a retail-spread-based margin proxy for E10/P95/P98/PDL with the
data currently available.** The spreads are stable enough to be worth recording here
as a starting point, but not stable or validated enough to safely gate an automated
price cut. `recommendation_status = "disabled_unsafe"` remains correct for these four
fuel types' FOLLOW recommendation.

A defensible path to activation, if pursued in a future phase:

1. Source real wholesale/TGP data for E10, P95, P98, and PDL (even a lower-frequency
   or delayed source would allow validation) - this directly resolves problem #1 and
   removes the need for a proxy at all.
2. If no real source is ever found, a proxy could be reconsidered, but only with (a)
   a wider, explicitly time-varying safety buffer that accounts for the observed
   3-5 cpl drift rather than a single fixed offset, and (b) an explicit sensitivity
   analysis showing the guardrail's behavior is acceptable across the full observed
   spread range (P10-P90 in SS3), not just at the mean.
