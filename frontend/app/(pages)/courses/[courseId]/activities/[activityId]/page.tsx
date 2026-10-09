"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import type { components } from "@/lib/generated/api";
import { ArrowLeft, ArrowRight, LoaderCircle } from "lucide-react";
import { LearningSidePanel } from "@/components/LearningSidePanel";
import { PreparedLessonContent } from "@/components/PreparedLessonContent";
import { studyWorkspaceIdentity, visibleWorkspaceContent } from "@/lib/study-workspace-state.mjs";
import { preparationFailureMessage } from "@/lib/learning-route";

type Activity = components["schemas"]["LearningActivityOut"];
type Preparation = components["schemas"]["PreparationOut"];
type Content = components["schemas"]["PreparedLessonContentOut"];
type Format = Content["presentation_format"];

const FORMATS: Format[] = ["concise", "detailed", "worked_example", "analogy", "diagram", "source_view", "quiz_first"];
const REMEDIATION = "PREREQUISITE_REMEDIATION";

function sourceError(category?: string | null): string {
  if (category === "DIAGRAM_SOURCE_SUPPORT_FAILED" || category === "INVALID_DIAGRAM_CONNECTION") {
    return preparationFailureMessage(category);
  }
  if (category === "INSUFFICIENT_SOURCE_PROVENANCE") {
    return "This activity has no valid linked passage in the published course sources. Your activity is saved; return to the course overview for current work.";
  }
  if (category === "INSUFFICIENT_SOURCE_SUPPORT" || category === "CONTENT_SCHEMA_OR_GROUNDING_FAILED") {
    return preparationFailureMessage(category);
  }
  if (category === "STALE_SOURCE_VERSION" || category === "STALE_COURSE_VERSION") {
    return "The published course version changed. Your saved progress is intact; return to the course overview to continue with current work.";
  }
  return "Preparation paused. Your activity and saved progress are safe. Retry preparation when you are ready.";
}

function labelFor(activity: Activity, names: string[]): string {
  if (activity.is_optional_linked_check) return `Optional prerequisite check: ${names[0] || "possible earlier knowledge"}`;
  if (activity.activity_type === REMEDIATION) return `Review ${names[0] || "this concept"}`;
  if (activity.activity_type === "TARGETED_PRACTICE") return `Practice ${names[0] || "this concept"}`;
  if (names.length > 1) return `Apply ${names.join(" and ")}`;
  return `Try an application of ${names[0] || "this concept"}`;
}

function formatLabel(format: Format): string {
  return format === "worked_example" ? "Worked example" : format.replaceAll("_", " ");
}

export default function AdaptiveActivityPage() {
  const params = useParams();
  const router = useRouter();
  const courseId = params.courseId as string;
  const activityId = params.activityId as string;
  const identity = studyWorkspaceIdentity(courseId, activityId);
  const [activityState, setActivity] = useState<{ identity: string; value: Activity } | null>(null);
  const activity = activityState?.identity === identity ? activityState.value : null;
  const [conceptNames, setConceptNames] = useState<Record<string, string>>({});
  const [preparation, setPreparation] = useState<Preparation | null>(null);
  const [contentState, setContent] = useState<{ identity: string; content: Content } | null>(null);
  const [quizReady, setQuizReady] = useState<{ identity: string; artifactId: string; ready: boolean } | null>(null);
  const [format, setFormat] = useState<Format>("detailed");
  const [isLoading, setIsLoading] = useState(true);
  const [contentLoading, setContentLoading] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [sourceToOpen, setSourceToOpen] = useState<string | null>(null);
  const [sourceOpenRequestId, setSourceOpenRequestId] = useState(0);
  const openSource = useCallback((chunkId: string) => {
    setSourceToOpen(chunkId);
    setSourceOpenRequestId((requestId) => requestId + 1);
  }, []);
  const [starting, setStarting] = useState(false);
  const [saved, setSaved] = useState(true);
  const [readingPosition, setReadingPosition] = useState(0);
  const activityRequestRef = useRef<AbortController | null>(null);
  const contentRequestRef = useRef<AbortController | null>(null);
  const activityInFlightRef = useRef(false);
  const formatRef = useRef(format);
  const saveTimerRef = useRef<number | null>(null);
  const saveQueueRef = useRef<Promise<void>>(Promise.resolve());
  const restoredActivityRef = useRef<string | null>(null);

  const isRemediation = activity?.activity_type === REMEDIATION;
  const isOptionalLinkedCheck = activity?.is_optional_linked_check ?? false;
  const isQuestionFirst = isOptionalLinkedCheck || activity?.activity_type === "TARGETED_PRACTICE" || activity?.activity_type === "CHALLENGE";
  const names = useMemo(() => activity?.target_concept_ids.map((id) => conceptNames[id] || "Selected concept") || [], [activity, conceptNames]);
  const heading = activity ? labelFor(activity, names) : "Learning activity";
  const displayedContent = isRemediation ? visibleWorkspaceContent(contentState, identity, format) as Content | null : null;
  const warmupReady = format !== "quiz_first" || (quizReady?.identity === identity
    && quizReady.artifactId === displayedContent?.artifact_id && quizReady.ready);
  const updateQuizReady = useCallback((ready: boolean) => {
    if (displayedContent) setQuizReady({ identity, artifactId: displayedContent.artifact_id, ready });
  }, [identity, displayedContent]);
  const isPreparing = preparation ? ["PENDING", "RUNNING"].includes(preparation.status) : false;
  const hasFailure = preparation?.status === "RECOVERABLE_FAILURE";
  const questionsReady = Boolean(preparation?.assessment_ready);

  const loadActivity = useCallback(async () => {
    if (activityInFlightRef.current) return;
    activityInFlightRef.current = true;
    activityRequestRef.current?.abort();
    const controller = new AbortController();
    activityRequestRef.current = controller;
    try {
      const response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}`, { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || "This saved activity could not be loaded.");
      if (controller.signal.aborted) return;
      const savedActivity = payload as Activity;
      setActivity({ identity, value: savedActivity });
      setPreparation(savedActivity.preparation ?? null);
      setReadingPosition(savedActivity.reading_position);
      setSaved(true);
      if (savedActivity.assessment_session_id) {
        router.replace(`/courses/${courseId}/assessment?type=activity&sessionId=${savedActivity.assessment_session_id}`);
        return;
      }
      if (savedActivity.experience_availability === "UNAVAILABLE") {
        setPageError(savedActivity.unavailable_reason || "This saved activity is unavailable in the current course version.");
      } else {
        setPageError(null);
        if (FORMATS.includes(savedActivity.presentation_format as Format)) {
          formatRef.current = savedActivity.presentation_format as Format;
          setFormat(savedActivity.presentation_format as Format);
        }
      }
    } catch (cause) {
      if (!controller.signal.aborted) setPageError(cause instanceof Error ? cause.message : "This saved activity could not be loaded.");
    } finally {
      if (!controller.signal.aborted) {
        activityInFlightRef.current = false;
        setIsLoading(false);
      }
    }
  }, [activityId, courseId, router, identity]);

  const loadContent = useCallback(async (selectedFormat: Format) => {
    if (!isRemediation) return;
    contentRequestRef.current?.abort();
    const controller = new AbortController();
    contentRequestRef.current = controller;
    setContentLoading(true);
    try {
      const response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}/content?format=${encodeURIComponent(selectedFormat)}`, { cache: "no-store", signal: controller.signal });
      const payload: components["schemas"]["ActivityContentResponseOut"] = await response.json();
      if (controller.signal.aborted || formatRef.current !== selectedFormat) return;
      setPreparation(payload.preparation ?? null);
      if (response.ok && payload.status === "READY" && payload.content) setContent({ identity, content: payload.content });
    } catch {
      if (!controller.signal.aborted) setActionError("Saved explanation could not be loaded. Your activity and progress remain available.");
    } finally {
      if (!controller.signal.aborted) setContentLoading(false);
    }
  }, [activityId, courseId, isRemediation, identity]);

  useEffect(() => {
    const controller = new AbortController();
    void loadActivity();
    void fetch(`/api/v1/courses/${courseId}/graph`, { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() : null)
      .then((graph: components["schemas"]["GraphOut"] | null) => {
        if (graph && !controller.signal.aborted) setConceptNames(Object.fromEntries(graph.concepts.map((concept) => [concept.id, concept.name])));
      })
      .catch(() => undefined);
    return () => {
      controller.abort();
      activityRequestRef.current?.abort();
      contentRequestRef.current?.abort();
      activityInFlightRef.current = false;
    };
  }, [activityId, courseId, loadActivity]);

  useEffect(() => { formatRef.current = format; }, [format]);
  useEffect(() => { if (activity && isRemediation) void loadContent(format); }, [activity, format, isRemediation, loadContent]);
  useEffect(() => {
    if (!isPreparing || pageError) return;
    const timer = window.setTimeout(() => {
      void loadActivity();
      if (isRemediation) void loadContent(formatRef.current);
    }, 2500);
    return () => window.clearTimeout(timer);
  }, [isPreparing, isRemediation, loadActivity, loadContent, pageError, preparation]);
  useEffect(() => () => { activityRequestRef.current?.abort(); contentRequestRef.current?.abort(); }, []);

  const saveProgress = useCallback((position: number, selectedFormat: Format, keepalive = false) => {
    const save = saveQueueRef.current.catch(() => undefined).then(async () => {
      const response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reading_position: position, presentation_format: selectedFormat }),
        keepalive,
      });
      if (!response.ok) throw new Error("Could not save activity progress");
      if (!keepalive) setSaved(true);
    });
    saveQueueRef.current = save;
    return save;
  }, [activityId, courseId]);

  useEffect(() => {
    if (!isRemediation || !activity) return;
    if (readingPosition > 0 && displayedContent && !contentLoading && restoredActivityRef.current !== activityId) {
      window.scrollTo(0, readingPosition);
      restoredActivityRef.current = activityId;
    }
  }, [activity, activityId, contentLoading, displayedContent, isRemediation, readingPosition]);

  useEffect(() => {
    if (!isRemediation || !activity) return;
    const onScroll = () => {
      setSaved(false);
      if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = window.setTimeout(() => {
        void saveProgress(Math.max(0, Math.round(window.scrollY)), formatRef.current).catch(() => {
          setActionError("Your latest reading position could not be saved. Retry before leaving.");
          setSaved(false);
        });
      }, 500);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
      void saveProgress(Math.max(0, Math.round(window.scrollY)), formatRef.current, true).catch(() => undefined);
    };
  }, [activity, isRemediation, saveProgress]);

  const retryPreparation = async () => {
    setActionError(null);
    try {
      const query = isRemediation && !displayedContent && preparation?.stage === "CONTENT"
        ? `?format=${encodeURIComponent(format)}` : "";
      const response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}/preparation/retry${query}`, { method: "POST" });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || "Preparation could not be retried.");
      setPreparation(payload.preparation ?? null);
      await loadActivity();
      if (isRemediation) await loadContent(format);
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "Preparation could not be retried.");
    }
  };

  const switchFormat = async (nextFormat: Format) => {
    if (nextFormat === format || !isRemediation) return;
    setActionError(null);
    const currentPosition = Math.max(0, Math.round(window.scrollY));
    try {
      await saveProgress(currentPosition, nextFormat);
      formatRef.current = nextFormat;
      setFormat(nextFormat);
    } catch {
      setActionError("The selected presentation format could not be saved. Your current format is unchanged.");
    }
  };

  const startQuestions = async () => {
    if (!activity || !questionsReady || (isRemediation && (!displayedContent || !warmupReady)) || starting) return;
    setStarting(true);
    setActionError(null);
    try {
      if (isRemediation && !activity.reading_completed_at) {
        await saveProgress(Math.max(0, Math.round(window.scrollY)), format);
        const response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}/reading-complete`, { method: "POST" });
        const payload = await response.json().catch(() => null);
        if (!response.ok) throw new Error(payload?.detail || "Reading completion could not be saved.");
        const savedActivity = payload as Activity;
        if (savedActivity.assessment_session_id) {
          router.push(`/courses/${courseId}/assessment?type=activity&sessionId=${savedActivity.assessment_session_id}`);
          return;
        }
      }
      router.push(`/courses/${courseId}/assessment?type=activity&activityId=${activityId}`);
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "Questions could not be opened.");
    } finally {
      setStarting(false);
    }
  };

  const skipOptionalCheck = async () => {
    if (!isOptionalLinkedCheck || starting) return;
    setStarting(true);
    setActionError(null);
    try {
      const response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}/skip-optional-check`, { method: "POST" });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || "This optional check could not be skipped.");
      router.push(`/courses/${courseId}/learn`);
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "This optional check could not be skipped.");
    } finally {
      setStarting(false);
    }
  };

  if (isLoading && !activity) return <main className="nl-screen grid place-items-center p-6"><p className="flex items-center gap-3" role="status"><LoaderCircle className="size-5 animate-spin" />Loading your saved activity…</p></main>;
  if (!activity || pageError || activity.experience_availability === "UNAVAILABLE") return <main className="nl-screen grid place-items-center px-5 py-14 text-center"><section className="nl-card nl-card-raised w-full max-w-2xl"><p className="nl-kicker">Saved learning activity</p><h1 className="text-2xl font-bold">This activity is unavailable right now</h1><p className="mt-3 text-zinc-700">{pageError || activity?.unavailable_reason || "Your saved activity could not be loaded."}</p><div className="nl-row mt-6 justify-center"><Link href={`/courses/${courseId}/learn`} className="nl-button nl-button-primary">Course overview</Link><Link href="/dashboard" className="nl-button">Dashboard</Link></div></section></main>;

  const sourceIds = displayedContent?.source_chunk_ids || [];

  return <div className="nl-screen">
    <nav className="nl-topbar" aria-label="Activity navigation"><Link href={`/courses/${courseId}/learn`} className="nl-button"><ArrowLeft className="size-4" /><span className="hidden sm:inline">Back to course</span></Link><div className="nl-crumb"><small>{isOptionalLinkedCheck ? "Optional prerequisite check" : isRemediation ? "Concept review" : activity.activity_type === "TARGETED_PRACTICE" ? "Targeted practice" : "Challenge"}</small><strong>{heading}</strong></div><span className="nl-spacer" /><span className={`nl-chip ${saved ? "nl-chip-mint" : "nl-chip-yellow"}`}>{saved ? "Progress saved" : "Saving progress…"}</span></nav>
    <main className="nl-shell nl-grid">
      <article className="nl-stack min-w-0">
        <header className="nl-card nl-card-accent">
          <p className="nl-kicker">{isRemediation ? "Focused teaching" : isQuestionFirst ? "Questions first" : "Selected activity"}</p>
          <h1 className="text-3xl font-bold">{heading}</h1>
          {activity.activity_type === "CHALLENGE" && names.length === 1 && <p className="mt-3"><span className="nl-chip nl-chip-yellow">Single-concept application</span></p>}
          {names.length > 0 && <ul className="mt-4 flex flex-wrap gap-2" aria-label="Selected concepts">{names.map((name, index) => <li key={`${name}-${index}`} className="nl-chip nl-chip-violet">{name}</li>)}</ul>}
        </header>

        <section className="nl-card">
          <h2 className="text-xl font-bold">Why this was selected</h2>
          <p className="mt-2 leading-relaxed">{activity.reason || "This activity was selected from your saved course evidence."}</p>
          <p className="mt-3 font-bold">{activity.question_count} fixed questions · question types are shown in the saved assessment</p>
          {isQuestionFirst && <p className="mt-2 text-sm text-zinc-700">{isOptionalLinkedCheck ? "The check uses supported passages from both courses. Skipping it records no answer or negative evidence." : "You can start questions directly. No lesson or reading completion is required."}</p>}
        </section>

        {isRemediation && <section className="nl-card" aria-label="Saved focused explanation" aria-live="polite">
          <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-xl font-bold">A focused explanation</h2><span className="text-sm text-zinc-700">Your reading place is saved.</span></div>
          <div className="mt-3 flex flex-wrap border-2 border-black" role="group" aria-label="Presentation format">{FORMATS.map((item) => <button key={item} type="button" aria-pressed={format === item} onClick={() => void switchFormat(item)} className={`border-r-2 border-black px-3 py-2 capitalize last:border-r-0 ${format === item ? "bg-[#FFD23F] text-black" : "bg-white hover:bg-zinc-100"}`}>{formatLabel(item)}</button>)}</div>
          {displayedContent ? <PreparedLessonContent state={contentState} identity={identity} courseId={courseId} format={format}
            activeSourceChunkId={sourceToOpen} onOpenSource={openSource} onQuizReadyChange={updateQuizReady} /> : contentLoading || isPreparing ? <p className="mt-4 flex items-center gap-3 border-2 border-dashed border-zinc-500 p-4 text-zinc-700" role="status"><LoaderCircle className="size-5 animate-spin" />{contentLoading ? `Loading saved ${formatLabel(format)} content…` : `Preparing saved explanation and questions… ${preparation?.progress ?? 0}%`}</p> : !hasFailure && <p className="mt-4 text-zinc-700">Saved teaching content is not available yet.</p>}
        </section>}

        {isPreparing && isQuestionFirst && <section className="nl-card" role="status"><h2 className="flex items-center gap-2 text-lg font-bold"><LoaderCircle className="size-5 animate-spin" />Preparing fresh questions</h2><p className="mt-2 text-zinc-700">The selected activity is saved while questions are prepared. You can return to the course overview.</p><div className="nl-progress mt-4"><span style={{ width: `${preparation?.progress ?? 0}%` }} /></div></section>}
        {hasFailure && <section className="border-2 border-amber-800 bg-amber-50 p-5 text-amber-950" role="alert"><h2 className="text-lg font-bold">{isRemediation && !displayedContent && preparation?.stage === "CONTENT" ? "This format could not be prepared" : "Valid questions are not ready"}</h2><p className="mt-2">{sourceError(preparation?.error_category)}</p><button type="button" onClick={() => void retryPreparation()} className="nl-button mt-4" disabled={starting}>Retry preparation</button></section>}
        {isRemediation && displayedContent && hasFailure && <p className="border-2 border-amber-800 bg-amber-50 p-4 text-sm text-amber-950" role="status">The saved explanation is still available. The failed questions stage can be retried above.</p>}
        {actionError && <p role="alert" className="border-2 border-red-800 bg-red-50 p-3 text-red-900">{actionError}</p>}

        <footer className="nl-card flex flex-wrap items-center justify-between gap-4">
          <p className="max-w-xl text-sm text-zinc-700">{isRemediation ? "Reading completion records coverage only. It does not count as understanding." : isOptionalLinkedCheck ? "This check is optional. Skipping it leaves your evidence unchanged." : "Question-first activities do not require a reading step."}</p>
          <div className="flex flex-wrap gap-3">
            {isOptionalLinkedCheck && <button type="button" onClick={() => void skipOptionalCheck()} disabled={starting} className="nl-button">Skip optional check</button>}
            <button type="button" onClick={() => void startQuestions()} disabled={!questionsReady || starting || (Boolean(isRemediation) && (!displayedContent || !warmupReady))} className="nl-button nl-button-primary nl-button-large">
              {starting || (isPreparing && questionsReady) ? <LoaderCircle className="size-5 animate-spin" /> : null}
              {isOptionalLinkedCheck ? "Start optional check" : isRemediation ? "Ready for questions" : activity.activity_type === "TARGETED_PRACTICE" ? "Start questions" : "Start challenge"} · {activity.question_count}<ArrowRight className="size-5" />
            </button>
          </div>
        </footer>
      </article>

      <LearningSidePanel courseId={courseId} contextLessonId={activity.lesson_id} decisionId={activity.decision_id} sourceIds={sourceIds} initialSourceChunkId={sourceToOpen} sourceOpenRequestId={sourceOpenRequestId} conversationStorageKey={`activity:${activity.id}`} />
    </main>
  </div>;
}
