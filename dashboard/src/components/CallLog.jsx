import React from 'react';

export default function CallLog({
  events = [],
  scorePoints = []
}) {
  return (
    <div style={{
      backgroundColor: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: '6px',
      padding: '20px 24px',
      display: 'flex',
      flexDirection: 'column',
      gap: '16px'
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{
            fontSize: '13px',
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
            color: 'var(--text-muted)'
          }}>
            Local Score Audit
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
            Derived score points and threshold events (SQLite-backed)
          </div>
        </div>

        <div style={{
          fontSize: '11px',
          padding: '3px 8px',
          borderRadius: '3px',
          backgroundColor: 'var(--bg)',
          border: '1px solid var(--border)',
          color: 'var(--text-muted)',
          fontFamily: 'var(--font-mono)'
        }}>
          SQLite · local
        </div>
      </div>

      <div style={{
        maxHeight: '220px',
        overflowY: 'auto',
        border: '1px solid var(--border)',
        borderRadius: '4px'
      }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px', textAlign: 'left' }}>
          <thead>
            <tr style={{ backgroundColor: 'var(--bg)', borderBottom: '1px solid var(--border)' }}>
              <th style={{ padding: '8px 12px', fontWeight: 600, color: 'var(--text-muted)' }}>Type</th>
              <th style={{ padding: '8px 12px', fontWeight: 600, color: 'var(--text-muted)' }}>Time (t)</th>
              <th style={{ padding: '8px 12px', fontWeight: 600, color: 'var(--text-muted)' }}>Call ID</th>
              <th style={{ padding: '8px 12px', fontWeight: 600, color: 'var(--text-muted)' }}>Details</th>
              <th style={{ padding: '8px 12px', fontWeight: 600, color: 'var(--text-muted)' }}>State</th>
            </tr>
          </thead>
          <tbody>
            {events.length === 0 && scorePoints.length === 0 ? (
              <tr>
                <td colSpan="5" style={{ padding: '24px', textAlign: 'center', color: 'var(--text-muted)', fontStyle: 'italic' }}>
                  Awaiting score points from the scorer...
                </td>
              </tr>
            ) : (
              // Combine threshold events and score points, sorted recent first
              [
                ...events.map(e => ({ ...e, logType: 'threshold' })),
                ...scorePoints.map(s => ({ ...s, logType: 'point' }))
              ]
                .sort((a, b) => (b.t || 0) - (a.t || 0))
                .slice(0, 25)
                .map((row, idx) => {
                  const isThreshold = row.logType === 'threshold';
                  const isAlertState = row.state === 'ESCALATE' || row.state === 'BLOCK' || row.to_state === 'ESCALATE' || row.to_state === 'BLOCK';

                  return (
                    <tr
                      key={idx}
                      style={{
                        borderBottom: '1px solid var(--border)',
                        backgroundColor: idx % 2 === 0 ? 'var(--surface)' : 'var(--bg)'
                      }}
                    >
                      <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)' }}>
                        {isThreshold ? (
                          <span style={{
                            padding: '2px 6px',
                            borderRadius: '3px',
                            fontSize: '11px',
                            fontWeight: 700,
                            backgroundColor: 'var(--border)',
                            color: 'var(--text)'
                          }}>
                            EVENT
                          </span>
                        ) : (
                          <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>
                            SCORE
                          </span>
                        )}
                      </td>
                      <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)' }}>
                        +{Number(row.t || 0).toFixed(1)}s
                      </td>
                      <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)' }}>
                        {row.call_id || '—'}
                      </td>
                      <td style={{ padding: '8px 12px' }}>
                        {isThreshold ? (
                          <span>
                            State transition: <strong>{row.from_state}</strong> → <strong style={{ color: isAlertState ? 'var(--alert)' : 'var(--text)' }}>{row.to_state}</strong>
                          </span>
                        ) : (
                          <span>
                            Score: <strong style={{ fontFamily: 'var(--font-mono)' }}>{Number(row.score || 0).toFixed(3)}</strong> (A: {Number(row.acoustic || 0).toFixed(2)}, P: {Number(row.prosody || 0).toFixed(2)}, S: {Number(row.speaker || 0).toFixed(2)})
                          </span>
                        )}
                      </td>
                      <td style={{ padding: '8px 12px' }}>
                        <span style={{
                          fontWeight: 700,
                          fontSize: '11px',
                          color: isAlertState ? 'var(--alert)' : 'var(--text-muted)'
                        }}>
                          {row.to_state || row.state || 'OK'}
                        </span>
                      </td>
                    </tr>
                  );
                })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
