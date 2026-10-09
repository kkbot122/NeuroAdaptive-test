"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import type { components } from "@/lib/generated/api";
import { ArrowLeft, CheckCircle, LoaderCircle } from "lucide-react";
import { LearningSidePanel } from "@/components/LearningSidePanel";
import { EmptyLessonContentState } from "@/components/EmptyLessonContentState";
import { PreparedLessonContent } from "@/components/PreparedLessonContent";
import { StudyPositionRestorer } from "@/components/StudyPositionRestorer";
import { preparationFailureMessage } from "@/lib/learning-route";
import {
  canCommitWorkspaceResponse,
  studyWorkspaceIdentity,
  updateWorkspaceState,
  visibleWorkspaceContent,
  workspaceStateForIdentity,
} from "@/lib/study-workspace-state.mjs";

const FORMATS = ["concise", "detailed", "worked_example", "analogy", "diagram", "source_view", "quiz_first"] as const;
type Format = (typeof FORMATS)[number];
type Content = components["schemas"]["PreparedLessonContentOut"];
type Preparation = components["schemas"]["PreparationOut"];
type Activity = components["schemas"]["LearningActivityOut"];
type WorkspaceContentState = {
  identity: string;
  content: Content | null;
  preparation: Preparation | null;
  error: string | null;
  loading: boolean;
};

function formatLabel(format: Format): string {
  return format === "worked_example" ? "Worked example" : format.replaceAll("_", " ");
}

export default function StudyLessonPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();
  const courseId = params.courseId as string;
  const lessonId = params.lessonId as string;
  const activityId = searchParams.get("activityId");
  const workspaceIdentity = studyWorkspaceIdentity(courseId, activityId);
  const requestedFormat = searchParams.get("format") as Format | null;
  const [course, setCourse] = useState<components["schemas"]["CourseOut"] | null>(null);
  const [lesson, setLesson] = useState<components["schemas"]["LessonOut"] | null>(null);
  const [activityState, setActivityState] = useState<{ identity: string; value: Activity | null } | null>(null);
  const [conceptNames, setConceptNames] = useState<Record<string, string>>({});
  const [format, setFormat] = useState<Format>(requestedFormat && FORMATS.includes(requestedFormat) ? requestedFormat : "detailed");
  const [workspaceContent, setWorkspaceContent] = useState<WorkspaceContentState | null>(null);
  const [savedReadingPosition, setSavedReadingPosition] = useState<{ identity: string; position: number } | null>(null);
  const [progressError, setProgressError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [sourceToOpen, setSourceToOpen] = useState<string | null>(null);
  const [sourceOpenRequestId, setSourceOpenRequestId] = useState(0);
  const openSource = useCallback((chunkId: string) => {
    setSourceToOpen(chunkId);
    setSourceOpenRequestId((requestId) => requestId + 1);
  }, []);
  const [isCompleting, setIsCompleting] = useState(false);
  const [quizReady, setQuizReady] = useState<{ identity: string; artifactId: string; ready: boolean } | null>(null);
  const saveTimer = useRef<number | null>(null);
  const formatRef = useRef(format);
  const contentRequestRef = useRef<AbortController | null>(null);
  const lessonRequestIdRef = useRef(0);
  const progressSaveQueueRef = useRef<Promise<void>>(Promise.resolve());
  const progressTargetRef = useRef(workspaceIdentity);
  const activeIdentityRef = useRef(workspaceIdentity);
  activeIdentityRef.current = workspaceIdentity;

  const syncFormatUrl = useCallback((selectedFormat: Format) => {
    const query = new URLSearchParams(window.location.search);
    query.set("format", selectedFormat);
    const nextUrl = `${window.location.pathname}?${query.toString()}`;
    if (`${window.location.pathname}${window.location.search}` !== nextUrl) {
      router.replace(nextUrl, { scroll: false });
    }
  }, [router]);

  const currentWorkspace = workspaceStateForIdentity(workspaceContent, workspaceIdentity) as WorkspaceContentState | null;
  const content = currentWorkspace?.content ?? null;
  const preparation = currentWorkspace?.preparation ?? null;
  const contentError = currentWorkspace?.error ?? null;
  const isContentLoading = currentWorkspace?.loading ?? true;
  const activity = activityState?.identity === workspaceIdentity ? activityState.value : null;
  const readingPosition = savedReadingPosition?.identity === workspaceIdentity ? savedReadingPosition.position : 0;
  const displayedContent = visibleWorkspaceContent(workspaceContent, workspaceIdentity, format) as Content | null;
  const warmupReady = format !== "quiz_first" || (quizReady?.identity === workspaceIdentity
    && quizReady.artifactId === displayedContent?.artifact_id && quizReady.ready);
  const updateQuizReady = useCallback((ready: boolean) => {
    if (displayedContent) setQuizReady({ identity: workspaceIdentity, artifactId: displayedContent.artifact_id, ready });
  }, [workspaceIdentity, displayedContent]);

  const fetchLessonData = useCallback(async () => {
    const requestId = ++lessonRequestIdRef.current;
    setIsLoading(true);
    setPageError(null);
    try {
      const [courseResponse, structureResponse, graphResponse, activityResponse] = await Promise.all([
        fetch(`/api/v1/courses/${courseId}`, { cache: "no-store" }),
        fetch(`/api/v1/courses/${courseId}/structure`, { cache: "no-store" }),
        fetch(`/api/v1/courses/${courseId}/graph`, { cache: "no-store" }),
        activityId ? fetch(`/api/v1/courses/${courseId}/activities/${activityId}`, { cache: "no-store" }) : Promise.resolve(null),
      ]);
      if (requestId !== lessonRequestIdRef.current) return;
      if (!courseResponse.ok || !structureResponse.ok) throw new Error("The saved course outline could not be loaded.");
      if (activityResponse && !activityResponse.ok) throw new Error("This saved activity is no longer available.");
      const currentCourse: components["schemas"]["CourseOut"] = await courseResponse.json();
      const structure: components["schemas"]["StructureOut"] = await structureResponse.json();
      const foundLesson = structure.modules.flatMap((module) => module.lessons).find((item) => item.id === lessonId);
      if (!foundLesson) throw new Error("This lesson is not in the published course version.");
      setCourse(currentCourse);
      setLesson(foundLesson);

      if (activityResponse) {
        const savedActivity: Activity = await activityResponse.json();
        setActivityState({ identity: workspaceIdentity, value: savedActivity });
        setSavedReadingPosition({ identity: workspaceIdentity, position: savedActivity.reading_position });
        setWorkspaceContent((current) => updateWorkspaceState(current, workspaceIdentity, {
          preparation: savedActivity.preparation ?? null,
        }));
        if (FORMATS.includes(savedActivity.presentation_format as Format)) {
          setFormat(savedActivity.presentation_format as Format);
          formatRef.current = savedActivity.presentation_format as Format;
          syncFormatUrl(savedActivity.presentation_format as Format);
        }
      } else {
        setActivityState({ identity: workspaceIdentity, value: null });
        setSavedReadingPosition({ identity: workspaceIdentity, position: 0 });
      }

      if (graphResponse.ok) {
        const graph: components["schemas"]["GraphOut"] = await graphResponse.json();
        setConceptNames(Object.fromEntries(graph.concepts.map((concept) => [concept.id, concept.name])));
      }
    } catch (cause) {
      if (requestId === lessonRequestIdRef.current) setPageError(cause instanceof Error ? cause.message : "The saved lesson could not be loaded.");
    } finally {
      if (requestId === lessonRequestIdRef.current) setIsLoading(false);
    }
  }, [activityId, courseId, lessonId, syncFormatUrl, workspaceIdentity]);

  const fetchContent = useCallback(async (selectedFormat: Format) => {
    const requestIdentity = workspaceIdentity;
    if (!activityId) {
      setWorkspaceContent((current) => updateWorkspaceState(current, requestIdentity, {
        error: "Open this lesson from Continue studying to load its saved learning activity.",
        loading: false,
      }));
      return;
    }
    contentRequestRef.current?.abort();
    const controller = new AbortController();
    contentRequestRef.current = controller;
    setWorkspaceContent((current) => updateWorkspaceState(current, requestIdentity, { error: null, loading: true }));
    try {
      const response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}/content?format=${encodeURIComponent(selectedFormat)}`, {
        cache: "no-store",
        signal: controller.signal,
      });
      const payload: components["schemas"]["ActivityContentResponseOut"] = await response.json();
      if (!canCommitWorkspaceResponse({
        requestIdentity,
        activeIdentity: activeIdentityRef.current,
        requestedFormat: selectedFormat,
        activeFormat: formatRef.current,
        aborted: controller.signal.aborted,
      })) return;
      setWorkspaceContent((current) => {
        const previous = updateWorkspaceState(current, requestIdentity, {});
        const ready = response.ok && payload.status === "READY" && payload.content;
        return {
          ...previous,
          preparation: payload.preparation ?? null,
          content: ready ? payload.content : previous.content,
          error: ready
            ? payload.preparation?.status === "RECOVERABLE_FAILURE"
              ? "Questions or another requested format need preparation. This saved lesson content remains available."
              : null
            : payload.status === "RECOVERABLE_FAILURE"
              ? preparationFailureMessage(payload.preparation?.error_category)
              : !response.ok && response.status !== 202
                ? "Saved lesson content is unavailable right now. Try again shortly."
                : null,
          loading: false,
        };
      });
    } catch (cause) {
      if (controller.signal.aborted) return;
      if (activeIdentityRef.current === requestIdentity) {
        setWorkspaceContent((current) => updateWorkspaceState(current, requestIdentity, {
          error: cause instanceof Error ? "Saved lesson content is unavailable right now. Try again shortly." : "Saved lesson content could not be loaded.",
          loading: false,
        }));
      }
    } finally {
      if (!controller.signal.aborted && activeIdentityRef.current === requestIdentity && formatRef.current === selectedFormat) {
        setWorkspaceContent((current) => updateWorkspaceState(current, requestIdentity, { loading: false }));
      }
    }
  }, [activityId, courseId, workspaceIdentity]);

  useEffect(() => { void fetchLessonData(); }, [fetchLessonData]);
  useEffect(() => { progressTargetRef.current = workspaceIdentity; }, [workspaceIdentity]);
  useEffect(() => { formatRef.current = format; }, [format]);
  useEffect(() => {
    const timer = window.setTimeout(() => void fetchContent(format), 0);
    return () => {
      window.clearTimeout(timer);
      contentRequestRef.current?.abort();
    };
  }, [fetchContent, format]);
  useEffect(() => {
    if (!preparation || !["PENDING", "RUNNING"].includes(preparation.status)) return;
    const timer = window.setTimeout(() => void fetchContent(format), 2500);
    return () => window.clearTimeout(timer);
  }, [preparation, format, fetchContent]);
  useEffect(() => { setSourceToOpen(null); }, [format, workspaceIdentity]);

  const saveProgress = useCallback((position: number, selectedFormat: Format, keepalive = false) => {
    if (!activityId) return Promise.resolve();
    const target = `${courseId}:${activityId}`;
    const save = progressSaveQueueRef.current.catch(() => undefined).then(async () => {
      const response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reading_position: position, presentation_format: selectedFormat }),
        keepalive,
      });
      if (!response.ok) throw new Error("Could not save lesson progress");
      if (!keepalive && progressTargetRef.current === target) setProgressError(null);
    });
    progressSaveQueueRef.current = save;
    return save;
  }, [activityId, courseId]);

  useEffect(() => {
    if (!activityId) return;
    const onScroll = () => {
      if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => {
        void saveProgress(Math.max(0, Math.round(window.scrollY)), formatRef.current).catch(() => {
          setProgressError("Progress could not be saved. Your last confirmed position is still available.");
        });
      }, 500);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
      void saveProgress(Math.max(0, Math.round(window.scrollY)), formatRef.current, true).catch(() => undefined);
    };
  }, [activityId, saveProgress]);

  const changeFormat = async (nextFormat: Format) => {
    if (nextFormat === format) return;
    const previousFormat = formatRef.current;
    formatRef.current = nextFormat;
    if (saveTimer.current !== null) {
      window.clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    try {
      await saveProgress(Math.max(0, Math.round(window.scrollY)), nextFormat);
      setProgressError(null);
    } catch {
      formatRef.current = previousFormat;
      setProgressError("The presentation format could not be saved. Your current format is unchanged.");
      return;
    }
    void fetch("/api/v1/presentation-affinity/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from_format: format, to_format: nextFormat }),
    }).catch(() => undefined);
    setFormat(nextFormat);
    syncFormatUrl(nextFormat);
  };

  const retryPreparation = async () => {
    if (!activityId) return;
    const requestIdentity = workspaceIdentity;
    setWorkspaceContent((current) => updateWorkspaceState(current, requestIdentity, { error: null, loading: true }));
    try {
      const response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}/preparation/retry?format=${encodeURIComponent(format)}`, { method: "POST" });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || "Preparation could not be retried.");
      if (activeIdentityRef.current === requestIdentity) {
        setWorkspaceContent((current) => updateWorkspaceState(current, requestIdentity, { preparation: payload.preparation ?? null }));
      }
      await fetchContent(format);
    } catch {
      if (activeIdentityRef.current === requestIdentity) {
        setWorkspaceContent((current) => updateWorkspaceState(current, requestIdentity, {
          error: "Preparation could not be restarted. Your saved activity and lesson remain available.",
          loading: false,
        }));
      }
    }
  };

  const readyForQuestions = async () => {
    if (!activityId || !preparation?.assessment_ready || !content || content.presentation_format !== format || !warmupReady || isCompleting) return;
    setIsCompleting(true);
    setProgressError(null);
    try {
      await saveProgress(Math.max(0, Math.round(window.scrollY)), format);
      const response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}/reading-complete`, { method: "POST" });
      const payload: Activity = await response.json().catch(() => null);
      if (!response.ok) throw new Error("Reading completion could not be saved.");
      if (payload?.assessment_session_id) {
        router.push(`/courses/${courseId}/assessment?type=activity&sessionId=${payload.assessment_session_id}`);
      } else {
        router.push(`/courses/${courseId}/assessment?type=activity&activityId=${activityId}`);
      }
    } catch {
      setProgressError("Reading completion could not be saved. Your place is still available; try again.");
    } finally {
      setIsCompleting(false);
    }
  };

  const citedIds = displayedContent?.source_chunk_ids || [];

  if (isLoading) return <main className="nl-screen grid place-items-center p-6"><p role="status" className="flex items-center gap-3"><LoaderCircle className="size-5 animate-spin" />Loading saved lesson and activity…</p></main>;

  return <div className="nl-screen">
    <StudyPositionRestorer
      identity={workspaceIdentity}
      readingPosition={readingPosition}
      hasActivity={Boolean(activity)}
      isLoading={isLoading}
      isContentLoading={isContentLoading}
      contentFormat={displayedContent?.presentation_format}
      format={format}
    />
    <nav className="nl-topbar" aria-label="Learning workspace">
      <Link href={`/courses/${courseId}/learn`} className="nl-button" aria-label="Back to course overview"><ArrowLeft className="size-4" /><span className="hidden sm:inline">Back to course</span></Link>
      <div className="nl-crumb"><small>{course?.title || "Course"}</small><strong>{lesson?.title || "Learning activity"}</strong></div>
      <span className="nl-spacer" />
      <span className="nl-chip nl-chip-mint">{preparation && ["PENDING", "RUNNING"].includes(preparation.status) ? `preparing content · ${preparation.progress}%` : activity?.status.replaceAll("_", " ").toLowerCase() || "Saved activity"}</span>
    </nav>

    <main className="nl-shell nl-grid">
      <article className="nl-stack min-w-0" aria-label="Lesson content">
        {pageError ? <section className="nl-card" role="alert"><h1 className="text-2xl font-bold">This saved lesson is unavailable</h1><p className="mt-3 text-zinc-700">{pageError}</p><button className="nl-button mt-4" onClick={() => void fetchLessonData()}>Try again</button></section> : lesson && <>
          <header className="nl-card nl-card-accent">
            <span className="nl-chip nl-chip-yellow">Lesson</span>
            <h1 className="mt-3 text-3xl font-bold md:text-4xl">{lesson.title}</h1>
            {activity?.reason && <p className="mt-3 max-w-3xl text-zinc-700">{activity.reason}</p>}
            {lesson.objective && <p className="mt-4 border-l-4 border-black pl-4"><strong>Objective:</strong> {lesson.objective}</p>}
            {lesson.concepts.length > 0 && <ul className="mt-4 flex flex-wrap gap-2" aria-label="Concepts covered">{lesson.concepts.map((concept) => <li key={concept.concept_id} className="nl-chip nl-chip-violet">{conceptNames[concept.concept_id] || "Course concept"}</li>)}</ul>}
            <p className="mt-4 text-sm text-zinc-600">Your selected activity, course version, presentation format, and reading position are saved with this workspace.</p>
          </header>

          <section className="nl-card" aria-labelledby="format-heading">
            <div className="flex flex-wrap items-center justify-between gap-3"><h2 id="format-heading" className="font-bold">Presentation format</h2><span className="text-sm text-zinc-600">Changing format keeps your place.</span></div>
            <div className="mt-3 flex flex-wrap border-2 border-black" role="group" aria-label="Presentation format">
              {FORMATS.map((item) => <button key={item} type="button" aria-pressed={format === item} onClick={() => void changeFormat(item)} className={`min-h-11 border-r-2 border-black px-3 py-2 capitalize last:border-r-0 ${format === item ? "bg-[#FFD23F] font-bold text-black shadow-[inset_0_-3px_0_#000]" : "bg-white hover:bg-zinc-100"}`}>{formatLabel(item)}{format === item && <span className="sr-only">, selected</span>}</button>)}
            </div>
          </section>

          <section className="nl-card min-h-96" aria-label="Saved validated lesson content" aria-live="polite">
            <span className="nl-chip nl-chip-violet">{formatLabel(format)} variant</span>
            {contentError && <div className="mt-4 border-2 border-amber-700 bg-amber-50 p-4 text-amber-950" role={displayedContent ? "status" : "alert"}><p>{contentError}</p><button type="button" className="nl-button mt-3" onClick={() => void retryPreparation()}>Retry preparation</button></div>}
            {isContentLoading && !displayedContent ? <EmptyLessonContentState preparation={preparation} isLoading />
              : displayedContent ? <div>
                <PreparedLessonContent state={workspaceContent} identity={workspaceIdentity} courseId={courseId} format={format}
                  activeSourceChunkId={sourceToOpen} onOpenSource={openSource} onQuizReadyChange={updateQuizReady} />
                {preparation?.status === "RUNNING" && !preparation.assessment_ready && <p className="mt-4 flex items-center gap-2 border-2 border-blue-800 bg-blue-50 p-3 text-sm" role="status"><LoaderCircle className="size-4 animate-spin" />Lesson saved. Questions are preparing ({preparation.progress}%).</p>}
              </div>
              : !contentError && <EmptyLessonContentState preparation={preparation} isLoading={isContentLoading} />}
          </section>

          <footer className="nl-card flex flex-wrap items-center justify-between gap-4">
            <p className="max-w-xl text-sm text-zinc-700">Ready for questions records reading completion only. It does not count as understanding.</p>
            {progressError && <p role="alert" className="w-full text-sm font-semibold text-red-800">{progressError}</p>}
            {!warmupReady && <p className="text-sm text-zinc-700">Try each warm-up question or choose “I’m not sure” to reveal its explanation before continuing.</p>}
            <button type="button" className="nl-button nl-button-primary nl-button-large" onClick={() => void readyForQuestions()} disabled={!activityId || !preparation?.assessment_ready || !displayedContent || !warmupReady || isCompleting}>
              {isCompleting ? <LoaderCircle className="size-5 animate-spin" /> : <CheckCircle className="size-5" />}
              {activity?.reading_completed_at ? "Continue to questions" : "Ready for questions"}
            </button>
          </footer>
        </>}
      </article>

      {lesson && <LearningSidePanel
        courseId={courseId}
        contextLessonId={lesson.id}
        decisionId={activity?.decision_id}
        sourceIds={citedIds}
        initialSourceChunkId={sourceToOpen}
        sourceOpenRequestId={sourceOpenRequestId}
        conversationStorageKey={activityId ? `course:${courseId}:activity:${activityId}` : `course:${courseId}:lesson:${lesson.id}`}
      />}
    </main>
  </div>;
}
