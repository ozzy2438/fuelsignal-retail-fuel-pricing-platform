"""Transparent HOLD / FOLLOW / LEAD pricing-policy decision rule (Week 2 Phase 4-5).

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
   only make it worse). LEAD only ever fires for automation-enabled fuel types, and
   raising price cannot breach a margin floor, so a LEAD's `recommendation_status`
   is always "automated".
3. FOLLOW: triggered reactively once the station is priced at least
   `follow_min_overpriced_cpl` above the local competitor median, or proactively if
   the 3-day forecast predicts a decline of at least `follow_forecast_decline_cpl`.
   The TGP margin guardrail can cap how far a FOLLOW is allowed to cut price; if the
   capped price is not actually below the current price, the recommendation is
   downgraded to HOLD rather than issuing a FOLLOW that doesn't move anything. A
   price *cut* is the one action that can breach a margin floor, so FOLLOW's
   `recommendation_status` depends on whether TGP data exists to guard it at all -
   see `_recommendation_status` below.
4. HOLD: the default when nothing above triggers.

## Three-way `recommendation_status` (Week 2 Phase 5 - operationalisation safety gate)

Every `PolicyDecision` carries a `recommendation_status` distinct from `action`,
because "what the rule computed" and "what is safe to automatically act on" are not
the same question:

- **"automated"** - safe to surface as an automated recommendation. HOLD when the
  fuel type has jump-model automation enabled; LEAD always (§2); FOLLOW only when
  TGP data exists to guard the cut (today: DL only, among automated fuel types).
- **"watch_only"** - informational, human-review-only. HOLD/FOLLOW for a fuel type
  without jump-model automation (U91 - "kept in watch-only mode as already defined",
  never LEAD) whenever TGP data *is* available to guard the FOLLOW.
- **"disabled_unsafe"** - never present this as actionable. Any FOLLOW where TGP
  data is unavailable (E10, P95, P98, PDL - confirmed live 2026-07-18, TGP is 100%
  null for these four in the current archive) - there is no validated margin
  guardrail to protect a price cut for these fuel types, full stop, regardless of
  how reliable their jump signal is. The `action` field still reports what the raw
  rule would recommend (never silently rewritten to HOLD) so the signal stays
  visible and auditable - `recommendation_status` is what a dashboard or downstream
  automation must gate on, not `action` alone.
"""

from __future__ import annotations

from dataclasses import dataclass

STATUS_AUTOMATED = "automated"
STATUS_WATCH_ONLY = "watch_only"
STATUS_DISABLED_UNSAFE = "disabled_unsafe"


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
    """The recommendation plus enough detail to explain, evaluate, and safely gate
    it. `action` is the raw rule's output; `recommendation_status` is what a
    dashboard or downstream automation must actually key off - see module
    docstring."""

    action: str  # "HOLD" | "FOLLOW" | "LEAD"
    reason: str
    mode: str  # "automated" | "watch_only" - the fuel type's jump-model automation setting
    recommendation_status: str  # "automated" | "watch_only" | "disabled_unsafe"
    guardrail_triggered: bool
    jump_signal_used: bool
    forecast_signal_used: bool
    hypothetical_price_cpl: float | None
    hypothetical_margin_cpl: float | None


def _recommendation_status(action: str, automation_enabled: bool, has_margin_data: bool) -> str:
    if action == "LEAD":
        return STATUS_AUTOMATED
    if action == "FOLLOW":
        if not has_margin_data:
            return STATUS_DISABLED_UNSAFE
        return STATUS_AUTOMATED if automation_enabled else STATUS_WATCH_ONLY
    return STATUS_AUTOMATED if automation_enabled else STATUS_WATCH_ONLY


def decide_policy(inputs: PolicyInputs, params: PolicyParams) -> PolicyDecision:
    mode = "automated" if inputs.automation_enabled else "watch_only"
    has_margin_data = inputs.tgp_cpl is not None

    if inputs.current_price_cpl is None:
        return PolicyDecision(
            action="HOLD",
            reason="insufficient_data",
            mode=mode,
            recommendation_status=_recommendation_status(
                "HOLD", inputs.automation_enabled, has_margin_data
            ),
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
            recommendation_status=_recommendation_status(
                "LEAD", inputs.automation_enabled, has_margin_data
            ),
            guardrail_triggered=False,
            jump_signal_used=True,
            forecast_signal_used=True,
            hypothetical_price_cpl=hypothetical_price,
            hypothetical_margin_cpl=_margin(hypothetical_price, inputs.tgp_cpl),
        )

    if priced_above_market or forecast_declining:
        return _follow_decision(
            inputs, params, mode, has_margin_data, priced_above_market, forecast_declining
        )

    return PolicyDecision(
        action="HOLD",
        reason="no_trigger",
        mode=mode,
        recommendation_status=_recommendation_status(
            "HOLD", inputs.automation_enabled, has_margin_data
        ),
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
    has_margin_data: bool,
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
                    recommendation_status=_recommendation_status(
                        "HOLD", inputs.automation_enabled, has_margin_data
                    ),
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
        recommendation_status=_recommendation_status(
            "FOLLOW", inputs.automation_enabled, has_margin_data
        ),
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
