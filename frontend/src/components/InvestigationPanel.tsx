import React, { useState } from 'react';
import {
  Citation,
  Fact,
  InvestigationResult,
  ReconciliationTotals,
  RunStateResponse,
} from '../types';

interface InvestigationPanelProps {
  selectedCaseId: string | null;
  activeRun: RunStateResponse | null;
  isRunning: boolean;
  onStartInvestigation: (question: string) => void;
  onCancelRun: () => void;
  onSelectCitation: (citation: Citation) => void;
}

const PRESET_QUESTIONS = [
  'Why did we receive INR 186,000 instead of our gross sales for the September cohort?',
  'Why did we receive less than gross sales for the September cohort?',
  'Why has order O-005 not been paid out yet?',
  'What fee rate was deducted from the September orders?',
];

function formatPaiseAmount(paise: number | undefined | null, currency: string = 'INR'): string {
  if (paise === undefined || paise === null) return '—';
  const sign = paise < 0 ? '-' : '';
  const abs = Math.abs(paise);
  const rupees = Math.floor(abs / 100);
  const rem = abs % 100;
  if (rem === 0) {
    return `${sign}${currency} ${rupees.toLocaleString()}`;
  }
  return `${sign}${currency} ${rupees.toLocaleString()}.${rem.toString().padStart(2, '0')}`;
}

export const InvestigationPanel: React.FC<InvestigationPanelProps> = ({
  selectedCaseId,
  activeRun,
  isRunning,
  onStartInvestigation,
  onCancelRun,
  onSelectCitation,
}) => {
  const [question, setQuestion] = useState(PRESET_QUESTIONS[0]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!question.trim() || isRunning || !selectedCaseId) return;
    onStartInvestigation(question.trim());
  };

  const result: InvestigationResult | null | undefined = activeRun?.result;
  const recon: ReconciliationTotals | null | undefined = result?.reconciliation;
  const isFailed = activeRun?.state === 'failed';
  const isCancelled = activeRun?.state === 'cancelled';

  return (
    <div className="card">
      <div className="card-title">
        <span>Investigation Query</span>
        {activeRun && (
          <span
            className={`badge ${
              activeRun.state === 'completed'
                ? 'badge-green'
                : activeRun.state === 'running'
                ? 'badge-blue'
                : activeRun.state === 'cancelled'
                ? 'badge-dim'
                : 'badge-red'
            }`}
          >
            {activeRun.state}
          </span>
        )}
      </div>

      <form onSubmit={handleSubmit} className="question-box">
        <textarea
          className="question-textarea"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask a natural-language discrepancy question (e.g., Why did we receive less than gross sales?)"
          disabled={isRunning || !selectedCaseId}
        />

        <div className="question-presets">
          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Examples:</span>
          {PRESET_QUESTIONS.map((q, idx) => (
            <button
              key={idx}
              type="button"
              className="preset-chip"
              onClick={() => setQuestion(q)}
              disabled={isRunning || !selectedCaseId}
            >
              {q.length > 40 ? q.slice(0, 40) + '…' : q}
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '4px' }}>
          {isRunning ? (
            <button
              type="button"
              className="button-danger"
              onClick={onCancelRun}
            >
              Cancel Investigation
            </button>
          ) : (
            <button
              type="submit"
              className="button-primary"
              disabled={!selectedCaseId || !question.trim()}
            >
              Start Investigation
            </button>
          )}
        </div>
      </form>

      {/* Live Running Banner */}
      {isRunning && (
        <div className="progress-banner" style={{ backgroundColor: 'var(--status-blue-bg)' }}>
          <div className="progress-info">
            <div className="spinner" />
            <div>
              <div style={{ fontWeight: 600, color: '#fff' }}>Investigation in progress…</div>
              <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                Deterministic reconciliation → policy lookup → evidence retrieval → model synthesis
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Failure Banner */}
      {isFailed && (
        <div className="progress-banner" style={{ backgroundColor: 'var(--status-red-bg)' }}>
          <div>
            <div style={{ fontWeight: 600, color: 'var(--status-red)' }}>
              Investigation Failed [{activeRun?.error?.code || 'ERROR'}]
            </div>
            <div style={{ fontSize: '12px', color: '#fff', marginTop: '2px' }}>
              {activeRun?.error?.message || 'An error occurred during investigation'}
            </div>
          </div>
        </div>
      )}

      {/* Cancelled Banner */}
      {isCancelled && (
        <div className="progress-banner" style={{ backgroundColor: 'rgba(100, 116, 139, 0.2)' }}>
          <div style={{ color: 'var(--text-muted)' }}>
            Investigation was cancelled by user.
          </div>
        </div>
      )}

      {/* Investigation Results */}
      {result && (
        <div style={{ marginTop: '20px' }}>
          {/* Outcome Header */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '12px' }}>
            <span style={{ fontSize: '15px', fontWeight: 700, color: '#fff' }}>Investigation Result:</span>
            <span
              className={`badge ${
                result.outcome === 'explained'
                  ? 'badge-green'
                  : result.outcome === 'partial'
                  ? 'badge-amber'
                  : 'badge-red'
              }`}
              style={{ fontSize: '13px', padding: '4px 10px' }}
            >
              {result.outcome}
            </span>
          </div>

          {/* Financial Reconciliation Grid */}
          {recon && (
            <div style={{ marginBottom: '16px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '6px' }}>
                Cohort Financial Reconciliation
              </div>
              <div className="recon-grid">
                <div className="recon-item">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2px' }}>
                    <div className="recon-label">Gross Sales</div>
                    {recon.gross_complete === false && (
                      <span className="badge badge-amber" style={{ fontSize: '10px', padding: '1px 5px' }}>
                        Known records only
                      </span>
                    )}
                  </div>
                  <div className="recon-value">{formatPaiseAmount(recon.gross_paise)}</div>
                </div>
                <div className="recon-item">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2px' }}>
                    <div className="recon-label">Completed Refunds</div>
                    {recon.refunds_complete === false && (
                      <span className="badge badge-amber" style={{ fontSize: '10px', padding: '1px 5px' }}>
                        Known records only
                      </span>
                    )}
                  </div>
                  <div className="recon-value" style={{ color: 'var(--status-amber)' }}>
                    -{formatPaiseAmount(recon.completed_refunds_paise)}
                  </div>
                </div>
                <div className="recon-item">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2px' }}>
                    <div className="recon-label">Assessed Fees</div>
                    {recon.fees_complete === false && (
                      <span className="badge badge-amber" style={{ fontSize: '10px', padding: '1px 5px' }}>
                        Known records only
                      </span>
                    )}
                  </div>
                  <div className="recon-value" style={{ color: 'var(--status-amber)' }}>
                    -{formatPaiseAmount(recon.assessed_fees_paise)}
                  </div>
                </div>
                <div className="recon-item">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2px' }}>
                    <div className="recon-label">Expected Net</div>
                    {(recon.gross_complete === false || recon.refunds_complete === false || recon.fees_complete === false) && (
                      <span className="badge badge-amber" style={{ fontSize: '10px', padding: '1px 5px' }}>
                        Known records only
                      </span>
                    )}
                  </div>
                  <div className="recon-value" style={{ color: 'var(--accent-blue)' }}>
                    {formatPaiseAmount(recon.expected_net_paise)}
                  </div>
                </div>
                <div className="recon-item">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2px' }}>
                    <div className="recon-label">Received Payout</div>
                    {recon.received_complete === false && (
                      <span className="badge badge-amber" style={{ fontSize: '10px', padding: '1px 5px' }}>
                        Known records only
                      </span>
                    )}
                  </div>
                  <div className="recon-value" style={{ color: 'var(--status-green)' }}>
                    {formatPaiseAmount(recon.received_allocations_paise)}
                  </div>
                </div>
                <div className="recon-item">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2px' }}>
                    <div className="recon-label">Documented Pending</div>
                    {recon.pending_complete === false && (
                      <span className="badge badge-amber" style={{ fontSize: '10px', padding: '1px 5px' }}>
                        Known records only
                      </span>
                    )}
                  </div>
                  <div className="recon-value" style={{ color: 'var(--accent-cyan)' }}>
                    {formatPaiseAmount(recon.documented_pending_paise)}
                  </div>
                </div>
                <div className="recon-item">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2px' }}>
                    <div className="recon-label">Unexplained Diff</div>
                    {(recon.gross_complete === false || recon.refunds_complete === false || recon.fees_complete === false || recon.received_complete === false || recon.pending_complete === false) && (
                      <span className="badge badge-amber" style={{ fontSize: '10px', padding: '1px 5px' }}>
                        Known records only
                      </span>
                    )}
                  </div>
                  <div
                    className={`recon-value ${
                      recon.unexplained_difference_paise === 0 &&
                      recon.gross_complete !== false &&
                      recon.refunds_complete !== false &&
                      recon.fees_complete !== false &&
                      recon.received_complete !== false &&
                      recon.pending_complete !== false
                        ? 'diff-zero'
                        : recon.unexplained_difference_paise === 0
                        ? 'diff-incomplete'
                        : 'diff-nonzero'
                    }`}
                  >
                    {formatPaiseAmount(recon.unexplained_difference_paise)}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Grounded Narrative Claims */}
          <div style={{ marginBottom: '16px' }}>
            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '6px' }}>
              Grounded Narrative & Findings ({result.claims.length} claims)
            </div>
            <div className="claims-container">
              {result.claims.map((claim, idx) => (
                <div key={idx} className="claim-card">
                  <div className="claim-text">{claim.text || claim.text_template}</div>
                  {claim.citations && claim.citations.length > 0 && (
                    <div className="claim-footer">
                      <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>Citations:</span>
                      {claim.citations.map((cit, cIdx) => (
                        <button
                          key={cIdx}
                          type="button"
                          className="citation-chip"
                          onClick={() => onSelectCitation(cit)}
                          title={`Source: ${cit.source_id} | Locator: ${cit.locator_id}`}
                        >
                          📎 {cit.label} ({cit.locator_id})
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Unresolved Gaps */}
          {result.unresolved && result.unresolved.length > 0 && (
            <div style={{ marginBottom: '16px', backgroundColor: 'var(--status-amber-bg)', padding: '12px', borderRadius: '6px' }}>
              <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--status-amber)', marginBottom: '4px' }}>
                ⚠️ Unresolved Evidence Gaps ({result.unresolved.length})
              </div>
              <ul style={{ paddingLeft: '20px', fontSize: '12px', color: '#fff' }}>
                {result.unresolved.map((gap, gIdx) => (
                  <li key={gIdx}>
                    <strong>[{gap.code}]</strong>: {gap.needed_evidence}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Registered Facts Table */}
          {result.selected_facts && result.selected_facts.length > 0 && (
            <div>
              <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '6px' }}>
                Registered Grounded Facts ({result.selected_facts.length})
              </div>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Fact ID</th>
                      <th>Kind</th>
                      <th>Value</th>
                      <th>Status</th>
                      <th>Citations</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.selected_facts.map((fact: Fact) => (
                      <tr key={fact.fact_id}>
                        <td className="font-mono" style={{ color: 'var(--accent-blue)' }}>
                          {fact.fact_id}
                        </td>
                        <td>{fact.kind}</td>
                        <td className="font-mono">
                          {fact.kind === 'money'
                            ? formatPaiseAmount(fact.value_paise)
                            : String(fact.value)}
                        </td>
                        <td>
                          <span className={`badge ${fact.complete ? 'badge-green' : 'badge-amber'}`}>
                            {fact.complete ? 'complete' : 'gap'}
                          </span>
                        </td>
                        <td>
                          {fact.citations.map((c, i) => (
                            <button
                              key={i}
                              type="button"
                              className="citation-chip"
                              style={{ marginRight: '4px', marginBottom: '2px' }}
                              onClick={() => onSelectCitation(c)}
                            >
                              {c.locator_id}
                            </button>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
