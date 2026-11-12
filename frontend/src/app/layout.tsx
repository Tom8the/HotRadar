import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HotRadar 热点雷达",
  description: "MCN 热点与趋势情报工作台",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
