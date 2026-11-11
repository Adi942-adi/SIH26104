import React, { useEffect, useRef } from 'react';
import WaveSurfer from 'wavesurfer.js';

export default function WaveformDisplay({ isAlert = false }) {
  const containerRef = useRef(null);
  const wavesurferRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return;

    // Create a synthesized demo audio waveform using Web Audio API / canvas peaks
    const ws = WaveSurfer.create({
      container: containerRef.current,
      height: 48,
      waveColor: '#8A8680',
      progressColor: isAlert ? '#C2410C' : '#8A8680',
      cursorColor: 'transparent',
      barWidth: 2,
      barGap: 2,
      barRadius: 1,
      normalize: true,
      interact: false
    });

    wavesurferRef.current = ws;

    // Generate initial synthetic audio frame peaks (16 kHz wideband representation)
    const generatePeaks = () => {
      const peaks = [];
      const numBars = 120;
      for (let i = 0; i < numBars; i++) {
        // Natural speech cadence modulation
        const modulation = Math.sin(i / 8) * Math.cos(i / 14);
        const noise = (Math.random() - 0.5) * 0.4;
        const peak = Math.max(0.05, Math.min(0.95, Math.abs(modulation + noise)));
        peaks.push(peak);
      }
      return peaks;
    };

    // Load peaks into wavesurfer
    try {
      ws.load('', [generatePeaks()], 10);
    } catch (err) {
      console.warn('Wavesurfer peak loading:', err);
    }

    // Interval to gently pulse waveform representing incoming live frames
    const interval = setInterval(() => {
      if (wavesurferRef.current) {
        try {
          wavesurferRef.current.load('', [generatePeaks()], 10);
        } catch {
          // ignore
        }
      }
    }, 1000);

    return () => {
      clearInterval(interval);
      try {
        ws.destroy();
      } catch {
        // ignore
      }
    };
  }, []);

  return (
    <div style={{
      backgroundColor: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: '6px',
      padding: '16px 20px',
      display: 'flex',
      flexDirection: 'column',
      gap: '8px'
    }}>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        fontSize: '12px',
        color: 'var(--text-muted)'
      }}>
<span style={{ fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
          Live Ingest Stream (16 kHz mono · 10 ms / 320 B frames)
        </span>
        <span style={{ fontFamily: 'var(--font-mono)' }}>
          Wideband 16 kHz → AASIST native input
        </span>
      </div>

      <div
        ref={containerRef}
        style={{
          width: '100%',
          height: '48px',
          backgroundColor: 'var(--bg)',
          borderRadius: '4px',
          overflow: 'hidden'
        }}
      />
    </div>
  );
}
