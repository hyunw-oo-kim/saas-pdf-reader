import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SaaS PDF Reader",
  description: "웹 기반 PDF 문서 열람, 검색, 주석 처리 서비스",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
