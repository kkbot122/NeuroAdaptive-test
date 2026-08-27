import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/auth";
import { requireInternalToken } from "@/lib/internal-auth";

/**
 * Persists a completed quiz attempt for the signed-in user.
 *
 * The backend re-grades the attempt from the questions and answers; the
 * client's own score is not accepted. Before this existed the quiz page made
 * no network calls at all and every result died in sessionStorage.
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
    const response = await fetch(`${apiUrl}/api/v1/assessment/quiz-attempts`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-user-email": session.user.email,
        "x-internal-token": requireInternalToken(),
      },
      body: JSON.stringify(body),
    });

    const payload = await response.json().catch(() => null);

    if (!response.ok) {
      console.error("Quiz attempt forward failed:", response.status, payload);
      return NextResponse.json(
        { error: "Failed to record quiz attempt" },
        { status: response.status },
      );
    }

    return NextResponse.json(payload, { status: 201 });
  } catch (error) {
    console.error("Quiz route error:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}
