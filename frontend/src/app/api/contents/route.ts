import { NextRequest, NextResponse } from "next/server";
export const dynamic = "force-dynamic";
const base = process.env.HOTRADAR_API_URL ?? "http://127.0.0.1:18810";
export async function GET(request: NextRequest) {
  const date = request.nextUrl.searchParams.get("date");
  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return NextResponse.json({ error: "内容采样日期无效" }, { status: 400 });
  try {
    const response = await fetch(new URL(`/api/v1/contents?date=${date}`, base), { cache: "no-store", signal: AbortSignal.timeout(8000) });
    const result = await response.json();
    if (!response.ok) return NextResponse.json({ error: "内容数据读取失败" }, { status: response.status });
    return NextResponse.json(result, { headers: { "Cache-Control": "no-store" } });
  } catch { return NextResponse.json({ error: "内容数据读取失败，请检查后端；未填充示例内容。" }, { status: 503 }); }
}
