import React, { useRef, useState } from 'react';

const API = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api';

const TIER = { high: 'tier-high', medium: 'tier-medium', low: 'tier-low' };

export default function AnalyzeUpload({ onCallAnalyzed }) {
  const inputRef = useRef(null);
  const [fileName, setFileName] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  const pick = () => setFileName(inputRef.current?.files?.[0]?.name || '');

  const run = async () => {
    const file = inputRef.current?.files?.[0];
    if (!file) {
      setError('Choose a 16-bit mono WAV file first.');
      return;
    }
    setBusy(true);
    setError('');
    setResult(null);
    const form = new FormData();
    form.append('audio', file);
    form.append('call_id', file.name);
    try {
      const res = await fetch(`${API}/analyze`, { method: 'POST', body: form });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      if (body.status === 'inconclusive') throw new Error(body.error || 'inconclusive');
      setResult(body);
      if (onCallAnalyzed) onCallAnalyzed();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="upload-card">
      <h3>Upload & analyze</h3>
      <div className="upload-row">
        <label className="file-pick">
          <input ref={inputRef} type="file" accept=".wav,audio/wav" onChange={pick} />
          {fileName || 'Choose WAV…'}
        </label>
        <button className="button button--primary" type="button" onClick={run} disabled={busy}>
          {busy ? 'Scoring…' : 'Run analysis'}
        </button>
      </div>

      {error && <p className="operations-message">{error}</p>}

      {result && (
        <div className="upload-result">
          <div className="call-detail-head">
            <span className={`tier-badge ${TIER[result.confidence_tier] || 'tier-low'}`}>
              {result.confidence_tier || result.status}
            </span>
            <strong>{result.risk_score != null ? result.risk_score.toFixed(1) : '—'} / 100</strong>
            <span className="mono-muted">call #{result.call_id} · {result.chunk_scores.length} chunk{result.chunk_scores.length === 1 ? '' : 's'}</span>
          </div>
          {result.chunks && (
            <div className="chunk-grid">
              {result.chunks.map((c, i) => (
                <div className="chunk-cell" key={i}>
                  <span className="mono-muted">c{i}</span>
                  <strong>{c.raw_score.toFixed(2)}</strong>
                  <small>A {c.acoustic.toFixed(2)} · P {c.prosody.toFixed(2)} · S {c.speaker.toFixed(2)}
                    {c.svm !== undefined && ` · V ${c.svm.toFixed(2)}`}</small>
                </div>
              ))}
            </div>
          )}
          {result.chunks && result.chunks.some((c) => c.svm !== undefined) && (
            <p className="operations-message">
              V = AIVoice MFCC-SVM detector (secondary check; raw AASIST drives the fusion).{' '}
              {result.chunks.some((c) => c.ai_consensus) ? (
                <strong>Consensus: AASIST + SVM both flag synthetic speech.</strong>
              ) : (
                'No detector consensus on synthetic speech.'
              )}
            </p>
          )}
          {result.alert && (
            <p className="operations-message">
              <strong className={result.alert.severity === 'block' ? 'tier-high' : 'tier-medium'}>[{result.alert.severity}]</strong> {result.alert.message}
            </p>
          )}
        </div>
      )}
    </div>
  );
}