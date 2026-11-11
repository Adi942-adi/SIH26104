import React, { useEffect, useState } from 'react';
import AnalyzeUpload from './AnalyzeUpload.jsx';
import CallDetailView from './CallDetailView.jsx';

const API = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api';

export default function OperationsPanel() {
  const [alerts, setAlerts] = useState([]);
  const [calls, setCalls] = useState([]);
  const [selectedCall, setSelectedCall] = useState(null);
  const [reviewState, setReviewState] = useState('needs_more_evidence');
  const [reviewer, setReviewer] = useState('fraud-analyst');
  const [message, setMessage] = useState('');

  const refresh = async () => {
    try {
      const [alertsResponse, callsResponse] = await Promise.all([fetch(`${API}/alerts`), fetch(`${API}/calls`)]);
      if (alertsResponse.ok) setAlerts(await alertsResponse.json());
      if (callsResponse.ok) setCalls(await callsResponse.json());
    } catch {
      setMessage('Operations API unavailable');
    }
  };

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 4000);
    return () => window.clearInterval(timer);
  }, []);

  const acknowledge = async (id) => {
    await fetch(`${API}/alerts/${id}/acknowledge`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ acknowledged_by: reviewer })
    });
    refresh();
  };

  const submitReview = async (event) => {
    event.preventDefault();
    if (!selectedCall) return;
    const response = await fetch(`${API}/calls/${selectedCall}/review`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reviewer, decision: reviewState, notes: 'Reviewed from live console' })
    });
    setMessage(response.ok ? 'Case review saved' : 'Could not save case review');
    refresh();
  };

  return (
    <section className="operations-panel" aria-labelledby="operations-heading">
      <div className="operations-header">
        <div><div className="section-kicker">Analyst workspace</div><h2 id="operations-heading">Cases, alerts and analysis</h2></div>
        <span className="operations-count">{alerts.length} active</span>
      </div>
      <div className="operations-grid">
        <div className="operations-col">
          <AnalyzeUpload onCallAnalyzed={refresh} />
          <div>
            <h3>Active alerts</h3>
            <div className="alert-list">
              {alerts.length === 0 ? <p className="empty-state">No unacknowledged alerts.</p> : alerts.slice(0, 5).map((alert) => (
                <div className="alert-row" key={alert.id}>
                  <div><strong>{alert.severity}</strong><span>{alert.message}</span><small>Call #{alert.call_id} · {alert.risk_score_id ? 'risk snapshot' : 'new'}</small></div>
                  <button className="text-button" type="button" onClick={() => acknowledge(alert.id)}>Acknowledge</button>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="operations-col">
          <h3>Recent calls</h3>
          <div className="call-list">
            {calls.length === 0 ? <p className="empty-state">No analyzed calls yet.</p> : calls.slice(0, 5).map((call) => (
              <button className={`call-row ${selectedCall === call.id ? 'call-row--selected' : ''}`} type="button" key={call.id} onClick={() => setSelectedCall(call.id)}>
                <strong>Call #{call.id}</strong><span>{call.status}</span><small>{call.source_type} · {call.external_ref || 'no external ref'}</small>
              </button>
            ))}
          </div>
          {selectedCall && (
            <>
              <CallDetailView callId={selectedCall} onClose={() => setSelectedCall(null)} />
              <form className="review-form" onSubmit={submitReview}>
                <strong>Review call #{selectedCall}</strong>
                <label>Reviewer<input value={reviewer} onChange={(event) => setReviewer(event.target.value)} /></label>
                <label>Decision<select value={reviewState} onChange={(event) => setReviewState(event.target.value)}><option value="needs_more_evidence">Needs more evidence</option><option value="confirmed_spoof">Confirmed spoof</option><option value="confirmed_genuine">Confirmed genuine</option></select></label>
                <button className="button button--primary" type="submit">Save review</button>
              </form>
            </>
          )}
        </div>
      </div>
      {message && <p className="operations-message">{message}</p>}
    </section>
  );
}