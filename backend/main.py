"""
Stock Oracle — Pure Quantitative Multi-Factor Engine
-----------------------------------------------------
Three-pillar model, all signals derived from Yahoo Finance data.

Pillars
-------
  Value    (35%)  — DCF margin of safety, trailing P/E, P/B, EV/EBITDA
  Growth   (35%)  — Revenue CAGR, EPS trend (OLS), FCF trend (OLS)
  Momentum (30%)  — 52-week percentile, price vs 50/200-day MA, RSI-14

Scoring pipeline
----------------
  Each metric  →  z-score against calibrated population distributions
  Pillar score =  mean z-score of available metrics in that pillar
  Composite z  =  weighted mean of available pillar scores
                  (weights redistributed proportionally when a pillar
                   has no data at all)
  Probability  =  logistic(composite_z, k=0.8)

Missing data
------------
  Any metric that cannot be computed is dropped silently.
  Its weight is redistributed to the remaining metrics/pillars.
  A data_coverage field (0–1) is returned so the caller knows how
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
# large-cap US equities.  They convert raw values into z-scores on a common
# scale.  For "lower is better" metrics the z-score is negated so that
# higher z always means "more attractive".

DISTRIBUTIONS: dict[str, dict] = {
    # ── Value ────────────────────────────────────────────────────────────────
    # (intrinsic_price - current_price) / current_price  →  positive = cheap
    "dcf_margin_of_safety": {"mu":  0.00, "sigma": 0.40, "higher_is_better": True},
    # Trailing twelve-month P/E                          →  lower = cheaper
    "pe_ratio":             {"mu": 22.00, "sigma": 12.0, "higher_is_better": False},
    # Price-to-book                                      →  lower = cheaper
    "pb_ratio":             {"mu":  3.50, "sigma":  3.0, "higher_is_better": False},
    # Enterprise Value / EBITDA                          →  lower = cheaper
    "ev_ebitda":            {"mu": 14.00, "sigma":  8.0, "higher_is_better": False},

    # ── Growth ───────────────────────────────────────────────────────────────
    # Annualised revenue CAGR over available annual periods
    "revenue_cagr":         {"mu":  0.06, "sigma": 0.12, "higher_is_better": True},
    # OLS slope of EPS series / |mean EPS|  (normalised trend)
    "eps_trend":            {"mu":  0.05, "sigma": 0.20, "higher_is_better": True},
    # OLS slope of FCF series / |mean FCF|  (normalised trend)
    "fcf_trend":            {"mu":  0.05, "sigma": 0.20, "higher_is_better": True},

    # ── Momentum ─────────────────────────────────────────────────────────────
    # Position within 52-week range: 0 = at 52-wk low, 1 = at 52-wk high
    "week52_percentile":    {"mu":  0.50, "sigma": 0.25, "higher_is_better": True},
    # (price / 50-day MA) - 1
    "price_vs_ma50":        {"mu":  0.00, "sigma": 0.08, "higher_is_better": True},
    # (price / 200-day MA) - 1
    "price_vs_ma200":       {"mu":  0.00, "sigma": 0.15, "higher_is_better": True},
    # RSI-14 centred at 50; above 50 = bullish momentum
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
    "momentum": {
        "metrics": ["week52_percentile", "price_vs_ma50", "price_vs_ma200", "rsi14"],
        "weight":  0.30,
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
    Z-score a raw metric value using the population parameters defined in
    DISTRIBUTIONS.  The sign is flipped for 'lower is better' metrics so that
    a higher z always means 'more attractive'.
    """
    d = DISTRIBUTIONS[metric]
    z = (value - d["mu"]) / d["sigma"]
    return z if d["higher_is_better"] else -z


def ols_normalised_slope(series: list[float]) -> float | None:
    """
    Fit OLS to (t=0,1,…,n-1) vs y and return slope / |mean(y)|.
    Requires ≥ 3 points and a non-zero mean.  Returns None otherwise.
    The normalisation makes the slope comparable across stocks of different
    sizes (analogous to a percentage growth rate per period).
    """
    if len(series) < 3:
        return None
    mean_y = float(np.mean(series))
    if abs(mean_y) < 1e-9:
        return None
    x = np.arange(len(series), dtype=float)
    x_c = x - x.mean()
    y_c = np.array(series) - mean_y
    slope = float(np.dot(x_c, y_c) / np.dot(x_c, x_c))
    return slope / abs(mean_y)


def logistic(z: float, k: float = 0.8) -> float:
    """Sigmoid mapping composite z → (0, 1)."""
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
# Metric computers — Value
# ─────────────────────────────────────────────────────────────────────────────

def _dcf_margin_of_safety(info: dict, financials, cashflow) -> float | None:
    """
    WACC-based 5-year DCF.
    Returns (intrinsic_price - current_price) / current_price.
    Positive → model says undervalued; negative → model says overvalued.

    FCF growth is estimated via OLS normalised slope over all available annual
    periods (more stable than a single year-over-year delta).
    """
    try:
        beta       = safe_float(info.get("beta")) or 1.0
        mkt_cap    = safe_float(info.get("marketCap"))
        total_debt = safe_float(info.get("totalDebt")) or 0.0
        cash       = safe_float(info.get("totalCash")) or 0.0
        shares     = safe_float(info.get("sharesOutstanding"))
        price      = safe_float(info.get("currentPrice"))

        if not all([mkt_cap, shares, price]):
            return None

        # WACC
        rf, erp, tax = 0.045, 0.055, 0.21
        ke = rf + beta * erp

        try:
            int_exp   = abs(float(financials.loc["Interest Expense"].iloc[0]))
            kd        = int_exp / total_debt if total_debt > 0 else 0.04
        except Exception:
            kd = 0.04

        total_cap = mkt_cap + total_debt
        wacc      = (mkt_cap / total_cap) * ke + (total_debt / total_cap) * kd * (1 - tax)

        # FCF series — yfinance returns most-recent first; reverse → chronological
        try:
            fcf_vals = cashflow.loc["Free Cash Flow"].dropna().tolist()
            fcf_vals = list(reversed(fcf_vals))
        except Exception:
            return None

        if len(fcf_vals) < 2:
            return None

        # Growth estimate
        slope = ols_normalised_slope(fcf_vals)
        if slope is None:
            # Fallback: two-point CAGR (requires positive endpoints)
            if fcf_vals[0] > 0 and fcf_vals[-1] > 0:
                n     = len(fcf_vals) - 1
                slope = (fcf_vals[-1] / fcf_vals[0]) ** (1 / n) - 1
            else:
                return None

        growth = max(min(float(slope), 0.20), -0.05)

        # 5-year projection
        terminal_g = 0.025
        if wacc <= terminal_g:
            return None

        fcf_latest = fcf_vals[-1]
        projected  = [fcf_latest * (1 + growth) ** t for t in range(1, 6)]
        pv_fcfs    = sum(cf / (1 + wacc) ** t for t, cf in enumerate(projected, 1))

        tv         = projected[-1] * (1 + terminal_g) / (wacc - terminal_g)
        pv_tv      = tv / (1 + wacc) ** 5

        intrinsic  = ((pv_fcfs + pv_tv) - total_debt + cash) / shares
        return (intrinsic - price) / price

    except Exception as exc:
        log.debug("DCF error: %s", exc)
        return None


def _pe_ratio(info: dict) -> float | None:
    return safe_float(info.get("trailingPE"))


def _pb_ratio(info: dict) -> float | None:
    return safe_float(info.get("priceToBook"))


def _ev_ebitda(info: dict) -> float | None:
    ev     = safe_float(info.get("enterpriseValue"))
    ebitda = safe_float(info.get("ebitda"))
    if ev and ebitda and ebitda != 0:
        return ev / ebitda
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Metric computers — Growth
# ─────────────────────────────────────────────────────────────────────────────

def _revenue_cagr(financials) -> float | None:
    """Annualised revenue CAGR across all available annual periods."""
    try:
        rev = financials.loc["Total Revenue"].dropna().tolist()
        rev = list(reversed(rev))           # chronological
        if len(rev) < 2 or rev[0] <= 0:
            return None
        n = len(rev) - 1
        return (rev[-1] / rev[0]) ** (1 / n) - 1
    except Exception as exc:
        log.debug("Revenue CAGR error: %s", exc)
        return None


def _eps_trend(financials) -> float | None:
    """OLS normalised slope of diluted EPS."""
    try:
        eps = financials.loc["Diluted EPS"].dropna().tolist()
        return ols_normalised_slope(list(reversed(eps)))
    except Exception as exc:
        log.debug("EPS trend error: %s", exc)
        return None


def _fcf_trend(cashflow) -> float | None:
    """OLS normalised slope of Free Cash Flow."""
    try:
        fcf = cashflow.loc["Free Cash Flow"].dropna().tolist()
        return ols_normalised_slope(list(reversed(fcf)))
    except Exception as exc:
        log.debug("FCF trend error: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Metric computers — Momentum  (requires price history fetch)
# ─────────────────────────────────────────────────────────────────────────────

def _momentum_metrics(ticker: str) -> dict[str, float | None]:
    """
    Fetches 1 year of daily closes to compute:
      week52_percentile — where price sits in the 52-week range
      price_vs_ma50     — (price / 50-day MA) - 1
      price_vs_ma200    — (price / 200-day MA) - 1
      rsi14             — Wilder RSI with a 14-period lookback
    """
    out: dict[str, float | None] = {
        "week52_percentile": None,
        "price_vs_ma50":     None,
        "price_vs_ma200":    None,
        "rsi14":             None,
    }

    try:
        hist   = yf.Ticker(ticker).history(period="1y")
        if hist.empty or len(hist) < 15:
            return out

        closes = hist["Close"].dropna()
        price  = float(closes.iloc[-1])

        # 52-week percentile
        lo, hi = float(closes.min()), float(closes.max())
        if hi > lo:
            out["week52_percentile"] = (price - lo) / (hi - lo)

        # Moving averages
        if len(closes) >= 50:
            out["price_vs_ma50"]  = price / float(closes.iloc[-50:].mean()) - 1
        if len(closes) >= 200:
            out["price_vs_ma200"] = price / float(closes.iloc[-200:].mean()) - 1

        # RSI-14 (simple rolling average, not Wilder EMA — sufficient for scoring)
        if len(closes) >= 15:
            delta    = closes.diff().dropna()
            gains    = delta.clip(lower=0)
            losses   = (-delta).clip(lower=0)
            avg_gain = float(gains.iloc[-14:].mean())
            avg_loss = float(losses.iloc[-14:].mean())
            if avg_loss == 0:
                out["rsi14"] = 100.0
            else:
                rs            = avg_gain / avg_loss
                out["rsi14"] = 100.0 - 100.0 / (1.0 + rs)

    except Exception as exc:
        log.debug("Momentum error: %s", exc)

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Aggregate metric collection
# ─────────────────────────────────────────────────────────────────────────────

def collect_metrics(ticker: str, info: dict, financials, cashflow) -> dict[str, float | None]:
    mom = _momentum_metrics(ticker)
    return {
        # Value
        "dcf_margin_of_safety": _dcf_margin_of_safety(info, financials, cashflow),
        "pe_ratio":             _pe_ratio(info),
        "pb_ratio":             _pb_ratio(info),
        "ev_ebitda":            _ev_ebitda(info),
        # Growth
        "revenue_cagr":         _revenue_cagr(financials),
        "eps_trend":            _eps_trend(financials),
        "fcf_trend":            _fcf_trend(cashflow),
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
    Convert raw metrics → z-scores → pillar scores → composite z.

    Returns
    -------
    composite_z   : float
    pillar_detail : dict   (transparent breakdown for the API response)
    data_coverage : float  (fraction of metrics successfully computed)
    """

    # Step 1 — z-score every available metric
    zs: dict[str, float] = {
        m: z_score(m, v) for m, v in raw.items() if v is not None
    }

    data_coverage = len(zs) / len(raw) if raw else 0.0

    # Step 2 — pillar scores (mean z of available metrics)
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

    # Step 3 — redistribute weight from empty pillars
    active_weight = sum(w for p, w in pillar_weight.items() if pillar_z[p] is not None)
    eff_weight: dict[str, float] = {
        p: (pillar_weight[p] / active_weight if active_weight > 0 and pillar_z[p] is not None else 0.0)
        for p in PILLARS
    }

    # Step 4 — composite z
    composite_z = sum(
        eff_weight[p] * pillar_z[p]
        for p in PILLARS
        if pillar_z[p] is not None
    )

    # Build transparent pillar breakdown for the response
    pillar_detail = {
        p: {
            "z_score":         round(pillar_z[p], 4) if pillar_z[p] is not None else None,
            "effective_weight": round(eff_weight[p], 4),
            "metrics_used":    [m for m in PILLARS[p]["metrics"] if m in zs],
            "metrics_missing": [m for m in PILLARS[p]["metrics"] if m not in zs],
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

    stock      = yf.Ticker(ticker)
    info       = stock.info
    financials = stock.financials
    cashflow   = stock.cashflow

    # Minimal sanity check — yfinance returns an empty dict for unknown tickers
    if not info or not (info.get("currentPrice") or info.get("regularMarketPrice")):
        raise HTTPException(status_code=404, detail=f"No price data found for '{ticker}'")

    raw_metrics             = collect_metrics(ticker, info, financials, cashflow)
    composite_z, pillars, coverage = score(raw_metrics)
    prob                    = logistic(composite_z)

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
        "rawMetrics":     {k: (round(v, 6) if v is not None else None) for k, v in raw_metrics.items()},
    }


@app.get("/health")
def health():
    return {"status": "ok"}