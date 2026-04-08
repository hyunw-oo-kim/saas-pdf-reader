"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { authCallback } from "@/lib/api";

function CallbackHandler() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const calledRef = useRef(false);

  useEffect(() => {
    // Strict Mode 중복 호출 방지
    if (calledRef.current) return;
    calledRef.current = true;

    const code = searchParams.get("code");
    const errorParam = searchParams.get("error");
    const errorDesc = searchParams.get("error_description");

    if (errorParam) {
      setError(errorDesc ?? errorParam);
      return;
    }

    if (!code) {
      setError("인증 코드가 없습니다. 다시 로그인해주세요.");
      return;
    }

    const redirectUri = `${window.location.origin}/login/callback`;

    authCallback(code, redirectUri)
      .then((tokens) => {
        localStorage.setItem("access_token", tokens.access_token);
        localStorage.setItem("refresh_token", tokens.refresh_token);
        router.replace("/documents");
      })
      .catch((err) => {
        // 이미 토큰이 있으면 (이전 호출이 성공했으면) 에러 무시하고 이동
        if (localStorage.getItem("access_token")) {
          router.replace("/documents");
          return;
        }
        setError(err instanceof Error ? err.message : "인증 처리에 실패했습니다");
      });
  }, [searchParams, router]);

  if (error) {
    return (
      <main style={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: "100vh", backgroundColor: "#f5f5f5" }}>
        <div style={{ textAlign: "center", maxWidth: 400, padding: 32, backgroundColor: "#fff", borderRadius: 8, boxShadow: "0 2px 12px rgba(0,0,0,0.08)" }}>
          <h2 style={{ margin: "0 0 12px", fontSize: 18, color: "#dc2626" }}>인증 실패</h2>
          <p role="alert" style={{ color: "#666", fontSize: 14, margin: "0 0 20px" }}>{error}</p>
          <button
            onClick={() => router.replace("/login")}
            style={{ padding: "8px 20px", border: "none", borderRadius: 4, backgroundColor: "#0070f3", color: "#fff", fontSize: 14, cursor: "pointer" }}
          >
            로그인 페이지로 돌아가기
          </button>
        </div>
      </main>
    );
  }

  return (
    <main style={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: "100vh" }}>
      <p style={{ fontSize: 16, color: "#666" }}>인증 처리 중...</p>
    </main>
  );
}

export default function AuthCallbackPage() {
  return (
    <Suspense
      fallback={
        <main style={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: "100vh" }}>
          <p style={{ fontSize: 16, color: "#666" }}>로딩 중...</p>
        </main>
      }
    >
      <CallbackHandler />
    </Suspense>
  );
}
