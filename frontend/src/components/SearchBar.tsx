"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { WebViewerInstanceType } from "@/lib/webviewer";

export interface SearchBarProps {
  instance: WebViewerInstanceType | null;
  /** OCR extracted text to include in search results. */
  ocrText?: string | null;
}

export default function SearchBar({ instance, ocrText }: SearchBarProps) {
  const [query, setQuery] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [currentMatch, setCurrentMatch] = useState(0);
  const [totalMatches, setTotalMatches] = useState(0);
  const [searched, setSearched] = useState(false);
  const [ocrMatches, setOcrMatches] = useState(0);

  const activeQueryRef = useRef("");

  // Listen for search result events from WebViewer
  useEffect(() => {
    if (!instance) return;

    const { documentViewer } = instance.Core;

    let matchCount = 0;
    let currentIdx = 0;

    const searchListener = (
      result: InstanceType<typeof instance.Core.Search.SearchResult>,
    ) => {
      const ResultCode = instance.Core.Search.ResultCode;

      if (result.resultCode === ResultCode.FOUND) {
        matchCount += 1;
        currentIdx = matchCount;
        setCurrentMatch(currentIdx);
        setTotalMatches(matchCount);
        setSearched(true);
      } else if (result.resultCode === ResultCode.NOT_FOUND) {
        if (matchCount === 0) {
          setCurrentMatch(0);
          setTotalMatches(0);
          setSearched(true);
        }
      }
    };

    documentViewer.addEventListener("searchResultChanged", searchListener);

    return () => {
      documentViewer.removeEventListener("searchResultChanged", searchListener);
    };
  }, [instance]);

  const executeSearch = useCallback(() => {
    if (!instance || !query.trim()) {
      setSearched(false);
      setCurrentMatch(0);
      setTotalMatches(0);
      setOcrMatches(0);
      return;
    }

    const { documentViewer } = instance.Core;
    const SearchMode = instance.Core.Search.Mode;

    let mode = SearchMode.HIGHLIGHT | SearchMode.PAGE_STOP;
    if (caseSensitive) {
      mode |= SearchMode.CASE_SENSITIVE;
    }

    // Reset counters
    setCurrentMatch(0);
    setTotalMatches(0);
    setOcrMatches(0);
    setSearched(false);
    activeQueryRef.current = query;

    // Count OCR text matches
    if (ocrText) {
      const searchIn = caseSensitive ? ocrText : ocrText.toLowerCase();
      const searchFor = caseSensitive ? query : query.toLowerCase();
      let ocrCount = 0;
      let idx = searchIn.indexOf(searchFor);
      while (idx !== -1) {
        ocrCount++;
        idx = searchIn.indexOf(searchFor, idx + 1);
      }
      setOcrMatches(ocrCount);
    }

    // Use full-document search to count all matches
    let count = 0;

    documentViewer.textSearchInit(query, mode, {
      fullSearch: false,
      onResult: (result: { resultCode: number; quads: unknown }) => {
        const ResultCode = instance.Core.Search.ResultCode;
        if (result.resultCode === ResultCode.FOUND) {
          count += 1;
          setCurrentMatch(1);
          setTotalMatches(count);
          setSearched(true);
        } else if (result.resultCode === ResultCode.NOT_FOUND) {
          if (count === 0) {
            setSearched(true);
            setCurrentMatch(0);
            setTotalMatches(0);
          }
        }
      },
    });
  }, [instance, query, caseSensitive, ocrText]);

  const goToNext = useCallback(() => {
    if (!instance || totalMatches === 0) return;
    const { documentViewer } = instance.Core;
    const SearchMode = instance.Core.Search.Mode;

    let mode = SearchMode.HIGHLIGHT | SearchMode.PAGE_STOP;
    if (caseSensitive) {
      mode |= SearchMode.CASE_SENSITIVE;
    }

    documentViewer.textSearchInit(activeQueryRef.current, mode, {
      fullSearch: false,
      onResult: (result: { resultCode: number }) => {
        const ResultCode = instance.Core.Search.ResultCode;
        if (result.resultCode === ResultCode.FOUND) {
          setCurrentMatch((prev) =>
            prev >= totalMatches ? 1 : prev + 1,
          );
        }
      },
    });
  }, [instance, caseSensitive, totalMatches]);

  const goToPrev = useCallback(() => {
    if (!instance || totalMatches === 0) return;
    const { documentViewer } = instance.Core;
    const SearchMode = instance.Core.Search.Mode;

    let mode =
      SearchMode.HIGHLIGHT | SearchMode.PAGE_STOP | SearchMode.SEARCH_UP;
    if (caseSensitive) {
      mode |= SearchMode.CASE_SENSITIVE;
    }

    documentViewer.textSearchInit(activeQueryRef.current, mode, {
      fullSearch: false,
      onResult: (result: { resultCode: number }) => {
        const ResultCode = instance.Core.Search.ResultCode;
        if (result.resultCode === ResultCode.FOUND) {
          setCurrentMatch((prev) =>
            prev <= 1 ? totalMatches : prev - 1,
          );
        }
      },
    });
  }, [instance, caseSensitive, totalMatches]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      if (e.shiftKey) {
        goToPrev();
      } else if (searched && totalMatches > 0) {
        goToNext();
      } else {
        executeSearch();
      }
    }
  };

  const clearSearch = useCallback(() => {
    setQuery("");
    setSearched(false);
    setCurrentMatch(0);
    setTotalMatches(0);
    setOcrMatches(0);
    activeQueryRef.current = "";
    if (instance) {
      instance.Core.documentViewer.clearSearchResults();
    }
  }, [instance]);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "8px",
        padding: "6px 12px",
        borderBottom: "1px solid #ddd",
        backgroundColor: "#fafafa",
        fontSize: "14px",
      }}
    >
      {/* Search input */}
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="텍스트 검색…"
        aria-label="텍스트 검색"
        style={{
          padding: "4px 8px",
          border: "1px solid #ccc",
          borderRadius: "4px",
          fontSize: "14px",
          width: "200px",
        }}
      />

      {/* Search button */}
      <button
        onClick={executeSearch}
        disabled={!query.trim()}
        aria-label="검색"
        style={{
          padding: "4px 10px",
          border: "1px solid #ccc",
          borderRadius: "4px",
          backgroundColor: "#fff",
          cursor: query.trim() ? "pointer" : "default",
        }}
      >
        검색
      </button>

      {/* Case-sensitive toggle */}
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: "4px",
          cursor: "pointer",
          userSelect: "none",
        }}
      >
        <input
          type="checkbox"
          checked={caseSensitive}
          onChange={(e) => setCaseSensitive(e.target.checked)}
          aria-label="대소문자 구분"
        />
        대소문자 구분
      </label>

      {/* Match count & navigation */}
      {searched && (
        <>
          {totalMatches > 0 ? (
            <>
              <span style={{ minWidth: "60px", textAlign: "center" }}>
                {currentMatch} / {totalMatches}
                {ocrMatches > 0 && (
                  <span style={{ color: "#666", fontSize: "12px" }}>
                    {" "}(+OCR {ocrMatches})
                  </span>
                )}
              </span>
              <button
                onClick={goToPrev}
                aria-label="이전 결과"
                style={{
                  padding: "4px 8px",
                  border: "1px solid #ccc",
                  borderRadius: "4px",
                  backgroundColor: "#fff",
                  cursor: "pointer",
                }}
              >
                ◀
              </button>
              <button
                onClick={goToNext}
                aria-label="다음 결과"
                style={{
                  padding: "4px 8px",
                  border: "1px solid #ccc",
                  borderRadius: "4px",
                  backgroundColor: "#fff",
                  cursor: "pointer",
                }}
              >
                ▶
              </button>
            </>
          ) : ocrMatches > 0 ? (
            <span style={{ color: "#0070f3" }}>
              OCR 텍스트에서 {ocrMatches}건 발견
            </span>
          ) : (
            <span style={{ color: "#999" }}>검색 결과가 없습니다</span>
          )}
        </>
      )}

      {/* Clear button */}
      {query && (
        <button
          onClick={clearSearch}
          aria-label="검색 초기화"
          style={{
            padding: "4px 8px",
            border: "none",
            background: "none",
            cursor: "pointer",
            color: "#999",
            fontSize: "16px",
          }}
        >
          ✕
        </button>
      )}
    </div>
  );
}
