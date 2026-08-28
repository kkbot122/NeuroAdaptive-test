"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Brain, ArrowLeft, Loader2 } from "lucide-react";

export default function NewCoursePage() {
  const router = useRouter();
  
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [deadline, setDeadline] = useState("");
  const [sessionLength, setSessionLength] = useState("30");
  const [startingConfidence, setStartingConfidence] = useState("3");
  
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) {
      setError("Title is required.");
      return;
    }

    setIsLoading(true);
    setError("");

    try {
      const res = await fetch("/api/v1/courses", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title,
          goal: goal.trim() || null,
          starting_confidence: parseInt(startingConfidence, 10),
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Failed to create course");
      }

      const data = await res.json();
      router.push(`/courses/${data.id}/workspace`);
    } catch (err: unknown) {
      console.error(err);
      setError(err instanceof Error ? err.message : "Failed to create course. Please try again.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#F4F1EA] text-black font-[family-name:var(--font-kodchasan)] pb-28">
      <nav className="w-full bg-white border-b-2 border-black px-6 py-4 flex items-center gap-4 sticky top-0 z-50">
        <Link
          href="/dashboard"
          className="p-2 hover:bg-gray-100 rounded-full border-2 border-transparent hover:border-black transition-all"
        >
          <ArrowLeft className="w-5 h-5" />
        </Link>
        <div className="w-10 h-10 bg-purple-500 rounded-lg border-2 border-black flex items-center justify-center shadow-[3px_3px_0px_0px_rgba(0,0,0,1)]">
          <Brain className="w-6 h-6 text-white" strokeWidth={2.5} />
        </div>
        <span className="text-xl font-bold tracking-tight">Create New Course</span>
      </nav>

      <main className="max-w-2xl mx-auto px-6 py-10">
        <div className="bg-white border-2 border-black rounded-xl p-8 shadow-[8px_8px_0px_0px_rgba(0,0,0,1)]">
          <h1 className="text-3xl font-bold mb-2">Set Your Learning Goal</h1>
          <p className="text-gray-600 font-medium mb-8">
            We&apos;ll create a course outline and master learning plan. We&apos;ll generate a personalized curriculum just for you.
          </p>

          {error && (
            <div className="mb-6 p-4 bg-red-100 border-2 border-red-500 rounded-lg text-red-700 font-medium">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-6">
            <div>
              <label className="block font-bold mb-2 text-lg">Course Title <span className="text-red-500">*</span></label>
              <input
                type="text"
                placeholder="e.g., Introduction to Quantum Computing"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full bg-gray-50 border-2 border-black rounded-lg px-4 py-3 font-medium focus:outline-none focus:bg-white focus:ring-2 focus:ring-purple-500"
                required
              />
            </div>

            <div>
              <label className="block font-bold mb-2 text-lg">Learning Goal (Optional)</label>
              <textarea
                placeholder="Why are you taking this course? What do you hope to achieve?"
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                className="w-full bg-gray-50 border-2 border-black rounded-lg px-4 py-3 font-medium min-h-[100px] focus:outline-none focus:bg-white focus:ring-2 focus:ring-purple-500"
              />
            </div>
            
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div>
                <label className="block font-bold mb-2">Target Deadline (Optional)</label>
                <input
                  type="date"
                  value={deadline}
                  onChange={(e) => setDeadline(e.target.value)}
                  className="w-full bg-gray-50 border-2 border-black rounded-lg px-4 py-3 font-medium focus:outline-none focus:bg-white focus:ring-2 focus:ring-purple-500"
                />
              </div>
              
              <div>
                <label className="block font-bold mb-2">Session Length (Minutes)</label>
                <select
                  value={sessionLength}
                  onChange={(e) => setSessionLength(e.target.value)}
                  className="w-full bg-gray-50 border-2 border-black rounded-lg px-4 py-3 font-medium focus:outline-none focus:bg-white focus:ring-2 focus:ring-purple-500"
                >
                  <option value="15">15 Minutes (Microlearning)</option>
                  <option value="30">30 Minutes (Standard)</option>
                  <option value="60">60 Minutes (Deep Dive)</option>
                </select>
              </div>
            </div>

            <div>
              <label className="block font-bold mb-2">Starting Confidence Level</label>
              <div className="flex items-center justify-between bg-gray-50 border-2 border-black rounded-lg px-4 py-3">
                <span className="text-sm font-medium text-gray-500">Beginner</span>
                <input
                  type="range"
                  min="1"
                  max="5"
                  step="1"
                  value={startingConfidence}
                  onChange={(e) => setStartingConfidence(e.target.value)}
                  className="w-1/2 accent-purple-600"
                />
                <span className="text-sm font-medium text-gray-500">Expert</span>
              </div>
            </div>

            <button
              type="submit"
              disabled={isLoading}
              className="w-full flex items-center justify-center gap-2 bg-[#FF9F1C] hover:bg-[#ff8c00] border-2 border-black px-6 py-4 rounded-xl font-bold text-lg shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-all active:translate-x-1 active:translate-y-1 active:shadow-none disabled:opacity-50 mt-8"
            >
              {isLoading ? (
                <>
                  <Loader2 className="w-6 h-6 animate-spin" />
                  Creating...
                </>
              ) : (
                "Create Course"
              )}
            </button>
          </form>
        </div>
      </main>
    </div>
  );
}
