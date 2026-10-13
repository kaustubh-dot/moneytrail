import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  CaseSummary,
  Citation,
  RunEvent,
  RunStateResponse,
} from './types';
import {
  checkHealth,
  listCases,
  startInvestigation,
  getRun,
  getRunEvents,
  cancelRun,
  setOwnerToken,
} from './api';
import { ImportPanel } from './components/ImportPanel';
import { InvestigationPanel } from './components/InvestigationPanel';
import { SourceViewer } from './components/SourceViewer';
import { RunTrace } from './components/RunTrace';
import './styles.css';

export const App: React.FC = () => {
  // Authentication & System State
  const [ownerTokenInput, setOwnerTokenInput] = useState<string>('');
  const [isHealthOk, setIsHealthOk] = useState<boolean | null>(null);

  // Cases State
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [isLoadingCases, setIsLoadingCases] = useState<boolean>(false);

  // Investigation Run State
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<RunStateResponse | null>(null);
  const [runEvents, setRunEvents] = useState<RunEvent[]>([]);
  const [isRunning, setIsRunning] = useState<boolean>(false);

  // Source Viewer State
  const [activeCitation, setActiveCitation] = useState<Citation | null>(null);
  const [activeTab, setActiveTab] = useState<'source' | 'trace'>('source');

  // Polling cursor ref
  const eventsCursorRef = useRef<number>(0);

  // Update in-memory token whenever input changes
  const handleTokenChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setOwnerTokenInput(val);
    setOwnerToken(val);
  };

  // Health check on initial mount
  useEffect(() => {
    checkHealth()
      .then(() => setIsHealthOk(true))
      .catch(() => setIsHealthOk(false));
  }, []);

  // Fetch case list
  const fetchCases = useCallback(async () => {
    setIsLoadingCases(true);
    try {
      const data = await listCases();
      setCases(data);
      if (data.length > 0 && !selectedCaseId) {
        setSelectedCaseId(data[0].case_id);
      }
    } catch {
      // Token may be missing or invalid
    } finally {
      setIsLoadingCases(false);
    }
  }, [selectedCaseId]);

  useEffect(() => {
    fetchCases();
  }, [fetchCases]);

  // Polling loop for active investigation
  useEffect(() => {
    if (!isRunning || !activeRunId) return;

    let isSubscribed = true;

    const pollInterval = setInterval(async () => {
      try {
        const eventsRes = await getRunEvents(activeRunId, eventsCursorRef.current);
        if (!isSubscribed) return;

        if (eventsRes.events.length > 0) {
          setRunEvents((prev) => [...prev, ...eventsRes.events]);
          eventsCursorRef.current = eventsRes.next_after;
        }

        if (eventsRes.terminal) {
          clearInterval(pollInterval);
          const finalRun = await getRun(activeRunId);
          if (isSubscribed) {
            setActiveRun(finalRun);
            setIsRunning(false);
          }
        }
      } catch (err) {
        console.error('Polling error:', err);
      }
    }, 1000);

    return () => {
      isSubscribed = false;
      clearInterval(pollInterval);
    };
  }, [isRunning, activeRunId]);

  // Start investigation handler
  const handleStartInvestigation = async (question: string) => {
    if (!selectedCaseId || isRunning) return;

    eventsCursorRef.current = 0;
    setRunEvents([]);
    setActiveCitation(null);

    try {
      const startRes = await startInvestigation(selectedCaseId, question);
      setActiveRunId(startRes.run_id);
      setIsRunning(true);
      setActiveTab('trace');

      const initialRun = await getRun(startRes.run_id);
      setActiveRun(initialRun);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to launch investigation';
      setActiveRun({
        run_id: 'error',
        case_id: selectedCaseId,
        state: 'failed',
        error: { code: 'START_FAILED', message: msg },
      });
      setIsRunning(false);
    }
  };

  // Cancel investigation handler
  const handleCancelRun = async () => {
    if (!activeRunId) return;
    try {
      await cancelRun(activeRunId);
      setActiveRun((prev) => (prev ? { ...prev, state: 'cancelled' } : null));
      setIsRunning(false);
    } catch (err) {
      console.error('Cancel failed:', err);
    }
  };

  const handleSelectCitation = (citation: Citation) => {
    setActiveCitation(citation);
    setActiveTab('source');
  };

  return (
    <div className="app-container">
      {/* Top Header */}
      <header className="app-header">
        <div className="brand">
          <span className="brand-icon">🔍</span>
          <div>
            <div className="brand-title">MoneyTrail</div>
            <div className="brand-subtitle">
              Merchant Payment Discrepancy Investigator • Pure Integer Accounting
            </div>
          </div>
        </div>

        <div className="auth-bar">
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span
              style={{
                width: '8px',
                height: '8px',
                borderRadius: '50%',
                backgroundColor: isHealthOk ? 'var(--status-green)' : 'var(--status-red)',
              }}
            />
            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              {isHealthOk ? 'API Connected' : 'Connecting…'}
            </span>
          </div>

          <label className="auth-label" htmlFor="owner-token-input">
            Owner Token:
          </label>
          <input
            id="owner-token-input"
            type="password"
            className="auth-input"
            placeholder="Paste owner bearer token…"
            value={ownerTokenInput}
            onChange={handleTokenChange}
          />
          <button
            type="button"
            className="button-secondary"
            onClick={fetchCases}
            style={{ fontSize: '12px' }}
          >
            Authenticate
          </button>
        </div>
      </header>

      {/* Main Two-Column Layout */}
      <div className="main-layout">
        {/* Left Sidebar: Cases & Uploads */}
        <aside className="sidebar">
          <ImportPanel
            cases={cases}
            selectedCaseId={selectedCaseId}
            onSelectCase={(id) => setSelectedCaseId(id)}
            onRefreshCases={fetchCases}
            isLoading={isLoadingCases}
          />
        </aside>

        {/* Center & Right Content: Investigation & Inspection */}
        <main className="content-area">
          <InvestigationPanel
            selectedCaseId={selectedCaseId}
            activeRun={activeRun}
            isRunning={isRunning}
            onStartInvestigation={handleStartInvestigation}
            onCancelRun={handleCancelRun}
            onSelectCitation={handleSelectCitation}
          />

          {/* Lower Tabs: Evidence Viewer & Execution Trace */}
          <div className="card">
            <div style={{ display: 'flex', borderBottom: '1px solid var(--border-color)', marginBottom: '12px', gap: '8px' }}>
              <button
                type="button"
                className={`button-secondary ${activeTab === 'source' ? 'button-primary' : ''}`}
                style={{ borderRadius: '4px 4px 0 0', borderBottom: 'none', fontSize: '12px' }}
                onClick={() => setActiveTab('source')}
              >
                📄 Source & Locator Evidence {activeCitation ? `(${activeCitation.locator_id})` : ''}
              </button>
              <button
                type="button"
                className={`button-secondary ${activeTab === 'trace' ? 'button-primary' : ''}`}
                style={{ borderRadius: '4px 4px 0 0', borderBottom: 'none', fontSize: '12px' }}
                onClick={() => setActiveTab('trace')}
              >
                ⏱️ Execution Trace ({runEvents.length} events)
              </button>
            </div>

            {activeTab === 'source' ? (
              <SourceViewer
                selectedCaseId={selectedCaseId}
                activeCitation={activeCitation}
                onClose={() => setActiveCitation(null)}
              />
            ) : (
              <RunTrace events={runEvents} isRunning={isRunning} />
            )}
          </div>
        </main>
      </div>
    </div>
  );
};
