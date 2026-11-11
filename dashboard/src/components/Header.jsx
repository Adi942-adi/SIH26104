import React from 'react';

export default function Header({
  elapsed,
  codec = '16 kHz wideband PCM',
  callId = 'demo1',
  wsConnected,
  scenario,
  onScenarioChange,
  scenarios
}) {
  const formatTimer = (sec) => {
    if (sec == null || isNaN(sec)) return '00:00.0';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    const ms = Math.floor((sec % 1) * 10);
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${ms}`;
  };

  return (
    <header style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '16px 24px',
      backgroundColor: 'var(--surface)',
      borderBottom: '1px solid var(--border)',
      gap: '16px',
      flexWrap: 'wrap'
    }}>
      {/* Brand & Call Info */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
        <div>
          <h1 style={{ fontSize: '18px', fontWeight: 600, letterSpacing: '-0.02em', color: 'var(--text)' }}>
            Voice Cloning Detection
          </h1>
          <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '2px', display: 'flex', gap: '10px' }}>
            <span>Call: <strong style={{ color: 'var(--text)', fontFamily: 'var(--font-mono)' }}>{callId || '—'}</strong></span>
            <span>·</span>
            <span>Codec: <strong style={{ color: 'var(--text)' }}>{codec}</strong></span>
          </div>
        </div>
      </div>

      {/* Middle: Timer & Connectivity */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '24px' }}>
        {/* Elapsed Timer */}
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)' }}>
            Elapsed Call Time
          </div>
          <div style={{
            fontSize: '22px',
            fontFamily: 'var(--font-mono)',
            fontWeight: 700,
            color: 'var(--text)',
            letterSpacing: '0.02em'
          }}>
            {formatTimer(elapsed)}
          </div>
        </div>

        {/* Live Status Badges */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{
              width: '8px',
              height: '8px',
              borderRadius: '50%',
              backgroundColor: wsConnected ? 'var(--line-neutral)' : 'var(--border)',
              display: 'inline-block'
            }} />
            <span style={{ color: 'var(--text-muted)' }}>
              WebSocket {wsConnected ? 'Connected' : 'Offline'}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{
              width: '8px',
              height: '8px',
              borderRadius: '50%',
              backgroundColor: 'var(--line-neutral)',
              display: 'inline-block'
            }} />
            <span style={{ color: 'var(--text-muted)' }}>
              Store SQLite · local
            </span>
          </div>
        </div>
      </div>

      {/* Scenario Selector */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <label htmlFor="scenario-select" style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
          Scenario Policy:
        </label>
        <select
          id="scenario-select"
          value={scenario}
          onChange={(e) => onScenarioChange(e.target.value)}
          style={{
            padding: '8px 12px',
            borderRadius: '4px',
            border: '1px solid var(--border)',
            backgroundColor: 'var(--bg)',
            color: 'var(--text)',
            fontSize: '13px',
            fontWeight: 500,
            cursor: 'pointer',
            outline: 'none'
          }}
        >
          {Object.entries(scenarios).map(([key, s]) => (
            <option key={key} value={key}>
              {s.label} (Escalate {s.escalate.toFixed(2)}, Block {s.block.toFixed(2)})
            </option>
          ))}
        </select>
      </div>
    </header>
  );
}
