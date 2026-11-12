import { NextRequest, NextResponse } from "next/server";
export const dynamic = "force-dynamic";
const base = process.env.HOTRADAR_API_URL ?? "http://127.0.0.1:18810";
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export async function GET(request: NextRequest) {
  const start = request.nextUrl.searchParams.get("start"), end = request.nextUrl.searchParams.get("end");
  const id = request.nextUrl.searchParams.get("id");
  if (!start || !end || !/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end) || (id && !uuid.test(id))) {
    return NextResponse.json({ error: "趋势日期或事件无效" }, { status: 400 });
  }
  try {
    const response = await fetch(new URL(`/api/v1/trends${id ? "/events/" + id : ""}?start=${start}&end=${end}`, base), { cache: "no-store", signal: AbortSignal.timeout(20000) });
    const result = await response.json();
    if (!response.ok) return NextResponse.json({ error: typeof result.detail === "string" ? result.detail : "趋势读取失败" }, { status: response.status });
    return NextResponse.json(result, { headers: { "Cache-Control": "no-store" } });
  } catch { return NextResponse.json({ error: "趋势读取失败，请检查后端与数据库；未补造历史。" }, { status: 503 }); }
}
