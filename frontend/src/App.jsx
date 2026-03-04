import { useState, useRef } from "react";

// ─── Labels & directions for rawMetrics keys ────────────────────────────────
const METRIC_CONFIG = {
  dcf_margin_of_safety: { label: "DCF Margin of Safety", fmt: "pct", higher_is_better: true  },
  pe_ratio:             { label: "P/E Ratio",             fmt: "x",   higher_is_better: false },
  pb_ratio:             { label: "P/B Ratio",             fmt: "x",   higher_is_better: false },
  ev_ebitda:            { label: "EV / EBITDA",           fmt: "x",   higher_is_better: false },
  revenue_cagr:         { label: "Revenue CAGR",          fmt: "pct", higher_is_better: true  },
  eps_trend:            { label: "EPS Trend (OLS)",       fmt: "dec", higher_is_better: true  },
  fcf_trend:            { label: "FCF Trend (OLS)",       fmt: "dec", higher_is_better: true  },
  week52_percentile:    { label: "52-Week Percentile",    fmt: "pct", higher_is_better: true  },
  price_vs_ma50:        { label: "Price vs MA-50",        fmt: "pct", higher_is_better: true  },
  price_vs_ma200:       { label: "Price vs MA-200",       fmt: "pct", higher_is_better: true  },
  rsi14:                { label: "RSI-14",                fmt: "raw", higher_is_better: null  },
};


const PILLAR_LABELS = { value: "VALUE", growth: "GROWTH", momentum: "MOMENTUM" };

const STEPS = [
  "Fetching financials",
  "Computing DCF model",
  "Scoring value metrics",
  "Scoring growth metrics",
  "Scoring momentum metrics",
  "Calculating buy probability",
];

// ─── Helpers ────────────────────────────────────────────────────────────────
function fmtValue(v, fmt) {
  if (v === null || v === undefined) return "—";
  if (fmt === "pct")  return (v * 100).toFixed(1) + "%";
  if (fmt === "x")    return v.toFixed(1) + "x";
  if (fmt === "dec")  return v.toFixed(3);
  if (fmt === "raw")  return v.toFixed(1);
  return String(v);
}

function metricColor(value, higher_is_better) {
  if (higher_is_better === null) return "#94a3b8"; // neutral (RSI)
  // z-score the "goodness" direction
  return higher_is_better
    ? value >= 0 ? "#4ade80" : "#f87171"
    : value <= 0 ? "#4ade80" : "#f87171";
}

const verdictColor = v =>
  v === "STRONG BUY" ? "#4ade80"
  : v === "BUY"      ? "#86efac"
  : v === "HOLD"     ? "#facc15"
  : v === "SELL"     ? "#fca5a5"
  : "#f87171";

// ─── Gauge ──────────────────────────────────────────────────────────────────
function GaugeArc({ probability }) {
  // Half-circle gauge. 0% → left end, 100% → right end.
  // We draw in a 220×130 viewBox with extra bottom padding so nothing clips.
  const cx = 110, cy = 110, r = 88;
  const clamp = Math.max(0, Math.min(1, probability));
  // angle: 0% = π (left), 100% = 0 (right), so angle = π - clamp*π
  const angle  = Math.PI - clamp * Math.PI;
  const arcX   = cx + r * Math.cos(angle);
  const arcY   = cy + r * Math.sin(angle);
  const needleLen = 70;
  const nx = cx + needleLen * Math.cos(angle);
  const ny = cy + needleLen * Math.sin(angle);

  const color =
    probability >= 0.65 ? "#4ade80"
    : probability >= 0.45 ? "#facc15"
    : "#f87171";

  return (
    <svg viewBox="0 0 220 125" style={{ width: "100%", maxWidth: 280 }}>
      {/* Track */}
      <path
        d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`}
        fill="none" stroke="#1e293b" strokeWidth="14" strokeLinecap="round"
      />
      {/* Fill */}
      <path
        d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${arcX} ${arcY}`}
        fill="none" stroke={color} strokeWidth="14" strokeLinecap="round"
        style={{ filter: `drop-shadow(0 0 8px ${color}88)` }}
      />
      {/* Needle */}
      <line
        x1={cx} y1={cy} x2={nx} y2={ny}
        stroke="#fff" strokeWidth="2.5" strokeLinecap="round"
      />
      <circle cx={cx} cy={cy} r="5" fill="#fff" />
      {/* Labels */}
      <text x={cx - r - 2} y={cy + 18} fill="#475569" fontSize="9" fontFamily="monospace">SELL</text>
      <text x={cx + r - 24} y={cy + 18} fill="#475569" fontSize="9" fontFamily="monospace">BUY</text>
      {/* Percentage */}
      <text
        x={cx} y={cy - 18}
        textAnchor="middle" fill={color}
        fontSize="32" fontFamily="'DM Serif Display', serif"
      >
        {Math.round(probability * 100)}%
      </text>
      <text
        x={cx} y={cy - 4}
        textAnchor="middle" fill="#64748b"
        fontSize="8" fontFamily="monospace" letterSpacing="2"
      >
        BUY PROBABILITY
      </text>
    </svg>
  );
}

// ─── Step list ───────────────────────────────────────────────────────────────
function StepList({ currentStep, done }) {
  return (
    <div>
      {STEPS.map((s, i) => {
        const isDone   = done || i < currentStep;
        const isActive = !done && i === currentStep;
        return (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10,
            opacity: isDone || isActive ? 1 : 0.2, transition: "opacity 0.5s" }}>
            <div style={{
              width: 18, height: 18, borderRadius: "50%", flexShrink: 0,
              background: isDone ? "#4ade80" : isActive ? "transparent" : "#1e293b",
              border: isActive ? "2px solid #38bdf8" : "none",
              display: "flex", alignItems: "center", justifyContent: "center",
              boxShadow: isActive ? "0 0 12px #38bdf866" : "none", transition: "all 0.4s",
            }}>
              {isDone   && <span style={{ fontSize: 9, color: "#0f172a", fontWeight: 700 }}>✓</span>}
              {isActive && <div style={{ width: 5, height: 5, borderRadius: "50%", background: "#38bdf8", animation: "pulse 1s infinite" }} />}
            </div>
            <span style={{
              fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
              color: isDone ? "#4ade80" : isActive ? "#e2e8f0" : "#475569", transition: "color 0.4s",
            }}>{s}</span>
          </div>
        );
      })}
    </div>
  );
}

// ─── Pillar card ─────────────────────────────────────────────────────────────
function PillarCard({ name, data }) {
  const z = data.z_score;
  const color = z === null ? "#94a3b8" : z >= 0.5 ? "#4ade80" : z >= -0.5 ? "#facc15" : "#f87171";
  return (
    <div style={{ background: "#071528", border: "1px solid #1e3a5f", borderRadius: 3, padding: "14px 16px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, letterSpacing: 3, color: "#334155" }}>
          {PILLAR_LABELS[name]}
        </span>
        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, color, fontWeight: 500 }}>
          {z !== null ? (z >= 0 ? "+" : "") + z.toFixed(2) : "—"}
        </span>
      </div>
      <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#334155" }}>
        {data.metrics_used.length}/{data.metrics_used.length + data.metrics_missing.length} metrics ·{" "}
        {Math.round(data.effective_weight * 100)}% weight
      </div>
    </div>
  );
}

// ─── Metric row ──────────────────────────────────────────────────────────────
function MetricRow({ metricKey, value }) {
  const cfg = METRIC_CONFIG[metricKey];
  if (!cfg || value === null || value === undefined) return null;

  // For direction-based coloring we need the raw value and the "goodness" direction
  // For ratio metrics (lower_is_better = false), negative rawMetric means cheap → green
  // We pass the raw value; color logic lives in metricColor
  const color = metricColor(
    cfg.higher_is_better === false ? -value : value,
    cfg.higher_is_better === null ? null : true
  );

  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "7px 0", borderBottom: "1px solid #0d1829",
    }}>
      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: "#64748b" }}>
        {cfg.label}
      </span>
      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, color, fontWeight: 500 }}>
        {fmtValue(value, cfg.fmt)}
      </span>
    </div>
  );
}

// ─── App ─────────────────────────────────────────────────────────────────────
export default function App() {
  const [ticker,  setTicker]  = useState("");
  const [loading, setLoading] = useState(false);
  const [step,    setStep]    = useState(-1);
  const [result,  setResult]  = useState(null);
  const [error,   setError]   = useState(null);
  const timerRef = useRef(null);

  const analyze = async () => {
    const t = ticker.trim().toUpperCase();
    if (!t || loading) return;

    setLoading(true);
    setResult(null);
    setError(null);
    setStep(0);

    let s = 0;
    timerRef.current = setInterval(() => {
      s = Math.min(s + 1, STEPS.length - 1);
      setStep(s);
    }, 900);

    try {
      const res = await fetch("http://localhost:8000/analyze", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ ticker: t }),
      });

      clearInterval(timerRef.current);

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err?.detail || `Server error ${res.status}`);
      }

      const data = await res.json();
      setStep(STEPS.length);
      setResult(data);
    } catch (e) {
      clearInterval(timerRef.current);
      setError(e.message || "Analysis failed. Is the backend running on localhost:8000?");
      setStep(-1);
    } finally {
      setLoading(false);
    }
  };

  // Split rawMetrics by pillar for display
  const metricsByPillar = result ? {
    value:    ["dcf_margin_of_safety", "pe_ratio", "pb_ratio", "ev_ebitda"],
    growth:   ["revenue_cagr", "eps_trend", "fcf_trend"],
    momentum: ["week52_percentile", "price_vs_ma50", "price_vs_ma200", "rsi14"],
  } : null;

  return (
    <div style={{ minHeight: "100vh", background: "#060d1a", color: "#e2e8f0", padding: "0 16px 80px" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=JetBrains+Mono:wght@300;400;500&display=swap');
        @keyframes pulse   { 0%,100%{opacity:1} 50%{opacity:0.3} }
        @keyframes fadeIn  { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }
        * { box-sizing: border-box }
        input::placeholder { color: #334155 }
        input:focus { outline: none }
      `}</style>

      {/* Header */}
      <div style={{ textAlign: "center", padding: "52px 0 32px" }}>
        <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, letterSpacing: 6, color: "#38bdf8", marginBottom: 14 }}>
          EQUITY INTELLIGENCE
        </div>
        <h1 style={{ fontFamily: "'DM Serif Display',serif", fontSize: "clamp(36px,7vw,66px)", margin: 0, lineHeight: 1, fontWeight: 400 }}>
          Stock <em style={{ color: "#38bdf8" }}>Oracle</em>
        </h1>
        <p style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: "#334155", marginTop: 10, letterSpacing: 1 }}>
          DCF · Value · Growth · Momentum · Pure Quantitative
        </p>
      </div>

      {/* Input */}
      <div style={{ maxWidth: 500, margin: "0 auto 32px" }}>
        <div style={{ display: "flex", background: "#0d1829", border: "1px solid #1e3a5f", borderRadius: 3, overflow: "hidden" }}>
          <input
            value={ticker}
            onChange={e => setTicker(e.target.value.toUpperCase().replace(/[^A-Z.]/g, ""))}
            onKeyDown={e => e.key === "Enter" && analyze()}
            placeholder="Enter ticker — AAPL, NVDA, MSFT…"
            disabled={loading}
            maxLength={8}
            style={{
              flex: 1, padding: "15px 18px", background: "transparent", border: "none",
              color: "#e2e8f0", fontSize: 15, fontFamily: "'JetBrains Mono',monospace", letterSpacing: 2,
            }}
          />
          <button
            onClick={analyze}
            disabled={loading || !ticker.trim()}
            style={{
              padding: "0 24px",
              background: loading || !ticker.trim() ? "#0d1829" : "#38bdf8",
              border: "none", cursor: loading ? "wait" : "pointer",
              color: loading || !ticker.trim() ? "#38bdf8" : "#060d1a",
              fontFamily: "'JetBrains Mono',monospace", fontSize: 11, letterSpacing: 2, fontWeight: 500,
              transition: "all 0.2s", flexShrink: 0, opacity: !ticker.trim() && !loading ? 0.4 : 1,
            }}
          >
            {loading ? "ANALYZING…" : "ANALYZE →"}
          </button>
        </div>
      </div>

      {/* Loading */}
      {loading && (
        <div style={{ maxWidth: 380, margin: "0 auto", background: "#0d1829", border: "1px solid #1e3a5f", borderRadius: 3, padding: "22px 26px", animation: "fadeIn 0.3s ease" }}>
          <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, letterSpacing: 4, color: "#38bdf8", marginBottom: 14 }}>RUNNING ANALYSIS</div>
          <StepList currentStep={step} done={false} />
        </div>
      )}

      {/* Error */}
      {error && !loading && (
        <div style={{ maxWidth: 500, margin: "0 auto", background: "#160808", border: "1px solid #7f1d1d", borderRadius: 3, padding: "14px 18px", fontFamily: "'JetBrains Mono',monospace", fontSize: 12, color: "#fca5a5", animation: "fadeIn 0.3s ease" }}>
          ✕ {error}
        </div>
      )}

      {/* Results */}
      {result && !loading && (
        <div style={{ maxWidth: 960, margin: "0 auto", animation: "fadeIn 0.5s ease" }}>

          {/* Header bar */}
          <div style={{ background: "#0d1829", border: "1px solid #1e3a5f", borderRadius: 3, padding: "22px 26px", marginBottom: 12, display: "flex", flexWrap: "wrap", gap: 24, alignItems: "center" }}>
            <div style={{ flex: "1 1 200px" }}>
              <div style={{ fontFamily: "'DM Serif Display',serif", fontSize: "clamp(20px,3.5vw,32px)", lineHeight: 1.1 }}>
                {result.companyName}
              </div>
              <div style={{ fontFamily: "'JetBrains Mono',monospace", color: "#38bdf8", fontSize: 13, marginTop: 4, letterSpacing: 2 }}>
                {result.ticker}
              </div>
            </div>

            <div style={{ textAlign: "center" }}>
              <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: "#334155", letterSpacing: 2 }}>PRICE</div>
              <div style={{ fontFamily: "'DM Serif Display',serif", fontSize: 28 }}>
                ${typeof result.currentPrice === "number" ? result.currentPrice.toLocaleString() : "—"}
              </div>
            </div>

            <div style={{ textAlign: "center" }}>
              <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: "#334155", letterSpacing: 2, marginBottom: 6 }}>VERDICT</div>
              <div style={{
                fontFamily: "'JetBrains Mono',monospace", fontSize: 12, letterSpacing: 2,
                color: verdictColor(result.verdict),
                border: `1px solid ${verdictColor(result.verdict)}44`,
                padding: "7px 14px", borderRadius: 2,
              }}>
                {result.verdict}
              </div>
            </div>

            <div style={{ textAlign: "center" }}>
              <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: "#334155", letterSpacing: 2 }}>DATA COVERAGE</div>
              <div style={{ fontFamily: "'DM Serif Display',serif", fontSize: 28, color: result.dataCoverage >= 0.8 ? "#4ade80" : "#facc15" }}>
                {Math.round(result.dataCoverage * 100)}%
              </div>
            </div>
          </div>

          {/* Main grid */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>

            {/* Gauge + Pillars */}
            <div style={{ background: "#0d1829", border: "1px solid #1e3a5f", borderRadius: 3, padding: "22px 26px", display: "flex", flexDirection: "column", alignItems: "center" }}>
              <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: "#334155", letterSpacing: 3, marginBottom: 12, alignSelf: "flex-start" }}>
                CONFIDENCE GAUGE
              </div>
              <GaugeArc probability={result.buyProbability || 0} />

              <div style={{ marginTop: 20, width: "100%" }}>
                <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: "#334155", letterSpacing: 3, marginBottom: 10 }}>
                  PILLAR BREAKDOWN
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                  {result.pillars && Object.entries(result.pillars).map(([name, data]) => (
                    <PillarCard key={name} name={name} data={data} />
                  ))}
                </div>
              </div>

              <div style={{ marginTop: 16, width: "100%" }}>
                <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: "#334155", letterSpacing: 3, marginBottom: 8 }}>
                  COMPOSITE Z-SCORE
                </div>
                <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 20,
                  color: result.compositeZ >= 0 ? "#4ade80" : "#f87171" }}>
                  {result.compositeZ >= 0 ? "+" : ""}{result.compositeZ?.toFixed(3)}
                </div>
              </div>
            </div>

            {/* Metrics by pillar */}
            <div style={{ background: "#0d1829", border: "1px solid #1e3a5f", borderRadius: 3, padding: "22px 26px" }}>
              <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: "#334155", letterSpacing: 3, marginBottom: 12 }}>
                FACTOR METRICS
              </div>

              {metricsByPillar && Object.entries(metricsByPillar).map(([pillar, keys]) => (
                <div key={pillar} style={{ marginBottom: 18 }}>
                  <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, letterSpacing: 3,
                    color: "#1e3a5f", marginBottom: 6 }}>
                    — {PILLAR_LABELS[pillar]}
                  </div>
                  {keys.map(k => (
                    <MetricRow key={k} metricKey={k} value={result.rawMetrics?.[k]} />
                  ))}
                </div>
              ))}
            </div>

            {/* Analysis steps (full width) */}
            <div style={{ background: "#0d1829", border: "1px solid #1e3a5f", borderRadius: 3, padding: "22px 26px", gridColumn: "1 / -1" }}>
              <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: "#334155", letterSpacing: 3, marginBottom: 10 }}>
                ANALYSIS STEPS
              </div>
              <StepList currentStep={-1} done={true} />
            </div>
          </div>

          <div style={{ textAlign: "center", marginTop: 20, fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: "#1e293b", letterSpacing: 1 }}>
            NOT FINANCIAL ADVICE — EDUCATIONAL PURPOSES ONLY
          </div>
        </div>
      )}
    </div>
  );
}