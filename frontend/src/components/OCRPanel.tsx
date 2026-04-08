"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getOCRResult,
  getOCRStatus,
  startOCR,
  type OCRResultResponse,
  type OCRStatusResponse,
} from "@/lib/api";

/** Polling interval while OCR is in progress (ms). */
const POLL_INTERVAL_MS = 3000;

type OCRState = "idle" | "queued" | "processing" | "completed" | "failed";

export interface OCRPanelProps {
  documentId: string;
  /** Called when OCR completes with extracted text for search integration. */
  onOCRComplete?: (result: OCRResultResponse) => void;
}

export default function OCRPanel({ documentId, onOCRComplete }: OCRPanelProps) {
  const [state, setState] = useState<OCRState>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  /** Poll OCR status and handle completion/failure. */
  const pollStatus = useCallback(async () => {
    try {
      const status: OCRStatusResponse = await getOCRStatus(documentId);
      setProgress(status.progress_percent);

      if (status.status === "completed") {
        stopPolling();
        setState("completed");
        try {
          const result = await getOCRResult(documentId);
          onOCRComplete?.(result);
        } catch {
          // Result fetch failed but OCR itself completed
        }
      } else if (status.status === "failed") {
        stopPolling();
        setState("failed");
        setErrorMessage(status.error_message ?? "OCR 처리에 실패했습니다");
      } else {
        setState(status.status as OCRState);
      }
    } catch {
      // Status check failed — keep polling, don't disrupt
    }
  }, [documentId, onOCRComplete, stopPolling]);

  /** Start OCR and begin polling. */
  const handleStart = useCallback(async () => {
    setErrorMessage(null);
    try {
      const job = await startOCR(documentId);
      setState(job.status as OCRState);
      setProgress(0);
      // Start polling
      stopPolling();
      pollRef.current = setInterval(pollStatus, POLL_INTERVAL_MS);
    } catch (err) {
      setState("failed");
      setErrorMessage(
        err instanceof Error ? err.message : "OCR 요청에 실패했습니다",
      );
    }
  }, [documentId, pollStatus, stopPolling]);

  /** Check for existing OCR status on mount. */
  useEffect(() => {
    let cancelled = false;
    async function checkExisting() {
      try {
        const status = await getOCRStatus(documentId);
        if (cancelled) return;
        if (status.status === "completed") {
          setState("completed");
          try {
            const result = await getOCRResult(documentId);
            if (!cancelled) onOCRComplete?.(result);
          } catch {
            // ignore
          }
        } else if (status.status === "failed") {
          setState("failed");
          setErrorMessage(status.error_message ?? "OCR 처리에 실패했습니다");
        } else if (status.status === "queued" || status.status === "processing") {
          setState(status.status);
          setProgress(status.progress_percent);
          pollRef.current = setInterval(pollStatus, POLL_INTERVAL_MS);
        }
      } catch {
        // No existing OCR job — stay idle
      }
    }
    checkExisting();
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [documentId, onOCRComplete, pollStatus, stopPolling]);

  // Cleanup on unmount
  useEffect(() => stopPolling, [stopPolling]);

  const isProcessing = state === "queued" || state === "processing";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "8px",
        padding: "6px 12px",
        borderBottom: "1px solid #ddd",
        backgroundColor: "#f8f9fa",
        fontSize: "14px",
      }}
    >
      <span style={{ fontWeight: 500 }}>OCR</span>

      {state === "idle" && (
        <button
          onClick={handleStart}
          aria-label="OCR 시작"
          style={{
            padding: "4px 10px",
            border: "1px solid #0070f3",
            borderRadius: "4px",
            backgroundColor: "#0070f3",
            color: "#fff",
            cursor: "pointer",
            fontSize: "13px",
          }}
        >
          OCR 시작
        </button>
      )}

      {isProcessing && (
        <>
          <span
            role="status"
            aria-label="OCR 처리 중"
            style={{ color: "#666" }}
          >
            {state === "queued" ? "대기 중…" : "처리 중…"}
            {progress != null && progress > 0 && ` (${progress}%)`}
          </span>
          <span
            style={{ display: "inline-block", animation: "spin 1s linear infinite" }}
            aria-hidden="true"
          >
            ⏳
          </span>
        </>
      )}

      {state === "completed" && (
        <span style={{ color: "#16a34a" }} role="status" aria-label="OCR 완료">
          ✅ OCR 완료
        </span>
      )}

      {state === "failed" && (
        <>
          <span style={{ color: "#dc2626" }} role="alert">
            ❌ {errorMessage}
          </span>
          <button
            onClick={handleStart}
            aria-label="재시도"
            style={{
              padding: "4px 10px",
              border: "1px solid #dc2626",
              borderRadius: "4px",
              backgroundColor: "#fff",
              color: "#dc2626",
              cursor: "pointer",
              fontSize: "13px",
            }}
          >
            재시도
          </button>
        </>
      )}
    </div>
  );
}
