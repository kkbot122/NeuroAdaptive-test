"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { StateWrapper } from "@/components/StateWrapper";
import { Brain, ArrowLeft, Settings, CheckCircle } from "lucide-react";

export default function StudyLessonPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();
  
  const courseId = params.courseId as string;
  const lessonId = params.lessonId as string;
  
  // Optional initial format passed from the next-activity recommendation
  const initialFormat = searchParams.get("format") || "DEFAULT";

  const [isLoading, setIsLoading] = useState(true);
  const [isError, setIsError] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  const [course, setCourse] = useState<{ title: string } | null>(null);
  const [lesson, setLesson] = useState<{ id: string; title: string; objective: string; concepts?: { name: string }[] } | null>(null);
  const [format, setFormat] = useState(initialFormat);

  const fetchLessonData = async () => {
    setIsLoading(true);
    setIsError(false);
    try {
      // Fetch course
      const courseRes = await fetch(`/api/v1/courses/${courseId}`);
      if (!courseRes.ok) throw new Error("Failed to load course");
      setCourse(await courseRes.json());

      // Fetch structure to find the lesson
      const structureRes = await fetch(`/api/v1/courses/${courseId}/structure`);
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

    } catch (err: unknown) {
      console.error(err);
      setIsError(true);
      setErrorMsg(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (courseId && lessonId) {
      fetchLessonData();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [courseId, lessonId]);

  const handleFormatSwitch = async (newFormat: string) => {
    if (newFormat === format) return;
    try {
      await fetch("/api/v1/presentation-affinity/switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          from_format: format,
          to_format: newFormat
        })
      });
      setFormat(newFormat);
    } catch (err) {
      console.error("Failed to record format switch", err);
      // Still allow UI change even if logging fails
      setFormat(newFormat);
    }
  };

  const handleComplete = async (success: boolean) => {
    try {
      await fetch("/api/v1/presentation-affinity/outcome", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          format: format,
          success: success
        })
      });
      // Route to assessment or dashboard
      router.push(`/courses/${courseId}/assessment?lessonId=${lessonId}`);
    } catch (err) {
      console.error("Failed to record outcome", err);
      router.push(`/courses/${courseId}/assessment?lessonId=${lessonId}`);
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
                <p className="text-xl font-medium text-gray-700">{lesson.objective}</p>
              </div>

              {/* Format Controls */}
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-gray-100 border-2 border-black rounded-lg p-4">
                <div className="flex items-center gap-2 font-bold text-gray-700">
                  <Settings className="w-5 h-5" />
                  Presentation Variant
                </div>
                <div className="flex gap-2 flex-wrap">
                  {["DEFAULT", "ANALOGY", "CONCISE", "VISUAL"].map(fmt => (
                    <button
                      key={fmt}
                      onClick={() => handleFormatSwitch(fmt)}
                      className={`px-4 py-2 border-2 border-black rounded-lg font-bold transition-all ${
                        format === fmt 
                        ? "bg-purple-500 text-white shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]" 
                        : "bg-white hover:bg-gray-50"
                      }`}
                    >
                      {fmt}
                    </button>
                  ))}
                </div>
              </div>

              {/* Lesson Content Area (Placeholder since backend generation is missing) */}
              <div className="bg-white border-2 border-black p-8 rounded-xl shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] min-h-[400px]">
                <div className="mb-6 inline-block bg-blue-100 border-2 border-black px-3 py-1 font-bold text-sm uppercase tracking-widest shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]">
                  {format} VARIANT
                </div>
                
                <h3 className="text-2xl font-bold mb-4">Concepts Covered</h3>
                <ul className="list-disc pl-6 space-y-2 mb-8 text-lg font-medium text-gray-800">
                  {lesson.concepts?.map((c: { name: string }, i: number) => (
                    <li key={i}>{c.name}</li>
                  ))}
                </ul>

                <div className="p-6 bg-gray-50 border-2 border-dashed border-gray-400 rounded-lg text-gray-600 font-medium">
                  <p>
                    [This is a placeholder for the lesson content. In a complete implementation, this area would render the actual educational text generated by the AI based on the selected &quot;{format}&quot; variant and the underlying source documents.]
                  </p>
                  <p className="mt-4">
                    The objective of this lesson is: &quot;{lesson.objective}&quot;
                  </p>
                </div>
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
