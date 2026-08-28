import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/auth"; // Update this import if your auth is elsewhere
import { requireInternalToken } from "@/lib/internal-auth";

// Rough proxy for the backend's own MAX_MESSAGE_CHARS guard (chat/router.py),
// checked here on raw upload bytes before ever proxying to the backend. A
// large attached file previously spent tens of seconds being read, forwarded,
// and PDF-extracted just to be rejected by the backend's character check --
// this rejects it instantly instead, so the dev server (and this one Node
// process) isn't tied up on a request that was always going to fail.
const MAX_FILE_BYTES = 10 * 1024 * 1024; // 10 MB
const MAX_PROMPT_CHARS = 60_000;

export async function POST(req: NextRequest) {
  try {
    const session = await auth();

    if (!session?.user?.email) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const incoming = await req.formData();

    // Explicitly extract and reconstruct FormData to avoid forwarding issues
    // where the raw FormData object from req.formData() may not serialize
    // correctly when passed as body to a new fetch() call.
    const prompt = (incoming.get("prompt") as string | null) ?? "";
    const sessionId = incoming.get("session_id") as string | null;
    const file = incoming.get("file") as File | null;

    if (prompt.length > MAX_PROMPT_CHARS) {
      return NextResponse.json(
        { error: `Your message is too long (${prompt.length.toLocaleString()} characters, limit ${MAX_PROMPT_CHARS.toLocaleString()}). Try a shorter message.` },
        { status: 413 }
      );
    }
    if (file && file.size > MAX_FILE_BYTES) {
      return NextResponse.json(
        { error: `That file is too large (${(file.size / 1024 / 1024).toFixed(1)} MB, limit ${MAX_FILE_BYTES / 1024 / 1024} MB). Try a smaller file.` },
        { status: 413 }
      );
    }

    const backendFormData = new FormData();
    backendFormData.append("prompt", prompt);
    if (sessionId) backendFormData.append("session_id", sessionId);
    if (file && file.size > 0) backendFormData.append("file", file, file.name);

    const apiUrl =
      process.env.INTERNAL_API_URL ||
      process.env.NEXT_PUBLIC_API_URL ||
      "http://backend:8000";

    const internalKey = process.env.INTERNAL_API_KEY;

    if (!internalKey) {
      return NextResponse.json({ error: "Server misconfiguration" }, { status: 500 });
    }

    const response = await fetch(`${apiUrl}/api/v1/chat/message`, {
      method: "POST",
      headers: {
        "x-user-email": session.user.email,
        "x-internal-token": requireInternalToken(),
        // Content-Type set automatically by fetch when body is FormData
      },
      body: backendFormData,
    });

    if (!response.ok) {
        const errorText = await response.text();
        console.error("FastAPI Chat Error:", response.status, errorText);
        
        let errorDetail = "Failed to process chat";
        try {
          const parsed = JSON.parse(errorText);
          if (parsed.detail) errorDetail = typeof parsed.detail === 'string' ? parsed.detail : JSON.stringify(parsed.detail);
        } catch(e) {}

        return NextResponse.json({ error: errorDetail }, { status: response.status });
    }

    const data = await response.json();
    
    return NextResponse.json(data);

  } catch (e: unknown) {
    console.error("Chat API error:", e);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}
