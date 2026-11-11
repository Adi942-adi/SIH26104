import React, { useState, useEffect, useRef, useMemo } from 'react';
import Header from './components/Header.jsx';
import ScoreChart from './components/ScoreChart.jsx';
import LayerBars from './components/LayerBars.jsx';
import ContextFlags from './components/ContextFlags.jsx';
import WaveformDisplay from './components/WaveformDisplay.jsx';
import OperationsPanel from './components/OperationsPanel.jsx';

const SCENARIOS = {
  high_value: {
    label: 'High-Value Transfer',
    escalate: 0.55,
    block: 0.80
  },
  retail: {
    label: 'Retail Banking',
    escalate: 0.65,
    block: 0.85
  },
  privileged: {
    label: 'Privileged Access',
    escalate: 0.45,
    block: 0.70
  }
};

export default function App() {
  const [selectedScenario, setSelectedScenario] = useState('high_value');
  const scenario = SCENARIOS[selectedScenario];

  // Streaming Live Data State (from WebSocket ws://localhost:8000/ws)
  const [callId, setCallId] = useState('demo1');
  const [elapsed, setElapsed] = useState(0);
  const [score, setScore] = useState(0.0);
  const [smoothScore, setSmoothScore] = useState(0.0);
  const [layers, setLayers] = useState({ acoustic: 0, prosody: 0, speaker: 0 });
  const [contextFlags, setContextFlags] = useState([]);
  const [history, setHistory] = useState([]);
  const [flagHistory, setFlagHistory] = useState([]);

  // Connection states
  const [wsConnected, setWsConnected] = useState(false);

  const wsRef = useRef(null);
  const seenFlagsRef = useRef(new Set());
  const demoCallRef = useRef(null);
  const scoreRef = useRef(0);
  const smoothScoreRef = useRef(0);

  // Continuously animate the fused-score readout between 0.5 s measurements so
  // the number flows instead of jumping. `score` stays the authoritative value
  // used for state/locking; `smoothScore` is only the displayed needle.
  useEffect(() => {
    let raf;
    let last = performance.now();
    const tick = (now) => {
      const dt = Math.min(0.15, (now - last) / 1000);
      last = now;
      const target = scoreRef.current;
      const next = smoothScoreRef.current + (target - smoothScoreRef.current) * (1 - Math.exp(-dt * 16));
      smoothScoreRef.current = next;
      setSmoothScore(next);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  // Live-microphone streaming (sends the user's voice over /ws/live into the
  // same streaming pipeline the demo clips use; browser audio is 16 kHz wideband).
  const [recording, setRecording] = useState(false);
  const micWsRef = useRef(null);
  const micStreamRef = useRef(null);
  const micCtxRef = useRef(null);
  const micSrcRef = useRef(null);
  const micProcRef = useRef(null);
  const micOutRef = useRef(new Float32Array(0));

  const wsApiHost = () => {
    const apiHost = import.meta.env.VITE_API_URL || `${window.location.protocol === 'https:' ? 'https' : 'http'}://${window.location.hostname}:8000`;
    return apiHost.replace(/^http/, 'ws');
  };

  const toggleMic = async () => {
    if (micWsRef.current) {
      if (micWsRef.current.readyState === WebSocket.OPEN) {
        try { micWsRef.current.send(JSON.stringify({ stop: true })); } catch { /* already closed */ }
      }
      try { micWsRef.current.close(); } catch { /* already closed */ }
      micWsRef.current = null;
      if (micProcRef.current) { micProcRef.current.onaudioprocess = null; micProcRef.current.disconnect(); micProcRef.current = null; }
      if (micSrcRef.current) { try { micSrcRef.current.disconnect(); } catch { /* already gone */ } micSrcRef.current = null; }
      if (micStreamRef.current) micStreamRef.current.getTracks().forEach((t) => t.stop());
      if (micCtxRef.current) micCtxRef.current.close();
      micStreamRef.current = null;
      micCtxRef.current = null;
      micOutRef.current = new Float32Array(0);
      demoCallRef.current = null;
      setRecording(false);
      return;
    }
    // Create + resume the AudioContext synchronously in the click gesture so it
    // is not left 'suspended' by the await below (a suspended context never
    // fires onaudioprocess, so no audio would ever be captured).
    const ctx = new AudioContext();
    ctx.resume();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      const callId = `mic-${Math.random().toString(36).slice(2, 10)}`;
      const liveWs = new WebSocket(`${wsApiHost()}/ws/live`);
      micWsRef.current = liveWs;
      liveWs.onopen = () => liveWs.send(JSON.stringify({ start: true, call_id: callId, rate: 16000 }));

      demoCallRef.current = callId; // render only this call's broadcasts
      setCallId(callId);
      setScore(0.0);
      scoreRef.current = 0.0;
      smoothScoreRef.current = 0.0;
      setSmoothScore(0.0);
      setLayers({ acoustic: 0, prosody: 0, speaker: 0 });
      setHistory([]);
      setFlagHistory([]);
      setContextFlags([]);
      setElapsed(0);
      seenFlagsRef.current = new Set();
      setRecording(true);

      // The browser holds wideband audio; stream it at 16 kHz so AASIST sees
      // its native input distribution (8 kHz narrowband flips its scores to
      // ~1.0 for natural speech too - see /ws/live notes server-side).
      const ratio = ctx.sampleRate / 16000;
      const proc = ctx.createScriptProcessor(4096, 1, 1);
      const source = ctx.createMediaStreamSource(stream);
      source.connect(proc);
      // Chrome only advances a Web Audio graph that reaches its destination;
      // a processor with nothing downstream never fires onaudioprocess. Route it
      // through a muted gain so the rendering clock runs with no audible
      // loopback into the speakers.
      const muteTap = ctx.createGain();
      muteTap.gain.value = 0;
      proc.connect(muteTap);
      muteTap.connect(ctx.destination);
      proc.onaudioprocess = (e) => {
        const input = e.inputBuffer.getChannelData(0);
        const outLen = Math.floor(input.length / ratio);
        const out = new Float32Array(outLen);
        for (let i = 0; i < outLen; i++) {
          const pos = i * ratio;
          const idx = Math.floor(pos);
          const frac = pos - idx;
          const a = input[idx];
          const b = input[Math.min(idx + 1, input.length - 1)];
          out[i] = a + (b - a) * frac;
        }
        const carry = micOutRef.current;
        const combined = new Float32Array(carry.length + out.length);
        combined.set(carry, 0);
        combined.set(out, carry.length);
        const full = Math.floor(combined.length / 160) * 160;
        if (full > 0 && micWsRef.current && micWsRef.current.readyState === WebSocket.OPEN) {
          const ints = new Int16Array(full);
          for (let i = 0; i < full; i++) {
            const s = Math.max(-1, Math.min(1, combined[i]));
            ints[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
          }
          micWsRef.current.send(ints.buffer);
        }
        micOutRef.current = combined.slice(full);
      };
      micProcRef.current = proc;
      micSrcRef.current = source;
      micStreamRef.current = stream;
      micCtxRef.current = ctx;
    } catch (err) {
      console.warn('[Mic] could not start live capture:', err);
      if (micWsRef.current) { try { micWsRef.current.close(); } catch { /* noop */ } micWsRef.current = null; }
      if (micSrcRef.current) { try { micSrcRef.current.disconnect(); } catch { /* noop */ } micSrcRef.current = null; }
      if (micStreamRef.current) micStreamRef.current.getTracks().forEach((t) => t.stop());
      if (micProcRef.current) { try { micProcRef.current.disconnect(); } catch { /* noop */ } }
      if (micCtxRef.current) micCtxRef.current.close();
      micStreamRef.current = null;
      micProcRef.current = null;
      micCtxRef.current = null;
      demoCallRef.current = null;
      setRecording(false);
    }
  };

  // Determine State based on live scenario thresholds
  const computedState = useMemo(() => {
    if (score >= scenario.block) return 'BLOCK';
    if (score >= scenario.escalate) return 'ESCALATE';
    return 'OK';
  }, [score, scenario]);

  const isAlert = score >= scenario.escalate;

  // 1. Establish WebSocket Connection to ws://localhost:8000/ws
  useEffect(() => {
    let reconnectTimeout = null;
    let isSubscribed = true;

    const connectWebSocket = () => {
      try {
        const apiHost = import.meta.env.VITE_API_URL || `${window.location.protocol === 'https:' ? 'https' : 'http'}://${window.location.hostname}:8000`;
        const ws = new WebSocket(`${apiHost.replace(/^http/, 'ws')}/ws`);
        wsRef.current = ws;

        ws.onopen = () => {
          if (!isSubscribed) return;
          console.log('[WebSocket] Connected to ws://localhost:8000/ws');
          setWsConnected(true);
        };

        ws.onmessage = (event) => {
          if (!isSubscribed) return;
          try {
            const data = JSON.parse(event.data);
            if (demoCallRef.current !== null && data.call_id !== demoCallRef.current) return;
            // Expected JSON: { call_id, t, score, layers: { acoustic, prosody, speaker }, context_flags, state }
            const currentT = Number(data.t || 0);
            const currentScore = Number(data.score || 0);

            setCallId(data.call_id || 'demo1');
            setElapsed(currentT);
            setScore(currentScore);
            scoreRef.current = currentScore;

            if (data.layers) {
              setLayers({
                acoustic: Number(data.layers.acoustic || 0),
                prosody: Number(data.layers.prosody || 0),
                speaker: Number(data.layers.speaker || 0)
              });
            }

            const incomingFlags = Array.isArray(data.context_flags) ? data.context_flags : [];
            setContextFlags(incomingFlags);

            // Record any newly observed context flags
            incomingFlags.forEach((flag) => {
              if (!seenFlagsRef.current.has(flag)) {
                seenFlagsRef.current.add(flag);
                setFlagHistory((prev) => [{ flag, t: currentT }, ...prev]);
              }
            });

            // Append to time-series history
            setHistory((prev) => {
              const updated = [...prev, { t: currentT, score: currentScore }];
              // Keep last 100 points for smooth rendering
              return updated.length > 100 ? updated.slice(updated.length - 100) : updated;
            });
          } catch (err) {
            console.error('[WebSocket] Failed to parse message:', err);
          }
        };

        ws.onclose = () => {
          if (!isSubscribed) return;
          console.warn('[WebSocket] Disconnected. Retrying in 1.5s...');
          setWsConnected(false);
          reconnectTimeout = setTimeout(connectWebSocket, 1500);
        };

        ws.onerror = () => {
          ws.close();
        };
      } catch (err) {
        console.error('[WebSocket] Connection attempt error:', err);
        reconnectTimeout = setTimeout(connectWebSocket, 1500);
      }
    };

    connectWebSocket();

    return () => {
      isSubscribed = false;
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  return (
    <div className="app-shell">
      <Header elapsed={elapsed} callId={callId} codec="16 kHz wideband PCM" wsConnected={wsConnected} scenario={selectedScenario} onScenarioChange={setSelectedScenario} scenarios={SCENARIOS} />
      <main className="app-main">
        <div className="page-intro">
          <div><p className="eyebrow">Fraud operations / live session</p><h1>Voice trust console</h1><p className="intro-copy">Detect synthetic speech before a high-value approval is released.</p></div>
          <div className="session-meta"><span className="live-dot" />{wsConnected ? 'Live scoring' : 'Reconnecting'}<span className="divider" />Session <strong>{callId}</strong></div>
        </div>
        <div className="live-control">
          <button className="text-button live-button" type="button" data-recording={recording} disabled={!wsConnected} onClick={toggleMic}>{recording ? '■ Stop live recording' : '● Start live recording'}</button>
          <span className={`demo-bar-hint${recording ? ' recording' : ''}`}>{recording ? 'Recording — speak now; the fused synthetic-risk score follows your voice.' : 'Evaluate the fused synthetic-risk score from a live microphone recording.'}</span>
        </div>
        <div className="decision-grid">
          <section className="signal-column">
            <ScoreChart score={score} displayScore={smoothScore} history={history} escalateThreshold={scenario.escalate} blockThreshold={scenario.block} state={computedState} />
            <WaveformDisplay isAlert={isAlert} />
          </section>
        </div>
        <div className="evidence-grid">
          <LayerBars layers={layers} isAlert={isAlert} />
          <ContextFlags activeFlags={contextFlags} flagHistory={flagHistory} />
        </div>
        <OperationsPanel />
      </main>
    </div>
  );
}
