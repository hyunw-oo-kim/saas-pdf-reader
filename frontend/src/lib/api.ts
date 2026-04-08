/**
 * Backend API client utility.
 *
 * Provides typed helpers for calling the FastAPI backend.
 * Base URL is read from NEXT_PUBLIC_API_URL env variable.
 *
 * Features:
 * - JWT 토큰 자동 첨부 (Bearer token from localStorage)
 * - 토큰 만료 시 자동 리프레시 (TOKEN_EXPIRED → /api/auth/refresh → retry)
 * - 리프레시 실패 시 /login 리다이렉트
 * - 공통 오류 응답 형식 처리
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ── Types ─────────────────────────────────────────────────────────

export interface DocumentViewResponse {
  sas_url: string;
  expires_at: string; // ISO 8601
}

export interface ApiError {
  error: {
    code: string;
    message: string;
    details?: unknown;
    timestamp?: string;
    request_id?: string;
  };
}

export interface AuthTokenResponse {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  token_type?: string;
}

// ── Token helpers ─────────────────────────────────────────────────

function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("access_token");
}

function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("refresh_token");
}

function setTokens(access: string, refresh: string): void {
  localStorage.setItem("access_token", access);
  localStorage.setItem("refresh_token", refresh);
}

function clearTokens(): void {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
}

function redirectToLogin(): void {
  if (typeof window !== "undefined") {
    clearTokens();
    window.location.href = "/login";
  }
}

// ── Refresh logic (singleton to avoid concurrent refreshes) ──────

let refreshPromise: Promise<boolean> | null = null;

async function tryRefreshToken(): Promise<boolean> {
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    const refreshToken = getRefreshToken();
    if (!refreshToken) return false;

    try {
      const res = await fetch(`${API_BASE}/api/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });

      if (!res.ok) return false;

      const data = (await res.json()) as AuthTokenResponse;
      setTokens(data.access_token, data.refresh_token);
      return true;
    } catch {
      return false;
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

// ── Core request function ─────────────────────────────────────────

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;

  // Build headers with JWT token auto-attach
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };

  const token = getAccessToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(url, { ...init, headers });

  // Handle 401 with TOKEN_EXPIRED → try refresh → retry once
  if (res.status === 401) {
    const body = await res.json().catch(() => null);
    const errorCode = (body as ApiError)?.error?.code;

    if (errorCode === "TOKEN_EXPIRED") {
      const refreshed = await tryRefreshToken();
      if (refreshed) {
        // Retry with new token
        const newToken = getAccessToken();
        const retryHeaders: Record<string, string> = { ...headers };
        if (newToken) {
          retryHeaders["Authorization"] = `Bearer ${newToken}`;
        }
        const retryRes = await fetch(url, { ...init, headers: retryHeaders });
        if (!retryRes.ok) {
          const retryBody = await retryRes.json().catch(() => null);
          const message =
            (retryBody as ApiError)?.error?.message ?? `Request failed: ${retryRes.status}`;
          throw new Error(message);
        }
        if (retryRes.status === 204 || retryRes.headers.get("content-length") === "0") {
          return undefined as T;
        }
        return retryRes.json() as Promise<T>;
      }
      // Refresh failed → redirect to login
      redirectToLogin();
      throw new Error("세션이 만료되었습니다. 다시 로그인해주세요");
    }

    // Other 401 errors (AUTH_REQUIRED, INVALID_TOKEN, etc.)
    const message = (body as ApiError)?.error?.message ?? "인증이 필요합니다";
    throw new Error(message);
  }

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const message =
      (body as ApiError)?.error?.message ?? `Request failed: ${res.status}`;
    throw new Error(message);
  }

  // 204 No Content 등 body가 없는 응답 처리
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as T;
  }

  return res.json() as Promise<T>;
}

// ── Document View API ─────────────────────────────────────────────

/** GET /api/documents/{id}/view — SAS Token URL 조회 */
export function fetchDocumentView(
  documentId: string,
): Promise<DocumentViewResponse> {
  return request<DocumentViewResponse>(
    `/api/documents/${documentId}/view`,
  );
}

// ── Annotation API ────────────────────────────────────────────────

export interface AnnotationResponse {
  document_id: string;
  xfdf_data: string;
  updated_at: string; // ISO 8601
}

/** GET /api/documents/{id}/annotations — 저장된 XFDF 주석 조회 */
export function fetchAnnotations(
  documentId: string,
): Promise<AnnotationResponse> {
  return request<AnnotationResponse>(
    `/api/documents/${documentId}/annotations`,
  );
}

/** PUT /api/documents/{id}/annotations — XFDF 주석 저장 */
export function saveAnnotations(
  documentId: string,
  xfdfData: string,
): Promise<AnnotationResponse> {
  return request<AnnotationResponse>(
    `/api/documents/${documentId}/annotations`,
    {
      method: "PUT",
      body: JSON.stringify({ xfdf_data: xfdfData }),
    },
  );
}

// ── OCR API ───────────────────────────────────────────────────────

export interface OCRJobResponse {
  job_id: string;
  status: "queued" | "processing" | "completed" | "failed";
}

export interface OCRStatusResponse {
  job_id: string;
  status: "queued" | "processing" | "completed" | "failed";
  progress_percent: number | null;
  error_message: string | null;
}

export interface OCRWord {
  text: string;
  bounding_box: number[];
  confidence: number;
}

export interface OCRPageResult {
  page_number: number;
  text: string;
  words: OCRWord[];
}

export interface OCRResultResponse {
  document_id: string;
  pages: OCRPageResult[];
}

/** POST /api/documents/{id}/ocr — OCR 작업 시작 */
export function startOCR(documentId: string): Promise<OCRJobResponse> {
  return request<OCRJobResponse>(`/api/documents/${documentId}/ocr`, {
    method: "POST",
  });
}

/** GET /api/documents/{id}/ocr/status — OCR 진행 상태 조회 */
export function getOCRStatus(documentId: string): Promise<OCRStatusResponse> {
  return request<OCRStatusResponse>(
    `/api/documents/${documentId}/ocr/status`,
  );
}

/** GET /api/documents/{id}/ocr/result — OCR 결과 조회 */
export function getOCRResult(documentId: string): Promise<OCRResultResponse> {
  return request<OCRResultResponse>(
    `/api/documents/${documentId}/ocr/result`,
  );
}

// ── Share API ─────────────────────────────────────────────────────

export interface ShareLinkResponse {
  share_id: string;
  share_url: string;
  expires_at: string; // ISO 8601
  permission: string;
}

export interface SharedDocumentResponse {
  sas_url: string;
  expires_at: string; // ISO 8601
  permission: string;
  filename: string;
}

/** POST /api/documents/{id}/share — 공유 링크 생성 */
export function createShareLink(
  documentId: string,
  expiry: "1h" | "1d" | "7d" | "30d",
  permission: "read_only" | "annotate",
): Promise<ShareLinkResponse> {
  return request<ShareLinkResponse>(
    `/api/documents/${documentId}/share`,
    {
      method: "POST",
      body: JSON.stringify({ expiry, permission }),
    },
  );
}

/** GET /api/documents/{id}/share — 공유 링크 목록 조회 */
export function listShareLinks(
  documentId: string,
): Promise<ShareLinkResponse[]> {
  return request<ShareLinkResponse[]>(
    `/api/documents/${documentId}/share`,
  );
}

/** DELETE /api/documents/{id}/share/{share_id} — 공유 링크 취소 */
export async function revokeShareLink(
  documentId: string,
  shareId: string,
): Promise<void> {
  await request<Record<string, never>>(
    `/api/documents/${documentId}/share/${shareId}`,
    { method: "DELETE" },
  );
}

/** GET /api/shared/{share_token} — 공유 문서 접근 (인증 불필요) */
export function accessSharedDocument(
  shareToken: string,
): Promise<SharedDocumentResponse> {
  return request<SharedDocumentResponse>(`/api/shared/${shareToken}`);
}

// ── Auth API ──────────────────────────────────────────────────────

/** POST /api/auth/callback — Okta OIDC 콜백 처리 → JWT 발급 */
export function authCallback(
  code: string,
  redirectUri: string,
): Promise<AuthTokenResponse> {
  return request<AuthTokenResponse>("/api/auth/callback", {
    method: "POST",
    body: JSON.stringify({ code, redirect_uri: redirectUri }),
  });
}

/** POST /api/auth/logout — 세션 종료 */
export function authLogout(): Promise<{ message: string }> {
  return request<{ message: string }>("/api/auth/logout", { method: "POST" });
}

// ── Document List / CRUD API ──────────────────────────────────────

export interface DocumentMeta {
  id: string;
  filename: string;
  size_bytes: number;
  uploaded_at: string;
  owner_id: string;
  ocr_completed: boolean;
}

export interface DocumentListResponse {
  items: DocumentMeta[];
  total: number;
  page: number;
  page_size: number;
}

/** GET /api/documents — 문서 목록 조회 (페이지네이션, 정렬) */
export function listDocuments(params?: {
  page?: number;
  page_size?: number;
  sort_by?: string;
  sort_order?: "asc" | "desc";
}): Promise<DocumentListResponse> {
  const qs = new URLSearchParams();
  if (params?.page) qs.set("page", String(params.page));
  if (params?.page_size) qs.set("page_size", String(params.page_size));
  if (params?.sort_by) qs.set("sort_by", params.sort_by);
  if (params?.sort_order) qs.set("sort_order", params.sort_order);
  const query = qs.toString();
  return request<DocumentListResponse>(`/api/documents${query ? `?${query}` : ""}`);
}

/** POST /api/documents/upload — 문서 업로드 (multipart) */
export async function uploadDocument(
  file: File,
  onProgress?: (percent: number) => void,
): Promise<DocumentMeta> {
  const url = `${API_BASE}/api/documents/upload`;
  return new Promise<DocumentMeta>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);

    // Attach JWT token for upload requests
    const token = getAccessToken();
    if (token) {
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    }

    if (onProgress) {
      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      });
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        try {
          const body = JSON.parse(xhr.responseText);
          reject(new Error(body?.error?.message ?? `Upload failed: ${xhr.status}`));
        } catch {
          reject(new Error(`Upload failed: ${xhr.status}`));
        }
      }
    };

    xhr.onerror = () => reject(new Error("네트워크 오류가 발생했습니다"));

    const formData = new FormData();
    formData.append("file", file);
    xhr.send(formData);
  });
}

/** PATCH /api/documents/{id} — 문서 이름 변경 */
export function renameDocument(
  documentId: string,
  filename: string,
): Promise<DocumentMeta> {
  return request<DocumentMeta>(`/api/documents/${documentId}`, {
    method: "PATCH",
    body: JSON.stringify({ filename }),
  });
}

/** DELETE /api/documents/{id} — 문서 삭제 */
export async function deleteDocument(documentId: string): Promise<void> {
  await request<Record<string, never>>(`/api/documents/${documentId}`, {
    method: "DELETE",
  });
}

// ── Audit Log API ─────────────────────────────────────────────────

export interface AuditLogEntry {
  id: string;
  user_id: string;
  action_type: string;
  document_id: string | null;
  timestamp: string;
  ip_address: string;
  details: Record<string, unknown> | null;
}

export interface AuditLogListResponse {
  items: AuditLogEntry[];
  total: number;
  page: number;
  page_size: number;
}

/** GET /api/audit-logs — 감사 로그 조회 (관리자 전용) */
export function listAuditLogs(params?: {
  page?: number;
  page_size?: number;
  start_date?: string;
  end_date?: string;
  user_id?: string;
  action_type?: string;
}): Promise<AuditLogListResponse> {
  const qs = new URLSearchParams();
  if (params?.page) qs.set("page", String(params.page));
  if (params?.page_size) qs.set("page_size", String(params.page_size));
  if (params?.start_date) qs.set("start_date", params.start_date);
  if (params?.end_date) qs.set("end_date", params.end_date);
  if (params?.user_id) qs.set("user_id", params.user_id);
  if (params?.action_type) qs.set("action_type", params.action_type);
  const query = qs.toString();
  return request<AuditLogListResponse>(`/api/audit-logs${query ? `?${query}` : ""}`);
}
