/**
 * Catch-all BFF proxy for every `/api/v1/*` call the frontend makes.
 *
 * Every page under app/(pages)/courses/** and the dashboard calls fetch()
 * against a bare relative path like `/api/v1/courses` -- that request never
 * reaches the FastAPI backend at all; it hits this Next.js server, which
 * previously had no route registered for it (404), and even if it had, the
 * backend's get_current_user (core/security.py) requires x-user-email and
 * x-internal-token headers that only server-side code can attach -- a
 * browser can never supply the internal token, since it is a secret that is
 * deliberately never sent to the client (see lib/internal-auth.ts).
 *
 * This mirrors the pattern app/api/chat/route.ts already established for
 * one endpoint: resolve the NextAuth session server-side, attach the
 * trusted BFF headers, forward to the backend, stream the response straight
 * back. Doing it once, generically, here means every current and future
 * `/api/v1/...` call from any client component works without a bespoke
 * route file per endpoint.
 *
 * Request and response bodies are streamed through untouched (not buffered
 * as JSON), so this transparently supports both ordinary JSON calls and the
 * two cases that would break under a naive JSON-only proxy: multipart file
 * uploads (courses/[id]/workspace's document upload) and the tutor's SSE
 * (text/event-stream) response.
 */
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/auth";
import { requireInternalToken } from "@/lib/internal-auth";

const BACKEND_URL =
  process.env.INTERNAL_API_URL ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "http://backend:8000";

// Hop-by-hop / connection-management headers must not be forwarded verbatim
// in either direction -- copying them can corrupt the proxied response
// (e.g. a stale Content-Length after the body is re-streamed).
const STRIP_REQUEST_HEADERS = new Set(["host", "connection", "content-length"]);
const STRIP_RESPONSE_HEADERS = new Set(["content-encoding", "content-length", "connection", "transfer-encoding"]);

async function proxy(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const session = await auth();
  if (!session?.user?.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let internalToken: string;
  try {
    internalToken = requireInternalToken();
  } catch (err) {
    console.error("api/v1 proxy misconfiguration:", err);
    return NextResponse.json({ error: "Server misconfiguration" }, { status: 500 });
  }

  const { path } = await context.params;
  const targetUrl = `${BACKEND_URL}/api/v1/${path.join("/")}${req.nextUrl.search}`;

  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!STRIP_REQUEST_HEADERS.has(key.toLowerCase())) headers.set(key, value);
  });
  headers.set("x-user-email", session.user.email);
  headers.set("x-internal-token", internalToken);

  const hasBody = req.method !== "GET" && req.method !== "HEAD";

  let backendRes: Response;
  try {
    backendRes = await fetch(targetUrl, {
      method: req.method,
      headers,
      body: hasBody ? req.body : undefined,
      // Required by undici/fetch when streaming a request body from a
      // ReadableStream (the incoming request) rather than a buffered value.
      ...(hasBody ? { duplex: "half" } : {}),
    } as RequestInit);
  } catch (err) {
    console.error("api/v1 proxy: backend unreachable:", err);
    return NextResponse.json({ error: "Backend is unreachable" }, { status: 502 });
  }

  const responseHeaders = new Headers();
  backendRes.headers.forEach((value, key) => {
    if (!STRIP_RESPONSE_HEADERS.has(key.toLowerCase())) responseHeaders.set(key, value);
  });

  return new NextResponse(backendRes.body, {
    status: backendRes.status,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
