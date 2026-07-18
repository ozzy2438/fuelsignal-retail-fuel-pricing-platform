"""Transparent HOLD / FOLLOW / LEAD pricing-policy decision rule (Week 2 Phase 4).

One station-fuel-day in, one decision out - no learned parameters of its own. The
policy composes four existing signals: the calibrated jump-model probability (only
for the four fuel types whose threshold cleared Phase 3's business rule - see
config/pricing_policy.yml), the 3-day price forecast, live competitor positioning,
and a TGP-based minimum-margin guardrail. Every numeric threshold is documented and
justified in config/pricing_policy.yml and docs/pricing-policy.md - never hardcoded
here.

Decision precedence, in order:
1. Missing the current price makes any recommendation meaningless - HOLD.
2. LEAD: jump-model automation is enabled for this fuel type AND the calibrated jump
   probability clears its threshold AND the 3-day forecast confirms a rise of at
   least `lead_min_forecast_change_cpl` AND the station is not already priced above
   its local competitor median (leading from an already-uncompetitive position would
   only make it worse).
3. FOLLOW: triggered reactively once the station is priced at least
   `follow_min_overpriced_cpl` above the local competitor median, or proactively if
   the 3-day forecast predicts a decline of at least `follow_forecast_decline_cpl`.
   The TGP margin guardrail can cap how far a FOLLOW is allowed to cut price; if the
   capped price is not actually below the current price, the recommendation is
   downgraded to HOLD rather than issuing a FOLLOW that doesn't move anything.
4. HOLD: the default when nothing above triggers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyParams:
    """Numeric thresholds - always sourced from config/pricing_policy.yml, never
    hardcoded at a call site."""

    lead_min_forecast_change_cpl: float
    lead_step_cpl: float
    follow_min_overpriced_cpl: float
    follow_forecast_decline_cpl: float
    min_margin_guardrail_cpl: float


@dataclass(frozen=True)
class PolicyInputs:
    """One station-fuel-day's worth of already-computed signals."""

    fuel_type: str
    automation_enabled: bool
    current_price_cpl: float | None
    jump_probability: float | None
    jump_threshold: float
    forecast_3d_change_cpl: float | None
    station_vs_competitor_median_cpl: float | None
    tgp_cpl: float | None
    indicative_margin_cpl: float | None


@dataclass(frozen=True)
class PolicyDecision:
    """The recommendation plus enough detail to explain and evaluate it."""

    action: str  # "HOLD" | "FOLLOW" | "LEAD"
    reason: str
    mode: str  # "automated" | "watch_only"
    guardrail_triggered: bool
    jump_signal_used: bool
    forecast_signal_used: bool
    hypothetical_price_cpl: float | None
    hypothetical_margin_cpl: float | None


def decide_policy(inputs: PolicyInputs, params: PolicyParams) -> PolicyDecision:
    mode = "automated" if inputs.automation_enabled else "watch_only"

    if inputs.current_price_cpl is None:
        return PolicyDecision(
            action="HOLD",
            reason="insufficient_data",
            mode=mode,
            guardrail_triggered=False,
            jump_signal_used=False,
            forecast_signal_used=False,
            hypothetical_price_cpl=None,
            hypothetical_margin_cpl=None,
        )

    priced_above_market = (
        inputs.station_vs_competitor_median_cpl is not None
        and inputs.station_vs_competitor_median_cpl >= params.follow_min_overpriced_cpl
    )
    forecast_rising = (
        inputs.forecast_3d_change_cpl is not None
        and inputs.forecast_3d_change_cpl >= params.lead_min_forecast_change_cpl
    )
    forecast_declining = (
        inputs.forecast_3d_change_cpl is not None
        and inputs.forecast_3d_change_cpl <= -params.follow_forecast_decline_cpl
    )
    jump_expected = (
        inputs.automation_enabled
        and inputs.jump_probability is not None
        and inputs.jump_probability >= inputs.jump_threshold
    )

    if jump_expected and forecast_rising and not priced_above_market:
        hypothetical_price = inputs.current_price_cpl + params.lead_step_cpl
        return PolicyDecision(
            action="LEAD",
            reason="jump_signal_and_rising_forecast",
            mode=mode,
            guardrail_triggered=False,
            jump_signal_used=True,
            forecast_signal_used=True,
            hypothetical_price_cpl=hypothetical_price,
            hypothetical_margin_cpl=_margin(hypothetical_price, inputs.tgp_cpl),
        )

    if priced_above_market or forecast_declining:
        return _follow_decision(inputs, params, mode, priced_above_market, forecast_declining)

    return PolicyDecision(
        action="HOLD",
        reason="no_trigger",
        mode=mode,
        guardrail_triggered=False,
        jump_signal_used=jump_expected,
        forecast_signal_used=forecast_rising or forecast_declining,
        hypothetical_price_cpl=inputs.current_price_cpl,
        hypothetical_margin_cpl=inputs.indicative_margin_cpl,
    )


def _follow_decision(
    inputs: PolicyInputs,
    params: PolicyParams,
    mode: str,
    priced_above_market: bool,
    forecast_declining: bool,
) -> PolicyDecision:
    if priced_above_market and inputs.station_vs_competitor_median_cpl is not None:
        target_price = inputs.current_price_cpl - inputs.station_vs_competitor_median_cpl
        reason = "priced_above_competitor_median"
    elif inputs.forecast_3d_change_cpl is not None:
        target_price = inputs.current_price_cpl + inputs.forecast_3d_change_cpl
        reason = "forecast_decline"
    else:
        target_price = None
        reason = "forecast_decline"

    guardrail_triggered = False
    if target_price is not None and inputs.tgp_cpl is not None:
        target_margin = target_price - inputs.tgp_cpl
        if target_margin < params.min_margin_guardrail_cpl:
            guardrail_triggered = True
            target_price = inputs.tgp_cpl + params.min_margin_guardrail_cpl
            if target_price >= inputs.current_price_cpl:
                return PolicyDecision(
                    action="HOLD",
                    reason="margin_guardrail_blocked_follow",
                    mode=mode,
                    guardrail_triggered=True,
                    jump_signal_used=False,
                    forecast_signal_used=forecast_declining,
                    hypothetical_price_cpl=inputs.current_price_cpl,
                    hypothetical_margin_cpl=inputs.indicative_margin_cpl,
                )

    if guardrail_triggered:
        reason += "_margin_capped"

    return PolicyDecision(
        action="FOLLOW",
        reason=reason,
        mode=mode,
        guardrail_triggered=guardrail_triggered,
        jump_signal_used=False,
        forecast_signal_used=forecast_declining,
        hypothetical_price_cpl=target_price,
        hypothetical_margin_cpl=_margin(target_price, inputs.tgp_cpl),
    )


def _margin(price_cpl: float | None, tgp_cpl: float | None) -> float | None:
    if price_cpl is None or tgp_cpl is None:
        return None
    return price_cpl - tgp_cpl
