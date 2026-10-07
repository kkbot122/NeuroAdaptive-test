"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Loader2 } from "lucide-react";
import type { components } from "@/lib/generated/api";

type Activity = components["schemas"]["LearningActivityOut"];
type Preparation = components["schemas"]["PreparationOut"];
type Content = components["schemas"]["PreparedLessonContentOut"];

const REMEDIATION = "PREREQUISITE_REMEDIATION";

function sourceError(category?: string | null): string {
  if (category === "INSUFFICIENT_SOURCE_PROVENANCE" || category === "INSUFFICIENT_SOURCE_SUPPORT") {
    return "This course version has no supporting source material linked to this concept. Your activity is saved, but a grounded explanation and questions cannot be prepared until supporting material is available.";
  }
  if (category === "STALE_SOURCE_VERSION" || category === "STALE_COURSE_VERSION") {
    return "The published course version changed while this activity was preparing. Your saved progress is intact; return to Continue studying to load current work.";
  }
  return "Preparation paused. Your activity and saved progress are safe. Retry preparation when you are ready.";
}

export default function AdaptiveActivityPage() {
  const params = useParams();
  const router = useRouter();
  const courseId = params.courseId as string;
  const activityId = params.activityId as string;
  const [activity, setActivity] = useState<Activity | null>(null);
  const [conceptNames, setConceptNames] = useState<Record<string, string>>({});
  const [preparation, setPreparation] = useState<Preparation | null>(null);
  const [content, setContent] = useState<Content | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isStarting, setIsStarting] = useState(false);
  const requestInFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    try {
      const response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}`, { cache: "no-store" });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || "This saved activity could not be loaded.");
      const savedActivity = payload as Activity;
      setActivity(savedActivity);
      setPreparation(savedActivity.preparation ?? null);
      if (savedActivity.experience_availability === "UNAVAILABLE") {
        setPageError(savedActivity.unavailable_reason || "This activity is unavailable from the saved course version.");
        setContent(null);
        return;
      }
      setPageError(null);
      if (savedActivity.activity_type === REMEDIATION) {
        const contentResponse = await fetch(
          `/api/v1/courses/${courseId}/activities/${activityId}/content?format=${encodeURIComponent(savedActivity.presentation_format)}`,
          { cache: "no-store" },
        );
        const contentPayload: components["schemas"]["ActivityContentResponseOut"] = await contentResponse.json();
        setPreparation(contentPayload.preparation ?? savedActivity.preparation ?? null);
        setContent(contentResponse.ok && contentPayload.status === "READY" ? contentPayload.content ?? null : null);
      } else {
        setContent(null);
      }
    } catch (error) {
      setPageError(error instanceof Error ? error.message : "This saved activity could not be loaded.");
    } finally {
      setIsLoading(false);
      requestInFlight.current = false;
    }
  }, [activityId, courseId]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    let cancelled = false;
    void fetch(`/api/v1/courses/${courseId}/graph`, { cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then((graph: components["schemas"]["GraphOut"] | null) => {
        if (!graph || cancelled) return;
        setConceptNames(Object.fromEntries((graph.concepts || []).map((concept) => [concept.id, concept.name])));
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [courseId]);

  const targets = activity?.target_concept_ids ?? [];
  const names = targets.map((id) => conceptNames[id] || id);
  const isRemediation = activity?.activity_type === REMEDIATION;
  const isQuestionOnly = activity?.activity_type === "TARGETED_PRACTICE" || activity?.activity_type === "CHALLENGE";
  const ready = Boolean(preparation?.assessment_ready);
  const isPreparing = preparation?.status === "PENDING" || preparation?.status === "RUNNING";
  const hasSourceLimitation = preparation?.error_category === "INSUFFICIENT_SOURCE_PROVENANCE"
    || preparation?.error_category === "INSUFFICIENT_SOURCE_SUPPORT";

  const retryPreparation = async () => {
    setActionError(null);
    try {
      const response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}/preparation/retry`, { method: "POST" });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || "Preparation could not be retried.");
      setPreparation(payload.preparation ?? null);
      await refresh();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Preparation could not be retried.");
    }
  };

  const startQuestions = async () => {
    if (!activity || !ready || (isRemediation && !content)) return;
    setIsStarting(true);
    setActionError(null);
    try {
      if (isRemediation && !activity.reading_completed_at) {
        const response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}/reading-complete`, { method: "POST" });
        const payload = await response.json().catch(() => null);
        if (!response.ok) throw new Error(payload?.detail || "Reading completion could not be saved.");
      }
      router.push(`/courses/${courseId}/assessment?type=activity&activityId=${activityId}`);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Questions could not be opened.");
    } finally {
      setIsStarting(false);
    }
  };

  if (isLoading && !activity) {
    return <main className="flex min-h-screen items-center justify-center bg-[#F4F1EA] font-[family-name:var(--font-kodchasan)]"><Loader2 className="mr-3 animate-spin" />Loading your saved activity…</main>;
  }

  if (!activity || pageError) {
    return (
      <main className="min-h-screen bg-[#F4F1EA] px-6 py-16 text-center font-[family-name:var(--font-kodchasan)] text-black">
        <h1 className="text-2xl font-black">This activity is unavailable right now</h1>
        <p className="mx-auto mt-3 max-w-2xl">{pageError || "The saved activity could not be loaded."}</p>
        <div className="mt-6 flex justify-center gap-4">
          <Link href={`/courses/${courseId}/learn`} className="border-2 border-black bg-[#FF9F1C] px-5 py-3 font-bold">Continue studying</Link>
          <Link href="/dashboard" className="border-2 border-black bg-white px-5 py-3 font-bold">Dashboard</Link>
        </div>
      </main>
    );
  }

  const heading = isRemediation
    ? `Review ${names[0] || "this concept"}`
    : activity.activity_type === "TARGETED_PRACTICE"
      ? `Practice ${names[0] || "this concept"}`
      : names.length > 1
        ? `Apply ${names.join(" and ")}`
        : `Try a new application of ${names[0] || "this concept"}`;

  return (
    <main className="min-h-screen bg-[#F4F1EA] pb-24 font-[family-name:var(--font-kodchasan)] text-black">
      <nav className="sticky top-0 z-10 flex items-center gap-4 border-b-2 border-black bg-white px-6 py-4">
        <Link href="/dashboard" aria-label="Return to dashboard" className="rounded-full border-2 border-transparent p-2 hover:border-black"><ArrowLeft className="h-5 w-5" /></Link>
        <strong className="truncate text-lg">Continue studying</strong>
      </nav>
      <div className="mx-auto max-w-4xl space-y-6 px-6 py-10">
        <header className="border-4 border-black bg-white p-7 shadow-[7px_7px_0px_0px_rgba(0,0,0,1)]">
          <p className="text-sm font-black uppercase tracking-widest text-purple-700">
            {isRemediation ? "Concept review" : activity.activity_type === "TARGETED_PRACTICE" ? "Targeted practice" : "Challenge"}
          </p>
          <h1 className="mt-2 text-3xl font-black">{heading}</h1>
          {activity.activity_type === "CHALLENGE" && names.length === 1 && <p className="mt-2 font-semibold">Single-concept application</p>}
          <ul className="mt-4 flex flex-wrap gap-2" aria-label="Selected concepts">
            {names.map((name) => <li key={name} className="border-2 border-black bg-blue-100 px-3 py-1 font-bold">{name}</li>)}
          </ul>
        </header>

        <section className="border-2 border-black bg-white p-6">
          <h2 className="text-xl font-black">Why this was selected</h2>
          <p className="mt-2 leading-relaxed">{activity.reason}</p>
          <p className="mt-3 font-bold">{activity.question_count} fresh multiple-choice questions</p>
          {isQuestionOnly && <p className="mt-2 text-gray-700">Questions are ready without a lesson or reading step.</p>}
        </section>

        {isRemediation && (
          <section className="border-2 border-black bg-white p-6">
            <h2 className="text-xl font-black">A focused explanation</h2>
            {content ? (
              <div className="mt-5 space-y-7">
                {(["objective", "explanation", "example", "recap"] as const).map((section) => (
                  <section key={section}>
                    <h3 className="text-lg font-black capitalize">{section === "example" ? "Worked example" : section}</h3>
                    <div className="mt-2 space-y-4 leading-relaxed">
                      {content.sections[section].map((statement, index) => (
                        <div key={`${section}-${index}`}>
                          <p>{statement.text}</p>
                          <p className="mt-1 flex flex-wrap gap-x-3 text-sm font-bold text-blue-700">
                            {statement.citation_chunk_ids.map((chunkId, citationIndex) => (
                              <Link key={chunkId} href={`/courses/${courseId}/sources/${chunkId}`} className="hover:underline">Source {citationIndex + 1}</Link>
                            ))}
                          </p>
                        </div>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            ) : isPreparing ? (
              <p className="mt-4 flex items-center gap-2 text-gray-700"><Loader2 className="h-5 w-5 animate-spin" />Preparing a source-grounded explanation and questions… {preparation?.progress ?? 0}%</p>
            ) : preparation?.status === "RECOVERABLE_FAILURE" ? (
              <div className="mt-4 rounded border-2 border-orange-400 bg-orange-50 p-4 text-orange-950">
                <p>{sourceError(preparation.error_category)}</p>
                <button onClick={() => void retryPreparation()} className="mt-3 border-2 border-black bg-white px-4 py-2 font-bold">Retry preparation</button>
              </div>
            ) : <p className="mt-4 text-gray-700">Preparing your focused explanation…</p>}
          </section>
        )}

        {isRemediation && content && preparation?.status === "RECOVERABLE_FAILURE" && (
          <section role="alert" className="border-2 border-orange-400 bg-orange-50 p-5 text-orange-950">
            <p>{sourceError(preparation.error_category)}</p>
            <button onClick={() => void retryPreparation()} className="mt-3 border-2 border-black bg-white px-4 py-2 font-bold">Retry preparation</button>
          </section>
        )}

        {isQuestionOnly && isPreparing && (
          <section className="flex items-center gap-3 border-2 border-black bg-white p-6 text-gray-700"><Loader2 className="h-5 w-5 animate-spin" />Preparing fresh questions… {preparation?.progress ?? 0}%</section>
        )}
        {isQuestionOnly && preparation?.status === "RECOVERABLE_FAILURE" && (
          <section className="border-2 border-orange-400 bg-orange-50 p-5 text-orange-950">
            <p>{sourceError(preparation.error_category)}</p>
            <button onClick={() => void retryPreparation()} className="mt-3 border-2 border-black bg-white px-4 py-2 font-bold">Retry preparation</button>
          </section>
        )}

        {actionError && <p role="alert" className="font-bold text-red-700">{actionError}</p>}
        {hasSourceLimitation && !isRemediation && <p className="text-orange-900">{sourceError(preparation?.error_category)}</p>}
        <div className="flex flex-col items-end gap-3">
          <button
            onClick={() => void startQuestions()}
            disabled={!ready || isStarting || (Boolean(isRemediation) && !content)}
            className="border-2 border-black bg-[#FF9F1C] px-7 py-3 font-black shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isStarting ? "Opening questions…" : isRemediation ? activity.reading_completed_at ? "Continue to questions" : "Ready for questions" : "Start questions"}
          </button>
          {!ready && <p className="text-sm text-gray-700">Questions will open when the saved set is ready.</p>}
        </div>
      </div>
    </main>
  );
}
