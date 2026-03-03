import { useState, useRef } from "react";

const METRICS_LABELS = {
  pe: "P/E Ratio",
  evEbitda: "EV/EBITDA",
  ps: "P/S Ratio",
  peg: "PEG Ratio",
  dcf: "DCF Upside %",
  revenueCAGR: "Revenue 3yr CAGR",
  epsGrowth: "EPS Growth",
  fcfGrowth: "FCF Growth",
  grossMargin: "Gross Margin",
  operatingMargin: "Operating Margin",
  netMargin: "Net Margin",
  roe: "ROE",
  roic: "ROIC",
};

const STEPS = [
  "Identifying peer group",
  "Normalizing financials",
  "Comparing multiples",
  "Adjusting for growth",
  "Evaluating premium/discount",
  "Determining mispricing",
];

function GaugeArc({ probability }) {
  const r = 80, cx = 100, cy = 100;
  const angle = Math.PI - probability * Math.PI;
  const x = cx + r * Math.cos(angle);
  const y = cy + r * Math.sin(angle);
  const color = probability >= 0.65 ? "#4ade80" : probability >= 0.45 ? "#facc15" : "#f87171";
  return (
    <svg viewBox="0 0 200 115" style={{ width: "100%", maxWidth: 260 }}>
      <path d={`M ${cx-r} ${cy} A ${r} ${r} 0 0 1 ${cx+r} ${cy}`} fill="none" stroke="#1e293b" strokeWidth="14" strokeLinecap="round"/>
      <path d={`M ${cx-r} ${cy} A ${r} ${r} 0 0 1 ${x} ${y}`} fill="none" stroke={color} strokeWidth="14" strokeLinecap="round" style={{filter:`drop-shadow(0 0 8px ${color}88)`}}/>
      <line x1={cx} y1={cy} x2={cx+66*Math.cos(angle)} y2={cy+66*Math.sin(angle)} stroke="#fff" strokeWidth="2.5" strokeLinecap="round"/>
      <circle cx={cx} cy={cy} r="5" fill="#fff"/>
      <text x={cx} y={cy-16} textAnchor="middle" fill={color} fontSize="30" fontFamily="'DM Serif Display',serif">{Math.round(probability*100)}%</text>
      <text x={cx} y={cy-2} textAnchor="middle" fill="#64748b" fontSize="9" fontFamily="monospace" letterSpacing="2">BUY PROBABILITY</text>
      <text x={cx-r+2} y={cy+16} fill="#334155" fontSize="9" fontFamily="monospace">SELL</text>
      <text x={cx+r-22} y={cy+16} fill="#334155" fontSize="9" fontFamily="monospace">BUY</text>
    </svg>
  );
}

function StepList({ currentStep, done }) {
  return (
    <div>
      {STEPS.map((s, i) => {
        const isDone = done || i < currentStep;
        const isActive = !done && i === currentStep;
        return (
          <div key={i} style={{display:"flex",alignItems:"center",gap:10,marginBottom:10,opacity:isDone||isActive?1:0.2,transition:"opacity 0.5s"}}>
            <div style={{width:18,height:18,borderRadius:"50%",flexShrink:0,background:isDone?"#4ade80":isActive?"transparent":"#1e293b",border:isActive?"2px solid #38bdf8":"none",display:"flex",alignItems:"center",justifyContent:"center",boxShadow:isActive?"0 0 12px #38bdf866":"none",transition:"all 0.4s"}}>
              {isDone && <span style={{fontSize:9,color:"#0f172a",fontWeight:700}}>✓</span>}
              {isActive && <div style={{width:5,height:5,borderRadius:"50%",background:"#38bdf8",animation:"pulse 1s infinite"}}/>}
            </div>
            <span style={{fontFamily:"'JetBrains Mono',monospace",fontSize:11,color:isDone?"#4ade80":isActive?"#e2e8f0":"#475569",transition:"color 0.4s"}}>{s}</span>
          </div>
        );
      })}
    </div>
  );
}

function MetricRow({ label, value, peerAvg, direction }) {
  if (value === null || value === undefined) return null;
  const isGood = peerAvg !== null ? (direction === "lower_better" ? value <= peerAvg : value >= peerAvg) : null;
  const color = isGood === null ? "#94a3b8" : isGood ? "#4ade80" : "#f87171";
  const needsPct = label.includes("Margin")||label.includes("CAGR")||label.includes("Growth")||label.includes("ROE")||label.includes("ROIC")||label.includes("Upside");
  const fmt = v => typeof v === "number" ? (Math.abs(v)>=100?v.toFixed(0):v.toFixed(1)) + (needsPct?"%":"x") : String(v);
  return (
    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"7px 0",borderBottom:"1px solid #0d1829"}}>
      <span style={{fontFamily:"'JetBrains Mono',monospace",fontSize:11,color:"#64748b"}}>{label}</span>
      <div style={{display:"flex",gap:12,alignItems:"center"}}>
        {peerAvg !== null && <span style={{fontFamily:"'JetBrains Mono',monospace",fontSize:10,color:"#334155"}}>{fmt(peerAvg)} peer</span>}
        <span style={{fontFamily:"'JetBrains Mono',monospace",fontSize:12,color,fontWeight:500,minWidth:52,textAlign:"right"}}>{fmt(value)}</span>
      </div>
    </div>
  );
}

const vColor = v => !v?"#94a3b8":v==="STRONG BUY"?"#4ade80":v==="MODERATE BUY"?"#86efac":v==="HOLD"?"#facc15":v==="MODERATE SELL"?"#fca5a5":"#f87171";

export default function App() {
  const [ticker, setTicker] = useState("");
  const [loading, setLoading] = useState(false);
  const [step, setStep] = useState(-1);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
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
    }, 1100);

    try {
      // ── Calls your FastAPI backend, not Anthropic directly ──
      const res = await fetch("http://localhost:8000/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: t }),
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
      setError(e.message || "Analysis failed. Is the backend running?");
      setStep(-1);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{minHeight:"100vh",background:"#060d1a",color:"#e2e8f0",padding:"0 16px 80px"}}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=JetBrains+Mono:wght@300;400;500&display=swap');
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}}
        @keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
        *{box-sizing:border-box}
        input::placeholder{color:#334155}
        input:focus{outline:none}
      `}</style>

      <div style={{textAlign:"center",padding:"52px 0 32px"}}>
        <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:10,letterSpacing:6,color:"#38bdf8",marginBottom:14}}>EQUITY INTELLIGENCE</div>
        <h1 style={{fontFamily:"'DM Serif Display',serif",fontSize:"clamp(36px,7vw,66px)",margin:0,lineHeight:1,fontWeight:400}}>
          Stock <em style={{color:"#38bdf8"}}>Oracle</em>
        </h1>
        <p style={{fontFamily:"'JetBrains Mono',monospace",fontSize:11,color:"#334155",marginTop:10,letterSpacing:1}}>
          DCF · Peer Multiples · Growth-Adjusted · Mispricing Detection
        </p>
      </div>

      <div style={{maxWidth:500,margin:"0 auto 32px"}}>
        <div style={{display:"flex",background:"#0d1829",border:"1px solid #1e3a5f",borderRadius:3,overflow:"hidden"}}>
          <input
            value={ticker}
            onChange={e=>setTicker(e.target.value.toUpperCase().replace(/[^A-Z.]/g,""))}
            onKeyDown={e=>e.key==="Enter"&&analyze()}
            placeholder="Enter ticker — AAPL, NVDA, MSFT…"
            disabled={loading}
            maxLength={8}
            style={{flex:1,padding:"15px 18px",background:"transparent",border:"none",color:"#e2e8f0",fontSize:15,fontFamily:"'JetBrains Mono',monospace",letterSpacing:2}}
          />
          <button
            onClick={analyze}
            disabled={loading||!ticker.trim()}
            style={{padding:"0 24px",background:loading||!ticker.trim()?"#0d1829":"#38bdf8",border:"none",cursor:loading?"wait":"pointer",color:loading||!ticker.trim()?"#38bdf8":"#060d1a",fontFamily:"'JetBrains Mono',monospace",fontSize:11,letterSpacing:2,fontWeight:500,transition:"all 0.2s",flexShrink:0,opacity:!ticker.trim()&&!loading?0.4:1}}
          >
            {loading ? "ANALYZING…" : "ANALYZE →"}
          </button>
        </div>
      </div>

      {loading && (
        <div style={{maxWidth:380,margin:"0 auto",background:"#0d1829",border:"1px solid #1e3a5f",borderRadius:3,padding:"22px 26px",animation:"fadeIn 0.3s ease"}}>
          <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:10,letterSpacing:4,color:"#38bdf8",marginBottom:14}}>RUNNING ANALYSIS</div>
          <StepList currentStep={step} done={false}/>
        </div>
      )}

      {error && !loading && (
        <div style={{maxWidth:500,margin:"0 auto",background:"#160808",border:"1px solid #7f1d1d",borderRadius:3,padding:"14px 18px",fontFamily:"'JetBrains Mono',monospace",fontSize:12,color:"#fca5a5",animation:"fadeIn 0.3s ease"}}>
          ✕ {error}
        </div>
      )}

      {result && !loading && (
        <div style={{maxWidth:940,margin:"0 auto",animation:"fadeIn 0.5s ease"}}>
          <div style={{background:"#0d1829",border:"1px solid #1e3a5f",borderRadius:3,padding:"22px 26px",marginBottom:12,display:"flex",flexWrap:"wrap",gap:24,alignItems:"center"}}>
            <div style={{flex:"1 1 180px"}}>
              <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:9,color:"#334155",letterSpacing:3,marginBottom:4}}>{(result.sector||"").toUpperCase()}</div>
              <div style={{fontFamily:"'DM Serif Display',serif",fontSize:"clamp(20px,3.5vw,32px)",lineHeight:1.1}}>{result.companyName}</div>
              <div style={{fontFamily:"'JetBrains Mono',monospace",color:"#38bdf8",fontSize:13,marginTop:4,letterSpacing:2}}>{result.ticker}</div>
            </div>
            <div style={{textAlign:"center"}}>
              <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:9,color:"#334155",letterSpacing:2}}>PRICE</div>
              <div style={{fontFamily:"'DM Serif Display',serif",fontSize:28}}>${typeof result.currentPrice==="number"?result.currentPrice.toLocaleString():result.currentPrice}</div>
            </div>
            <div style={{textAlign:"center"}}>
              <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:9,color:"#334155",letterSpacing:2,marginBottom:6}}>VERDICT</div>
              <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:12,letterSpacing:2,color:vColor(result.verdict),border:`1px solid ${vColor(result.verdict)}44`,padding:"7px 14px",borderRadius:2}}>{result.verdict}</div>
            </div>
            <div style={{textAlign:"center"}}>
              <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:9,color:"#334155",letterSpacing:2}}>UPSIDE</div>
              <div style={{fontFamily:"'DM Serif Display',serif",fontSize:32,color:result.upsidePotential>=0?"#4ade80":"#f87171"}}>
                {result.upsidePotential>=0?"+":""}{typeof result.upsidePotential==="number"?result.upsidePotential.toFixed(1):result.upsidePotential}%
              </div>
            </div>
          </div>

          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12}}>
            <div style={{background:"#0d1829",border:"1px solid #1e3a5f",borderRadius:3,padding:"22px 26px",display:"flex",flexDirection:"column",alignItems:"center"}}>
              <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:9,color:"#334155",letterSpacing:3,marginBottom:12,alignSelf:"flex-start"}}>CONFIDENCE GAUGE</div>
              <GaugeArc probability={result.buyProbability||0}/>
              <div style={{marginTop:18,width:"100%"}}>
                <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:9,color:"#334155",letterSpacing:2,marginBottom:8}}>PEER GROUP</div>
                <div style={{display:"flex",flexWrap:"wrap",gap:6}}>
                  {(result.peerGroup||[]).map(p=>(
                    <span key={p} style={{fontFamily:"'JetBrains Mono',monospace",fontSize:10,color:"#38bdf8",background:"#071528",padding:"3px 10px",borderRadius:2,border:"1px solid #1e3a5f"}}>{p}</span>
                  ))}
                </div>
              </div>
              <div style={{marginTop:16,width:"100%"}}>
                <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:9,color:"#334155",letterSpacing:2,marginBottom:6}}>DCF ASSUMPTIONS</div>
                <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:10,color:"#475569",lineHeight:1.9}}>{result.dcfAssumptions}</div>
              </div>
            </div>

            <div style={{background:"#0d1829",border:"1px solid #1e3a5f",borderRadius:3,padding:"22px 26px"}}>
              <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:9,color:"#334155",letterSpacing:3,marginBottom:12}}>FINANCIAL METRICS VS PEERS</div>
              {result.metrics && Object.entries(result.metrics).map(([k,m])=>(
                <MetricRow key={k} label={METRICS_LABELS[k]||k} value={m.value} peerAvg={m.peerAvg} direction={m.direction}/>
              ))}
              <div style={{marginTop:8,fontFamily:"'JetBrains Mono',monospace",fontSize:9,color:"#1e3a5f"}}>Green = favorable · Red = unfavorable vs peer avg</div>
            </div>

            <div style={{background:"#0d1829",border:"1px solid #1e3a5f",borderRadius:3,padding:"22px 26px"}}>
              <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:9,color:"#334155",letterSpacing:3,marginBottom:10}}>MISPRICING ANALYSIS</div>
              <p style={{fontFamily:"'JetBrains Mono',monospace",fontSize:11,color:"#94a3b8",lineHeight:1.9,margin:"0 0 14px"}}>{result.mispricingAnalysis}</p>
              <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:9,color:"#334155",letterSpacing:2,marginBottom:6}}>PREMIUM / DISCOUNT JUSTIFICATION</div>
              <p style={{fontFamily:"'JetBrains Mono',monospace",fontSize:11,color:"#94a3b8",lineHeight:1.9,margin:"0 0 18px"}}>{result.premiumJustification}</p>
              <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:9,color:"#334155",letterSpacing:2,marginBottom:8}}>ANALYSIS STEPS</div>
              <StepList currentStep={-1} done={true}/>
            </div>

            <div style={{background:"#0d1829",border:"1px solid #1e3a5f",borderRadius:3,padding:"22px 26px"}}>
              <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:20}}>
                <div>
                  <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:9,color:"#f87171",letterSpacing:3,marginBottom:12}}>KEY RISKS</div>
                  {(result.keyRisks||[]).map((r,i)=>(
                    <div key={i} style={{fontFamily:"'JetBrains Mono',monospace",fontSize:11,color:"#94a3b8",marginBottom:10,lineHeight:1.7,display:"flex",gap:8}}>
                      <span style={{color:"#f8717155",flexShrink:0}}>—</span>{r}
                    </div>
                  ))}
                </div>
                <div>
                  <div style={{fontFamily:"'JetBrains Mono',monospace",fontSize:9,color:"#4ade80",letterSpacing:3,marginBottom:12}}>OPPORTUNITIES</div>
                  {(result.keyOpportunities||[]).map((o,i)=>(
                    <div key={i} style={{fontFamily:"'JetBrains Mono',monospace",fontSize:11,color:"#94a3b8",marginBottom:10,lineHeight:1.7,display:"flex",gap:8}}>
                      <span style={{color:"#4ade8055",flexShrink:0}}>+</span>{o}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <div style={{textAlign:"center",marginTop:20,fontFamily:"'JetBrains Mono',monospace",fontSize:9,color:"#1e293b",letterSpacing:1}}>
            NOT FINANCIAL ADVICE — EDUCATIONAL PURPOSES ONLY
          </div>
        </div>
      )}
    </div>
  );
}
