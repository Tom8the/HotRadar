import { NextRequest, NextResponse } from "next/server";
import { isLocalWorkspaceOrigin } from "../../../lib/local-origin";
export const dynamic = "force-dynamic";
const base = process.env.HOTRADAR_API_URL ?? "http://127.0.0.1:18810";

export async function GET() {
  try {
    const response = await fetch(new URL("/api/v1/source-management", base), { cache: "no-store", signal: AbortSignal.timeout(8000) });
    if (!response.ok) throw new Error("Unavailable");
    return NextResponse.json(await response.json(), { headers: { "Cache-Control": "no-store" } });
  } catch { return NextResponse.json({ error: "来源状态读取失败，请检查后端与数据库。" }, { status: 503 }); }
}

export async function PATCH(request: NextRequest) {
  if (!isLocalWorkspaceOrigin(request.nextUrl.protocol, request.headers.get("host"), request.headers.get("origin"))) {
    return NextResponse.json({ error: "只允许本工作台修改来源配置。" }, { status: 403 });
  }
  let body;
  try { body = await request.json(); } catch { return NextResponse.json({ error: "配置格式无效" }, { status: 400 }); }
  if (!body || !["douyin-hot", "douyin-creator-hot", "douyin-wuxi", "douyin-wuxi-app", "weibo-wuxi", "weibo-hot", "baidu-hot", "douyin-content"].includes(body.board_id) || !body.settings ||
    Object.keys(body).some(key => !["board_id", "settings"].includes(key))) {
    return NextResponse.json({ error: "来源或配置无效" }, { status: 400 });
  }
  const path = body.board_id === "douyin-content" ? "/api/v1/content-sources/douyin/settings" : `/api/v1/boards/${body.board_id}/settings`;
  try {
    const response = await fetch(new URL(path, base), {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body.settings), signal: AbortSignal.timeout(8000),
    });
    if (response.status === 409 && body.board_id === "douyin-content") {
      const { detail } = await response.json();
      const error = detail === "session_required" ? "请先到采集授权页验证抖音精选会话。" : "抖音精选正在采集，请在本次任务结束后重试。";
      return NextResponse.json({ error }, { status: 409 });
    }
    if (!response.ok) return NextResponse.json({ error: response.status === 422 ? "仅支持15、30、60分钟采集周期及启停配置。" : "来源配置保存失败" }, { status: response.status });
    return NextResponse.json(await response.json(), { headers: { "Cache-Control": "no-store" } });
  } catch { return NextResponse.json({ error: "配置尚未确认保存，请重新读取状态后核对。" }, { status: 503 }); }
}
