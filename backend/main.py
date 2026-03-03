"""
Stock Oracle — FastAPI Backend
--------------------------------
- Uses Yahoo Finance (yfinance) for free financial data
- Computes DCF, buy probability, and upside %
- Generates structured qualitative reasoning
- No API keys required

Run with:
uvicorn main:app --reload --port 8000
"""

import math
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── App Setup ───────────────────────────────────────────────

app = FastAPI(title="Stock Oracle API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Request Model ───────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    ticker: str


# ── Utility Functions ───────────────────────────────────────

def safe(value, fallback=None):
    if value is None:
        return fallback
    try:
        if not math.isfinite(float(value)):
            return fallback
    except (TypeError, ValueError):
        return fallback
    return value


def pct(value):
    v = safe(value)
    return round(v * 100, 1) if v is not None else None


def cagr(start, end, years):
    if not start or not end or years <= 0 or start <= 0:
        return None
    return round(((end / start) ** (1 / years) - 1) * 100, 1)


# ── DCF Engine ──────────────────────────────────────────────

def compute_dcf(
    fcf_per_share: float,
    fcf_cagr_pct: float,
    wacc_pct: float = 9.0,
    terminal_growth_pct: float = 3.0,
    years: int = 5,
):
    wacc = wacc_pct / 100
    g = terminal_growth_pct / 100
    fcf_cagr = fcf_cagr_pct / 100

    pv = 0.0
    fcf = fcf_per_share

    for t in range(1, years + 1):
        fcf *= (1 + fcf_cagr)
        pv += fcf / (1 + wacc) ** t

    if wacc <= g:
        terminal_value = 0
    else:
        terminal_value = (fcf * (1 + g)) / (wacc - g)

    pv += terminal_value / (1 + wacc) ** years

    return round(pv, 2)


# ── Scoring System ──────────────────────────────────────────

def score_buy_probability(metrics: dict, dcf_upside: float) -> float:
    score = 0.5

    # DCF weight
    if dcf_upside > 20:
        score += 0.25
    elif dcf_upside > 5:
        score += 0.12
    elif dcf_upside < -20:
        score -= 0.25
    elif dcf_upside < -5:
        score -= 0.12

    # Growth
    if metrics.get("revenueCAGR") and metrics["revenueCAGR"] > 10:
        score += 0.05

    if metrics.get("epsGrowth") and metrics["epsGrowth"] > 10:
        score += 0.05

    # Profitability
    if metrics.get("roe") and metrics["roe"] > 15:
        score += 0.05

    # Valuation sanity
    if metrics.get("pe") and metrics["pe"] < 25:
        score += 0.05

    return round(max(0.0, min(1.0, score)), 2)


def verdict_from_score(probability: float, dcf_upside: float) -> str:
    if probability >= 0.70 and dcf_upside > 10:
        return "STRONG BUY"
    elif probability >= 0.55:
        return "MODERATE BUY"
    elif probability >= 0.42:
        return "HOLD"
    elif probability >= 0.30:
        return "MODERATE SELL"
    else:
        return "STRONG SELL"


# ── Qualitative Generator ───────────────────────────────────

def generate_qualitative(dcf_upside, pe):
    if dcf_upside > 15:
        mispricing = "DCF suggests the stock may be undervalued relative to intrinsic cash flow projections."
    elif dcf_upside < -15:
        mispricing = "DCF suggests the stock may be overvalued relative to projected cash flows."
    else:
        mispricing = "DCF indicates the stock is trading near intrinsic value."

    if pe and pe > 30:
        valuation = "The stock trades at a relatively high earnings multiple."
    elif pe and pe < 15:
        valuation = "The stock trades at a relatively low earnings multiple."
    else:
        valuation = "Valuation appears broadly in line with typical market ranges."

    return {
        "mispricingAnalysis": mispricing,
        "valuationCommentary": valuation,
        "keyRisks": [
            "Macroeconomic slowdown",
            "Margin compression",
            "Competitive pressure",
        ],
        "keyOpportunities": [
            "Revenue expansion",
            "Margin improvement",
            "Capital allocation efficiency",
        ],
    }


# ── Main Route ───────────────────────────────────────────────

@app.post("/analyze")
async def analyze(req: AnalyzeRequest):

    ticker = req.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")

    try:
        stock = yf.Ticker(ticker)

        info = stock.info
        financials = stock.financials
        cashflow = stock.cashflow

        if not info:
            raise HTTPException(status_code=404, detail="Ticker not found")

        current_price = safe(info.get("currentPrice"))
        shares_outstanding = safe(info.get("sharesOutstanding"))
        company_name = info.get("longName", ticker)
        sector = info.get("sector", "Unknown")

        # Valuation
        pe = safe(info.get("trailingPE"))
        ps = safe(info.get("priceToSalesTrailing12Months"))
        peg = safe(info.get("pegRatio"))
        ev = safe(info.get("enterpriseValue"))
        ebitda = safe(info.get("ebitda"))
        ev_ebitda = round(ev / ebitda, 2) if ev and ebitda else None

        # Margins
        gross_margin = pct(info.get("grossMargins"))
        operating_margin = pct(info.get("operatingMargins"))
        net_margin = pct(info.get("profitMargins"))
        roe = pct(info.get("returnOnEquity"))

        # Revenue CAGR
        revenue_cagr = None
        try:
            revenues = financials.loc["Total Revenue"].dropna()
            if len(revenues) >= 4:
                revenue_cagr = cagr(revenues.iloc[3], revenues.iloc[0], 3)
        except:
            pass

        # EPS Growth
        eps_growth = None
        try:
            eps = financials.loc["Diluted EPS"].dropna()
            if len(eps) >= 2:
                eps_growth = round(
                    (eps.iloc[0] - eps.iloc[1]) / abs(eps.iloc[1]) * 100,
                    1,
                )
        except:
            pass

        # Free Cash Flow
        fcf_now = None
        fcf_growth = None
        fcf_per_share = None

        try:
            fcf_series = cashflow.loc["Free Cash Flow"].dropna()
            if len(fcf_series) >= 4:
                fcf_now = fcf_series.iloc[0]
                fcf_then = fcf_series.iloc[3]
                fcf_growth = cagr(fcf_then, fcf_now, 3)

            if fcf_now and shares_outstanding:
                fcf_per_share = fcf_now / shares_outstanding
        except:
            pass

        # DCF
        if fcf_per_share and fcf_growth:
            intrinsic_value = compute_dcf(
                fcf_per_share=fcf_per_share,
                fcf_cagr_pct=min(max(fcf_growth, -5), 20),
            )
            dcf_upside = round((intrinsic_value - current_price) / current_price * 100, 1)
        else:
            intrinsic_value = None
            dcf_upside = 0.0

        # Score
        flat_metrics = {
            "pe": pe,
            "revenueCAGR": revenue_cagr,
            "epsGrowth": eps_growth,
            "roe": roe,
        }

        buy_probability = score_buy_probability(flat_metrics, dcf_upside)
        verdict = verdict_from_score(buy_probability, dcf_upside)

        qualitative = generate_qualitative(dcf_upside, pe)

        return {
            "ticker": ticker,
            "companyName": company_name,
            "currentPrice": current_price,
            "sector": sector,
            "intrinsicValue": intrinsic_value,
            "upsidePercent": dcf_upside,
            "buyProbability": buy_probability,
            "verdict": verdict,
            "metrics": {
                "pe": {
                    "value": pe,
                    "peerAvg": None,
                    "direction": "neutral",
                },
                "ps": {
                    "value": ps,
                    "peerAvg": None,
                    "direction": "neutral",
                },
                "peg": {
                    "value": peg,
                    "peerAvg": None,
                    "direction": "neutral",
                },
                "evEbitda": {
                    "value": ev_ebitda,
                    "peerAvg": None,
                    "direction": "neutral",
                },
                "revenueCAGR": {
                    "value": revenue_cagr,
                    "peerAvg": None,
                    "direction": "neutral",
                },
                "epsGrowth": {
                "value": eps_growth,
                "peerAvg": None,
                "direction": "neutral",
                },
                "grossMargin": {
                    "value": gross_margin,
                    "peerAvg": None,
                    "direction": "neutral",
                },
                "operatingMargin": {
                    "value": operating_margin,
                    "peerAvg": None,
                    "direction": "neutral",
                },
                "netMargin": {
                    "value": net_margin,
                    "peerAvg": None,
                    "direction": "neutral",
                },
                "roe": {
                    "value": roe,
                    "peerAvg": None,
                    "direction": "neutral",
                },
                "fcfGrowth": {
                    "value": fcf_growth,
                    "peerAvg": None,
                    "direction": "neutral",
                },
            },
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Health Check ─────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}