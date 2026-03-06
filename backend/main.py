"""
Stock Oracle — Institutional Multi-Factor Engine v2
----------------------------------------------------
Four-factor model inspired by AQR / Fama-French factor research.
All data sourced from Yahoo Finance via yfinance.

Factors & weights
-----------------
  Value         (30%)  — DCF margin of safety (confidence-weighted),
                          EV/EBITDA, EV/FCF, trailing P/E, P/B
  Quality       (25%)  — ROE, ROIC (ROA proxy), gross margin,
                          operating margin, FCF margin
  Growth        (25%)  — Revenue CAGR, EPS CAGR, operating income growth,
                          FCF growth (only when FCF is stable)
  Momentum      (20%)  — 12-month momentum, 6-month momentum,
                          price vs MA-200, volatility-adjusted return

Scoring pipeline
----------------
  1. Each metric computed from yfinance data
  2. Each metric converted to a 0–100 percentile score using empirically
     calibrated market distributions (anchored at 5th / 95th percentiles
     of large-cap US equities).  Percentile scoring is more robust than
     z-scores because it does not assume normality and is bounded.
  3. Factor score = mean percentile of available metrics in that factor
  4. Composite score = weighted mean of available factor scores
     (weights redistributed when a factor has no data)
  5. Final score scaled 0–100.  Guardrails applied for low data coverage
     and negative fundamental signals.
  6. Score → verdict band

DCF specifics
-------------
  - FCF stability is assessed before running DCF (CV of FCF series)
  - When FCF is unstable, revenue-based growth is used as a proxy
  - A DCF confidence score (0–1) scales its contribution to the Value factor
  - Debt/cash pulled from balance sheet first, info dict as fallback
  - Growth capped at [-5%, +20%]; terminal g = 2.5%

Guardrails
----------
  - data_coverage < 0.50 → score penalised proportionally
  - Negative revenue growth → Growth factor capped at 40/100
  - Negative FCF (most recent year) → Quality factor capped at 45/100
  - Negative operating margin → Quality factor capped at 45/100

Missing data
------------
  Any metric that cannot be computed is dropped silently.
  Its weight is redistributed proportionally to remaining metrics/factors.
"""

import math
import logging
import re
import statistics

import numpy as np
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger("stock_oracle")

# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Stock Oracle — Institutional Engine v2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    ticker: str


# ─────────────────────────────────────────────────────────────────────────────
# Percentile calibration tables
# ─────────────────────────────────────────────────────────────────────────────
# Each entry defines the p5 (bottom 5%) and p95 (top 5%) of the metric's
# cross-sectional distribution across large-cap US equities.
# Values outside this range are clamped before percentile scoring.
#
# For "lower is better" metrics (higher_is_better=False), the raw value is
# negated before percentile scoring so that a higher percentile always means
# "more attractive".
#
# Calibration sources: S&P 500 cross-sectional medians, Damodaran sector
# data, academic factor literature (Fama-French, AQR).

METRIC_CALIBRATION: dict[str, dict] = {
    # ── Value ──────────────────────────────────────────────────────────────────
    # DCF margin of safety: (intrinsic - price) / price
    "dcf_mos":          {"p5": -0.60, "p95":  0.80, "higher_is_better": True},
    # EV / EBITDA
    "ev_ebitda":        {"p5":  3.0,  "p95": 35.0,  "higher_is_better": False},
    # EV / FCF  (enterprise value / free cash flow)
    "ev_fcf":           {"p5":  8.0,  "p95": 60.0,  "higher_is_better": False},
    # Trailing P/E
    "pe_ratio":         {"p5":  8.0,  "p95": 45.0,  "higher_is_better": False},
    # Price / Book
    "pb_ratio":         {"p5":  0.8,  "p95": 12.0,  "higher_is_better": False},

    # ── Quality / Profitability ────────────────────────────────────────────────
    # Return on Equity
    "roe":              {"p5":  0.02, "p95":  0.45, "higher_is_better": True},
    # Return on Assets (ROIC proxy)
    "roic":             {"p5":  0.01, "p95":  0.25, "higher_is_better": True},
    # Gross margin
    "gross_margin":     {"p5":  0.10, "p95":  0.80, "higher_is_better": True},
    # Operating margin
    "operating_margin": {"p5": -0.05, "p95":  0.35, "higher_is_better": True},
    # FCF margin = FCF / revenue
    "fcf_margin":       {"p5": -0.05, "p95":  0.30, "higher_is_better": True},

    # ── Growth ────────────────────────────────────────────────────────────────
    # Revenue CAGR (annualised over available years)
    "revenue_cagr":     {"p5": -0.05, "p95":  0.30, "higher_is_better": True},
    # EPS CAGR (only when stable — both endpoints positive)
    "eps_cagr":         {"p5": -0.10, "p95":  0.40, "higher_is_better": True},
    # Operating income growth (YoY, most recent year)
    "opinc_growth":     {"p5": -0.20, "p95":  0.40, "higher_is_better": True},
    # FCF growth (only when both years positive)
    "fcf_growth":       {"p5": -0.15, "p95":  0.40, "higher_is_better": True},

    # ── Momentum ──────────────────────────────────────────────────────────────
    # 12-month price return (skip most recent month — standard momentum)
    "momentum_12m":     {"p5": -0.35, "p95":  0.65, "higher_is_better": True},
    # 6-month price return (skip most recent month)
    "momentum_6m":      {"p5": -0.25, "p95":  0.45, "higher_is_better": True},
    # Price vs 200-day MA
    "price_vs_ma200":   {"p5": -0.25, "p95":  0.30, "higher_is_better": True},
    # Volatility-adjusted 12m return (return / annualised vol)
    "sharpe_12m":       {"p5": -1.50, "p95":  2.00, "higher_is_better": True},
}

# ─────────────────────────────────────────────────────────────────────────────
# Factor membership & base weights
# ─────────────────────────────────────────────────────────────────────────────

FACTORS: dict[str, dict] = {
    "value": {
        "metrics": ["dcf_mos", "ev_ebitda", "ev_fcf", "pe_ratio", "pb_ratio"],
        "weight":  0.30,
    },
    "quality": {
        "metrics": ["roe", "roic", "gross_margin", "operating_margin", "fcf_margin"],
        "weight":  0.25,
    },
    "growth": {
        "metrics": ["revenue_cagr", "eps_cagr", "opinc_growth", "fcf_growth"],
        "weight":  0.25,
    },
    "momentum": {
        "metrics": ["momentum_12m", "momentum_6m", "price_vs_ma200", "sharpe_12m"],
        "weight":  0.20,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Core utilities
# ─────────────────────────────────────────────────────────────────────────────

def safe_float(x) -> float | None:
    """Return float or None; never raises."""
    try:
        if x is None:
            return None
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def to_percentile(metric: str, value: float) -> float:
    """
    Convert a raw metric value to a 0–100 percentile score using the
    empirically calibrated p5/p95 anchors in METRIC_CALIBRATION.

    Steps:
      1. If metric is 'lower is better', negate the value so higher always
         means better before any further transformation.
      2. Linearly interpolate between the (negated) p5 and p95 anchors,
         clamping the result to [0, 100].

    This is equivalent to assuming a uniform distribution between p5 and p95
    and assigning 0 to anything at or below p5 and 100 to anything at or
    above p95.  It is robust to outliers and does not assume normality.
    """
    cal = METRIC_CALIBRATION[metric]
    v   = value if cal["higher_is_better"] else -value

    # Negate calibration anchors for lower-is-better metrics too
    lo = cal["p5"]  if cal["higher_is_better"] else -cal["p95"]
    hi = cal["p95"] if cal["higher_is_better"] else -cal["p5"]

    if hi == lo:
        return 50.0

    pct = (v - lo) / (hi - lo) * 100.0
    return float(np.clip(pct, 0.0, 100.0))


def cagr(series: list[float]) -> float | None:
    """
    Compound annual growth rate over all available annual periods.
    Requires >= 2 points and positive values at both endpoints.
    """
    if len(series) < 2:
        return None
    start, end = series[0], series[-1]
    if start <= 0 or end <= 0:
        return None
    n = len(series) - 1
    return (end / start) ** (1.0 / n) - 1.0


def coefficient_of_variation(series: list[float]) -> float | None:
    """
    CV = std / |mean|.  Measures relative volatility of a series.
    Returns None when mean is near zero.
    """
    if len(series) < 3:
        return None
    mean = statistics.mean(series)
    if abs(mean) < 1e-9:
        return None
    return statistics.stdev(series) / abs(mean)


def verdict(score: float) -> str:
    if score >= 72:
        return "STRONG BUY"
    if score >= 60:
        return "BUY"
    if score >= 45:
        return "HOLD"
    if score >= 33:
        return "SELL"
    return "STRONG SELL"


# ─────────────────────────────────────────────────────────────────────────────
# Balance sheet helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_debt_and_cash(balance_sheet, info: dict) -> tuple[float, float]:
    """
    (total_debt, cash) from balance sheet first, info dict as fallback.
    Using the balance sheet ensures debt and cash come from the same date.
    """
    total_debt = None
    cash       = None

    for label in ("Total Debt", "Long Term Debt And Capital Lease Obligation", "Long Term Debt"):
        try:
            v = safe_float(balance_sheet.loc[label].iloc[0])
            if v is not None:
                total_debt = abs(v)
                break
        except Exception:
            continue

    if total_debt is None:
        try:
            ltd = safe_float(balance_sheet.loc["Long Term Debt"].iloc[0]) or 0.0
            std = safe_float(balance_sheet.loc["Current Debt"].iloc[0])   or 0.0
            total_debt = abs(ltd) + abs(std)
        except Exception:
            pass

    if total_debt is None:
        total_debt = safe_float(info.get("totalDebt")) or 0.0

    for label in ("Cash And Cash Equivalents",
                  "Cash Cash Equivalents And Short Term Investments",
                  "Cash And Short Term Investments"):
        try:
            v = safe_float(balance_sheet.loc[label].iloc[0])
            if v is not None:
                cash = v
                break
        except Exception:
            continue

    if cash is None:
        cash = safe_float(info.get("totalCash")) or 0.0

    return float(total_debt), float(cash)


# ─────────────────────────────────────────────────────────────────────────────
# Value metrics
# ─────────────────────────────────────────────────────────────────────────────

def _dcf(info: dict, financials, cashflow, balance_sheet) -> tuple[float | None, float]:
    """
    WACC-based 5-year DCF.

    Returns
    -------
    margin_of_safety : (intrinsic - price) / price, or None if DCF cannot run
    confidence       : 0–1 scalar reflecting how reliable this DCF output is

    Confidence is reduced by:
      - High FCF volatility (CV > 0.5)
      - Very short FCF history (< 3 years)
      - Negative base FCF
      - Extreme beta (< 0.3 or > 2.5, suggesting unreliable CAPM input)
      - Fallback growth (revenue-based rather than FCF-based)
    """
    confidence = 1.0

    try:
        beta    = safe_float(info.get("beta")) or 1.0
        mkt_cap = safe_float(info.get("marketCap"))
        shares  = safe_float(info.get("sharesOutstanding"))
        price   = safe_float(info.get("currentPrice"))

        if not all([mkt_cap, shares, price]):
            return None, 0.0

        # Beta reliability check
        if beta < 0.3 or beta > 2.5:
            beta = 1.0
            confidence -= 0.15

        total_debt, cash = _get_debt_and_cash(balance_sheet, info)

        # ── WACC ────────────────────────────────────────────────────────────
        rf, erp, tax = 0.045, 0.055, 0.21
        ke = rf + beta * erp

        try:
            int_exp = safe_float(financials.loc["Interest Expense"].iloc[0])
            kd = abs(int_exp) / total_debt if (int_exp and total_debt > 0) else 0.04
        except Exception:
            kd = 0.04

        kd        = max(0.005, min(kd, 0.15))
        total_cap = mkt_cap + total_debt
        wacc      = (mkt_cap / total_cap) * ke + (total_debt / total_cap) * kd * (1.0 - tax)

        # ── FCF series ──────────────────────────────────────────────────────
        try:
            fcf_vals = list(reversed(cashflow.loc["Free Cash Flow"].dropna().tolist()))
        except Exception:
            return None, 0.0

        if len(fcf_vals) < 2:
            return None, 0.0

        # Short history penalty
        if len(fcf_vals) < 3:
            confidence -= 0.20

        # FCF volatility check
        cv = coefficient_of_variation(fcf_vals)
        if cv is not None:
            if cv > 1.0:
                confidence -= 0.30
            elif cv > 0.5:
                confidence -= 0.15

        # Negative base FCF
        fcf_base = fcf_vals[-1]
        if fcf_base <= 0:
            confidence -= 0.25

        # ── Growth rate ─────────────────────────────────────────────────────
        # Primary: FCF CAGR.  Fallback: revenue CAGR (with confidence penalty).
        growth = cagr(fcf_vals)

        if growth is None:
            # FCF endpoints not both positive — use revenue growth as proxy
            try:
                rev = list(reversed(financials.loc["Total Revenue"].dropna().tolist()))
                growth = cagr(rev)
                confidence -= 0.20   # penalise for using proxy growth
            except Exception:
                growth = None

        if growth is None:
            return None, 0.0

        growth     = max(-0.05, min(growth, 0.20))
        terminal_g = 0.025

        if wacc <= terminal_g:
            return None, 0.0

        # ── 5-year projection ───────────────────────────────────────────────
        if fcf_base <= 0:
            # Cannot project from negative base; use absolute value but
            # apply a heavy confidence penalty (already done above)
            fcf_base = abs(fcf_base)
            if fcf_base < 1:
                return None, 0.0

        projected = [fcf_base * (1.0 + growth) ** t for t in range(1, 6)]
        pv_fcfs   = sum(cf / (1.0 + wacc) ** t for t, cf in enumerate(projected, 1))
        tv        = projected[-1] * (1.0 + terminal_g) / (wacc - terminal_g)
        pv_tv     = tv / (1.0 + wacc) ** 5

        intrinsic = ((pv_fcfs + pv_tv) - total_debt + cash) / shares
        mos       = (intrinsic - price) / price

        # Extreme MoS is a sign of model noise, not signal — cap and penalise
        if abs(mos) > 2.0:
            confidence -= 0.20
            mos = np.clip(mos, -2.0, 2.0)

        confidence = float(np.clip(confidence, 0.0, 1.0))
        return float(mos), confidence

    except Exception as exc:
        log.debug("DCF error: %s", exc)
        return None, 0.0


def _ev_ebitda(info: dict) -> float | None:
    ev     = safe_float(info.get("enterpriseValue"))
    ebitda = safe_float(info.get("ebitda"))
    return ev / ebitda if ev and ebitda and ebitda > 0 else None


def _ev_fcf(info: dict, cashflow) -> float | None:
    """EV / Free Cash Flow — punishes expensive cash-flow multiples."""
    ev = safe_float(info.get("enterpriseValue"))
    if not ev:
        return None
    try:
        fcf = safe_float(cashflow.loc["Free Cash Flow"].iloc[0])
        if fcf and fcf > 0:
            return ev / fcf
    except Exception:
        pass
    return None


def _pe_ratio(info: dict) -> float | None:
    v = safe_float(info.get("trailingPE"))
    return v if v and 0 < v < 500 else None


def _pb_ratio(info: dict) -> float | None:
    v = safe_float(info.get("priceToBook"))
    return v if v and v > 0 else None


# ─────────────────────────────────────────────────────────────────────────────
# Quality / Profitability metrics
# ─────────────────────────────────────────────────────────────────────────────

def _roe(info: dict) -> float | None:
    v = safe_float(info.get("returnOnEquity"))
    # Drop negative ROE — ambiguous signal (losses vs negative book equity)
    return v if v is not None and v > 0 else None


def _roic(info: dict) -> float | None:
    """ROA as ROIC proxy — best single field available from yfinance."""
    v = safe_float(info.get("returnOnAssets"))
    return v if v is not None and v > 0 else None


def _gross_margin(info: dict) -> float | None:
    v = safe_float(info.get("grossMargins"))
    return v if v is not None and 0 < v < 1 else None


def _operating_margin(info: dict) -> float | None:
    v = safe_float(info.get("operatingMargins"))
    return v if v is not None and -0.5 < v < 1 else None


def _fcf_margin(info: dict, financials, cashflow) -> float | None:
    """FCF / Revenue — measures cash conversion quality."""
    try:
        fcf = safe_float(cashflow.loc["Free Cash Flow"].iloc[0])
        rev = safe_float(financials.loc["Total Revenue"].iloc[0])
        if fcf is not None and rev and rev > 0:
            return fcf / rev
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Growth metrics
# ─────────────────────────────────────────────────────────────────────────────

def _revenue_cagr(financials) -> float | None:
    try:
        rev = list(reversed(financials.loc["Total Revenue"].dropna().tolist()))
        return cagr(rev)
    except Exception as exc:
        log.debug("Revenue CAGR: %s", exc)
        return None


def _eps_cagr(financials) -> float | None:
    """EPS CAGR — only computed when both endpoints are positive."""
    try:
        eps = list(reversed(financials.loc["Diluted EPS"].dropna().tolist()))
        return cagr(eps)   # cagr() already requires positive endpoints
    except Exception as exc:
        log.debug("EPS CAGR: %s", exc)
        return None


def _opinc_growth(financials) -> float | None:
    """
    Operating income YoY growth (most recent year).
    More stable than multi-year OLS slope for a single forward-looking signal.
    """
    try:
        opinc = list(reversed(financials.loc["Operating Income"].dropna().tolist()))
        if len(opinc) < 2 or opinc[-2] == 0 or opinc[-2] < 0:
            return None
        return (opinc[-1] - opinc[-2]) / abs(opinc[-2])
    except Exception as exc:
        log.debug("Op income growth: %s", exc)
        return None


def _fcf_growth(cashflow) -> float | None:
    """
    FCF YoY growth — only computed when both years are positive to avoid
    meaningless sign changes around zero.
    """
    try:
        fcf = list(reversed(cashflow.loc["Free Cash Flow"].dropna().tolist()))
        if len(fcf) < 2 or fcf[-2] <= 0 or fcf[-1] <= 0:
            return None
        return (fcf[-1] - fcf[-2]) / fcf[-2]
    except Exception as exc:
        log.debug("FCF growth: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Momentum metrics
# ─────────────────────────────────────────────────────────────────────────────

def _momentum_metrics(ticker: str) -> dict[str, float | None]:
    """
    Fetches 14 months of daily closes to compute:
      momentum_12m   — 12-month return, skipping the most recent month
                       (standard academic momentum to avoid short-term reversal)
      momentum_6m    — 6-month return, same skip convention
      price_vs_ma200 — (price / 200-day MA) - 1
      sharpe_12m     — annualised return / annualised volatility over 12 months
                       (Sharpe-like ratio; no risk-free subtraction for simplicity)
    """
    out: dict[str, float | None] = {
        "momentum_12m":   None,
        "momentum_6m":    None,
        "price_vs_ma200": None,
        "sharpe_12m":     None,
    }

    try:
        # 2 years guarantees 200-day MA and full momentum window
        hist   = yf.Ticker(ticker).history(period="2y")
        if hist.empty or len(hist) < 60:
            return out

        closes = hist["Close"].dropna()
        n      = len(closes)

        # Skip most recent ~21 trading days (1 month) per momentum convention
        skip = 21
        if n > skip + 252:
            ret_12m = float(closes.iloc[-(skip + 252)] )
            price_skip = float(closes.iloc[-skip])
            out["momentum_12m"] = (price_skip - ret_12m) / ret_12m

        if n > skip + 126:
            ret_6m = float(closes.iloc[-(skip + 126)])
            price_skip = float(closes.iloc[-skip])
            out["momentum_6m"] = (price_skip - ret_6m) / ret_6m

        if n >= 200:
            price = float(closes.iloc[-1])
            ma200 = float(closes.iloc[-200:].mean())
            out["price_vs_ma200"] = (price / ma200) - 1.0

        # Sharpe-like: annualised return / annualised vol over past 252 days
        if n >= 252:
            window  = closes.iloc[-252:]
            daily_r = window.pct_change().dropna()
            ann_ret = float((1 + daily_r.mean()) ** 252 - 1)
            ann_vol = float(daily_r.std() * math.sqrt(252))
            if ann_vol > 0:
                out["sharpe_12m"] = ann_ret / ann_vol

    except Exception as exc:
        log.debug("Momentum error: %s", exc)

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Guardrails
# ─────────────────────────────────────────────────────────────────────────────

def _compute_guardrail_flags(info: dict, financials, cashflow) -> dict:
    """
    Returns a dict of boolean warning flags. Each True flag indicates a
    structural concern that will penalise the final score.
    """
    flags: dict[str, bool] = {
        "negative_revenue_growth": False,
        "negative_fcf":            False,
        "negative_operating_margin": False,
    }

    # Negative revenue growth (most recent YoY)
    try:
        rev = list(reversed(financials.loc["Total Revenue"].dropna().tolist()))
        if len(rev) >= 2 and rev[-2] > 0:
            if (rev[-1] - rev[-2]) / rev[-2] < 0:
                flags["negative_revenue_growth"] = True
    except Exception:
        pass

    # Negative FCF (most recent year)
    try:
        fcf = safe_float(cashflow.loc["Free Cash Flow"].iloc[0])
        if fcf is not None and fcf < 0:
            flags["negative_fcf"] = True
    except Exception:
        pass

    # Negative operating margin
    try:
        om = safe_float(info.get("operatingMargins"))
        if om is not None and om < 0:
            flags["negative_operating_margin"] = True
    except Exception:
        pass

    return flags


def _apply_guardrails(
    factor_scores: dict[str, float | None],
    flags: dict[str, bool],
    data_coverage: float,
) -> dict[str, float | None]:
    """
    Applies caps and penalties to factor scores based on guardrail flags.
    Does not modify scores in place — returns a new dict.
    """
    s = dict(factor_scores)

    # Negative revenue growth → cap Growth factor at 40
    if flags.get("negative_revenue_growth") and s.get("growth") is not None:
        s["growth"] = min(s["growth"], 40.0)

    # Negative FCF or negative operating margin → cap Quality at 45
    if (flags.get("negative_fcf") or flags.get("negative_operating_margin")):
        if s.get("quality") is not None:
            s["quality"] = min(s["quality"], 45.0)

    return s


# ─────────────────────────────────────────────────────────────────────────────
# Aggregate metric collection
# ─────────────────────────────────────────────────────────────────────────────

def collect_metrics(
    ticker: str,
    info: dict,
    financials,
    cashflow,
    balance_sheet,
) -> tuple[dict[str, float | None], float, float]:
    """
    Returns
    -------
    raw_metrics   : dict of metric_name -> raw value (or None)
    dcf_mos       : DCF margin of safety (raw, or None)
    dcf_confidence: 0–1 DCF reliability score
    """
    dcf_mos, dcf_confidence = _dcf(info, financials, cashflow, balance_sheet)
    mom = _momentum_metrics(ticker)

    raw: dict[str, float | None] = {
        # Value
        "dcf_mos":          dcf_mos,
        "ev_ebitda":        _ev_ebitda(info),
        "ev_fcf":           _ev_fcf(info, cashflow),
        "pe_ratio":         _pe_ratio(info),
        "pb_ratio":         _pb_ratio(info),
        # Quality
        "roe":              _roe(info),
        "roic":             _roic(info),
        "gross_margin":     _gross_margin(info),
        "operating_margin": _operating_margin(info),
        "fcf_margin":       _fcf_margin(info, financials, cashflow),
        # Growth
        "revenue_cagr":     _revenue_cagr(financials),
        "eps_cagr":         _eps_cagr(financials),
        "opinc_growth":     _opinc_growth(financials),
        "fcf_growth":       _fcf_growth(cashflow),
        # Momentum
        "momentum_12m":     mom["momentum_12m"],
        "momentum_6m":      mom["momentum_6m"],
        "price_vs_ma200":   mom["price_vs_ma200"],
        "sharpe_12m":       mom["sharpe_12m"],
    }

    return raw, dcf_mos, dcf_confidence


# ─────────────────────────────────────────────────────────────────────────────
# Scoring engine
# ─────────────────────────────────────────────────────────────────────────────

def score_metrics(
    raw: dict[str, float | None],
    dcf_confidence: float,
    flags: dict[str, bool],
) -> tuple[float, dict, float]:
    """
    Convert raw metrics -> percentile scores -> factor scores -> composite.

    The DCF's contribution to the Value factor is scaled by dcf_confidence,
    so an unreliable DCF has less influence.

    Returns
    -------
    composite_score : float  (0–100)
    factor_detail   : dict
    data_coverage   : float  (0–1)
    """

    # Step 1 — percentile-score every available metric
    pscores: dict[str, float] = {}
    for metric, value in raw.items():
        if value is not None and metric in METRIC_CALIBRATION:
            pscores[metric] = to_percentile(metric, value)

    # Apply DCF confidence weighting:
    # Replace raw DCF percentile score with confidence-scaled version.
    # A confidence of 0.5 pulls the DCF score halfway toward neutral (50).
    if "dcf_mos" in pscores:
        pscores["dcf_mos"] = 50.0 + (pscores["dcf_mos"] - 50.0) * dcf_confidence

    data_coverage = len(pscores) / len(raw) if raw else 0.0

    # Step 2 — factor scores = mean percentile of available metrics
    factor_raw: dict[str, float | None] = {}
    factor_weight: dict[str, float]     = {}

    for name, cfg in FACTORS.items():
        available = [pscores[m] for m in cfg["metrics"] if m in pscores]
        if available:
            factor_raw[name]    = float(np.mean(available))
            factor_weight[name] = cfg["weight"]
        else:
            factor_raw[name]    = None
            factor_weight[name] = 0.0

    # Step 3 — apply guardrails (caps on factor scores)
    factor_scores = _apply_guardrails(factor_raw, flags, data_coverage)

    # Step 4 — redistribute weight from missing factors
    active_w = sum(w for f, w in factor_weight.items() if factor_scores[f] is not None)
    eff_weight: dict[str, float] = {
        f: (factor_weight[f] / active_w if active_w > 0 and factor_scores[f] is not None else 0.0)
        for f in FACTORS
    }

    # Step 5 — composite score (0–100)
    composite = sum(
        eff_weight[f] * factor_scores[f]
        for f in FACTORS
        if factor_scores[f] is not None
    )

    # Step 6 — data coverage penalty
    # Below 50% coverage, linearly pull the score toward 50 (neutral)
    if data_coverage < 0.50:
        blend = data_coverage / 0.50   # 0 at 0% coverage, 1 at 50% coverage
        composite = 50.0 + (composite - 50.0) * blend

    composite = float(np.clip(composite, 0.0, 100.0))

    factor_detail = {
        f: {
            "score":            round(factor_scores[f], 2) if factor_scores[f] is not None else None,
            "effective_weight": round(eff_weight[f], 4),
            "metrics_used":     [m for m in FACTORS[f]["metrics"] if m in pscores],
            "metrics_missing":  [m for m in FACTORS[f]["metrics"] if m not in pscores],
        }
        for f in FACTORS
    }

    return composite, factor_detail, data_coverage


# ─────────────────────────────────────────────────────────────────────────────
# Input validation
# ─────────────────────────────────────────────────────────────────────────────

_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")

def _validate_ticker(raw: str) -> str:
    t = raw.strip().upper()
    if not _TICKER_RE.match(t):
        raise HTTPException(status_code=400, detail=f"Invalid ticker: '{raw}'")
    return t


# ─────────────────────────────────────────────────────────────────────────────
# Route
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    ticker = _validate_ticker(req.ticker)
    log.info("Analyzing %s", ticker)

    stock         = yf.Ticker(ticker)
    info          = stock.info
    financials    = stock.financials
    cashflow      = stock.cashflow
    balance_sheet = stock.balance_sheet

    if not info or not (info.get("currentPrice") or info.get("regularMarketPrice")):
        raise HTTPException(status_code=404, detail=f"No price data found for '{ticker}'")

    raw_metrics, dcf_mos, dcf_confidence = collect_metrics(
        ticker, info, financials, cashflow, balance_sheet
    )

    flags = _compute_guardrail_flags(info, financials, cashflow)

    composite, factors, coverage = score_metrics(raw_metrics, dcf_confidence, flags)

    log.info(
        "%s | score=%.1f | %s | coverage=%.0f%% | flags=%s",
        ticker, composite, verdict(composite), coverage * 100,
        [k for k, v in flags.items() if v],
    )

    return {
        "ticker":          ticker,
        "companyName":     info.get("longName", ticker),
        "currentPrice":    safe_float(info.get("currentPrice")),
        # Primary output
        "score":           round(composite, 1),
        "verdict":         verdict(composite),
        # Supporting detail
        "dcfConfidence":   round(dcf_confidence, 3),
        "dataCoverage":    round(coverage, 4),
        "guardrailFlags":  flags,
        "factors":         factors,
        "rawMetrics":      {
            k: (round(v, 6) if v is not None else None)
            for k, v in raw_metrics.items()
        },
    }


@app.get("/health")
def health():
    return {"status": "ok"}