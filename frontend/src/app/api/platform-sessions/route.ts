import { NextRequest, NextResponse } from "next/server";
import { isLocalWorkspaceOrigin } from "../../../lib/local-origin";

export const dynamic = "force-dynamic";
const base = process.env.HOTRADAR_API_URL ?? "http://127.0.0.1:18810";
const scopes = new Set(["douyin-hot", "douyin-creator-hot", "douyin-wuxi", "douyin-content", "weibo-wuxi", "weibo-content"]);
const browserActions = new Set(["open", "frame", "input", "city", "login", "verify", "close"]);
const noStore = { "Cache-Control": "no-store" };

function failure(detail: string, status: number) {
  return NextResponse.json({ detail }, { status, headers: noStore });
}

export async function GET() {
  try {
    const response = await fetch(new URL("/api/v1/platform-sessions", base), {
      cache: "no-store", signal: AbortSignal.timeout(8000), redirect: "error",
    });
    if (!response.ok) return failure("status_unavailable", 503);
    return NextResponse.json(await response.json(), { headers: noStore });
  } catch {
    return failure("status_unavailable", 503);
  }
}

export async function POST(request: NextRequest) {
  const origin = request.headers.get("origin");
  const deployed = process.env.HOTRADAR_PUBLIC_ORIGIN;
  const allowed = deployed
    ? deployed.startsWith("https://") && origin === deployed
    : isLocalWorkspaceOrigin(request.nextUrl.protocol, request.headers.get("host"), origin);
  if (!allowed) return failure("origin_not_allowed", 403);
  const scope = request.nextUrl.searchParams.get("source");
  if (!scope || !scopes.has(scope)) return failure("unknown_scope", 404);
  const action = request.nextUrl.searchParams.get("action");
  if (!action || !browserActions.has(action)) return failure("unknown_scope", 404);
  const key = request.headers.get("authorization");
  if (action !== "open" && !key) return failure("invalid_browser_key", 401);
  try {
    let body: string | undefined;
    if (action === "input") {
      if (request.headers.get("content-type")?.split(";", 1)[0] !== "application/json") {
        return failure("invalid_browser_input", 415);
      }
      const reader = request.body?.getReader();
      if (!reader) return failure("invalid_browser_input", 400);
      let size = 0;
      const chunks: Uint8Array[] = [];
      for (let read = await reader.read(); !read.done; read = await reader.read()) {
        size += read.value.byteLength;
        if (size > 2048) {
          await reader.cancel();
          return failure("invalid_browser_input", 413);
        }
        chunks.push(read.value);
      }
      body = Buffer.concat(chunks, size).toString("utf8");
    }
    const path = `browser/${scope}/${action}`;
    const headers = new Headers();
    if (key && action !== "open") headers.set("Authorization", key);
    if (body !== undefined) headers.set("Content-Type", "application/json");
    const response = await fetch(new URL("/api/v1/platform-sessions/" + path, base), {
      method: "POST",
      headers,
      body,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(action !== "open" && action !== "verify" ? 20000 : 90000),
    });
    if (response.status === 204) return new NextResponse(null, { status: 204, headers: noStore });
    if (action === "frame" && response.ok) {
      return new NextResponse(response.body, { headers: { ...noStore, "Content-Type": "image/jpeg" } });
    }
    return NextResponse.json(await response.json(), { status: response.status, headers: noStore });
  } catch {
    return failure("verification_unavailable", 503);
  }
}
