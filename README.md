# Nick's Stock Analyzer

Stock Analyzer is a quantitative stock analysis tool I built to help cut through the noise when researching equities. You type in a ticker, it pulls financial data from Yahoo Finance, and it returns a score from 0 to 100 based purely on the numbers — no news, no sentiment, no narrative. Just math.

The idea came from frustration with the usual approach to stock research, where you end up reading analyst reports full of qualitative language and walking away with no clearer picture than when you started. This tool doesn't tell you a story. It crunches the financials and tells you where a company stands relative to the rest of the market across four dimensions: how cheap it is, how well-run it is, how fast it's growing, and what the price trend is doing.

## How the score works

Every metric gets converted to a percentile score from 0 to 100 based on where it sits in the distribution of large-cap US equities. A 50 means the company is right at market average for that metric. An 80 means it beats 80% of the market. Those percentile scores get averaged into four factor scores, which then get blended into a single composite score using the weights below.

Value carries 30% of the weight and looks at whether the stock is cheap — DCF margin of safety, EV/EBITDA, EV/FCF, P/E, and P/B. Quality carries 25% and measures how well the business actually runs — ROE, ROIC, gross margin, operating margin, and FCF margin. Growth carries 25% and looks at whether the business is getting bigger — revenue CAGR, EPS CAGR, operating income growth, and FCF growth. Momentum carries 20% and reflects what the price action is saying — 12-month and 6-month returns (skipping the most recent month, which is standard in academic momentum models to avoid short-term reversal effects), price vs the 200-day moving average, and a Sharpe-like ratio.

I chose percentile scoring over z-scores because z-scores assume normal distributions and produce unbounded values, both of which cause real problems when one noisy metric can swing your composite wildly. Percentiles are bounded, don't care about the shape of the distribution, and are easier to interpret.

## The DCF

The value factor includes a discounted cash flow model. It uses FCF CAGR as the growth input — not an OLS slope, because CAGR is what actually compounds correctly in a projection. It builds out a five-year FCF forecast, adds a terminal value using the Gordon Growth Model, discounts everything back at a WACC derived from beta and the company's actual debt costs, and bridges from enterprise value to equity value using balance sheet debt and cash pulled from the same reporting period.

The most important thing I added here is a confidence score. DCF outputs can be completely unreliable when FCF is volatile, when you only have two years of history, when the most recent FCF is negative, or when beta is extreme. Rather than letting a garbage DCF dominate the value factor, the model tracks all of these conditions and reduces the DCF's influence proportionally. A low-confidence DCF gets pulled toward neutral (50) instead of dragging the whole score up or down based on noise.

## Guardrails

Three automatic flags can cap factor scores regardless of what the rest of the model says. If revenue growth is negative, the growth factor gets capped at 40. If free cash flow is negative or operating margins are negative, the quality factor gets capped at 45. These show up explicitly in the UI so you always know when they've fired and why your score is lower than you might expect.

## Running it locally

The backend is FastAPI with yfinance. Install dependencies with `pip install fastapi uvicorn yfinance numpy pydantic` and start it with `uvicorn main:app --reload`. It runs on `http://localhost:8000` and you can explore the API at `/docs`.

The frontend is React. Run `npm install` then `npm run dev`. It expects the backend at `http://localhost:8000` — if you change that, update the fetch URL in `App.jsx`.

## Limitations

This is a screening tool, not a prediction engine. It scores stocks against market averages on quantitative metrics available from Yahoo Finance. It knows nothing about upcoming earnings, management changes, industry dynamics, regulatory risk, or anything else that requires actually reading about a company. A high score means the numbers look good relative to history and the market. It doesn't mean the stock will go up.

The DCF is particularly sensitive. Small changes in the FCF growth assumption move intrinsic value dramatically, which is an inherent property of DCF models and not something this implementation fully solves. The confidence score helps, but treat the DCF margin of safety as one signal among several, not a ground truth.

The percentile calibration was built around large-cap US equities. Applying it to small caps, REITs, banks, utilities, or foreign-listed companies will produce less reliable scores because those industries have structurally different financial profiles. A bank with a 15% gross margin isn't a bad business — banks just don't have gross margins in the traditional sense.

Finally, Yahoo Finance data quality is inconsistent. Field names change, values are sometimes stale, and some metrics are unavailable for certain tickers. The `dataCoverage` field in the response tells you what fraction of the 18 metrics actually computed. If you're seeing coverage below 70%, be skeptical of the score.

## License

MIT
