"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { StateWrapper } from "@/components/StateWrapper";
import { MarkdownMessage } from "@/components/MarkdownMessage";
import { Brain, ArrowLeft, Settings, CheckCircle, Loader2 } from "lucide-react";

// Matches app/modules/adaptation/models.py's PresentationFormat exactly --
// the same vocabulary the study page's format-switch/outcome calls already
// send to /presentation-affinity/*, so a switch here actually updates the
// affinity row it claims to.
const FORMATS = ["concise", "detailed", "worked_example", "analogy"] as const;
type Format = (typeof FORMATS)[number];

interface Citation {
  claim: string;
  chunk_id: string;
  validation_status: string;
}

export default function StudyLessonPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();

  const courseId = params.courseId as string;
  const lessonId = params.lessonId as string;

  const initialFormat = (searchParams.get("format") as Format) || "detailed";

  const [isLoading, setIsLoading] = useState(true);
  const [isError, setIsError] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  const [course, setCourse] = useState<{ title: string } | null>(null);
  const [lesson, setLesson] = useState<{ id: string; title: string; objective: string; concepts?: { concept_id: string }[] } | null>(null);
  const [conceptNames, setConceptNames] = useState<Record<string, string>>({});
  const [format, setFormat] = useState<Format>(FORMATS.includes(initialFormat) ? initialFormat : "detailed");

  const [contentMarkdown, setContentMarkdown] = useState<string | null>(null);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [groundingMode, setGroundingMode] = useState<string | null>(null);
  const [isContentLoading, setIsContentLoading] = useState(false);

  const fetchLessonData = async () => {
    setIsLoading(true);
    setIsError(false);
    try {
      const [courseRes, structureRes, graphRes] = await Promise.all([
        fetch(`/api/v1/courses/${courseId}`),
        fetch(`/api/v1/courses/${courseId}/structure`),
        fetch(`/api/v1/courses/${courseId}/graph`),
      ]);
      if (!courseRes.ok) throw new Error("Failed to load course");
      setCourse(await courseRes.json());

      if (!structureRes.ok) throw new Error("Failed to load course structure");
      const structure = await structureRes.json();

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

      // Lessons carry concept_id, not a name (curriculum/router.py's
      // _version_out) -- names come from the graph.
      if (graphRes.ok) {
        const graph = await graphRes.json();
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
  };

  const fetchContent = async (fmt: Format) => {
    setIsContentLoading(true);
    try {
      const res = await fetch(`/api/v1/courses/${courseId}/lessons/${lessonId}/content?format=${fmt}`);
      if (res.ok) {
        const data = await res.json();
        setContentMarkdown(data.content_markdown);
        setCitations(data.citations || []);
        setGroundingMode(data.grounding_mode);
      } else {
        setContentMarkdown(null);
      }
    } catch (err) {
      console.error("Failed to load lesson content", err);
      setContentMarkdown(null);
    } finally {
      setIsContentLoading(false);
    }
  };

  useEffect(() => {
    if (courseId && lessonId) {
      fetchLessonData();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [courseId, lessonId]);

  useEffect(() => {
    if (courseId && lessonId) {
      fetchContent(format);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [courseId, lessonId, format]);

  const handleFormatSwitch = async (newFormat: Format) => {
    if (newFormat === format) return;
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

  const handleComplete = async (success: boolean) => {
    try {
      await fetch("/api/v1/presentation-affinity/outcome", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ format, success }),
      });
    } catch (err) {
      console.error("Failed to record outcome", err);
    }
    router.push(`/courses/${courseId}/assessment?lessonId=${lessonId}`);
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
                <p className="text-xl font-medium text-gray-700">{lesson.objective}</p>
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

              {/* Lesson Content Area -- real, grounded generation, not a
                  placeholder: GET /courses/{id}/lessons/{lessonId}/content
                  reuses the tutor's own retrieval + citation-validated
                  generation pipeline. */}
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

                {isContentLoading ? (
                  <div className="flex items-center gap-3 p-6 bg-gray-50 border-2 border-dashed border-gray-400 rounded-lg text-gray-600 font-medium">
                    <Loader2 className="w-5 h-5 animate-spin" />
                    Generating content grounded in your uploaded material...
                  </div>
                ) : contentMarkdown ? (
                  <div>
                    {groundingMode === "insufficient" && (
                      <div className="mb-4 inline-block bg-orange-100 border-2 border-orange-500 text-orange-700 px-2 py-1 text-xs font-bold rounded">
                        UNCOVERED BY YOUR MATERIAL
                      </div>
                    )}
                    <MarkdownMessage content={contentMarkdown} />
                    {citations.length > 0 && (
                      <div className="mt-6 pt-4 border-t border-gray-200">
                        <h4 className="text-sm font-bold text-gray-500 mb-2">Sources</h4>
                        <ul className="space-y-1">
                          {citations.map((c, i) => (
                            <li key={i} className="text-xs">
                              <Link
                                href={`/courses/${courseId}/sources/${c.chunk_id}`}
                                className="text-blue-600 hover:underline"
                              >
                                [{i + 1}] {c.claim}
                              </Link>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="p-6 bg-red-50 border-2 border-dashed border-red-300 rounded-lg text-red-600 font-medium">
                    Could not generate content for this lesson right now.
                  </div>
                )}
              </div>

              {/* Completion Actions */}
              <div className="flex flex-col sm:flex-row justify-end gap-4 mt-8">
                <button
                  onClick={() => handleComplete(false)}
                  className="bg-gray-200 hover:bg-gray-300 border-2 border-black px-6 py-3 rounded-lg font-bold transition-all shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] active:translate-x-1 active:translate-y-1 active:shadow-none"
                >
                  I struggled with this
                </button>
                <button
                  onClick={() => handleComplete(true)}
                  className="flex items-center justify-center gap-2 bg-[#FF9F1C] hover:bg-[#ff8c00] border-2 border-black px-8 py-3 rounded-lg font-bold transition-all shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] active:translate-x-1 active:translate-y-1 active:shadow-none"
                >
                  <CheckCircle className="w-5 h-5" />
                  Complete Lesson
                </button>
              </div>
            </div>
          )}
        </StateWrapper>
      </main>
    </div>
  );
}
