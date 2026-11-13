import { NextRequest, NextResponse } from "next/server";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const board = request.nextUrl.searchParams.get("board");
  const snapshot = request.nextUrl.searchParams.get("snapshot");
  const date = request.nextUrl.searchParams.get("date");
  const view = request.nextUrl.searchParams.get("view") === "1";
  if (board && !["weibo-hot", "douyin-hot", "douyin-creator-hot", "douyin-wuxi", "douyin-wuxi-app", "weibo-wuxi", "baidu-hot"].includes(board)) {
    return NextResponse.json({ error: "来源不存在" }, { status: 404 });
  }
  if (snapshot && (!board || !/^[0-9a-f-]{36}$/.test(snapshot))) {
    return NextResponse.json({ error: "快照参数无效" }, { status: 400 });
  }
  if (date && !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return NextResponse.json({ error: "日期格式无效" }, { status: 400 });
  }
  const path = board && !view
    ? snapshot ? "/api/v1/boards/" + board + "/snapshots/" + snapshot
      : "/api/v1/boards/" + board + "/snapshots"
    : "/api/v1/boards";
  const url = new URL(path, process.env.HOTRADAR_API_URL ?? "http://127.0.0.1:18810");
  if (board && view) url.searchParams.set("board", board);
  if (date && !snapshot) url.searchParams.set("date", date);
  try {
    const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(8000) });
    if (!response.ok) {
      return NextResponse.json({ error: "数据服务暂不可用，请检查后端与数据库。" }, { status: response.status });
    }
    return NextResponse.json(await response.json(), { headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ error: "无法连接数据服务，请先启动后端。" }, { status: 503 });
  }
}
