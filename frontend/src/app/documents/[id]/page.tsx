"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchAnnotations, fetchDocumentView, getEmailFromToken, saveAnnotations } from "@/lib/api";
import {
  exportAnnotations,
  importAnnotations,
  initWebViewer,
  loadDocument,
  type WebViewerInstanceType,
} from "@/lib/webviewer";
import ShareDialog from "@/components/ShareDialog";

/** SAS token refresh interval — check every 12 minutes (720 000 ms). */
const TOKEN_CHECK_INTERVAL_MS = 12 * 60 * 1000;

/** Refresh when remaining lifetime ≤ 13 minutes. */
const TOKEN_REFRESH_THRESHOLD_MS = 13 * 60 * 1000;

/** Debounce delay for auto-saving annotations (ms). */
const ANNOTATION_SAVE_DEBOUNCE_MS = 1000;

/**
 * Determine if the current user has a read-only (Viewer) role.
 * In a real app this would come from auth context / JWT claims.
 * For now we read from a query param or default to false.
 */
function useIsReadOnly(): boolean {
  if (typeof window === "undefined") return false;
  const params = new URLSearchParams(window.location.search);
  return params.get("role") === "viewer";
}

export default function DocumentViewerPage() {
  const params = useParams<{ id: string }>();
  const documentId = params.id;
  const isReadOnly = useIsReadOnly();

  const viewerRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<WebViewerInstanceType | null>(null);
  const expiresAtRef = useRef<Date | null>(null);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [viewerInstance, setViewerInstance] =
    useState<WebViewerInstanceType | null>(null);
  const [ocrText, setOcrText] = useState<string | null>(null);
  const [shareOpen, setShareOpen] = useState(false);

  // query param에서 파일명 읽기
  const [filename, setFilename] = useState<string>("");
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setFilename(params.get("name") ?? documentId);
  }, [documentId]);

  /**
   * Fetch a fresh SAS URL from the backend.
   * Returns the URL string and stores the expiry time.
   */
  const fetchSasUrl = useCallback(async (): Promise<string> => {
    const data = await fetchDocumentView(documentId);
    expiresAtRef.current = new Date(data.expires_at);
    return data.sas_url;
  }, [documentId]);

  /**
   * Refresh the SAS token if it's about to expire,
   * then reload the document in the existing viewer.
   */
  const refreshTokenIfNeeded = useCallback(async () => {
    if (!expiresAtRef.current || !instanceRef.current) return;

    const remaining = expiresAtRef.current.getTime() - Date.now();
    if (remaining > TOKEN_REFRESH_THRESHOLD_MS) return;

    try {
      const newUrl = await fetchSasUrl();
      loadDocument(instanceRef.current, newUrl);
    } catch {
      // Silently retry on next interval — avoid disrupting the user
    }
  }, [fetchSasUrl]);

  /**
   * Save current annotations to the backend (debounced).
   */
  const debouncedSaveAnnotations = useCallback(async () => {
    if (!instanceRef.current) return;
    try {
      const xfdf = await exportAnnotations(instanceRef.current);
      await saveAnnotations(documentId, xfdf);
    } catch {
      // Silently fail — next change will retry
    }
  }, [documentId]);

  // ── Initialise viewer on mount ──────────────────────────────────
  useEffect(() => {
    let cancelled = false;

    async function init() {
      if (!viewerRef.current) return;

      try {
        const sasUrl = await fetchSasUrl();
        if (cancelled) return;

        const instance = await initWebViewer(viewerRef.current, sasUrl, {
          readOnly: isReadOnly,
        });
        if (cancelled) return;

        instanceRef.current = instance;
        setViewerInstance(instance);

        // Set annotation author to the current user's email
        const { documentViewer, annotationManager } = instance.Core;
        annotationManager.setCurrentUser(getEmailFromToken());

        documentViewer.addEventListener("documentLoaded", async () => {
          if (cancelled) return;
          try {
            const data = await fetchAnnotations(documentId);
            if (data.xfdf_data && !cancelled) {
              await importAnnotations(instance, data.xfdf_data);
            }
          } catch {
            // Annotations not available — continue without them
          }
        });

        // Auto-save annotations on change (only for non-readOnly users)
        if (!isReadOnly) {
          annotationManager.addEventListener(
            "annotationChanged",
            () => {
              if (saveTimerRef.current) {
                clearTimeout(saveTimerRef.current);
              }
              saveTimerRef.current = setTimeout(
                debouncedSaveAnnotations,
                ANNOTATION_SAVE_DEBOUNCE_MS,
              );
            },
          );
        }

        setLoading(false);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "문서를 불러올 수 없습니다",
          );
          setLoading(false);
        }
      }
    }

    init();

    return () => {
      cancelled = true;
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
      }
    };
  }, [fetchSasUrl, isReadOnly, documentId, debouncedSaveAnnotations]);

  // ── SAS token auto-refresh timer ───────────────────────────────
  useEffect(() => {
    const timer = setInterval(refreshTokenIfNeeded, TOKEN_CHECK_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refreshTokenIfNeeded]);

  return (
    <main style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      <header style={{ padding: "8px 16px", borderBottom: "1px solid #ddd", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span>문서 뷰어 — {filename}</span>
        {!isReadOnly && (
          <button
            onClick={() => setShareOpen(true)}
            style={{
              padding: "4px 12px",
              border: "1px solid #0070f3",
              borderRadius: "4px",
              backgroundColor: "#fff",
              color: "#0070f3",
              cursor: "pointer",
              fontSize: "13px",
            }}
          >
            🔗 공유
          </button>
        )}
      </header>

      {loading && (
        <div
          style={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          PDF 로딩 중…
        </div>
      )}

      {error && (
        <div
          style={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "red",
          }}
        >
          {error}
        </div>
      )}

      <div
        ref={viewerRef}
        style={{ flex: 1, display: loading || error ? "none" : "flex" }}
      />

      <ShareDialog
        documentId={documentId}
        open={shareOpen}
        onClose={() => setShareOpen(false)}
      />
    </main>
  );
}
