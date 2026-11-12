import { NextRequest, NextResponse } from "next/server";
import { isLocalWorkspaceOrigin } from "../../../lib/local-origin";
export const dynamic = "force-dynamic";
const base = process.env.HOTRADAR_API_URL ?? "http://127.0.0.1:18810";
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET(request: NextRequest) {
  const day = request.nextUrl.searchParams.get("date");
  const id = request.nextUrl.searchParams.get("id");
  if (!day || !/^\d{4}-\d{2}-\d{2}$/.test(day) || (id && !uuid.test(id))) {
    return NextResponse.json({ error: "日期或事件无效" }, { status: 400 });
  }
  try {
    const response = await fetch(new URL(`/api/v1/events${id ? "/" + id : ""}?date=${day}`, base), { cache: "no-store", signal: AbortSignal.timeout(15000) });
    if (!response.ok) return NextResponse.json({ error: response.status === 404 ? "该日期没有此事件的观测记录" : "事件读取失败" }, { status: response.status });
    return NextResponse.json(await response.json(), { headers: { "Cache-Control": "no-store" } });
  } catch { return NextResponse.json({ error: "事件读取失败，请检查后端与数据库。" }, { status: 503 }); }
}

export async function POST(request: NextRequest) {
  if (!isLocalWorkspaceOrigin(request.nextUrl.protocol, request.headers.get("host"), request.headers.get("origin"))) {
    return NextResponse.json({ error: "只允许本工作台修正事件。" }, { status: 403 });
  }
  let body;
  try { body = await request.json(); } catch { return NextResponse.json({ error: "操作格式无效" }, { status: 400 }); }
  if (!body || !["merge", "split", "restore", "metadata"].includes(body.action) || !body.payload || Object.keys(body).some(k => !["action", "payload"].includes(k))) {
    return NextResponse.json({ error: "操作无效" }, { status: 400 });
  }
  try {
    const response = await fetch(new URL("/api/v1/events/" + body.action, base), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body.payload), signal: AbortSignal.timeout(20000),
    });
    const result = await response.json();
    if (!response.ok) return NextResponse.json({ error: typeof result.detail === "string" ? result.detail : "请核对事件、修正原因及标签格式。" }, { status: response.status });
    return NextResponse.json(result, { headers: { "Cache-Control": "no-store" } });
  } catch { return NextResponse.json({ error: "保存结果尚未确认，请重新读取后核对，勿重复提交。" }, { status: 503 }); }
}
