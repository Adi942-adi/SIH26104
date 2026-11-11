import React from 'react';

const FLAG_DESCRIPTIONS = {
  unknown_origin: 'Unknown origin / unverified PSTN CLI',
  high_value_txn: 'High-value transaction pending approval',
  after_hours: 'Call initiated outside business hours',
  abnormal_frequency: 'Burst of auth requests detected',
  narrowband_artifact: 'Narrowband upsample spectral anomaly'
};

export default function ContextFlags({
  activeFlags = [],
  flagHistory = []
}) {
  return (
    <div style={{
      backgroundColor: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: '6px',
      padding: '20px',
      display: 'flex',
      flexDirection: 'column',
      gap: '16px',
      height: '100%'
    }}>
      <div>
        <div style={{
          fontSize: '13px',
          fontWeight: 600,
          textTransform: 'uppercase',
          letterSpacing: '0.04em',
          color: 'var(--text-muted)'
        }}>
          Context Rules & Flags
        </div>
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
          Rule-based contextual enrichment
        </div>
      </div>

      {/* Active Flags Section */}
      <div>
        <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', marginBottom: '8px' }}>
          Active Flags ({activeFlags.length})
        </div>

        {activeFlags.length === 0 ? (
          <div style={{
            padding: '12px',
            backgroundColor: 'var(--bg)',
            borderRadius: '4px',
            border: '1px dashed var(--border)',
            fontSize: '13px',
            color: 'var(--text-muted)',
            textAlign: 'center'
          }}>
            No active context flags
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {activeFlags.map((flag) => (
              <div
                key={flag}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '10px',
                  padding: '10px 12px',
                  backgroundColor: 'var(--bg)',
                  border: '1px solid var(--border)',
                  borderRadius: '4px'
                }}
              >
                <div style={{
                  width: '8px',
                  height: '8px',
                  borderRadius: '50%',
                  backgroundColor: 'var(--line-neutral)',
                  marginTop: '5px',
                  flexShrink: 0
                }} />
                <div>
                  <div style={{
                    fontSize: '13px',
                    fontWeight: 700,
                    fontFamily: 'var(--font-mono)',
                    color: 'var(--text)'
                  }}>
                    {flag}
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text)', marginTop: '2px' }}>
                    {FLAG_DESCRIPTIONS[flag] || 'Rule triggered by telephony context'}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Flag Trigger History */}
      <div style={{ marginTop: 'auto' }}>
        <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', marginBottom: '8px' }}>
          Flag Log
        </div>
        <div style={{
          maxHeight: '160px',
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: '6px',
          fontSize: '12px'
        }}>
          {flagHistory.length === 0 ? (
            <div style={{ color: 'var(--text-muted)', fontStyle: 'italic', fontSize: '12px' }}>
              No rules triggered yet.
            </div>
          ) : (
            flagHistory.map((item, idx) => (
              <div
                key={idx}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  padding: '6px 8px',
                  backgroundColor: 'var(--bg)',
                  borderRadius: '3px',
                  border: '1px solid var(--border)'
                }}
              >
                <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--text)' }}>
                  {item.flag}
                </span>
                <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                  +{item.t.toFixed(1)}s
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
