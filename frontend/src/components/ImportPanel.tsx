import React, { useState, useRef } from 'react';
import { CaseSummary, ImportResult } from '../types';
import { importCaseBundle } from '../api';

interface ImportPanelProps {
  cases: CaseSummary[];
  selectedCaseId: string | null;
  onSelectCase: (caseId: string) => void;
  onRefreshCases: () => void;
  isLoading: boolean;
}

export const ImportPanel: React.FC<ImportPanelProps> = ({
  cases,
  selectedCaseId,
  onSelectCase,
  onRefreshCases,
  isLoading,
}) => {
  const [isUploading, setIsUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const selectedCase = cases.find((c) => c.case_id === selectedCaseId);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.name.endsWith('.zip')) {
      setUploadError('Only .zip bundles are supported');
      return;
    }

    setIsUploading(true);
    setUploadMessage(null);
    setUploadError(null);

    try {
      const res: ImportResult = await importCaseBundle(file);
      setUploadMessage(
        res.reused
          ? `Bundle already exists. Reused case "${res.case_key}".`
          : `Successfully imported "${res.case_key}" (${res.orders_count} orders, ${res.sources_count} sources).`
      );
      onRefreshCases();
      onSelectCase(res.case_id);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Upload failed';
      setUploadError(msg);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  return (
    <div className="card">
      <div className="card-title">
        <span>Cases & Uploads</span>
        <button
          className="button-secondary"
          onClick={onRefreshCases}
          disabled={isLoading}
          style={{ padding: '2px 8px', fontSize: '11px' }}
        >
          {isLoading ? '...' : 'Refresh'}
        </button>
      </div>

      <div style={{ marginBottom: '16px' }}>
        <input
          type="file"
          ref={fileInputRef}
          onChange={handleFileUpload}
          accept=".zip"
          style={{ display: 'none' }}
        />
        <div
          className="dropzone"
          onClick={() => !isUploading && fileInputRef.current?.click()}
        >
          {isUploading ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px' }}>
              <div className="spinner" />
              <span className="dropzone-text">Importing bundle...</span>
            </div>
          ) : (
            <div>
              <div style={{ fontSize: '20px', marginBottom: '4px' }}>📦</div>
              <div className="dropzone-text">Click to upload .zip case bundle</div>
            </div>
          )}
        </div>

        {uploadMessage && (
          <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--status-green)' }}>
            ✓ {uploadMessage}
          </div>
        )}
        {uploadError && (
          <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--status-red)' }}>
            ✕ {uploadError}
          </div>
        )}
      </div>

      <div className="card-title" style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
        Available Cases ({cases.length})
      </div>

      <div className="case-list">
        {cases.length === 0 ? (
          <div style={{ fontSize: '12px', color: 'var(--text-dim)', textAlign: 'center', padding: '16px' }}>
            No cases loaded yet. Upload a bundle above.
          </div>
        ) : (
          cases.map((c) => (
            <div
              key={c.case_id}
              className={`case-item ${c.case_id === selectedCaseId ? 'selected' : ''}`}
              onClick={() => onSelectCase(c.case_id)}
            >
              <div className="case-header">
                <span className="case-key">{c.case_key}</span>
                <span className={`badge ${c.status === 'ready' ? 'badge-green' : 'badge-amber'}`}>
                  {c.status}
                </span>
              </div>
              <div className="case-meta">
                Merchant: {c.merchant_id} ({c.currency})
              </div>
              <div className="case-meta">
                Cutoff (as_of): {new Date(c.as_of).toLocaleDateString()}
              </div>
            </div>
          ))
        )}
      </div>

      {selectedCase && (
        <div style={{ marginTop: '16px', borderTop: '1px solid var(--border-color)', paddingTop: '12px' }}>
          <div style={{ fontSize: '12px', fontWeight: 600, color: '#fff', marginBottom: '6px' }}>
            Evidence Completeness
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
            {Object.entries(selectedCase.completeness || {}).map(([key, val]) => (
              <span
                key={key}
                className={`badge ${val === 'complete' ? 'badge-dim' : 'badge-amber'}`}
                style={{ fontSize: '10px' }}
              >
                {key}: {val}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
