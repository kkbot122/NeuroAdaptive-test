"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import type { components } from "@/lib/generated/api";
import { StateWrapper } from "@/components/StateWrapper";
import { Brain, ArrowLeft, Settings, CheckCircle, Loader2 } from "lucide-react";

// Keep the persisted activity format and content endpoint in sync with the
// server's PresentationFormat vocabulary, including formats this screen did
// not previously expose as controls.
const FORMATS = [
  "concise",
  "detailed",
  "worked_example",
  "analogy",
  "diagram",
  "source_view",
  "quiz_first",
] as const;
type Format = (typeof FORMATS)[number];

export default function StudyLessonPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();

  const courseId = params.courseId as string;
  const lessonId = params.lessonId as string;
  const activityId = searchParams.get("activityId");

  const initialFormat = (searchParams.get("format") as Format) || "detailed";

  const [isLoading, setIsLoading] = useState(true);
  const [isError, setIsError] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  const [course, setCourse] = useState<components["schemas"]["CourseOut"] | null>(null);
  const [lesson, setLesson] = useState<components["schemas"]["LessonOut"] | null>(null);
  const [conceptNames, setConceptNames] = useState<Record<string, string>>({});
  const [format, setFormat] = useState<Format>(FORMATS.includes(initialFormat) ? initialFormat : "detailed");

  const [content, setContent] = useState<components["schemas"]["PreparedLessonContentOut"] | null>(null);
  const [preparation, setPreparation] = useState<components["schemas"]["PreparationOut"] | null>(null);
  const [contentError, setContentError] = useState<string | null>(null);
  const [isContentLoading, setIsContentLoading] = useState(false);
  const [readingPosition, setReadingPosition] = useState(0);
  const [progressError, setProgressError] = useState<string | null>(null);
  const saveTimer = useRef<number | null>(null);
  const formatRef = useRef(format);

  const fetchLessonData = useCallback(async () => {
    setIsLoading(true);
    setIsError(false);
    try {
      const [courseRes, structureRes, graphRes, activityRes] = await Promise.all([
        fetch(`/api/v1/courses/${courseId}`),
        fetch(`/api/v1/courses/${courseId}/structure`),
        fetch(`/api/v1/courses/${courseId}/graph`),
        activityId ? fetch(`/api/v1/courses/${courseId}/activities/${activityId}`) : Promise.resolve(null),
      ]);
      if (!courseRes.ok) throw new Error("Failed to load course");
      setCourse(await courseRes.json());

      if (!structureRes.ok) throw new Error("Failed to load course structure");
      const structure: components["schemas"]["StructureOut"] = await structureRes.json();

      let foundLesson = null;
      for (const mod of structure.modules || []) {
        const match = mod.lessons.find((l: { id: string }) => l.id === lessonId);
        if (match) {
          foundLesson = match;
          break;
        }
      }
      if (!foundLesson) throw new Error("Lesson not found in course structure");
      setLesson(foundLesson);

      if (activityRes?.ok) {
        const activity: components["schemas"]["LearningActivityOut"] = await activityRes.json();
        setReadingPosition(activity.reading_position);
        setPreparation(activity.preparation ?? null);
        if (FORMATS.includes(activity.presentation_format as Format)) {
          setFormat(activity.presentation_format as Format);
        }
      }

      // Lessons carry concept_id, not a name (curriculum/router.py's
      // _version_out) -- names come from the graph.
      if (graphRes.ok) {
        const graph: components["schemas"]["GraphOut"] = await graphRes.json();
        const names: Record<string, string> = {};
        for (const c of graph.concepts || []) names[c.id] = c.name;
        setConceptNames(names);
      }
    } catch (err: unknown) {
      console.error(err);
      setIsError(true);
      setErrorMsg(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setIsLoading(false);
    }
  }, [courseId, lessonId, activityId, setIsLoading, setIsError, setErrorMsg, setCourse, setLesson, setConceptNames]);

  const fetchContent = useCallback(async (fmt: Format) => {
    if (!activityId) {
      setContent(null);
      setContentError("Open this lesson from Continue studying so its saved activity content can load.");
      return;
    }
    setIsContentLoading(true);
    try {
      const res = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}/content?format=${fmt}`, {
        cache: "no-store",
      });
      const data: components["schemas"]["ActivityContentResponseOut"] = await res.json();
      setPreparation(data.preparation ?? null);
      if (res.ok && data.status === "READY" && data.content) {
        setContent(data.content);
        setContentError(data.preparation?.status === "RECOVERABLE_FAILURE"
          ? "Preparation paused. Your saved lesson is available, but its assessment or requested format needs a retry."
          : null);
      } else if (data.status === "RECOVERABLE_FAILURE") {
        setContent(null);
        setContentError("Preparation paused. Your saved course is safe. Retry when you are ready.");
      } else if (!res.ok && res.status !== 202) {
        setContent(null);
        setContentError("Saved lesson content is unavailable right now. Try again shortly.");
      } else {
        setContent(null);
        setContentError(null);
      }
    } catch (err) {
      console.error("Failed to load prepared lesson content", err);
      setContent(null);
      setContentError("Saved lesson content is unavailable right now. Try again shortly.");
    } finally {
      setIsContentLoading(false);
    }
  }, [activityId, courseId]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (courseId && lessonId) void fetchLessonData();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [courseId, lessonId, fetchLessonData]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (courseId && lessonId) void fetchContent(format);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [courseId, lessonId, format, fetchContent]);

  useEffect(() => {
    if (!preparation || !["PENDING", "RUNNING"].includes(preparation.status)) return;
    const timer = window.setTimeout(() => void fetchContent(format), 2000);
    return () => window.clearTimeout(timer);
  }, [preparation, format, fetchContent]);

  useEffect(() => {
    formatRef.current = format;
  }, [format]);

  useEffect(() => {
    if (
      readingPosition > 0 &&
      !isLoading &&
      !isContentLoading &&
      content !== null
    ) {
      window.scrollTo(0, readingPosition);
    }
  }, [readingPosition, isLoading, isContentLoading, content]);

  const retryPreparation = async () => {
    if (!activityId) return;
    setContentError(null);
    try {
      const response = await fetch(
        `/api/v1/courses/${courseId}/activities/${activityId}/preparation/retry?format=${encodeURIComponent(format)}`,
        { method: "POST" },
      );
      if (!response.ok) throw new Error("Preparation could not be retried.");
      await fetchContent(format);
    } catch {
      setContentError("Preparation could not be restarted. Your saved course is safe; try again shortly.");
    }
  };

  const saveProgress = useCallback(async (position: number, selectedFormat: Format, keepalive = false) => {
    if (!activityId) return;
    const response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reading_position: position, presentation_format: selectedFormat }),
      keepalive,
    });
    if (!response.ok) throw new Error("Could not save lesson progress");
    if (!keepalive) setProgressError(null);
  }, [activityId, courseId]);

  useEffect(() => {
    if (!activityId) return;
    const handleScroll = () => {
      if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => {
        void saveProgress(Math.max(0, Math.round(window.scrollY)), formatRef.current).catch(() => {
          setProgressError("Progress could not be saved. Your last confirmed position is still available.");
        });
      }, 500);
    };
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", handleScroll);
      if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
      void saveProgress(
        Math.max(0, Math.round(window.scrollY)),
        formatRef.current,
        true,
      ).catch(() => undefined);
    };
  }, [activityId, saveProgress]);

  const handleFormatSwitch = async (newFormat: Format) => {
    if (newFormat === format) return;
    const previousFormat = formatRef.current;
    formatRef.current = newFormat;
    if (saveTimer.current !== null) {
      window.clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    try {
      await saveProgress(Math.max(0, Math.round(window.scrollY)), newFormat);
    } catch {
      formatRef.current = previousFormat;
      setProgressError("The presentation format could not be saved.");
      return;
    }
    try {
      await fetch("/api/v1/presentation-affinity/switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ from_format: format, to_format: newFormat }),
      });
    } catch (err) {
      console.error("Failed to record format switch", err);
    }
    setFormat(newFormat);
  };

  const handleComplete = async () => {
    if (!activityId) return;
    if (!preparation?.assessment_ready) {
      setProgressError("The lesson is saved. Questions are still preparing; this button will be ready when they are.");
      return;
    }
    try {
      await saveProgress(Math.max(0, Math.round(window.scrollY)), format);
      const response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}/reading-complete`, {
        method: "POST",
      });
      if (!response.ok) throw new Error("Could not save reading completion");
      router.push(`/courses/${courseId}/assessment?type=activity&activityId=${activityId}`);
    } catch {
      setProgressError("Reading completion could not be saved. Try again.");
    }
  };

  return (
    <div className="min-h-screen bg-[#F4F1EA] text-black font-[family-name:var(--font-kodchasan)] pb-28">
      <nav className="w-full bg-white border-b-2 border-black px-6 py-4 flex items-center justify-between sticky top-0 z-50">
        <div className="flex items-center gap-4">
          <Link
            href={`/dashboard`}
            className="p-2 hover:bg-gray-100 rounded-full border-2 border-transparent hover:border-black transition-all"
          >
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div className="w-10 h-10 bg-blue-500 rounded-lg border-2 border-black flex items-center justify-center shadow-[3px_3px_0px_0px_rgba(0,0,0,1)]">
            <span className="text-white font-bold tracking-tight">L</span>
          </div>
          <span className="text-xl font-bold tracking-tight truncate max-w-[250px]">
            {course ? course.title : "Study Lesson"}
          </span>
        </div>

        <Link
          href={`/courses/${courseId}/tutor?lessonId=${lessonId}`}
          className="flex items-center gap-2 bg-purple-100 hover:bg-purple-200 border-2 border-black px-4 py-2 rounded-lg font-bold transition-all shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] active:translate-x-1 active:translate-y-1 active:shadow-none"
        >
          <Brain className="w-4 h-4 text-purple-700" />
          <span className="hidden md:inline">Ask Tutor</span>
        </Link>
      </nav>

      <main className="max-w-4xl mx-auto px-6 py-10">
        <StateWrapper
          isLoading={isLoading}
          isError={isError}
          errorMessage={errorMsg}
          isUnauthorized={errorMsg.includes("Unauthorized")}
          isEmpty={false}
          onRetry={fetchLessonData}
        >
          {lesson && (
            <div className="space-y-8">
              {/* Header */}
              <div className="bg-white border-4 border-black p-8 shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] rotate-1">
                <h1 className="text-4xl font-extrabold mb-4">{lesson.title}</h1>
              </div>

              {/* Format Controls */}
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-gray-100 border-2 border-black rounded-lg p-4">
                <div className="flex items-center gap-2 font-bold text-gray-700">
                  <Settings className="w-5 h-5" />
                  Presentation Variant
                </div>
                <div className="flex gap-2 flex-wrap">
                  {FORMATS.map((fmt) => (
                    <button
                      key={fmt}
                      onClick={() => handleFormatSwitch(fmt)}
                      className={`px-4 py-2 border-2 border-black rounded-lg font-bold transition-all capitalize ${
                        format === fmt
                        ? "bg-purple-500 text-white shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]"
                        : "bg-white hover:bg-gray-50"
                      }`}
                    >
                      {fmt.replace("_", " ")}
                    </button>
                  ))}
                </div>
              </div>

              {/* This view renders only saved, fully validated statements. */}
              <div className="bg-white border-2 border-black p-8 rounded-xl shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] min-h-[400px]">
                <div className="mb-6 inline-block bg-blue-100 border-2 border-black px-3 py-1 font-bold text-sm uppercase tracking-widest shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]">
                  {format.replace("_", " ")} VARIANT
                </div>

                <h3 className="text-2xl font-bold mb-4">Concepts Covered</h3>
                <ul className="list-disc pl-6 space-y-2 mb-8 text-lg font-medium text-gray-800">
                  {lesson.concepts?.map((c) => (
                    <li key={c.concept_id}>{conceptNames[c.concept_id] || c.concept_id}</li>
                  ))}
                </ul>

                {isContentLoading || (preparation && ["PENDING", "RUNNING"].includes(preparation.status) && !content) ? (
                  <div className="flex items-center gap-3 p-6 bg-gray-50 border-2 border-dashed border-gray-400 rounded-lg text-gray-600 font-medium">
                    <Loader2 className="w-5 h-5 animate-spin" />
                    Preparing saved lesson content and its assessment…
                  </div>
                ) : content ? (
                  <div>
                    {preparation?.status === "RECOVERABLE_FAILURE" && (
                      <div className="mb-6 rounded-lg border-2 border-orange-400 bg-orange-50 p-4 font-medium text-orange-900">
                        <p>{contentError || "Assessment preparation paused. Your saved lesson is still available."}</p>
                        <button onClick={() => void retryPreparation()} className="mt-3 border-2 border-black bg-white px-4 py-2 font-bold text-black">
                          Retry preparation
                        </button>
                      </div>
                    )}
                    {preparation?.status === "RUNNING" && !preparation.assessment_ready && (
                      <div className="mb-6 flex items-center gap-3 rounded-lg border-2 border-blue-300 bg-blue-50 p-4 font-medium text-blue-900">
                        <Loader2 className="h-5 w-5 animate-spin" />
                        Lesson saved. Assessment preparation: {preparation.progress}%.
                      </div>
                    )}
                    {(["objective", "explanation", "example", "recap"] as const).map((sectionName) => (
                      <section key={sectionName} className="mb-8 last:mb-0">
                        <h3 className="mb-3 text-2xl font-bold capitalize">{sectionName}</h3>
                        <div className="space-y-4 text-lg leading-relaxed text-gray-800">
                          {(content.sections[sectionName] || []).map((statement, index) => (
                            <div key={`${sectionName}-${index}`}>
                              <p>{statement.text}</p>
                              <p className="mt-1 flex flex-wrap gap-x-3 text-xs font-bold text-blue-700">
                                {statement.citation_chunk_ids.map((chunkId, citationIndex) => (
                                  <Link key={chunkId} href={`/courses/${courseId}/sources/${chunkId}`} className="hover:underline">
                                    Source {citationIndex + 1}
                                  </Link>
                                ))}
                              </p>
                            </div>
                          ))}
                        </div>
                      </section>
                    ))}
                  </div>
                ) : (
                  <div className="p-6 bg-orange-50 border-2 border-dashed border-orange-300 rounded-lg font-medium text-orange-900">
                    {contentError || "Saved lesson content is not available yet."}
                    {(preparation?.status === "RECOVERABLE_FAILURE" || contentError) && (
                      <button onClick={() => void retryPreparation()} className="mt-4 block border-2 border-black bg-white px-4 py-2 font-bold text-black">
                        Retry preparation
                      </button>
                    )}
                  </div>
                )}
              </div>

              {/* Completion Actions */}
              <div className="flex flex-col sm:flex-row justify-end gap-4 mt-8">
                {progressError && <p role="status" className="text-red-700 font-bold">{progressError}</p>}
                <button
                  onClick={() => void handleComplete()}
                  disabled={!activityId || !preparation?.assessment_ready}
                  className="flex items-center justify-center gap-2 bg-[#FF9F1C] hover:bg-[#ff8c00] border-2 border-black px-8 py-3 rounded-lg font-bold transition-all shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] active:translate-x-1 active:translate-y-1 active:shadow-none"
                >
                  <CheckCircle className="w-5 h-5" />
                  {preparation?.assessment_ready ? "Ready for questions" : "Preparing questions…"}
                </button>
              </div>
            </div>
          )}
        </StateWrapper>
      </main>
    </div>
  );
}
