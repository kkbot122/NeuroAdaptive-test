"use client";

import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { LearningSidePanel } from "@/components/LearningSidePanel";
import type { components } from "@/lib/generated/api";

export default function TutorPage() {
  const { courseId } = useParams<{ courseId: string }>();
  const searchParams = useSearchParams();
  const lessonId = searchParams.get("lessonId") || undefined;
  const sessionId = searchParams.get("sessionId");
  const scope = `${courseId}:${sessionId || lessonId || "course"}`;
  const [sessionState, setSessionState] = useState<{ scope: string; submitted: boolean; error: boolean } | null>(null);

  useEffect(() => {
    if (!sessionId) return;
    const controller = new AbortController();
    void fetch(`/api/v1/courses/${courseId}/assessment-sessions/${sessionId}`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Saved assessment unavailable");
        const session: components["schemas"]["AssessmentSessionOut"] = await response.json();
        if (!controller.signal.aborted) setSessionState({ scope, submitted: session.submission_state === "SUBMITTED", error: false });
      })
      .catch(() => { if (!controller.signal.aborted) setSessionState({ scope, submitted: false, error: true }); });
    return () => controller.abort();
  }, [courseId, scope, sessionId]);

  const currentSession = sessionState?.scope === scope ? sessionState : null;
  return <div className="nl-screen">
    <nav className="nl-topbar" aria-label="Tutor navigation">
      <Link href={`/courses/${courseId}/learn`} className="nl-button"><ArrowLeft className="size-4" />Back to course</Link>
      <h1 className="text-xl font-bold">Course tutor</h1>
    </nav>
    <main className="nl-shell mx-auto max-w-5xl">
      {sessionId && !currentSession ? <p role="status">Loading saved assessment context…</p>
        : currentSession?.error ? <p role="alert">This saved assessment is unavailable. Return to the course and try again.</p>
        : <LearningSidePanel courseId={courseId} contextLessonId={lessonId}
            conversationStorageKey={scope} assessmentSubmitted={currentSession?.submitted ?? false} />}
    </main>
  </div>;
}
