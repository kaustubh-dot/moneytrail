import {
  CaseSummary,
  ImportResult,
  RunEventsResponse,
  RunStateResponse,
  SourceSegment,
  SourceSummary,
} from './types';

// Bearer token stays in memory only per security specification
let inMemoryToken: string = '';

export function setOwnerToken(token: string): void {
  inMemoryToken = token.trim();
}

export function getOwnerToken(): string {
  return inMemoryToken;
}

function getHeaders(customHeaders: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { ...customHeaders };
  if (inMemoryToken) {
    headers['Authorization'] = `Bearer ${inMemoryToken}`;
  }
  return headers;
}

export class ApiError extends Error {
  code: string;
  statusCode: number;
  details: Record<string, unknown>;

  constructor(message: string, code: string = 'ERROR', statusCode: number = 500, details: Record<string, unknown> = {}) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.statusCode = statusCode;
    this.details = details;
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let code = 'HTTP_' + res.status;
    let message = res.statusText || 'Request failed';
    let details: Record<string, unknown> = {};
    try {
      const data = await res.json();
      if (data && typeof data === 'object') {
        const errDetail = data.detail || data;
        code = errDetail.code || code;
        message = errDetail.message || message;
        details = errDetail.details || details;
      }
    } catch {
      // Non-JSON response body
    }
    throw new ApiError(message, code, res.status, details);
  }
  return res.json() as Promise<T>;
}

export async function checkHealth(): Promise<{ status: string }> {
  const res = await fetch('/healthz');
  return handleResponse<{ status: string }>(res);
}

export async function listCases(): Promise<CaseSummary[]> {
  const res = await fetch('/api/v1/cases', {
    headers: getHeaders(),
  });
  return handleResponse<CaseSummary[]>(res);
}

export async function getCase(caseId: string): Promise<CaseSummary & { sources: SourceSummary[] }> {
  const res = await fetch(`/api/v1/cases/${caseId}`, {
    headers: getHeaders(),
  });
  return handleResponse<CaseSummary & { sources: SourceSummary[] }>(res);
}

export async function importCaseBundle(file: File): Promise<ImportResult> {
  const formData = new FormData();
  formData.append('bundle', file);

  const res = await fetch('/api/v1/cases/import', {
    method: 'POST',
    headers: getHeaders(),
    body: formData,
  });
  return handleResponse<ImportResult>(res);
}

export async function startInvestigation(caseId: string, question: string): Promise<{ run_id: string; state: string }> {
  const res = await fetch('/api/v1/investigations', {
    method: 'POST',
    headers: getHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ case_id: caseId, question }),
  });
  return handleResponse<{ run_id: string; state: string }>(res);
}

export async function getRun(runId: string): Promise<RunStateResponse> {
  const res = await fetch(`/api/v1/runs/${runId}`, {
    headers: getHeaders(),
  });
  return handleResponse<RunStateResponse>(res);
}

export async function getRunEvents(runId: string, after: number = 0): Promise<RunEventsResponse> {
  const res = await fetch(`/api/v1/runs/${runId}/events?after=${after}`, {
    headers: getHeaders(),
  });
  return handleResponse<RunEventsResponse>(res);
}

export async function cancelRun(runId: string): Promise<{ run_id: string; state: string }> {
  const res = await fetch(`/api/v1/runs/${runId}/cancel`, {
    method: 'POST',
    headers: getHeaders(),
  });
  return handleResponse<{ run_id: string; state: string }>(res);
}

export async function getSourceSegment(
  caseId: string,
  sourceId: string,
  locatorId: string
): Promise<SourceSegment> {
  const params = new URLSearchParams({ locator_id: locatorId });
  const res = await fetch(`/api/v1/cases/${caseId}/sources/${sourceId}/segments?${params.toString()}`, {
    headers: getHeaders(),
  });
  return handleResponse<SourceSegment>(res);
}

export async function fetchSourceBlob(
  caseId: string,
  sourceId: string
): Promise<{ blob: Blob; mediaType: string; filename: string }> {
  const res = await fetch(`/api/v1/cases/${caseId}/sources/${sourceId}/content`, {
    headers: getHeaders(),
  });
  if (!res.ok) {
    let msg = 'Failed to fetch source content';
    let code = 'NOT_FOUND';
    try {
      const err = await res.json();
      msg = err.detail?.message || msg;
      code = err.detail?.code || code;
    } catch {
      // Ignore
    }
    throw new ApiError(msg, code, res.status);
  }
  const mediaType = res.headers.get('Content-Type') || 'application/octet-stream';
  const cd = res.headers.get('Content-Disposition') || '';
  const match = cd.match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : 'document';
  const blob = await res.blob();
  return { blob, mediaType, filename };
}
