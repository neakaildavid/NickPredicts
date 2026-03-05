"""
Stock Oracle — Pure Quantitative Multi-Factor Engine
-----------------------------------------------------
Four-pillar model, all signals derived from Yahoo Finance data.

Pillars
-------
  Value         (35%)  — DCF margin of safety, trailing P/E, P/B, EV/EBITDA
  Growth        (35%)  — Revenue CAGR, EPS trend (OLS), FCF trend (OLS)
  Profitability (20%)  — ROE, ROIC, gross margin, operating margin
  Momentum      (10%)  — 52-week percentile, price vs 50/200-day MA, RSI-14

Scoring pipeline
----------------
  Each metric  →  z-score against calibrated population distributions
  Pillar score =  mean z-score of available metrics in that pillar
  Composite z  =  weighted mean of available pillar scores
                  (weights redistributed proportionally when a pillar
                   has no data at all)
  Probability  =  logistic(composite_z, k=0.8)

DCF specifics
-------------
  - Growth rate: FCF CAGR over all available annual periods (not OLS slope)
  - Debt/cash:   pulled from balance sheet first, info dict as fallback
  - MA-200:      fetches 2 years of history to guarantee 200 data points
  - OLS slope:   used only for the growth pillar z-scores, not DCF inputs

Missing data
------------
  Any metric that cannot be computed is dropped silently.
  Its weight is redistributed to the remaining metrics/pillars.
  A data_coverage field (0-1) is returned so the caller knows how
  complete the analysis is.
"""

import math
import logging
import re

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

app = FastAPI(title="Stock Oracle — Quantitative Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    ticker: str


# ─────────────────────────────────────────────────────────────────────────────
# Population distribution parameters
# ─────────────────────────────────────────────────────────────────────────────
# mu/sigma approximate the cross-sectional distribution of each metric across
# large-cap US equities. They convert raw values into z-scores on a common
# scale. For "lower is better" metrics the z-score is negated so that
# higher z always means "more attractive".

DISTRIBUTIONS: dict[str, dict] = {
    # ── Value ─────────────────────────────────────────────────────────────────
    "dcf_margin_of_safety": {"mu":  0.00, "sigma": 0.40, "higher_is_better": True},
    "pe_ratio":             {"mu": 22.00, "sigma": 12.0, "higher_is_better": False},
    "pb_ratio":             {"mu":  3.50, "sigma":  3.0, "higher_is_better": False},
    "ev_ebitda":            {"mu": 14.00, "sigma":  8.0, "higher_is_better": False},

    # ── Growth ────────────────────────────────────────────────────────────────
    "revenue_cagr":         {"mu":  0.06, "sigma": 0.12, "higher_is_better": True},
    "eps_trend":            {"mu":  0.05, "sigma": 0.20, "higher_is_better": True},
    "fcf_trend":            {"mu":  0.05, "sigma": 0.20, "higher_is_better": True},

    # ── Profitability ─────────────────────────────────────────────────────────
    # Return on Equity — net income / shareholders equity
    "roe":                  {"mu":  0.15, "sigma": 0.15, "higher_is_better": True},
    # Return on Invested Capital — NOPAT / (debt + equity)
    "roic":                 {"mu":  0.10, "sigma": 0.10, "higher_is_better": True},
    # Gross margin — (revenue - COGS) / revenue
    "gross_margin":         {"mu":  0.40, "sigma": 0.20, "higher_is_better": True},
    # Operating margin — operating income / revenue
    "operating_margin":     {"mu":  0.12, "sigma": 0.12, "higher_is_better": True},

    # ── Momentum ──────────────────────────────────────────────────────────────
    "week52_percentile":    {"mu":  0.50, "sigma": 0.25, "higher_is_better": True},
    "price_vs_ma50":        {"mu":  0.00, "sigma": 0.08, "higher_is_better": True},
    "price_vs_ma200":       {"mu":  0.00, "sigma": 0.15, "higher_is_better": True},
    "rsi14":                {"mu": 50.00, "sigma": 12.0, "higher_is_better": True},
}

# ─────────────────────────────────────────────────────────────────────────────
# Pillar membership & base weights
# ─────────────────────────────────────────────────────────────────────────────

PILLARS: dict[str, dict] = {
    "value": {
        "metrics": ["dcf_margin_of_safety", "pe_ratio", "pb_ratio", "ev_ebitda"],
        "weight":  0.35,
    },
    "growth": {
        "metrics": ["revenue_cagr", "eps_trend", "fcf_trend"],
        "weight":  0.35,
    },
    "profitability": {
        "metrics": ["roe", "roic", "gross_margin", "operating_margin"],
        "weight":  0.20,
    },
    "momentum": {
        "metrics": ["week52_percentile", "price_vs_ma50", "price_vs_ma200", "rsi14"],
        "weight":  0.10,
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


def z_score(metric: str, value: float) -> float:
    """
    Z-score a raw metric value using the population parameters in DISTRIBUTIONS.
    Sign is flipped for 'lower is better' metrics so higher z always means
    'more attractive'.
    """
    d = DISTRIBUTIONS[metric]
    z = (value - d["mu"]) / d["sigma"]
    return z if d["higher_is_better"] else -z


def ols_normalised_slope(series: list[float]) -> float | None:
    """
    Fit OLS to (t=0,1,...,n-1) vs y and return slope / |mean(y)|.
    Requires >= 3 points and a non-zero mean. Returns None otherwise.
    Used only for growth pillar z-scores, NOT as a DCF growth rate input.
    """
    if len(series) < 3:
        return None
    mean_y = float(np.mean(series))
    if abs(mean_y) < 1e-9:
        return None
    x   = np.arange(len(series), dtype=float)
    x_c = x - x.mean()
    y_c = np.array(series, dtype=float) - mean_y
    slope = float(np.dot(x_c, y_c) / np.dot(x_c, x_c))
    return slope / abs(mean_y)


def fcf_cagr(fcf_vals: list[float]) -> float | None:
    """
    Compound annual growth rate of FCF across all available annual periods.
    Requires at least 2 data points and positive values at both endpoints.
    Used exclusively as the DCF growth input — not for z-scoring.
    """
    if len(fcf_vals) < 2:
        return None
    start, end = fcf_vals[0], fcf_vals[-1]
    if start <= 0 or end <= 0:
        return None
    n = len(fcf_vals) - 1
    return (end / start) ** (1.0 / n) - 1.0


def logistic(z: float, k: float = 0.8) -> float:
    """Sigmoid mapping composite z -> (0, 1)."""
    return round(1.0 / (1.0 + math.exp(-k * z)), 4)


def verdict(p: float) -> str:
    if p >= 0.72:
        return "STRONG BUY"
    if p >= 0.60:
        return "BUY"
    if p >= 0.45:
        return "HOLD"
    if p >= 0.33:
        return "SELL"
    return "STRONG SELL"


# ─────────────────────────────────────────────────────────────────────────────
# Balance sheet helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_debt_and_cash(balance_sheet, info: dict) -> tuple[float, float]:
    """
    Return (total_debt, cash) from balance sheet where possible,
    falling back to the info dict to avoid date-mismatch issues.
    """
    total_debt = None
    cash       = None

    # Debt — try explicit row first, then reconstruct from components
    for label in (
        "Total Debt",
        "Long Term Debt And Capital Lease Obligation",
        "Long Term Debt",
    ):
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

    # Cash
    for label in (
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
        "Cash And Short Term Investments",
    ):
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
# Metric computers — Value
# ─────────────────────────────────────────────────────────────────────────────

def _dcf_margin_of_safety(
    info: dict,
    financials,
    cashflow,
    balance_sheet,
) -> float | None:
    """
    WACC-based 5-year DCF.
    Returns (intrinsic_price - current_price) / current_price.
    Positive = undervalued vs model; negative = overvalued vs model.

    Growth rate  : FCF CAGR (not OLS slope — CAGR is correct for compounding)
    Debt / cash  : balance sheet sourced to ensure same reporting date
    Terminal g   : 2.5%
    Growth cap   : [-5%, +20%]
    """
    try:
        beta    = safe_float(info.get("beta")) or 1.0
        mkt_cap = safe_float(info.get("marketCap"))
        shares  = safe_float(info.get("sharesOutstanding"))
        price   = safe_float(info.get("currentPrice"))

        if not all([mkt_cap, shares, price]):
            return None

        total_debt, cash = _get_debt_and_cash(balance_sheet, info)

        # WACC
        rf, erp, tax = 0.045, 0.055, 0.21
        ke = rf + beta * erp

        try:
            int_exp = safe_float(financials.loc["Interest Expense"].iloc[0])
            kd = abs(int_exp) / total_debt if (int_exp and total_debt > 0) else 0.04
        except Exception:
            kd = 0.04

        kd        = max(0.005, min(kd, 0.15))
        total_cap = mkt_cap + total_debt
        wacc      = (
            (mkt_cap    / total_cap) * ke
            + (total_debt / total_cap) * kd * (1.0 - tax)
        )

        # FCF series — yfinance returns most-recent first; reverse → chronological
        try:
            fcf_vals = list(reversed(cashflow.loc["Free Cash Flow"].dropna().tolist()))
        except Exception:
            return None

        if len(fcf_vals) < 2:
            return None

        # Growth: CAGR preferred; median YoY as fallback when endpoints are negative
        growth = fcf_cagr(fcf_vals)
        if growth is None:
            yoy = [
                (fcf_vals[i] - fcf_vals[i - 1]) / fcf_vals[i - 1]
                for i in range(1, len(fcf_vals))
                if fcf_vals[i - 1] > 0
            ]
            if not yoy:
                return None
            growth = float(np.median(yoy))

        growth     = max(-0.05, min(growth, 0.20))
        terminal_g = 0.025

        if wacc <= terminal_g:
            return None

        fcf_base  = fcf_vals[-1]
        projected = [fcf_base * (1.0 + growth) ** t for t in range(1, 6)]
        pv_fcfs   = sum(cf / (1.0 + wacc) ** t for t, cf in enumerate(projected, 1))
        tv        = projected[-1] * (1.0 + terminal_g) / (wacc - terminal_g)
        pv_tv     = tv / (1.0 + wacc) ** 5

        intrinsic = ((pv_fcfs + pv_tv) - total_debt + cash) / shares
        return (intrinsic - price) / price

    except Exception as exc:
        log.debug("DCF error: %s", exc)
        return None


def _pe_ratio(info: dict) -> float | None:
    v = safe_float(info.get("trailingPE"))
    return v if v and v > 0 else None


def _pb_ratio(info: dict) -> float | None:
    v = safe_float(info.get("priceToBook"))
    return v if v and v > 0 else None


def _ev_ebitda(info: dict) -> float | None:
    ev     = safe_float(info.get("enterpriseValue"))
    ebitda = safe_float(info.get("ebitda"))
    if ev and ebitda and ebitda > 0:
        return ev / ebitda
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Metric computers — Growth
# ─────────────────────────────────────────────────────────────────────────────

def _revenue_cagr(financials) -> float | None:
    try:
        rev = list(reversed(financials.loc["Total Revenue"].dropna().tolist()))
        if len(rev) < 2 or rev[0] <= 0 or rev[-1] <= 0:
            return None
        n = len(rev) - 1
        return (rev[-1] / rev[0]) ** (1.0 / n) - 1.0
    except Exception as exc:
        log.debug("Revenue CAGR error: %s", exc)
        return None


def _eps_trend(financials) -> float | None:
    try:
        eps = list(reversed(financials.loc["Diluted EPS"].dropna().tolist()))
        return ols_normalised_slope(eps)
    except Exception as exc:
        log.debug("EPS trend error: %s", exc)
        return None


def _fcf_trend(cashflow) -> float | None:
    try:
        fcf = list(reversed(cashflow.loc["Free Cash Flow"].dropna().tolist()))
        return ols_normalised_slope(fcf)
    except Exception as exc:
        log.debug("FCF trend error: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Metric computers — Profitability
# ─────────────────────────────────────────────────────────────────────────────

def _roe(info: dict) -> float | None:
    """
    Return on Equity = net income / shareholders equity.
    yfinance provides this directly as returnOnEquity (expressed as a decimal).
    Negative ROE is dropped — it conflates two very different situations
    (loss-making vs negative book equity) and z-scoring it as "bad" is
    misleading.
    """
    v = safe_float(info.get("returnOnEquity"))
    return v if v is not None and v > 0 else None


def _roic(info: dict) -> float | None:
    """
    Return on Invested Capital = returnOnAssets used as a proxy.
    yfinance does not expose a direct ROIC field; ROA (net income / total
    assets) is the closest available single field and correlates strongly
    with true ROIC for capital-light businesses.
    Negative values dropped for the same reason as ROE.
    """
    v = safe_float(info.get("returnOnAssets"))
    return v if v is not None and v > 0 else None


def _gross_margin(info: dict) -> float | None:
    v = safe_float(info.get("grossMargins"))
    # Gross margin should be between 0 and 1 to be meaningful
    return v if v is not None and 0 < v < 1 else None


def _operating_margin(info: dict) -> float | None:
    v = safe_float(info.get("operatingMargins"))
    # Allow negative operating margin — it's a valid (bad) signal
    return v if v is not None else None


# ─────────────────────────────────────────────────────────────────────────────
# Metric computers — Momentum
# ─────────────────────────────────────────────────────────────────────────────

def _momentum_metrics(ticker: str) -> dict[str, float | None]:
    """
    Fetches 2 years of daily closes (guarantees >= 200 points for MA-200).
    Computes: 52-week percentile, price vs MA-50, price vs MA-200, RSI-14.
    """
    out: dict[str, float | None] = {
        "week52_percentile": None,
        "price_vs_ma50":     None,
        "price_vs_ma200":    None,
        "rsi14":             None,
    }

    try:
        hist = yf.Ticker(ticker).history(period="2y")
        if hist.empty or len(hist) < 15:
            return out

        closes = hist["Close"].dropna()
        price  = float(closes.iloc[-1])

        # 52-week percentile — trailing 252 trading days only
        year_closes = closes.iloc[-252:] if len(closes) >= 252 else closes
        lo, hi = float(year_closes.min()), float(year_closes.max())
        if hi > lo:
            out["week52_percentile"] = (price - lo) / (hi - lo)

        if len(closes) >= 50:
            out["price_vs_ma50"]  = price / float(closes.iloc[-50:].mean())  - 1.0
        if len(closes) >= 200:
            out["price_vs_ma200"] = price / float(closes.iloc[-200:].mean()) - 1.0

        # RSI-14
        if len(closes) >= 15:
            delta    = closes.diff().dropna()
            gains    = delta.clip(lower=0)
            losses   = (-delta).clip(lower=0)
            avg_gain = float(gains.iloc[-14:].mean())
            avg_loss = float(losses.iloc[-14:].mean())
            out["rsi14"] = (
                100.0 if avg_loss == 0
                else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
            )

    except Exception as exc:
        log.debug("Momentum error: %s", exc)

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Aggregate metric collection
# ─────────────────────────────────────────────────────────────────────────────

def collect_metrics(
    ticker: str,
    info: dict,
    financials,
    cashflow,
    balance_sheet,
) -> dict[str, float | None]:
    mom = _momentum_metrics(ticker)
    return {
        # Value
        "dcf_margin_of_safety": _dcf_margin_of_safety(info, financials, cashflow, balance_sheet),
        "pe_ratio":             _pe_ratio(info),
        "pb_ratio":             _pb_ratio(info),
        "ev_ebitda":            _ev_ebitda(info),
        # Growth
        "revenue_cagr":         _revenue_cagr(financials),
        "eps_trend":            _eps_trend(financials),
        "fcf_trend":            _fcf_trend(cashflow),
        # Profitability
        "roe":                  _roe(info),
        "roic":                 _roic(info),
        "gross_margin":         _gross_margin(info),
        "operating_margin":     _operating_margin(info),
        # Momentum
        "week52_percentile":    mom["week52_percentile"],
        "price_vs_ma50":        mom["price_vs_ma50"],
        "price_vs_ma200":       mom["price_vs_ma200"],
        "rsi14":                mom["rsi14"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Scoring engine
# ─────────────────────────────────────────────────────────────────────────────

def score(raw: dict[str, float | None]) -> tuple[float, dict, float]:
    """
    Raw metrics -> z-scores -> pillar scores -> composite z.

    Returns
    -------
    composite_z   : float
    pillar_detail : dict
    data_coverage : float  (fraction of metrics successfully computed)
    """

    # Step 1 — z-score every available metric
    zs: dict[str, float] = {
        m: z_score(m, v) for m, v in raw.items() if v is not None
    }

    data_coverage = len(zs) / len(raw) if raw else 0.0

    # Step 2 — pillar scores = mean z of available metrics
    pillar_z:      dict[str, float | None] = {}
    pillar_weight: dict[str, float]        = {}

    for name, cfg in PILLARS.items():
        available = [zs[m] for m in cfg["metrics"] if m in zs]
        if available:
            pillar_z[name]      = float(np.mean(available))
            pillar_weight[name] = cfg["weight"]
        else:
            pillar_z[name]      = None
            pillar_weight[name] = 0.0

    # Step 3 — redistribute weight from pillars with no data
    active_weight = sum(w for p, w in pillar_weight.items() if pillar_z[p] is not None)
    eff_weight: dict[str, float] = {
        p: (
            pillar_weight[p] / active_weight
            if active_weight > 0 and pillar_z[p] is not None
            else 0.0
        )
        for p in PILLARS
    }

    # Step 4 — weighted composite z
    composite_z = sum(
        eff_weight[p] * pillar_z[p]
        for p in PILLARS
        if pillar_z[p] is not None
    )

    pillar_detail = {
        p: {
            "z_score":          round(pillar_z[p], 4) if pillar_z[p] is not None else None,
            "effective_weight": round(eff_weight[p], 4),
            "metrics_used":     [m for m in PILLARS[p]["metrics"] if m in zs],
            "metrics_missing":  [m for m in PILLARS[p]["metrics"] if m not in zs],
        }
        for p in PILLARS
    }

    return composite_z, pillar_detail, data_coverage


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
# Routes
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

    raw_metrics                    = collect_metrics(ticker, info, financials, cashflow, balance_sheet)
    composite_z, pillars, coverage = score(raw_metrics)
    prob                           = logistic(composite_z)

    log.info(
        "%s | z=%.3f | p=%.3f | %s | coverage=%.0f%%",
        ticker, composite_z, prob, verdict(prob), coverage * 100,
    )

    return {
        "ticker":         ticker,
        "companyName":    info.get("longName", ticker),
        "currentPrice":   safe_float(info.get("currentPrice")),
        "buyProbability": prob,
        "verdict":        verdict(prob),
        "compositeZ":     round(composite_z, 4),
        "dataCoverage":   round(coverage, 4),
        "pillars":        pillars,
        "rawMetrics":     {
            k: (round(v, 6) if v is not None else None)
            for k, v in raw_metrics.items()
        },
    }


@app.get("/health")
def health():
    return {"status": "ok"}