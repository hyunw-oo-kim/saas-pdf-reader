"use client";

import { useCallback, useRef, useState } from "react";
import { initWebViewer, type WebViewerInstanceType } from "@/lib/webviewer";

/**
 * 데모 페이지 — 인증/백엔드 없이 로컬 PDF를 직접 로드하여
 * Apryse WebViewer의 PDF 렌더링, 검색, 주석 기능을 테스트한다.
 *
 * 접속: http://localhost:3000/demo
 */
export default function DemoPage() {
  const viewerRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<WebViewerInstanceType | null>(null);

  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewerInstance, setViewerInstance] = useState<WebViewerInstanceType | null>(null);

  const handleFileSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      setError("PDF 파일만 선택할 수 있습니다");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const blob = URL.createObjectURL(file);

      if (instanceRef.current) {
        // Already initialized — just load new doc
        instanceRef.current.UI.loadDocument(blob, { filename: file.name });
      } else if (viewerRef.current) {
        const instance = await initWebViewer(viewerRef.current, blob);
        instanceRef.current = instance;
        setViewerInstance(instance);
      }

      setLoaded(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "PDF를 불러올 수 없습니다");
    } finally {
      setLoading(false);
    }
  }, []);

  // Also support a public sample PDF URL for quick testing
  const loadSamplePdf = useCallback(async () => {
    const sampleUrl = "https://www.w3.org/WAI/WCAG21/Techniques/pdf/img/table-word.pdf";
    setLoading(true);
    setError(null);

    try {
      if (instanceRef.current) {
        instanceRef.current.UI.loadDocument(sampleUrl, { filename: "sample.pdf" });
      } else if (viewerRef.current) {
        const instance = await initWebViewer(viewerRef.current, sampleUrl);
        instanceRef.current = instance;
        setViewerInstance(instance);
      }
      setLoaded(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "샘플 PDF를 불러올 수 없습니다");
    } finally {
      setLoading(false);
    }
  }, []);

  return (
    <main style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      <header
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid #ddd",
          display: "flex",
          alignItems: "center",
          gap: "12px",
          backgroundColor: "#fafafa",
        }}
      >
        <span style={{ fontWeight: 600, fontSize: 16 }}>PDF Reader Demo</span>

        <label
          style={{
            padding: "6px 14px",
            border: "1px solid #0070f3",
            borderRadius: 4,
            backgroundColor: "#0070f3",
            color: "#fff",
            fontSize: 13,
            cursor: "pointer",
          }}
        >
          PDF 파일 선택
          <input
            type="file"
            accept=".pdf,application/pdf"
            onChange={handleFileSelect}
            style={{ display: "none" }}
          />
        </label>

        <button
          onClick={loadSamplePdf}
          style={{
            padding: "6px 14px",
            border: "1px solid #ccc",
            borderRadius: 4,
            backgroundColor: "#fff",
            fontSize: 13,
            cursor: "pointer",
          }}
        >
          샘플 PDF 로드
        </button>

        {loading && <span style={{ color: "#666", fontSize: 13 }}>로딩 중…</span>}

        {loaded && (
          <span style={{ color: "#666", fontSize: 12 }}>
            Ctrl+F (⌘+F) 로 검색
          </span>
        )}
      </header>

      {error && (
        <div
          style={{
            padding: 16,
            color: "#dc2626",
            textAlign: "center",
          }}
        >
          {error}
        </div>
      )}

      {!loaded && !loading && !error && (
        <div
          style={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#999",
            fontSize: 15,
          }}
        >
          PDF 파일을 선택하거나 샘플 PDF를 로드하세요
        </div>
      )}

      <div
        ref={viewerRef}
        style={{ flex: 1, display: loaded ? "flex" : "none" }}
      />
    </main>
  );
}
