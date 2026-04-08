"use client";

import { useCallback, useEffect, useState } from "react";
import { authLogout } from "@/lib/api";

function buildAuth0AuthUrl(): string {
  const redirectUri = `${window.location.origin}/login/callback`;
  const auth0Domain = process.env.NEXT_PUBLIC_AUTH0_DOMAIN ?? "";
  const auth0ClientId = process.env.NEXT_PUBLIC_AUTH0_CLIENT_ID ?? "";
  return (
    `https://${auth0Domain}/authorize` +
    `?client_id=${auth0ClientId}` +
    `&response_type=code` +
    `&redirect_uri=${encodeURIComponent(redirectUri)}` +
    `&scope=openid+profile+email` +
    `&state=auth0`
  );
}

export default function LoginPage() {
  const [error, setError] = useState<string | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);
  const [hasToken, setHasToken] = useState(false);

  useEffect(() => {
    setHasToken(!!localStorage.getItem("access_token"));
  }, []);

  const handleLogin = useCallback(() => {
    try {
      window.location.href = buildAuth0AuthUrl();
    } catch (err) {
      setError(err instanceof Error ? err.message : "로그인 요청에 실패했습니다");
    }
  }, []);

  const handleLogout = useCallback(async () => {
    setLoggingOut(true);
    try {
      await authLogout();
      localStorage.removeItem("access_token");
      localStorage.removeItem("refresh_token");
      setHasToken(false);
      window.location.href = "/login";
    } catch (err) {
      setError(err instanceof Error ? err.message : "로그아웃에 실패했습니다");
    } finally {
      setLoggingOut(false);
    }
  }, []);

  return (
    <main style={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: "100vh", backgroundColor: "#f5f5f5" }}>
      <div style={{ textAlign: "center", maxWidth: 400, padding: 32, backgroundColor: "#fff", borderRadius: 8, boxShadow: "0 2px 12px rgba(0,0,0,0.08)" }}>
        <h1 style={{ margin: "0 0 8px", fontSize: 24 }}>SaaS PDF Reader</h1>
        <p style={{ margin: "0 0 24px", color: "#666", fontSize: 14 }}>계정으로 로그인하세요</p>

        {error && (
          <p role="alert" style={{ color: "#dc2626", fontSize: 13, margin: "0 0 16px", padding: "8px 12px", backgroundColor: "#fef2f2", borderRadius: 4 }}>
            {error}
          </p>
        )}

        <button
          onClick={handleLogin}
          style={{ width: "100%", padding: "12px 16px", border: "none", borderRadius: 4, backgroundColor: "#00297a", color: "#fff", fontSize: 14, cursor: "pointer" }}
        >
          로그인
        </button>

        {hasToken && (
          <button
            onClick={handleLogout}
            disabled={loggingOut}
            style={{ marginTop: 24, padding: "8px 16px", border: "1px solid #ccc", borderRadius: 4, backgroundColor: "#fff", fontSize: 13, cursor: loggingOut ? "not-allowed" : "pointer", color: "#666" }}
          >
            {loggingOut ? "로그아웃 중…" : "로그아웃"}
          </button>
        )}
      </div>
    </main>
  );
}
