"use client";

import { useCallback, useRef, useState, useEffect } from "react";
import { uploadDocument } from "@/lib/api";

export interface UploadAreaProps {
  /** Called after a successful upload so the parent can refresh its list. */
  onUploadComplete?: () => void;
}

const MAX_SIZE_BYTES = 100 * 1024 * 1024; // 100 MB

export default function UploadArea({ onUploadComplete }: UploadAreaProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const validateFile = useCallback((file: File): string | null => {
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      return "PDF 형식만 업로드 가능합니다";
    }
    if (file.size > MAX_SIZE_BYTES) {
      return "파일 크기가 100MB를 초과합니다";
    }
    return null;
  }, []);

  const handleUpload = useCallback(
    async (file: File) => {
      const validationError = validateFile(file);
      if (validationError) {
        setError(validationError);
        return;
      }

      setUploading(true);
      setProgress(0);
      setError(null);
      setSuccess(false);

      try {
        await uploadDocument(file, (pct) => setProgress(pct));
        setSuccess(true);
        setProgress(100);
        onUploadComplete?.();
        // 3초 후 업로드 영역 초기화
        setTimeout(() => {
          setSuccess(false);
          setProgress(0);
          if (fileInputRef.current) fileInputRef.current.value = "";
        }, 3000);
      } catch (err) {
        setError(err instanceof Error ? err.message : "업로드에 실패했습니다");
      } finally {
        setUploading(false);
      }
    },
    [validateFile, onUploadComplete],
  );

  const handleRetry = useCallback(() => {
    if (fileInputRef.current?.files?.[0]) {
      handleUpload(fileInputRef.current.files[0]);
    }
  }, [handleUpload]);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const onDragLeave = useCallback(() => setDragging(false), []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) handleUpload(file);
    },
    [handleUpload],
  );

  const onFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleUpload(file);
    },
    [handleUpload],
  );

  return (
    <div
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      style={{
        border: `2px dashed ${dragging ? "#0070f3" : "#ccc"}`,
        borderRadius: 8,
        padding: 24,
        textAlign: "center",
        backgroundColor: dragging ? "#f0f7ff" : "#fafafa",
        transition: "all 0.2s",
        marginBottom: 16,
      }}
    >
      <input
        ref={fileInputRef}
        type="file"
        accept=".pdf,application/pdf"
        onChange={onFileSelect}
        style={{ display: "none" }}
        aria-label="PDF 파일 선택"
      />

      {!uploading && !success && (
        <>
          <p style={{ margin: "0 0 8px", fontSize: 14, color: "#666" }}>
            PDF 파일을 여기에 드래그하거나
          </p>
          <button
            onClick={() => fileInputRef.current?.click()}
            style={{
              padding: "8px 16px",
              border: "1px solid #0070f3",
              borderRadius: 4,
              backgroundColor: "#fff",
              color: "#0070f3",
              fontSize: 14,
              cursor: "pointer",
            }}
          >
            파일 선택
          </button>
          <p style={{ margin: "8px 0 0", fontSize: 12, color: "#999" }}>
            PDF 형식, 최대 100MB
          </p>
        </>
      )}

      {uploading && (
        <div>
          <p style={{ margin: "0 0 8px", fontSize: 14 }}>업로드 중… {progress}%</p>
          <div
            style={{
              width: "100%",
              height: 8,
              backgroundColor: "#e5e7eb",
              borderRadius: 4,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${progress}%`,
                height: "100%",
                backgroundColor: "#0070f3",
                transition: "width 0.3s",
              }}
            />
          </div>
        </div>
      )}

      {success && !uploading && (
        <p style={{ color: "#16a34a", fontSize: 14, margin: 0 }}>
          ✅ 업로드 완료
        </p>
      )}

      {error && (
        <div style={{ marginTop: 8 }}>
          <p role="alert" style={{ color: "#dc2626", fontSize: 13, margin: "0 0 8px" }}>
            ❌ {error}
          </p>
          <button
            onClick={handleRetry}
            style={{
              padding: "6px 12px",
              border: "1px solid #dc2626",
              borderRadius: 4,
              backgroundColor: "#fff",
              color: "#dc2626",
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            재시도
          </button>
        </div>
      )}
    </div>
  );
}
