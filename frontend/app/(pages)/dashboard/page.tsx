"use client";

import { useSession } from "next-auth/react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Brain, LogOut, BookOpen, Plus, Target, ArrowRight } from "lucide-react";
import { StateWrapper } from "@/components/StateWrapper";
import { MasteryMap, MasteryRow } from "@/components/MasteryMap";
import { signOut } from "next-auth/react";

interface Course {
  id: string;
  title: string;
  goal: string;
}

// Matches the actual shape of GET /courses/{id}/next-activity
// (backend/app/modules/adaptation/router.py's get_next_activity): the
// activity fields live under `recommended`, not at the top level.
interface RecommendedActivity {
  activity_type: string;
  concept_ids: string[];
  lesson_id: string | null;
  reason: string;
  score: number;
}

interface NextActivity {
  decision_id: string;
  recommended: RecommendedActivity;
  alternatives: RecommendedActivity[];
}

export default function DashboardPage() {
  const { data: session, status } = useSession();
  const router = useRouter();

  const [courses, setCourses] = useState<Course[]>([]);
  const [activeCourseId, setActiveCourseId] = useState<string | null>(null);
  
  const [masteryData, setMasteryData] = useState<MasteryRow[]>([]);
  const [nextActivity, setNextActivity] = useState<NextActivity | null>(null);

  const [isLoading, setIsLoading] = useState(true);
  const [isError, setIsError] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  const fetchDashboardData = async () => {
    setIsLoading(true);
    setIsError(false);
    try {
      // 1. Fetch courses
      const coursesRes = await fetch("/api/v1/courses");
      if (coursesRes.status === 401 || coursesRes.status === 403) {
        setIsError(true);
        setErrorMessage("Unauthorized");
        return;
      }
      if (!coursesRes.ok) throw new Error("Failed to load courses");
      
      const coursesData = await coursesRes.json();
      setCourses(coursesData);

      if (coursesData.length > 0) {
        const courseId = coursesData[0].id;
        setActiveCourseId(courseId);

        // Fetch mastery and recommendation in parallel
        const [masteryRes, nextRes] = await Promise.all([
          fetch(`/api/v1/courses/${courseId}/mastery-report`),
          fetch(`/api/v1/courses/${courseId}/next-activity`)
        ]);

        if (masteryRes.ok) {
          setMasteryData(await masteryRes.json());
        }
        if (nextRes.ok) {
          setNextActivity(await nextRes.json());
        }
      }
    } catch (err: unknown) {
      console.error("Dashboard fetch error", err);
      setIsError(true);
      setErrorMessage(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (status === "unauthenticated") {
      router.push("/signin");
    } else if (status === "authenticated") {
      fetchDashboardData();
    }
  }, [status, router]);

  if (status === "loading") return null;
  if (!session) return null;

  return (
    <div className="min-h-screen bg-[#F4F1EA] text-black font-[family-name:var(--font-kodchasan)] pb-28">
      {/* NAVBAR */}
      <nav className="w-full bg-white border-b-2 border-black px-6 py-4 flex items-center justify-between sticky top-0 z-50">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-purple-500 rounded-lg border-2 border-black flex items-center justify-center shadow-[3px_3px_0px_0px_rgba(0,0,0,1)]">
            <Brain className="w-6 h-6 text-white" strokeWidth={2.5} />
          </div>
          <span className="text-xl font-bold tracking-tight hidden md:block">
            NeuroLearn
          </span>
        </div>

        <div className="flex items-center gap-4">
          <span className="font-bold text-sm hidden md:block">
            {session.user?.name?.split(" ")[0]}
          </span>
          <button
            onClick={() => signOut({ callbackUrl: "/signin" })}
            className="flex items-center gap-2 bg-[#FF6B6B] hover:bg-[#ff5252] border-2 border-black px-4 py-2 rounded-lg font-bold shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] transition-all active:translate-y-1 active:shadow-none"
          >
            <LogOut className="w-4 h-4" strokeWidth={3} />
            <span className="hidden md:inline">Sign Out</span>
          </button>
        </div>
      </nav>

      {/* MAIN CONTENT */}
      <main className="max-w-6xl mx-auto px-6 py-10">
        <div className="mb-8 flex justify-between items-end">
          <div>
            <h1 className="text-4xl md:text-5xl font-bold leading-tight">
              Welcome back, {session.user?.name?.split(" ")[0]}!
            </h1>
            <p className="text-gray-600 font-medium mt-2 text-lg">
              Here&apos;s your learning progress across your active courses.
            </p>
          </div>
          <Link
            href="/courses/new"
            className="flex items-center gap-2 bg-[#FF9F1C] hover:bg-[#ff8c00] border-2 border-black px-6 py-3 rounded-xl font-bold shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-all active:translate-x-1 active:translate-y-1 active:shadow-none"
          >
            <Plus className="w-5 h-5" strokeWidth={3} />
            Create Course
          </Link>
        </div>

        <StateWrapper
          isLoading={isLoading}
          isError={isError}
          errorMessage={errorMessage}
          isUnauthorized={errorMessage === "Unauthorized"}
          isEmpty={!isLoading && !isError && courses.length === 0}
          emptyMessage="You haven't created any courses yet."
          onRetry={fetchDashboardData}
        >
          {courses.length > 0 && activeCourseId && (
            <div className="space-y-12">
              {/* COURSE SELECTOR / SUMMARY */}
              <div className="bg-white border-2 border-black rounded-xl p-6 shadow-[6px_6px_0px_0px_rgba(0,0,0,1)]">
                <div className="flex items-center gap-3 mb-4">
                  <div className="p-3 bg-blue-100 border-2 border-black rounded-lg w-fit">
                    <BookOpen className="w-6 h-6 text-blue-600" />
                  </div>
                  <div>
                    <h2 className="text-2xl font-bold">{courses.find(c => c.id === activeCourseId)?.title}</h2>
                    <p className="text-gray-500 font-medium">{courses.find(c => c.id === activeCourseId)?.goal}</p>
                  </div>
                </div>
                
                <div className="flex gap-4">
                  <Link
                    href={`/courses/${activeCourseId}/workspace`}
                    className="flex items-center gap-2 bg-gray-100 hover:bg-gray-200 border-2 border-black px-4 py-2 rounded-lg font-bold transition-all"
                  >
                    Course Workspace
                  </Link>
                  <Link
                    href={`/courses/${activeCourseId}/tutor`}
                    className="flex items-center gap-2 bg-purple-100 hover:bg-purple-200 border-2 border-black px-4 py-2 rounded-lg font-bold transition-all"
                  >
                    <Brain className="w-4 h-4" />
                    Ask Tutor
                  </Link>
                </div>
              </div>

              {/* NEXT ACTIVITY RECOMMENDATION */}
              <div className="bg-white border-2 border-black rounded-xl p-6 shadow-[6px_6px_0px_0px_rgba(0,0,0,1)]">
                <h3 className="text-xl font-bold mb-4 flex items-center gap-2">
                  <Target className="w-6 h-6 text-purple-600" />
                  Up Next
                </h3>
                {nextActivity ? (
                  <div className="bg-purple-50 border-2 border-black rounded-lg p-5">
                    <div className="flex justify-between items-center">
                      <div>
                        <h4 className="font-bold text-lg">{nextActivity.recommended.activity_type.replace(/_/g, " ")}</h4>
                        <p className="text-gray-700 mt-1">{nextActivity.recommended.reason}</p>
                      </div>
                      <Link
                        href={`/courses/${activeCourseId}/study/${nextActivity.recommended.lesson_id || "default"}`}
                        className="flex items-center gap-2 bg-black text-white hover:bg-gray-800 border-2 border-black px-5 py-2.5 rounded-lg font-bold shadow-[4px_4px_0px_0px_rgba(168,85,247,0.4)] transition-all active:translate-x-1 active:translate-y-1 active:shadow-none"
                      >
                        Start <ArrowRight className="w-4 h-4" />
                      </Link>
                    </div>
                  </div>
                ) : (
                  <div className="p-4 bg-gray-50 border-2 border-dashed border-gray-300 rounded-lg text-center">
                    <p className="text-gray-500 font-medium">No activity recommended at this time.</p>
                  </div>
                )}
              </div>

              {/* MASTERY MAP */}
              <div>
                <h3 className="text-xl font-bold mb-2">Here&apos;s your learning path</h3>
                <MasteryMap data={masteryData} />
              </div>
            </div>
          )}
        </StateWrapper>
      </main>
    </div>
  );
}
