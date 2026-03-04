import { useState, useRef } from "react";

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

// ─── Helpers ─────────────────────────────────────────────────────────────────
function fmtValue(v, fmt) {
  if (v === null || v === undefined) return "—";
  if (fmt === "pct") return (v * 100).toFixed(1) + "%";
  if (fmt === "x")   return v.toFixed(1) + "x";
  if (fmt === "dec") return v.toFixed(3);
  if (fmt === "raw") return v.toFixed(1);
  return String(v);
}

function metricColor(value, higher_is_better) {
  if (higher_is_better === null) return "#94a3b8";
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

const probColor = p =>
  p >= 0.65 ? "#4ade80" : p >= 0.45 ? "#facc15" : "#f87171";

// ─── Reusable section label with readable color ───────────────────────────────
function SectionLabel({ children, style = {} }) {
  return (
    <div style={{
      fontFamily: "'JetBrains Mono', monospace",
      fontSize: 10,
      letterSpacing: 3,
      color: "#7dd3fc",   // sky-300 — clearly readable on dark bg
      marginBottom: 12,
      ...style,
    }}>
      {children}
    </div>
  );
}

// ─── Buy probability: big % + progress bar (replaces gauge) ──────────────────
function ProbabilityDisplay({ probability }) {
  const pct   = Math.round((probability || 0) * 100);
  const color = probColor(probability || 0);

  return (
    <div style={{ width: "100%", padding: "4px 0 20px" }}>
      {/* Large percentage */}
      <div style={{
        fontFamily: "'DM Serif Display', serif",
        fontSize: 80,
        lineHeight: 1,
        color,
        textAlign: "center",
        filter: `drop-shadow(0 0 20px ${color}55)`,
        marginBottom: 6,
      }}>
        {pct}%
      </div>

      {/* Label */}
      <div style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 10,
        letterSpacing: 4,
        color: "#94a3b8",
        textAlign: "center",
        marginBottom: 20,
      }}>
        BUY PROBABILITY
      </div>

      {/* Progress bar track */}
      <div style={{
        width: "100%",
        height: 10,
        background: "#0f2137",
        borderRadius: 99,
        overflow: "hidden",
        border: "1px solid #1e3a5f",
      }}>
        {/* Filled portion */}
        <div style={{
          width: `${pct}%`,
          height: "100%",
          background: `linear-gradient(90deg, ${color}88, ${color})`,
          borderRadius: 99,
          boxShadow: `0 0 10px ${color}66`,
          transition: "width 0.6s cubic-bezier(0.4,0,0.2,1)",
        }} />
      </div>

      {/* SELL / BUY end labels */}
      <div style={{
        display: "flex",
        justifyContent: "space-between",
        marginTop: 6,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 9,
        color: "#64748b",
        letterSpacing: 2,
      }}>
        <span>SELL</span>
        <span>BUY</span>
      </div>
    </div>
  );
}

// ─── Step list ────────────────────────────────────────────────────────────────
function StepList({ currentStep, done }) {
  return (
    <div>
      {STEPS.map((s, i) => {
        const isDone   = done || i < currentStep;
        const isActive = !done && i === currentStep;
        return (
          <div key={i} style={{
            display: "flex", alignItems: "center", gap: 10, marginBottom: 10,
            opacity: isDone || isActive ? 1 : 0.2, transition: "opacity 0.5s",
          }}>
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
              color: isDone ? "#4ade80" : isActive ? "#e2e8f0" : "#475569",
              transition: "color 0.4s",
            }}>{s}</span>
          </div>
        );
      })}
    </div>
  );
}

// ─── Pillar card ──────────────────────────────────────────────────────────────
function PillarCard({ name, data }) {
  const z = data.z_score;
  const color = z === null ? "#94a3b8" : z >= 0.5 ? "#4ade80" : z >= -0.5 ? "#facc15" : "#f87171";
  return (
    <div style={{
      background: "#071528", border: "1px solid #1e3a5f",
      borderRadius: 3, padding: "14px 16px",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
        <span style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 10, letterSpacing: 3,
          color: "#94a3b8",   // was #334155 — now readable
        }}>
          {PILLAR_LABELS[name]}
        </span>
        <span style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 12, color, fontWeight: 500,
        }}>
          {z !== null ? (z >= 0 ? "+" : "") + z.toFixed(2) : "—"}
        </span>
      </div>
      <div style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 9,
        color: "#64748b",   // was #334155 — now readable
      }}>
        {data.metrics_used.length}/{data.metrics_used.length + data.metrics_missing.length} metrics ·{" "}
        {Math.round(data.effective_weight * 100)}% weight
      </div>
    </div>
  );
}

// ─── Metric row ───────────────────────────────────────────────────────────────
function MetricRow({ metricKey, value }) {
  const cfg = METRIC_CONFIG[metricKey];
  if (!cfg || value === null || value === undefined) return null;

  const color = metricColor(
    cfg.higher_is_better === false ? -value : value,
    cfg.higher_is_better === null ? null : true
  );

  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "7px 0", borderBottom: "1px solid #0d1829",
    }}>
      <span style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11,
        color: "#94a3b8",   // was #64748b — bumped up for readability
      }}>
        {cfg.label}
      </span>
      <span style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 12, color, fontWeight: 500,
      }}>
        {fmtValue(value, cfg.fmt)}
      </span>
    </div>
  );
}

// ─── App ──────────────────────────────────────────────────────────────────────
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

  const metricsByPillar = result ? {
    value:    ["dcf_margin_of_safety", "pe_ratio", "pb_ratio", "ev_ebitda"],
    growth:   ["revenue_cagr", "eps_trend", "fcf_trend"],
    momentum: ["week52_percentile", "price_vs_ma50", "price_vs_ma200", "rsi14"],
  } : null;

  return (
    <div style={{ minHeight: "100vh", background: "#060d1a", color: "#e2e8f0", padding: "0 16px 80px" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=JetBrains+Mono:wght@300;400;500&display=swap');
        @keyframes pulse  { 0%,100%{opacity:1} 50%{opacity:0.3} }
        @keyframes fadeIn { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }
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
        <p style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: "#64748b", marginTop: 10, letterSpacing: 1 }}>
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
              transition: "all 0.2s", flexShrink: 0,
              opacity: !ticker.trim() && !loading ? 0.4 : 1,
            }}
          >
            {loading ? "ANALYZING…" : "ANALYZE →"}
          </button>
        </div>
      </div>

      {/* Loading */}
      {loading && (
        <div style={{ maxWidth: 380, margin: "0 auto", background: "#0d1829", border: "1px solid #1e3a5f", borderRadius: 3, padding: "22px 26px", animation: "fadeIn 0.3s ease" }}>
          <SectionLabel>RUNNING ANALYSIS</SectionLabel>
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
              <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: "#7dd3fc", letterSpacing: 2, marginBottom: 4 }}>PRICE</div>
              <div style={{ fontFamily: "'DM Serif Display',serif", fontSize: 28 }}>
                ${typeof result.currentPrice === "number" ? result.currentPrice.toLocaleString() : "—"}
              </div>
            </div>

            <div style={{ textAlign: "center" }}>
              <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: "#7dd3fc", letterSpacing: 2, marginBottom: 6 }}>VERDICT</div>
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
              <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: "#7dd3fc", letterSpacing: 2, marginBottom: 4 }}>DATA COVERAGE</div>
              <div style={{ fontFamily: "'DM Serif Display',serif", fontSize: 28, color: result.dataCoverage >= 0.8 ? "#4ade80" : "#facc15" }}>
                {Math.round(result.dataCoverage * 100)}%
              </div>
            </div>
          </div>

          {/* Main grid */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>

            {/* Probability + Pillars */}
            <div style={{ background: "#0d1829", border: "1px solid #1e3a5f", borderRadius: 3, padding: "22px 26px", display: "flex", flexDirection: "column", alignItems: "center" }}>
              <SectionLabel style={{ alignSelf: "flex-start" }}>BUY PROBABILITY</SectionLabel>
              <ProbabilityDisplay probability={result.buyProbability} />

              <div style={{ marginTop: 8, width: "100%" }}>
                <SectionLabel>PILLAR BREAKDOWN</SectionLabel>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                  {result.pillars && Object.entries(result.pillars).map(([name, data]) => (
                    <PillarCard key={name} name={name} data={data} />
                  ))}
                </div>
              </div>

              <div style={{ marginTop: 16, width: "100%" }}>
                <SectionLabel>COMPOSITE Z-SCORE</SectionLabel>
                <div style={{
                  fontFamily: "'JetBrains Mono', monospace", fontSize: 20,
                  color: result.compositeZ >= 0 ? "#4ade80" : "#f87171",
                }}>
                  {result.compositeZ >= 0 ? "+" : ""}{result.compositeZ?.toFixed(3)}
                </div>
              </div>
            </div>

            {/* Factor metrics */}
            <div style={{ background: "#0d1829", border: "1px solid #1e3a5f", borderRadius: 3, padding: "22px 26px" }}>
              <SectionLabel>FACTOR METRICS</SectionLabel>

              {metricsByPillar && Object.entries(metricsByPillar).map(([pillar, keys]) => (
                <div key={pillar} style={{ marginBottom: 18 }}>
                  {/* Pillar sub-header */}
                  <div style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 9, letterSpacing: 3,
                    color: "#475569",   // was #1e3a5f — now visible
                    marginBottom: 6,
                  }}>
                    — {PILLAR_LABELS[pillar]}
                  </div>
                  {keys.map(k => (
                    <MetricRow key={k} metricKey={k} value={result.rawMetrics?.[k]} />
                  ))}
                </div>
              ))}
            </div>

            {/* Analysis steps — full width */}
            <div style={{ background: "#0d1829", border: "1px solid #1e3a5f", borderRadius: 3, padding: "22px 26px", gridColumn: "1 / -1" }}>
              <SectionLabel>ANALYSIS STEPS</SectionLabel>
              <StepList currentStep={-1} done={true} />
            </div>
          </div>

          <div style={{ textAlign: "center", marginTop: 20, fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: "#334155", letterSpacing: 1 }}>
            NOT FINANCIAL ADVICE — EDUCATIONAL PURPOSES ONLY
          </div>
        </div>
      )}
    </div>
  );
}
