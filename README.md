# Stock Oracle

Full-stack stock valuation tool.
**Frontend:** React + Vite | **Backend:** Python + FastAPI + Anthropic SDK

```
stock-oracle/
├── backend/
│   ├── main.py            ← FastAPI app — edit this to improve the analysis
│   ├── requirements.txt
│   └── .env.example       ← copy to .env and add your API key
└── frontend/
    ├── src/
    │   ├── App.jsx        ← React UI
    │   └── main.jsx
    ├── index.html
    ├── vite.config.js
    └── package.json
```

---

## Setup

### 1. Backend

```bash
cd backend

# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Add your Anthropic API key
cp .env.example .env
# Open .env and paste your key: ANTHROPIC_API_KEY=sk-ant-...

# Start the server
uvicorn main:app --reload --port 8000
```

Backend runs at: http://localhost:8000
API docs at:     http://localhost:8000/docs  (FastAPI auto-generates this)

### 2. Frontend

```bash
cd frontend

npm install
npm run dev
```

Frontend runs at: http://localhost:5173

Vite proxies all `/api/*` requests to FastAPI automatically — no CORS config needed.

---

## How to improve the backend

The main place to work is `backend/main.py`. Some ideas:

### Plug in real financial data
Replace Claude's estimated numbers with live data from a financial API:
- **yfinance** (free): `pip install yfinance` — great for quick prototyping
- **Financial Modeling Prep** (freemium): structured fundamentals
- **Alpha Vantage** (free tier): earnings, balance sheet, cash flow

Example with yfinance:
```python
import yfinance as yf

def get_real_financials(ticker: str) -> dict:
    stock = yf.Ticker(ticker)
    info = stock.info
    return {
        "currentPrice": info.get("currentPrice"),
        "trailingPE": info.get("trailingPE"),
        "freeCashflow": info.get("freeCashflow"),
        "sharesOutstanding": info.get("sharesOutstanding"),
        # ...etc
    }
```

Then pass those real numbers into the Claude prompt so it doesn't have to estimate them.

### Override DCF with Python math
There's a `compute_dcf()` function already in `main.py`. Uncomment the block
at the bottom of the `/analyze` route to cross-check Claude's DCF output with
a Python-computed one using real FCF data.

### Add caching
```python
from functools import lru_cache
# or use Redis for persistence across restarts
```

### Add more routes
```python
@app.post("/compare")        # Compare two tickers side by side
@app.get("/history/{ticker") # Fetch past analyses from a DB
@app.post("/screen")         # Screen a list of tickers
```
