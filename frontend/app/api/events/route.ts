import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/auth";
import { requireInternalToken } from "@/lib/internal-auth";

/**
 * Forwards learner telemetry to the backend on behalf of the signed-in user.
 *
 * This exists so the tracking components — which are client components — never
 * hold the internal token and never address FastAPI directly. Identity is taken
 * from the server-side session, not from anything the browser sent.
 */
export async function POST(req: NextRequest) {
  const session = await auth();

  if (!session?.user?.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const apiUrl =
    process.env.INTERNAL_API_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://backend:8000";

  try {
    const response = await fetch(`${apiUrl}/api/v1/events/batch`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-user-email": session.user.email,
        "x-internal-token": requireInternalToken(),
      },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const detail = await response.text();
      console.error("Events forward failed:", response.status, detail);
      return NextResponse.json(
        { error: "Failed to record events" },
        { status: response.status },
      );
    }

    return NextResponse.json(await response.json(), { status: 202 });
  } catch (error) {
    console.error("Events route error:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}
