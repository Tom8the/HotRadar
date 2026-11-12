import { NextRequest, NextResponse } from "next/server";
import { isLocalWorkspaceOrigin } from "../../../lib/local-origin";

export const dynamic = "force-dynamic";
const base = process.env.HOTRADAR_API_URL ?? "http://127.0.0.1:18810";
const scopes = new Set(["douyin-hot", "douyin-wuxi", "douyin-content", "weibo-wuxi", "weibo-content"]);
const noStore = { "Cache-Control": "no-store" };
const maxBytes = 256 * 1024;

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
  const token = request.headers.get("authorization");
  if (!token) return failure("invalid_operator_token", 401);
  if (request.headers.get("content-type")?.split(";", 1)[0] !== "application/json") {
    return failure("invalid_session", 415);
  }
  const reader = request.body?.getReader();
  if (!reader) return failure("invalid_session", 400);
  try {
    let size = 0;
    const chunks: Uint8Array[] = [];
    for (let read = await reader.read(); !read.done; read = await reader.read()) {
      size += read.value.byteLength;
      if (size > maxBytes) {
        await reader.cancel();
        return failure("session_too_large", 413);
      }
      chunks.push(read.value);
    }
    const response = await fetch(new URL("/api/v1/platform-sessions/" + scope, base), {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: token },
      body: Buffer.concat(chunks, size),
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(90000),
    });
    return NextResponse.json(await response.json(), { status: response.status, headers: noStore });
  } catch {
    return failure("verification_unavailable", 503);
  }
}
