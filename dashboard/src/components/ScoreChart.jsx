import React, { useMemo } from 'react';

export default function ScoreChart({
  score = 0,
  displayScore = score,
  history = [],
  escalateThreshold = 0.55,
  blockThreshold = 0.80,
  state = 'OK'
}) {
  const isAlert = score >= escalateThreshold;

  // Chart dimensions
  const svgWidth = 850;
  const svgHeight = 260;
  const padding = { top: 20, right: 90, bottom: 30, left: 45 };

  const plotWidth = svgWidth - padding.left - padding.right;
  const plotHeight = svgHeight - padding.top - padding.bottom;

  // Compute SVG coordinates for data points
  const points = useMemo(() => {
    if (!history || history.length === 0) return [];

    // X scale: based on elapsed time or last N points
    const minT = history[0]?.t || 0;
    const maxT = Math.max(minT + 10, history[history.length - 1]?.t || 10);
    const rangeT = maxT - minT || 1;

    return history.map((pt) => {
      const x = padding.left + ((pt.t - minT) / rangeT) * plotWidth;
      const y = padding.top + plotHeight - (Math.max(0, Math.min(1, pt.score))) * plotHeight;
      return { x, y, score: pt.score, t: pt.t };
    });
  }, [history, plotWidth, plotHeight, padding.left, padding.top]);

// Catmull-Rom spline through the measured points: the curve passes exactly
  // through every score sample while flowing naturally between hops.
  const smoothPath = useMemo(() => {
    if (points.length === 0) return '';
    if (points.length === 1) return `M ${points[0].x.toFixed(1)} ${points[0].y.toFixed(1)}`;
    const c1 = (p, i, len) => (i < len - 1 ? p[i + 1] : p[i]);
    const c0 = (p, i) => (i > 0 ? p[i - 1] : p[i]);
    let d = `M ${points[0].x.toFixed(1)} ${points[0].y.toFixed(1)}`;
    for (let i = 0; i < points.length - 1; i += 1) {
      const p1 = points[i];
      const p2 = points[i + 1];
      const prev = c0(points, i);
      const next = c1(points, i, points.length);
      const cp1x = p1.x + (p2.x - prev.x) / 6;
      const cp1y = p1.y + (p2.y - prev.y) / 6;
      const cp2x = p2.x - (next.x - p1.x) / 6;
      const cp2y = p2.y - (next.y - p1.y) / 6;
      d += ` C ${cp1x.toFixed(1)} ${cp1y.toFixed(1)}, ${cp2x.toFixed(1)} ${cp2y.toFixed(1)}, ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
    }
    return d;
  }, [points]);

  // Dense samples along the same spline, used to shape the alert highlight so
  // it follows the curve (and its crossings) instead of a straight-line guess.
  const curveSamples = useMemo(() => {
    if (points.length < 2) return [];
    const out = [];
    const SEG = 12;
    for (let i = 0; i < points.length - 1; i += 1) {
      const p1 = points[i];
      const p2 = points[i + 1];
      const prev = i > 0 ? points[i - 1] : p1;
      const next = i + 1 < points.length - 1 ? points[i + 2] : p2;
      const cp1x = p1.x + (p2.x - prev.x) / 6;
      const cp1y = p1.y + (p2.y - prev.y) / 6;
      const cp2x = p2.x - (next.x - p1.x) / 6;
      const cp2y = p2.y - (next.y - p1.y) / 6;
      for (let k = 0; k < SEG; k += 1) {
        const t = k / SEG;
        const mt = 1 - t;
        const x = mt * mt * mt * p1.x + 3 * mt * mt * t * cp1x + 3 * mt * t * t * cp2x + t * t * t * p2.x;
        const y = mt * mt * mt * p1.y + 3 * mt * mt * t * cp1y + 3 * mt * t * t * cp2y + t * t * t * p2.y;
        out.push({ x, y, score: padding.top + plotHeight >= y ? (padding.top + plotHeight - y) / plotHeight : 0 });
      }
    }
    const last = points[points.length - 1];
    out.push({ x: last.x, y: last.y, score: last.score });
    return out;
  }, [points, padding.top, plotHeight]);

  // Area under the curve
  const areaD = useMemo(() => {
    if (points.length < 2) return '';
    const last = points[points.length - 1];
    const first = points[0];
    const bottomY = padding.top + plotHeight;
    return `${smoothPath} L ${last.x.toFixed(1)} ${bottomY} L ${first.x.toFixed(1)} ${bottomY} Z`;
  }, [smoothPath, points, padding.top, plotHeight]);

  // Natural-coloured highlight only where the flowing score is at or above
  // the ESCALATE threshold (single alert colour).
  const alertRegions = useMemo(() => {
    const regions = [];
    let run = null;
    const escalateY = padding.top + plotHeight - escalateThreshold * plotHeight;
    const flush = () => {
      if (!run || run.length < 2) { run = null; return; }
      let d = `M ${run[0].x.toFixed(1)} ${run[0].y.toFixed(1)}`;
      for (let i = 1; i < run.length; i += 1) d += ` L ${run[i].x.toFixed(1)} ${run[i].y.toFixed(1)}`;
      d += ` L ${run[run.length - 1].x.toFixed(1)} ${escalateY.toFixed(1)} L ${run[0].x.toFixed(1)} ${escalateY.toFixed(1)} Z`;
      regions.push(d);
      run = null;
    };
    for (const s of curveSamples) {
      if (s.score >= escalateThreshold) {
        run = run || [{ x: s.x, y: s.y }];
        run.push({ x: s.x, y: s.y });
      } else {
        flush();
      }
    }
    flush();
    return regions;
  }, [curveSamples, escalateThreshold, padding.top, plotHeight]);

  // Y coordinate for thresholds
  const escalateY = padding.top + plotHeight - escalateThreshold * plotHeight;
  const blockY = padding.top + plotHeight - blockThreshold * plotHeight;

  return (
    <div style={{
      backgroundColor: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: '6px',
      padding: '24px',
      display: 'flex',
      flexDirection: 'column',
      gap: '16px'
    }}>
      {/* Top Bar: 64px+ Numeric Readout & State Pill */}
      <div style={{
        display: 'flex',
        alignItems: 'baseline',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: '16px'
      }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '20px' }}>
          <div>
            <div style={{ fontSize: '13px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Fused Synthetic Risk Score
            </div>
            {/* 64px+ Readout — Legible from back row */}
            <div style={{
              fontSize: '72px',
              fontWeight: 800,
              fontFamily: 'var(--font-mono)',
              lineHeight: 1,
              letterSpacing: '-0.03em',
              color: isAlert ? 'var(--alert)' : 'var(--text)'
            }}>
              {displayScore.toFixed(3)}
            </div>
          </div>

          {/* State Badge: neutral when OK, alert ONLY on threshold crossing. NEVER green. */}
          <div style={{
            padding: '6px 16px',
            borderRadius: '4px',
            fontSize: '15px',
            fontWeight: 700,
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
            backgroundColor: isAlert ? 'var(--alert)' : 'var(--border)',
            color: isAlert ? '#FFFFFF' : 'var(--text)'
          }}>
            {state}
          </div>
        </div>

        {/* Threshold Reference indicators */}
        <div style={{ display: 'flex', gap: '16px', fontSize: '13px', color: 'var(--text-muted)' }}>
          <div>
            <span style={{ display: 'inline-block', width: '12px', height: '2px', backgroundColor: 'var(--threshold)', verticalAlign: 'middle', marginRight: '6px', borderTop: '1px dashed var(--line-neutral)' }} />
            Escalate: <strong style={{ color: 'var(--text)' }}>{escalateThreshold.toFixed(2)}</strong>
          </div>
          <div>
            <span style={{ display: 'inline-block', width: '12px', height: '2px', backgroundColor: 'var(--threshold)', verticalAlign: 'middle', marginRight: '6px', borderTop: '1px dashed var(--line-neutral)' }} />
            Block: <strong style={{ color: 'var(--text)' }}>{blockThreshold.toFixed(2)}</strong>
          </div>
        </div>
      </div>

      {/* SVG Dominant Score Line Chart */}
      <div style={{ width: '100%', overflowX: 'auto' }}>
        <svg
          viewBox={`0 0 ${svgWidth} ${svgHeight}`}
          style={{ width: '100%', height: 'auto', display: 'block' }}
        >
          {/* Y Axis Grid lines (0.0, 0.25, 0.5, 0.75, 1.0) */}
          {[0, 0.25, 0.5, 0.75, 1.0].map((val) => {
            const y = padding.top + plotHeight - val * plotHeight;
            return (
              <g key={val}>
                <line
                  x1={padding.left}
                  y1={y}
                  x2={padding.left + plotWidth}
                  y2={y}
                  stroke="var(--border)"
                  strokeWidth="1"
                />
                <text
                  x={padding.left - 10}
                  y={y + 4}
                  textAnchor="end"
                  fontSize="11"
                  fill="var(--text-muted)"
                  fontFamily="var(--font-mono)"
                >
                  {val.toFixed(2)}
                </text>
              </g>
            );
          })}

          {/* Threshold 1: ESCALATE (Dashed line) */}
          <line
            x1={padding.left}
            y1={escalateY}
            x2={padding.left + plotWidth}
            y2={escalateY}
            stroke="var(--line-neutral)"
            strokeWidth="1.5"
            strokeDasharray="6,4"
          />
          <text
            x={padding.left + plotWidth + 8}
            y={escalateY + 4}
            fontSize="11"
            fontWeight="600"
            fill={score >= escalateThreshold ? 'var(--alert)' : 'var(--text-muted)'}
            fontFamily="var(--font-system)"
          >
            ESCALATE {escalateThreshold.toFixed(2)}
          </text>

          {/* Threshold 2: BLOCK (Dashed line) */}
          <line
            x1={padding.left}
            y1={blockY}
            x2={padding.left + plotWidth}
            y2={blockY}
            stroke="var(--line-neutral)"
            strokeWidth="1.5"
            strokeDasharray="6,4"
          />
          <text
            x={padding.left + plotWidth + 8}
            y={blockY + 4}
            fontSize="11"
            fontWeight="600"
            fill={score >= blockThreshold ? 'var(--alert)' : 'var(--text-muted)'}
            fontFamily="var(--font-system)"
          >
            BLOCK {blockThreshold.toFixed(2)}
          </text>

          {/* Fill under line */}
          {areaD && (
            <path
              d={areaD}
              fill="rgba(138, 134, 128, 0.06)"
            />
          )}

{/* Neutral score flow curve, with the alert highlight only above ESCALATE. */}
          {smoothPath && (
            <path
              d={smoothPath}
              fill="none"
              stroke="var(--line-neutral)"
              strokeWidth="2.5"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          )}
          {alertRegions.map((segment, index) => (
            <path
              key={index}
              d={segment}
              fill="var(--alert)"
              fillOpacity="0.14"
            />
          ))}
          {alertRegions.map((segment, index) => (
            <path
              key={`stroke-${index}`}
              d={segment}
              fill="none"
              stroke="var(--alert)"
              strokeWidth="2.5"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ))}

          {/* Current point cursor */}
          {points.length > 0 && (
            <circle
              cx={points[points.length - 1].x}
              cy={points[points.length - 1].y}
              r="4.5"
              fill={isAlert ? 'var(--alert)' : 'var(--line-neutral)'}
              stroke="var(--surface)"
              strokeWidth="2"
            />
          )}
        </svg>
      </div>
    </div>
  );
}
