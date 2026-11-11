import React, { useEffect, useMemo, useState } from 'react';

const API = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api';

function MiniSpark({ points = [], height = 70 }) {
  const width = 520;
  const pad = { top: 8, right: 10, bottom: 6, left: 10 };
  const plotH = height - pad.top - pad.bottom;

  const coords = useMemo(() => {
    if (!points.length) return [];
    const maxT = Math.max(1, points[points.length - 1].t - (points[0]?.t || 0));
    const minT = points[0]?.t || 0;
    return points.map((p) => {
      const x = pad.left + ((p.t - minT) / maxT) * (width - pad.left - pad.right);
      const y = pad.top + plotH - Math.max(0, Math.min(1, p.score)) * plotH;
      return { x, y, ...p };
    });
  }, [points, plotH]);

  const path = useMemo(
    () => coords.map((p, i) => `${i ? 'L' : 'M'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' '),
    [coords]
  );

  if (!coords.length) {
    return <p className="empty-state">No chunk scores recorded for this call.</p>;
  }

  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
      <path d={path} fill="none" stroke="var(--line-neutral)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      {coords.filter((p) => p.score >= 0.55).map((p) => (
        <circle key={p.t} cx={p.x} cy={p.y} r="2.6" fill="var(--alert)" />
      ))}
    </svg>
  );
}

export default function CallDetailView({ callId, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setData(null);
    setError(null);
    fetch(`${API}/calls/${callId}`)
      .then(async (r) => {
        if (!r.ok) throw new Error('call not found');
        return r.json();
      })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [callId]);

  if (error) return <div className="call-detail"><p className="empty-state">{error}</p></div>;
  if (!data) return <div className="call-detail"><p className="empty-state">Loading detail…</p></div>;

  const { call, chunks, risk_scores: riskScores, alerts, reviews } = data;
  const tierClass = (t) => ({ high: 'tier-high', medium: 'tier-medium', low: 'tier-low' }[t]);

  return (
    <div className="call-detail">
      <div className="call-detail-head">
        <strong>Call #{call.id} — {call.status}</strong>
        <span className="mono-muted">{call.source_type} · {call.external_ref || 'no external ref'} · {call.language || '—'}</span>
        <button className="text-button" type="button" onClick={onClose}>Close</button>
      </div>

      <div>
        <h4>Chunk score timeline (raw 0–1, fused per 0.5 s hop)</h4>
        <div className="spark-wrap">
          <MiniSpark points={chunks.map((c) => ({ t: c.start_ms / 1000, score: c.raw_score }))} />
        </div>
        <div className="tier-legend"><span className="dot" /> Above 0.55 = escalate threshold</div>
      </div>

      {riskScores.length > 0 && (
        <div>
          <h4>Fused risk snapshots (0–100)</h4>
          <div className="call-detail-rows">
            {riskScores.slice().reverse().map((r) => (
              <div className="call-detail-row" key={r.id}>
                <span className={`tier-badge ${tierClass(r.confidence_tier)}`}>{r.confidence_tier}</span>
                <strong>{r.risk_score !== null ? Number(r.risk_score).toFixed(1) : '—'}</strong>
                <small>{r.computed_at}</small>
              </div>
            ))}
          </div>
        </div>
      )}

      {alerts.length > 0 && (
        <div>
          <h4>Alerts</h4>
          <div className="call-detail-rows">
            {alerts.map((a) => (
              <div className="call-detail-row" key={a.id}>
                <span className="tier-badge tier-high">{a.severity}</span>
                <span>{a.message}</span>
                <small>{a.acknowledged ? `acknowledged by ${a.acknowledged_by || 'analyst'}` : 'unacknowledged'}</small>
              </div>
            ))}
          </div>
        </div>
      )}

      {reviews.length > 0 && (
        <div>
          <h4>Case reviews</h4>
          <div className="call-detail-rows">
            {reviews.map((r) => (
              <div className="call-detail-row" key={r.id}>
                <strong>{r.decision}</strong>
                <span>{r.reviewer}</span>
                <small>{r.notes || ''} · {r.reviewed_at}</small>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}