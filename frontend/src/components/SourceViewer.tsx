import React, { useState, useEffect } from 'react';
import { Citation, SourceSegment } from '../types';
import { getSourceSegment, fetchSourceBlob } from '../api';

interface SourceViewerProps {
  selectedCaseId: string | null;
  activeCitation: Citation | null;
  onClose: () => void;
}

export const SourceViewer: React.FC<SourceViewerProps> = ({
  selectedCaseId,
  activeCitation,
  onClose,
}) => {
  const [segment, setSegment] = useState<SourceSegment | null>(null);
  const [pdfBlobUrl, setPdfBlobUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [viewFullPdf, setViewFullPdf] = useState<boolean>(false);

  // Manage revocable blob URL lifecycle for PDF documents
  useEffect(() => {
    return () => {
      if (pdfBlobUrl) {
        URL.revokeObjectURL(pdfBlobUrl);
      }
    };
  }, [pdfBlobUrl]);

  useEffect(() => {
    if (!selectedCaseId || !activeCitation) {
      setSegment(null);
      setError(null);
      setViewFullPdf(false);
      return;
    }

    let isMounted = true;
    setIsLoading(true);
    setError(null);
    setViewFullPdf(false);

    // Revoke previous blob url
    if (pdfBlobUrl) {
      URL.revokeObjectURL(pdfBlobUrl);
      setPdfBlobUrl(null);
    }

    async function loadSegment() {
      try {
        if (!selectedCaseId || !activeCitation) return;
        const seg = await getSourceSegment(
          selectedCaseId,
          activeCitation.source_id,
          activeCitation.locator_id
        );
        if (isMounted) {
          setSegment(seg);
        }
      } catch (err: unknown) {
        if (isMounted) {
          const msg = err instanceof Error ? err.message : 'Failed to load source segment';
          setError(msg);
        }
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    }

    loadSegment();

    return () => {
      isMounted = false;
    };
  }, [selectedCaseId, activeCitation]);

  const handleLoadFullPdf = async () => {
    if (!selectedCaseId || !activeCitation) return;
    setIsLoading(true);
    try {
      const { blob } = await fetchSourceBlob(selectedCaseId, activeCitation.source_id);
      const url = URL.createObjectURL(blob);
      setPdfBlobUrl(url);
      setViewFullPdf(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to fetch PDF blob';
      setError(msg);
    } finally {
      setIsLoading(false);
    }
  };

  if (!activeCitation) {
    return (
      <div className="card">
        <div className="card-title">
          <span>Source & Evidence Viewer</span>
        </div>
        <div style={{ fontSize: '12px', color: 'var(--text-dim)', textAlign: 'center', padding: '24px' }}>
          Click any citation in the investigation result to inspect source records, lines, and PDF locators.
        </div>
      </div>
    );
  }

  const isPdf = segment?.media_type === 'application/pdf';

  return (
    <div className="card">
      <div className="card-title">
        <span>Source Evidence: {activeCitation.label}</span>
        <button
          className="button-secondary"
          onClick={onClose}
          style={{ padding: '2px 8px', fontSize: '11px' }}
        >
          Close
        </button>
      </div>

      <div className="source-header-meta">
        <span><strong>Locator:</strong> <code className="font-mono">{activeCitation.locator_id}</code></span>
        <span><strong>Source ID:</strong> <code className="font-mono">{activeCitation.source_id.slice(0, 8)}…</code></span>
        {segment?.media_type && (
          <span className="badge badge-dim">{segment.media_type}</span>
        )}
        {segment?.page && <span>Page: {segment.page}</span>}
        {segment?.line_start && (
          <span>Lines: {segment.line_start}–{segment.line_end || segment.line_start}</span>
        )}
        {segment?.row_index && <span>CSV Row: {segment.row_index}</span>}

        {isPdf && !viewFullPdf && (
          <button
            className="button-secondary"
            onClick={handleLoadFullPdf}
            disabled={isLoading}
            style={{ fontSize: '11px', padding: '2px 8px', marginLeft: 'auto' }}
          >
            Open Complete PDF Document
          </button>
        )}
      </div>

      <div className="source-viewer-body">
        {isLoading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '16px' }}>
            <div className="spinner" />
            <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Retrieving grounded source segment…</span>
          </div>
        )}

        {error && (
          <div style={{ color: 'var(--status-red)', fontSize: '12px', padding: '10px' }}>
            ✕ {error}
          </div>
        )}

        {viewFullPdf && pdfBlobUrl && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '6px' }}>
              <button
                className="button-secondary"
                onClick={() => setViewFullPdf(false)}
                style={{ fontSize: '11px', padding: '2px 8px' }}
              >
                Back to Segment
              </button>
            </div>
            <iframe
              src={pdfBlobUrl}
              className="pdf-frame"
              title="PDF Evidence Document"
            />
          </div>
        )}

        {!viewFullPdf && segment && (
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-dim)', marginBottom: '4px' }}>
              Grounded Segment Text:
            </div>
            <pre className="source-content-pre">
              {segment.content}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
};
