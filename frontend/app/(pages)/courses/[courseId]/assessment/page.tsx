"use client";

import type { components } from "@/lib/generated/api";
import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Brain, ArrowLeft, CheckCircle2, ChevronRight, Trophy, Loader2 } from "lucide-react";

type AssessmentSession = components["schemas"]["AssessmentSessionOut"];
type AssessmentQuestion = components["schemas"]["AssessmentQuestionOut"];

export default function AssessmentPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();

  const courseId = params.courseId as string;
  const type = searchParams.get("type") || "activity";
  const activityId = searchParams.get("activityId");
  const sessionId = searchParams.get("sessionId");

  const [session, setSession] = useState<AssessmentSession | null>(null);
  const [currentStep, setCurrentStep] = useState(0);
  const [draftAnswer, setDraftAnswer] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const loadSession = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      let response: Response;
      if (sessionId) {
        response = await fetch(`/api/v1/courses/${courseId}/assessment-sessions/${sessionId}`, {
          cache: "no-store",
        });
      } else if (type === "diagnostic") {
        response = await fetch(`/api/v1/courses/${courseId}/diagnostic`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
      } else if (activityId) {
        response = await fetch(`/api/v1/courses/${courseId}/activities/${activityId}/assessment`, {
          method: "POST",
        });
      } else {
        throw new Error("Open an activity from Continue studying to resume its assessment.");
      }
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        throw new Error(detail?.detail || "Assessment could not be loaded.");
      }
      const data: AssessmentSession = await response.json();
      if (data.questions.length === 0) throw new Error("No questions are available for this assessment yet.");
      setSession(data);
      const nextQuestion = data.questions.findIndex((question) => !question.answer);
      setCurrentStep(nextQuestion >= 0 ? nextQuestion : Math.max(0, data.questions.length - 1));
      if (!sessionId) {
        router.replace(`/courses/${courseId}/assessment?type=${type}&sessionId=${data.id}`);
      }
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Assessment could not be loaded.");
    } finally {
      setIsLoading(false);
    }
  }, [activityId, courseId, router, sessionId, type]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadSession(), 0);
    return () => window.clearTimeout(timer);
  }, [loadSession]);

  useEffect(() => {
    const question = session?.questions[currentStep];
    const savedAnswer = question?.answer?.given_answer;
    setDraftAnswer(typeof savedAnswer === "string" ? savedAnswer : "");
  }, [currentStep, session]);

  const currentQuestion: AssessmentQuestion | undefined = session?.questions[currentStep];
  const isSubmitted = session?.submission_state === "SUBMITTED";
  const isLocked = Boolean(currentQuestion?.answer);
  const isLastStep = Boolean(session && currentStep === session.questions.length - 1);
  const graded = session?.questions.filter((question) => typeof question.result?.correctness === "number") || [];
  const allGraded = session?.grading_state === "COMPLETE";
  const score = graded.reduce((sum, question) => sum + (question.result?.correctness || 0), 0);

  const confirmAnswer = async () => {
    if (!session || !currentQuestion || isLocked) return;
    setIsSubmitting(true);
    setActionError(null);
    try {
      const response = await fetch(
        `/api/v1/courses/${courseId}/assessment-sessions/${session.id}/questions/${currentQuestion.question_id}/answer`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ given_answer: draftAnswer }),
        },
      );
      const data = await response.json();
      if (!response.ok) throw new Error(data?.detail || "Answer could not be saved.");
      setSession(data);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Answer could not be saved.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const advance = async () => {
    if (!session) return;
    if (isLastStep) {
      setIsSubmitting(true);
      setActionError(null);
      try {
        const response = await fetch(
          `/api/v1/courses/${courseId}/assessment-sessions/${session.id}/submit`,
          { method: "POST" },
        );
        const data = await response.json();
        if (!response.ok) throw new Error(data?.detail || "Assessment could not be submitted.");
        setSession(data);
      } catch (error) {
        setActionError(error instanceof Error ? error.message : "Assessment could not be submitted.");
      } finally {
        setIsSubmitting(false);
      }
    } else {
      setCurrentStep((step) => step + 1);
    }
  };

  const retryGrading = async (questionId: string) => {
    if (!session) return;
    setIsSubmitting(true);
    setActionError(null);
    try {
      const response = await fetch(
        `/api/v1/courses/${courseId}/assessment-sessions/${session.id}/questions/${questionId}/retry-grading`,
        { method: "POST" },
      );
      const data = await response.json();
      if (!response.ok) throw new Error(data?.detail || "Grading could not be retried.");
      setSession(data);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Grading could not be retried.");
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="min-h-screen bg-[#F4F1EA] flex flex-col items-center justify-center font-[family-name:var(--font-kodchasan)]">
        <Loader2 className="w-16 h-16 text-purple-600 animate-spin mb-4" />
        <h2 className="text-2xl font-bold">Loading saved assessment…</h2>
        <p className="text-gray-600">Your question order and confirmed answers are being restored.</p>
      </div>
    );
  }

  if (loadError || !session || !currentQuestion) {
    return (
      <div className="min-h-screen bg-[#F4F1EA] flex flex-col items-center justify-center font-[family-name:var(--font-kodchasan)] text-center px-4">
        <h2 className="text-2xl font-bold mb-4">Could not load assessment</h2>
        {loadError && <p className="text-gray-600 mb-6 max-w-md">{loadError}</p>}
        <button onClick={() => router.push("/dashboard")} className="bg-black text-white px-6 py-3 rounded-lg font-bold">
          Return to dashboard
        </button>
      </div>
    );
  }

  if (isSubmitted) {
    return (
      <div className="min-h-screen bg-[#F4F1EA] flex flex-col items-center justify-center p-6 font-[family-name:var(--font-kodchasan)]">
        <div className="max-w-3xl w-full bg-white border-4 border-black p-8 md:p-10 rounded-3xl shadow-[12px_12px_0px_0px_rgba(0,0,0,1)]">
          <div className="inline-block p-3 bg-yellow-400 border-4 border-black rounded-full mb-5 rotate-3">
            <Trophy className="w-9 h-9 text-black" />
          </div>
          <h1 className="text-3xl font-black mb-2 uppercase tracking-tight">Assessment submitted</h1>
          {allGraded ? (
            <p className="text-lg font-bold text-gray-600 mb-6">Graded answers: {score}/{session.questions.length}. This result is evidence for the assessed concepts; it does not mark the course complete.</p>
          ) : (
            <p className="text-lg font-bold text-gray-600 mb-6">Grading is still pending for some saved answers. Pending answers are unresolved, not incorrect.</p>
          )}
          <div className="space-y-4">
            {session.questions.map((question) => (
              <section key={question.question_id} className="border-2 border-black rounded-xl p-4">
                <p className="font-black">{question.position + 1}. {question.prompt}</p>
                <p className="mt-2 text-gray-700">Your answer: {String(question.answer?.given_answer ?? "")}</p>
                {typeof question.result?.correctness === "number" ? (
                  <p className="mt-2 font-bold">Correctness: {Math.round(question.result.correctness * 100)}%</p>
                ) : (
                  <p className="mt-2 font-bold text-orange-700">Grading pending</p>
                )}
                {question.result?.expected_answer !== undefined && (
                  <p className="mt-1 text-gray-700">Expected answer: {JSON.stringify(question.result.expected_answer)}</p>
                )}
                {question.result?.rubric && <p className="mt-1 text-gray-700">Rubric: {question.result.rubric.join("; ")}</p>}
                {question.answer?.status !== "GRADED" && (
                  <button
                    onClick={() => void retryGrading(question.question_id)}
                    disabled={isSubmitting}
                    className="mt-3 border-2 border-black px-3 py-2 font-bold disabled:opacity-50"
                  >
                    Retry grading
                  </button>
                )}
              </section>
            ))}
          </div>
          {actionError && <p role="status" className="mt-4 text-red-700 font-bold">{actionError}</p>}
          <button
            onClick={() => router.push(allGraded ? `/courses/${courseId}/learn` : "/dashboard")}
            className="w-full mt-8 bg-black text-white hover:bg-gray-800 border-4 border-black py-4 rounded-2xl font-black text-xl"
          >
            {allGraded ? "CONTINUE LEARNING" : "RETURN TO DASHBOARD"}
          </button>
        </div>
      </div>
    );
  }

  const currentOptions = currentQuestion.options || [];

  return (
    <div className="min-h-screen bg-[#F4F1EA] text-black font-[family-name:var(--font-kodchasan)] flex flex-col">
      <nav className="w-full bg-white border-b-4 border-black px-6 py-4 flex items-center justify-between sticky top-0 z-50">
        <div className="flex items-center gap-3">
          <button onClick={() => router.push("/dashboard")} className="p-2 hover:bg-gray-100 rounded-full border-2 border-transparent hover:border-black transition-all">
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div className="w-10 h-10 bg-purple-500 rounded-lg border-2 border-black flex items-center justify-center shadow-[3px_3px_0px_0px_rgba(0,0,0,1)]">
            <Brain className="w-6 h-6 text-white" strokeWidth={2.5} />
          </div>
          <span className="text-xl font-bold tracking-tight">{session.assessment_type === "DIAGNOSTIC" ? "Diagnostic" : "Assessment"}</span>
        </div>
        <div className="bg-black text-white px-4 py-1.5 border-2 border-black font-bold rounded-lg">
          QUESTION {currentStep + 1} OF {session.questions.length}
        </div>
      </nav>

      <main className="flex-1 flex items-center justify-center p-6 pb-24">
        <div className="max-w-3xl w-full">
          <div className="w-full h-6 bg-white border-4 border-black rounded-full mb-10 overflow-hidden shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
            <div className="h-full bg-purple-500 border-r-4 border-black transition-all duration-500" style={{ width: `${((currentStep + 1) / session.questions.length) * 100}%` }} />
          </div>

          <div className="bg-white border-4 border-black p-8 md:p-12 rounded-3xl shadow-[10px_10px_0px_0px_rgba(0,0,0,1)]">
            <h2 className="text-2xl md:text-3xl font-black mb-8 leading-tight">{currentQuestion.prompt}</h2>
            {currentQuestion.question_type === "MCQ" && currentOptions.length > 0 ? (
              <div className="space-y-4">
                {currentOptions.map((option, index) => (
                  <button
                    key={index}
                    onClick={() => setDraftAnswer(option)}
                    disabled={isLocked || isSubmitting}
                    className={`w-full text-left p-5 border-4 border-black rounded-2xl font-black text-lg transition-all flex items-center justify-between group disabled:opacity-70 ${draftAnswer === option ? "bg-purple-100" : "bg-[#CBF3F0] hover:bg-white"}`}
                  >
                    <span className="flex items-center gap-4">
                      <span className="w-9 h-9 bg-white border-2 border-black rounded-lg flex items-center justify-center">{String.fromCharCode(65 + index)}</span>
                      {option}
                    </span>
                    {draftAnswer === option && <CheckCircle2 className="w-7 h-7 text-black" />}
                  </button>
                ))}
              </div>
            ) : (
              <textarea
                value={draftAnswer}
                onChange={(event) => setDraftAnswer(event.target.value)}
                disabled={isLocked || isSubmitting}
                rows={5}
                className="w-full border-4 border-black rounded-xl p-4 font-medium disabled:bg-gray-100"
                aria-label="Your answer"
              />
            )}

            {currentQuestion.answer && (
              <p className="mt-5 font-bold text-gray-700">
                Answer saved. {currentQuestion.answer.status === "GRADED" ? "Results appear after you submit the full set." : "Grading is pending; the answer remains saved."}
              </p>
            )}
            {actionError && <p role="status" className="mt-4 text-red-700 font-bold">{actionError}</p>}

            <div className="mt-10 flex justify-end gap-3">
              {!isLocked && (
                <button
                  disabled={draftAnswer.length === 0 || isSubmitting}
                  onClick={() => void confirmAnswer()}
                  className="px-6 py-3 rounded-xl border-4 border-black font-black bg-purple-300 disabled:bg-gray-200 disabled:text-gray-500"
                >
                  {isSubmitting ? "SAVING…" : "LOCK ANSWER"}
                </button>
              )}
              {isLocked && (
                <button
                  disabled={isSubmitting}
                  onClick={() => void advance()}
                  className="flex items-center gap-3 px-7 py-3 rounded-xl border-4 border-black font-black text-lg bg-[#FF9F1C] disabled:opacity-50"
                >
                  {isLastStep ? "SUBMIT QUESTION SET" : "NEXT QUESTION"}
                  <ChevronRight className="w-6 h-6" strokeWidth={3} />
                </button>
              )}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
