"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Brain, ArrowLeft, CheckCircle2, ChevronRight, Trophy, Loader2 } from "lucide-react";

// Matches mastery/schemas.py's QuestionOut exactly. Deliberately has no
// correct_answer -- that never leaves the server (see mastery/router.py's
// docstring). Grading happens server-side, per question, via
// POST /questions/{id}/attempts, not by comparing against a locally-known
// answer.
interface Question {
  id: string;
  question_type: string;
  prompt: string;
  options: string[] | null;
  difficulty: number;
}

export default function AssessmentPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();

  const courseId = params.courseId as string;
  const type = searchParams.get("type") || "standard";

  const [questions, setQuestions] = useState<Question[] | null>(null);
  const [currentStep, setCurrentStep] = useState(0);
  const [selectedAnswers, setSelectedAnswers] = useState<Record<string, string>>({});
  // Correctness comes back from the server per attempt -- it is never
  // computed client-side, because the client never has the correct answer.
  const [correctness, setCorrectness] = useState<Record<string, number>>({});
  const [showResults, setShowResults] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (type !== "diagnostic") {
      // Per-lesson, on-demand assessment generation has no backend endpoint
      // yet (Phase 3 only built the diagnostic generator, which samples
      // across the whole concept graph -- not a per-lesson question set).
      // Honest gap, not something to fake with an ad-hoc tutor prompt.
      setIsLoading(false);
      setLoadError(
        "A lesson-specific assessment isn't available yet -- only the course-wide diagnostic is implemented."
      );
      return;
    }
    generateDiagnostic();
  }, [type]);

  const generateDiagnostic = async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const response = await fetch(`/api/v1/courses/${courseId}/diagnostic`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!response.ok) throw new Error("Failed to generate diagnostic questions");

      const data: Question[] = await response.json();
      if (data.length === 0) {
        setLoadError("No diagnostic questions could be generated for this course yet.");
      } else {
        setQuestions(data);
      }
    } catch (err) {
      console.error(err);
      setLoadError(err instanceof Error ? err.message : "Failed to load the diagnostic.");
    } finally {
      setIsLoading(false);
    }
  };

  if (isLoading) {
    return (
      <div className="min-h-screen bg-[#F4F1EA] flex flex-col items-center justify-center font-[family-name:var(--font-kodchasan)]">
        <Loader2 className="w-16 h-16 text-purple-600 animate-spin mb-4" />
        <h2 className="text-2xl font-bold">Generating Diagnostic...</h2>
        <p className="text-gray-600">Sampling questions across your course&apos;s concepts.</p>
      </div>
    );
  }

  if (loadError || !questions || questions.length === 0) {
    return (
      <div className="min-h-screen bg-[#F4F1EA] flex flex-col items-center justify-center font-[family-name:var(--font-kodchasan)] text-center px-4">
        <h2 className="text-2xl font-bold mb-4">Could not load assessment</h2>
        {loadError && <p className="text-gray-600 mb-6 max-w-md">{loadError}</p>}
        <button onClick={() => router.push(`/dashboard`)} className="bg-black text-white px-6 py-3 rounded-lg font-bold">
          Return to Dashboard
        </button>
      </div>
    );
  }

  const currentQuestion = questions[currentStep];
  const isLastStep = currentStep === questions.length - 1;
  const currentOptions = currentQuestion.options || [];

  // Selecting an option submits the attempt immediately -- correctness is
  // whatever POST /questions/{id}/attempts reports, never inferred locally.
  const handleSelect = async (option: string) => {
    setSelectedAnswers((prev) => ({ ...prev, [currentQuestion.id]: option }));
    setIsSubmitting(true);
    try {
      const res = await fetch(`/api/v1/questions/${currentQuestion.id}/attempts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ given_answer: option }),
      });
      if (res.ok) {
        const attempt = await res.json();
        setCorrectness((prev) => ({ ...prev, [currentQuestion.id]: attempt.correctness }));
      }
    } catch (err) {
      console.error(err);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleNext = () => {
    if (isLastStep) {
      setShowResults(true);
    } else {
      setCurrentStep(currentStep + 1);
    }
  };

  // Each attempt already updated the learner's per-concept mastery
  // server-side at submission time (MasteryService.submit_attempt) -- there
  // is nothing further to persist on finishing, just a summary to show.
  const score = questions.reduce((sum, q) => sum + (correctness[q.id] ?? 0), 0);
  const total = questions.length;

  const handleFinish = () => {
    router.push(`/dashboard`);
  };

  if (showResults) {
    const percentage = Math.round((score / total) * 100);

    return (
      <div className="min-h-screen bg-[#F4F1EA] flex flex-col items-center justify-center p-6 font-[family-name:var(--font-kodchasan)]">
        <div className="max-w-2xl w-full bg-white border-4 border-black p-10 rounded-3xl shadow-[12px_12px_0px_0px_rgba(0,0,0,1)] text-center">
          <div className="inline-block p-4 bg-yellow-400 border-4 border-black rounded-full mb-6 rotate-3">
            <Trophy className="w-12 h-12 text-black" />
          </div>

          <h1 className="text-4xl font-black mb-2 uppercase tracking-tight">Diagnostic Complete</h1>
          <p className="text-xl font-bold text-gray-600 mb-8">
            Your mastery estimate has been updated for each concept covered.
          </p>

          <div className="flex items-center justify-center gap-8 mb-10">
            <div className="flex flex-col">
              <span className="text-6xl font-black">{percentage}%</span>
              <span className="text-sm font-bold uppercase tracking-widest text-gray-400">Accuracy</span>
            </div>
            <div className="w-px h-16 bg-black" />
            <div className="flex flex-col">
              <span className="text-6xl font-black">{score}/{total}</span>
              <span className="text-sm font-bold uppercase tracking-widest text-gray-400">Score</span>
            </div>
          </div>

          <button
            onClick={handleFinish}
            className="w-full bg-black text-white hover:bg-gray-800 border-4 border-black py-4 rounded-2xl font-black text-xl shadow-[6px_6px_0px_0px_rgba(255,159,28,1)] transition-all active:translate-y-1 active:shadow-none"
          >
            RETURN TO DASHBOARD
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#F4F1EA] text-black font-[family-name:var(--font-kodchasan)] flex flex-col">
      <nav className="w-full bg-white border-b-4 border-black px-6 py-4 flex items-center justify-between sticky top-0 z-50">
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.back()}
            className="p-2 hover:bg-gray-100 rounded-full border-2 border-transparent hover:border-black transition-all"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div className="w-10 h-10 bg-purple-500 rounded-lg border-2 border-black flex items-center justify-center shadow-[3px_3px_0px_0px_rgba(0,0,0,1)]">
            <Brain className="w-6 h-6 text-white" strokeWidth={2.5} />
          </div>
          <span className="text-xl font-bold tracking-tight">
            Diagnostic
          </span>
        </div>

        <div className="bg-black text-white px-4 py-1.5 border-2 border-black font-bold rounded-lg shadow-[3px_3px_0px_0px_rgba(168,85,247,1)]">
          QUESTION {currentStep + 1} OF {questions.length}
        </div>
      </nav>

      <main className="flex-1 flex items-center justify-center p-6 pb-24">
        <div className="max-w-3xl w-full">
          <div className="w-full h-6 bg-white border-4 border-black rounded-full mb-12 overflow-hidden shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
            <div
              className="h-full bg-purple-500 border-r-4 border-black transition-all duration-500"
              style={{ width: `${((currentStep + 1) / questions.length) * 100}%` }}
            />
          </div>

          <div className="bg-white border-4 border-black p-8 md:p-12 rounded-3xl shadow-[10px_10px_0px_0px_rgba(0,0,0,1)]">
            <h2 className="text-2xl md:text-3xl font-black mb-10 leading-tight">
              {currentQuestion.prompt}
            </h2>

            <div className="space-y-4">
              {currentOptions.map((option, i) => (
                <button
                  key={i}
                  onClick={() => handleSelect(option)}
                  disabled={isSubmitting}
                  className={`w-full text-left p-6 border-4 border-black rounded-2xl font-black text-xl transition-all flex items-center justify-between group disabled:opacity-60
                    ${selectedAnswers[currentQuestion.id] === option
                      ? "bg-purple-100 translate-x-1 translate-y-1 shadow-none"
                      : "bg-[#CBF3F0] hover:bg-white shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] active:translate-y-1 active:shadow-none"
                    }`}
                >
                  <span className="flex items-center gap-4">
                    <span className="w-10 h-10 bg-white border-2 border-black rounded-lg flex items-center justify-center group-hover:bg-purple-500 group-hover:text-white transition-colors">
                      {String.fromCharCode(65 + i)}
                    </span>
                    {option}
                  </span>
                  {selectedAnswers[currentQuestion.id] === option && (
                    <CheckCircle2 className="w-8 h-8 text-black" />
                  )}
                </button>
              ))}
            </div>

            <div className="mt-12 flex justify-end">
              <button
                disabled={!selectedAnswers[currentQuestion.id] || isSubmitting}
                onClick={handleNext}
                className={`flex items-center gap-3 px-8 py-4 rounded-2xl border-4 border-black font-black text-xl transition-all
                  ${!selectedAnswers[currentQuestion.id] || isSubmitting
                    ? "bg-gray-200 text-gray-400 cursor-not-allowed border-gray-300"
                    : "bg-[#FF9F1C] hover:bg-[#ff8c00] shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] active:translate-y-1 active:shadow-none"
                  }`}
              >
                {isLastStep ? "SEE RESULTS" : "NEXT QUESTION"}
                <ChevronRight className="w-6 h-6" strokeWidth={3} />
              </button>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
