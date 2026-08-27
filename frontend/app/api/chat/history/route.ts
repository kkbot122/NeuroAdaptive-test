import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/auth";
import { requireInternalToken } from "@/lib/internal-auth";

export async function GET(req: NextRequest) {
  try {
    const session = await auth();

    if (!session?.user?.email) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { searchParams } = new URL(req.url);
    const sessionId = searchParams.get("sessionId");

    if (!sessionId) {
      return NextResponse.json({ error: "Session ID is required" }, { status: 400 });
    }

    const apiUrl =
      process.env.INTERNAL_API_URL ||
      process.env.NEXT_PUBLIC_API_URL ||
      "http://backend:8000";
      

    const response = await fetch(`${apiUrl}/api/v1/chat/sessions/${sessionId}/messages`, {
      headers: {
        "x-user-email": session.user.email,
        "x-internal-token": requireInternalToken(),
      },
      cache: "no-store"
    });

    if (!response.ok) {
      return NextResponse.json({ error: "Failed to fetch history" }, { status: response.status });
    }

    const data = await response.json();
    return NextResponse.json(data);

  } catch (error) {
    console.error("Chat History Proxy Error:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}
