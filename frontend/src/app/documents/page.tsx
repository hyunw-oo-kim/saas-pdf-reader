"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listDocuments,
  renameDocument,
  deleteDocument,
  type DocumentMeta,
} from "@/lib/api";
import UploadArea from "@/components/UploadArea";

type SortField = "filename" | "uploaded_at" | "size_bytes";
type SortOrder = "asc" | "desc";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR");
}

export default function DocumentsPage() {
  const [items, setItems] = useState<DocumentMeta[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [sortBy, setSortBy] = useState<SortField>("uploaded_at");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Rename state
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const fetchDocs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listDocuments({
        page,
        page_size: pageSize,
        sort_by: sortBy,
        sort_order: sortOrder,
      });
      setItems(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "문서 목록을 불러올 수 없습니다");
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, sortBy, sortOrder]);

  useEffect(() => {
    fetchDocs();
  }, [fetchDocs]);

  const handleSort = useCallback(
    (field: SortField) => {
      if (sortBy === field) {
        setSortOrder((o) => (o === "asc" ? "desc" : "asc"));
      } else {
        setSortBy(field);
        setSortOrder("asc");
      }
      setPage(1);
    },
    [sortBy],
  );

  const handleDelete = useCallback(
    async (id: string, filename: string) => {
      if (!confirm(`"${filename}" 문서를 삭제하시겠습니까?`)) return;
      try {
        await deleteDocument(id);
        fetchDocs();
      } catch (err) {
        alert(err instanceof Error ? err.message : "삭제에 실패했습니다");
      }
    },
    [fetchDocs],
  );

  const startRename = useCallback((doc: DocumentMeta) => {
    setRenamingId(doc.id);
    setRenameValue(doc.filename);
  }, []);

  const submitRename = useCallback(
    async (id: string) => {
      if (!renameValue.trim()) return;
      try {
        await renameDocument(id, renameValue.trim());
        setRenamingId(null);
        fetchDocs();
      } catch (err) {
        alert(err instanceof Error ? err.message : "이름 변경에 실패했습니다");
      }
    },
    [renameValue, fetchDocs],
  );

  const sortIndicator = (field: SortField) => {
    if (sortBy !== field) return "";
    return sortOrder === "asc" ? " ▲" : " ▼";
  };

  const thStyle: React.CSSProperties = {
    padding: "8px 12px",
    textAlign: "left",
    cursor: "pointer",
    borderBottom: "2px solid #e5e7eb",
    fontSize: 13,
    fontWeight: 600,
    userSelect: "none",
    whiteSpace: "nowrap",
  };

  const tdStyle: React.CSSProperties = {
    padding: "8px 12px",
    borderBottom: "1px solid #f0f0f0",
    fontSize: 13,
  };

  return (
    <main style={{ padding: 24, maxWidth: 960, margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>문서 목록</h1>
        <a href="/login" style={{ fontSize: 13, color: "#666" }}>로그아웃</a>
      </div>

      <UploadArea onUploadComplete={fetchDocs} />

      {error && (
        <p role="alert" style={{ color: "#dc2626", fontSize: 13, margin: "0 0 12px" }}>
          {error}
        </p>
      )}

      <table style={{ width: "100%", borderCollapse: "collapse", backgroundColor: "#fff" }}>
        <thead>
          <tr>
            <th style={thStyle} onClick={() => handleSort("filename")}>
              파일명{sortIndicator("filename")}
            </th>
            <th style={thStyle} onClick={() => handleSort("size_bytes")}>
              크기{sortIndicator("size_bytes")}
            </th>
            <th style={thStyle} onClick={() => handleSort("uploaded_at")}>
              업로드 일시{sortIndicator("uploaded_at")}
            </th>
            <th style={{ ...thStyle, cursor: "default" }}>작업</th>
          </tr>
        </thead>
        <tbody>
          {loading && (
            <tr>
              <td colSpan={4} style={{ ...tdStyle, textAlign: "center", color: "#999" }}>
                로딩 중…
              </td>
            </tr>
          )}
          {!loading && items.length === 0 && (
            <tr>
              <td colSpan={4} style={{ ...tdStyle, textAlign: "center", color: "#999" }}>
                문서가 없습니다
              </td>
            </tr>
          )}
          {!loading &&
            items.map((doc) => (
              <tr key={doc.id}>
                <td style={tdStyle}>
                  {renamingId === doc.id ? (
                    <span style={{ display: "flex", gap: 4 }}>
                      <input
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") submitRename(doc.id);
                          if (e.key === "Escape") setRenamingId(null);
                        }}
                        style={{ flex: 1, padding: "2px 6px", fontSize: 13, border: "1px solid #ccc", borderRadius: 3 }}
                        autoFocus
                      />
                      <button
                        onClick={() => submitRename(doc.id)}
                        style={{ padding: "2px 8px", fontSize: 12, border: "1px solid #0070f3", borderRadius: 3, backgroundColor: "#0070f3", color: "#fff", cursor: "pointer" }}
                      >
                        저장
                      </button>
                      <button
                        onClick={() => setRenamingId(null)}
                        style={{ padding: "2px 8px", fontSize: 12, border: "1px solid #ccc", borderRadius: 3, backgroundColor: "#fff", cursor: "pointer" }}
                      >
                        취소
                      </button>
                    </span>
                  ) : (
                    <a href={`/documents/${doc.id}?name=${encodeURIComponent(doc.filename)}`} style={{ color: "#0070f3", textDecoration: "none" }}>
                      {doc.filename}
                    </a>
                  )}
                </td>
                <td style={tdStyle}>{formatBytes(doc.size_bytes)}</td>
                <td style={tdStyle}>{formatDate(doc.uploaded_at)}</td>
                <td style={tdStyle}>
                  <button
                    onClick={() => startRename(doc)}
                    style={{ marginRight: 6, padding: "2px 8px", fontSize: 12, border: "1px solid #ccc", borderRadius: 3, backgroundColor: "#fff", cursor: "pointer" }}
                  >
                    이름 변경
                  </button>
                  <button
                    onClick={() => handleDelete(doc.id, doc.filename)}
                    style={{ padding: "2px 8px", fontSize: 12, border: "1px solid #dc2626", borderRadius: 3, backgroundColor: "#fff", color: "#dc2626", cursor: "pointer" }}
                  >
                    삭제
                  </button>
                </td>
              </tr>
            ))}
        </tbody>
      </table>

      {/* Pagination */}
      {totalPages > 1 && (
        <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 8, marginTop: 16 }}>
          <button
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
            style={{ padding: "4px 12px", border: "1px solid #ccc", borderRadius: 4, backgroundColor: "#fff", cursor: page <= 1 ? "not-allowed" : "pointer", fontSize: 13 }}
          >
            이전
          </button>
          <span style={{ fontSize: 13, color: "#666" }}>
            {page} / {totalPages} (총 {total}건)
          </span>
          <button
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
            style={{ padding: "4px 12px", border: "1px solid #ccc", borderRadius: 4, backgroundColor: "#fff", cursor: page >= totalPages ? "not-allowed" : "pointer", fontSize: 13 }}
          >
            다음
          </button>
        </div>
      )}
    </main>
  );
}
