"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { accessSharedDocument } from "@/lib/api";
import {
  initWebViewer,
  loadDocument,
  type WebViewerInstanceType,
} from "@/lib/webviewer";

/** SAS token refresh interval — check every 12 minutes. */
const TOKEN_CHECK_INTERVAL_MS = 12 * 60 * 1000;
const TOKEN_REFRESH_THRESHOLD_MS = 13 * 60 * 1000;

export default function SharedDocumentPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;

  const viewerRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<WebViewerInstanceType | null>(null);
  const expiresAtRef = useRef<Date | null>(null);
  const permissionRef = useRef<string>("read_only");

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filename, setFilename] = useState<string>("공유 문서");

  const fetchSharedDoc = useCallback(async () => {
    const data = await accessSharedDocument(token);
    expiresAtRef.current = new Date(data.expires_at);
    permissionRef.current = data.permission;
    setFilename(data.filename);
    return data.sas_url;
  }, [token]);

  const refreshTokenIfNeeded = useCallback(async () => {
    if (!expiresAtRef.current || !instanceRef.current) return;
    const remaining = expiresAtRef.current.getTime() - Date.now();
    if (remaining > TOKEN_REFRESH_THRESHOLD_MS) return;
    try {
      const newUrl = await fetchSharedDoc();
      loadDocument(instanceRef.current, newUrl);
    } catch {
      // Retry on next interval
    }
  }, [fetchSharedDoc]);

  useEffect(() => {
    let cancelled = false;

    async function init() {
      if (!viewerRef.current) return;
      try {
        const sasUrl = await fetchSharedDoc();
        if (cancelled) return;

        const isReadOnly = permissionRef.current === "read_only";
        const instance = await initWebViewer(viewerRef.current, sasUrl, {
          readOnly: isReadOnly,
        });
        if (cancelled) return;

        instanceRef.current = instance;
        setLoading(false);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "공유 문서를 불러올 수 없습니다",
          );
          setLoading(false);
        }
      }
    }

    init();
    return () => { cancelled = true; };
  }, [fetchSharedDoc]);

  useEffect(() => {
    const timer = setInterval(refreshTokenIfNeeded, TOKEN_CHECK_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refreshTokenIfNeeded]);

  return (
    <main style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      <header style={{ padding: "8px 16px", borderBottom: "1px solid #ddd" }}>
        <span>공유 문서 — {filename}</span>
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
    </main>
  );
}
