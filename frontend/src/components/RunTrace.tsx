import React, { useEffect, useRef } from 'react';
import { RunEvent } from '../types';

interface RunTraceProps {
  events: RunEvent[];
  isRunning: boolean;
}

export const RunTrace: React.FC<RunTraceProps> = ({ events, isRunning }) => {
  const timelineEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isRunning && timelineEndRef.current) {
      timelineEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [events.length, isRunning]);

  if (events.length === 0) {
    return (
      <div className="card">
        <div className="card-title">
          <span>Execution Trace & Events</span>
        </div>
        <div style={{ fontSize: '12px', color: 'var(--text-dim)', textAlign: 'center', padding: '16px' }}>
          No execution events yet. Start an investigation to observe the step-by-step trace.
        </div>
      </div>
    );
  }

  const formatPayload = (payload: Record<string, unknown>): string => {
    try {
      const entries = Object.entries(payload);
      if (entries.length === 0) return '';
      return entries
        .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
        .join(', ');
    } catch {
      return '';
    }
  };

  return (
    <div className="card">
      <div className="card-title">
        <span>Execution Trace ({events.length} events)</span>
        {isRunning && (
          <span style={{ fontSize: '11px', color: 'var(--accent-blue)', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <div className="spinner" style={{ width: '10px', height: '10px' }} />
            Streaming events…
          </span>
        )}
      </div>

      <div className="trace-timeline">
        {events.map((ev) => (
          <div key={`${ev.run_id}-${ev.sequence}`} className={`trace-event kind-${ev.kind}`}>
            <span className="trace-seq">#{ev.sequence}</span>
            <span className="trace-time">+{ev.elapsed_ms}ms</span>
            <span className="trace-kind">{ev.kind}</span>
            <span className="trace-payload" title={JSON.stringify(ev.payload, null, 2)}>
              {formatPayload(ev.payload)}
            </span>
          </div>
        ))}
        <div ref={timelineEndRef} />
      </div>
    </div>
  );
};
