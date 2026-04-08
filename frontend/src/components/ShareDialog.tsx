"use client";

import { useCallback, useEffect, useState } from "react";
import {
  createShareLink,
  listShareLinks,
  revokeShareLink,
  type ShareLinkResponse,
} from "@/lib/api";

export interface ShareDialogProps {
  documentId: string;
  open: boolean;
  onClose: () => void;
}

type Expiry = "1h" | "1d" | "7d" | "30d";
type Permission = "read_only" | "annotate";

const EXPIRY_LABELS: Record<Expiry, string> = {
  "1h": "1시간",
  "1d": "1일",
  "7d": "7일",
  "30d": "30일",
};

const PERMISSION_LABELS: Record<Permission, string> = {
  read_only: "읽기 전용",
  annotate: "주석 허용",
};

export default function ShareDialog({
  documentId,
  open,
  onClose,
}: ShareDialogProps) {
  const [expiry, setExpiry] = useState<Expiry>("7d");
  const [permission, setPermission] = useState<Permission>("read_only");
  const [creating, setCreating] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [links, setLinks] = useState<ShareLinkResponse[]>([]);
  const [loadingLinks, setLoadingLinks] = useState(false);

  /** Load existing share links for this document. */
  const loadLinks = useCallback(async () => {
    setLoadingLinks(true);
    try {
      const data = await listShareLinks(documentId);
      setLinks(data);
    } catch {
      // Silently fail — list is supplementary
    } finally {
      setLoadingLinks(false);
    }
  }, [documentId]);

  useEffect(() => {
    if (open) {
      loadLinks();
      setCopied(false);
      setError(null);
    }
  }, [open, loadLinks]);

  /** Create a new share link and copy to clipboard. */
  const handleCreate = useCallback(async () => {
    setCreating(true);
    setError(null);
    setCopied(false);
    try {
      const link = await createShareLink(documentId, expiry, permission);
      const fullUrl = `${window.location.origin}/shared/${link.share_url.replace("/api/shared/", "")}`;
      await navigator.clipboard.writeText(fullUrl);
      setCopied(true);
      // Refresh the list
      await loadLinks();
    } catch (err) {
      setError(err instanceof Error ? err.message : "공유 링크 생성에 실패했습니다");
    } finally {
      setCreating(false);
    }
  }, [documentId, expiry, permission, loadLinks]);

  /** Revoke a share link. */
  const handleRevoke = useCallback(
    async (shareId: string) => {
      try {
        await revokeShareLink(documentId, shareId);
        setLinks((prev) => prev.filter((l) => l.share_id !== shareId));
      } catch (err) {
        setError(err instanceof Error ? err.message : "공유 링크 취소에 실패했습니다");
      }
    },
    [documentId],
  );

  /** Copy an existing share link URL to clipboard. */
  const handleCopyLink = useCallback(async (shareUrl: string) => {
    const token = shareUrl.replace("/api/shared/", "");
    const fullUrl = `${window.location.origin}/shared/${token}`;
    await navigator.clipboard.writeText(fullUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, []);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-label="문서 공유"
      style={{
        position: "fixed",
        inset: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(0,0,0,0.4)",
        zIndex: 1000,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          backgroundColor: "#fff",
          borderRadius: "8px",
          padding: "24px",
          width: "440px",
          maxHeight: "80vh",
          overflowY: "auto",
          boxShadow: "0 4px 24px rgba(0,0,0,0.15)",
        }}
      >
        <h2 style={{ margin: "0 0 16px", fontSize: "18px" }}>문서 공유</h2>

        {/* Create new share link */}
        <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
          <label style={{ display: "flex", flexDirection: "column", flex: 1, fontSize: "13px" }}>
            만료 시간
            <select
              value={expiry}
              onChange={(e) => setExpiry(e.target.value as Expiry)}
              style={{ marginTop: "4px", padding: "6px", borderRadius: "4px", border: "1px solid #ccc" }}
            >
              {(Object.keys(EXPIRY_LABELS) as Expiry[]).map((k) => (
                <option key={k} value={k}>{EXPIRY_LABELS[k]}</option>
              ))}
            </select>
          </label>
          <label style={{ display: "flex", flexDirection: "column", flex: 1, fontSize: "13px" }}>
            권한
            <select
              value={permission}
              onChange={(e) => setPermission(e.target.value as Permission)}
              style={{ marginTop: "4px", padding: "6px", borderRadius: "4px", border: "1px solid #ccc" }}
            >
              {(Object.keys(PERMISSION_LABELS) as Permission[]).map((k) => (
                <option key={k} value={k}>{PERMISSION_LABELS[k]}</option>
              ))}
            </select>
          </label>
        </div>

        <button
          onClick={handleCreate}
          disabled={creating}
          style={{
            width: "100%",
            padding: "8px",
            border: "none",
            borderRadius: "4px",
            backgroundColor: "#0070f3",
            color: "#fff",
            cursor: creating ? "not-allowed" : "pointer",
            fontSize: "14px",
            marginBottom: "8px",
          }}
        >
          {creating ? "생성 중…" : "공유 링크 생성 및 복사"}
        </button>

        {copied && (
          <p style={{ color: "#16a34a", fontSize: "13px", margin: "0 0 8px" }}>
            ✅ 링크가 클립보드에 복사되었습니다
          </p>
        )}
        {error && (
          <p style={{ color: "#dc2626", fontSize: "13px", margin: "0 0 8px" }} role="alert">
            ❌ {error}
          </p>
        )}

        {/* Existing share links */}
        <hr style={{ margin: "16px 0", border: "none", borderTop: "1px solid #eee" }} />
        <h3 style={{ margin: "0 0 8px", fontSize: "15px" }}>활성 공유 링크</h3>

        {loadingLinks && <p style={{ fontSize: "13px", color: "#666" }}>로딩 중…</p>}

        {!loadingLinks && links.length === 0 && (
          <p style={{ fontSize: "13px", color: "#666" }}>활성 공유 링크가 없습니다</p>
        )}

        {links.map((link) => (
          <div
            key={link.share_id}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "8px",
              marginBottom: "6px",
              border: "1px solid #eee",
              borderRadius: "4px",
              fontSize: "13px",
            }}
          >
            <div>
              <div>{PERMISSION_LABELS[link.permission as Permission] ?? link.permission}</div>
              <div style={{ color: "#666", fontSize: "12px" }}>
                만료: {new Date(link.expires_at).toLocaleString()}
              </div>
            </div>
            <div style={{ display: "flex", gap: "4px" }}>
              <button
                onClick={() => handleCopyLink(link.share_url)}
                title="링크 복사"
                style={{
                  padding: "4px 8px",
                  border: "1px solid #ccc",
                  borderRadius: "4px",
                  backgroundColor: "#fff",
                  cursor: "pointer",
                  fontSize: "12px",
                }}
              >
                📋 복사
              </button>
              <button
                onClick={() => handleRevoke(link.share_id)}
                title="공유 취소"
                style={{
                  padding: "4px 8px",
                  border: "1px solid #dc2626",
                  borderRadius: "4px",
                  backgroundColor: "#fff",
                  color: "#dc2626",
                  cursor: "pointer",
                  fontSize: "12px",
                }}
              >
                취소
              </button>
            </div>
          </div>
        ))}

        {/* Close button */}
        <button
          onClick={onClose}
          style={{
            marginTop: "16px",
            width: "100%",
            padding: "8px",
            border: "1px solid #ccc",
            borderRadius: "4px",
            backgroundColor: "#fff",
            cursor: "pointer",
            fontSize: "14px",
          }}
        >
          닫기
        </button>
      </div>
    </div>
  );
}
