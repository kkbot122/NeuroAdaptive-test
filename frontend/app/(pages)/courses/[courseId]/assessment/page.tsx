"use client";

import type { components } from "@/lib/generated/api";
import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, ChevronRight, Loader2 } from "lucide-react";
import { LearningSidePanel } from "@/components/LearningSidePanel";
import { GradingRetryButton } from "@/components/GradingRetryButton";
import { describeQuestionTypes, selectedActivityHref } from "@/lib/learning-route";

type AssessmentSession = components["schemas"]["AssessmentSessionOut"];
type AssessmentQuestion = components["schemas"]["AssessmentQuestionOut"];
type LearningState = components["schemas"]["LearningStateOut"];

function answerText(answer: unknown): string {
  if (typeof answer === "string") return answer;
  if (Array.isArray(answer)) return answer.map(String).join(", ");
  return answer == null ? "" : JSON.stringify(answer);
}

function gradingFailureMessage(failure: string | null | undefined): string | null {
  if (failure === "RETRIES_EXHAUSTED") {
    return "Grading could not finish after the available attempts. Your confirmed answer is saved and remains unresolved; it has not been counted as incorrect. No more grading retries are available.";
  }
  if (failure === "ALLOWANCE_UNAVAILABLE") {
    return "The grading allowance is currently unavailable. Your confirmed answer is saved and remains unresolved; it has not been counted as incorrect. Retry later if the retry button is available.";
  }
  if (failure === "GRADING_UNAVAILABLE") {
    return "Grading could not be completed. Your confirmed answer is saved and remains unresolved; it has not been counted as incorrect.";
  }
  return null;
}

function describeProgressChange(progress: NonNullable<AssessmentSession["concept_progress"]>[number]): string {
  if (progress.before_band !== progress.after_band) {
    return `The evidence label moved from ${progress.before_band} to ${progress.after_band} at this assessment's reference time.`;
  }
  if (progress.before_evidence_strength !== progress.after_evidence_strength) {
    return `The understanding label stayed ${progress.after_band}; evidence strength changed from ${progress.before_evidence_strength} to ${progress.after_evidence_strength}.`;
  }
  return `No label boundary changed at this assessment's reference time.`;
}

export default function AssessmentPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();

  const courseId = params.courseId as string;
  const type = searchParams.get("type") || "activity";
  const activityId = searchParams.get("activityId");
  const sessionId = searchParams.get("sessionId");

  const [session, setSession] = useState<AssessmentSession | null>(null);
  const [learningState, setLearningState] = useState<LearningState | null>(null);
  const [coverageError, setCoverageError] = useState<string | null>(null);
  const [currentStep, setCurrentStep] = useState(0);
  const [draftAnswer, setDraftAnswer] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reportText, setReportText] = useState<Record<string, string>>({});
  const [reportError, setReportError] = useState<Record<string, string>>({});
  const [reportSending, setReportSending] = useState<string | null>(null);
  const [sourceToOpen, setSourceToOpen] = useState<string | null>(null);
  const [sourceOpenRequestId, setSourceOpenRequestId] = useState(0);
  const openSource = useCallback((chunkId: string) => {
    setSourceToOpen(chunkId);
    setSourceOpenRequestId((requestId) => requestId + 1);
  }, []);
  const [continueBusy, setContinueBusy] = useState(false);
  const sessionRequestRef = useRef(0);

  const loadLearningState = useCallback(async () => {
    setCoverageError(null);
    try {
      const response = await fetch(`/api/v1/courses/${courseId}/learning-state`, { cache: "no-store" });
      if (!response.ok) throw new Error("Lesson coverage is unavailable right now.");
      setLearningState(await response.json());
    } catch (error) {
      setCoverageError(error instanceof Error ? error.message : "Lesson coverage is unavailable right now.");
    }
  }, [courseId]);

  const loadSession = useCallback(async () => {
    const requestId = ++sessionRequestRef.current;
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
      if (requestId !== sessionRequestRef.current) return;
      if (data.questions.length === 0) throw new Error("No questions are available for this assessment yet.");
      setSession(data);
      if (data.submission_state === "SUBMITTED") void loadLearningState();
      const nextQuestion = data.questions.findIndex((question) => !question.answer);
      setCurrentStep(nextQuestion >= 0 ? nextQuestion : Math.max(0, data.questions.length - 1));
      if (!sessionId) {
        router.replace(`/courses/${courseId}/assessment?type=${type}&sessionId=${data.id}`);
      }
    } catch (error) {
      if (requestId === sessionRequestRef.current) setLoadError(error instanceof Error ? error.message : "Assessment could not be loaded.");
    } finally {
      if (requestId === sessionRequestRef.current) setIsLoading(false);
    }
  }, [activityId, courseId, loadLearningState, router, sessionId, type]);

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
  const allGraded = session?.grading_state === "COMPLETE";
  const isGradingActive = session?.grading_state === "AWAITING_GRADING";
  const questionTypeSummary = session ? describeQuestionTypes(session.questions) : "";

  const submittedSessionId = session?.id;
  useEffect(() => {
    if (!isSubmitted || !isGradingActive || !submittedSessionId) return;
    const controller = new AbortController();
    let timer: number | null = null;
    const poll = async () => {
      try {
        const response = await fetch(
          `/api/v1/courses/${courseId}/assessment-sessions/${submittedSessionId}`,
          { cache: "no-store", signal: controller.signal },
        );
        if (!response.ok) throw new Error("Saved grading state could not be refreshed.");
        const updated: AssessmentSession = await response.json();
        if (controller.signal.aborted || updated.id !== submittedSessionId) return;
        setSession(updated);
        if (updated.grading_state === "AWAITING_GRADING") timer = window.setTimeout(() => void poll(), 3000);
      } catch {
        if (!controller.signal.aborted) timer = window.setTimeout(() => void poll(), 3000);
      }
    };
    timer = window.setTimeout(() => void poll(), 3000);
    return () => {
      if (timer !== null) window.clearTimeout(timer);
      controller.abort();
    };
  }, [courseId, isGradingActive, isSubmitted, submittedSessionId]);

  const lockConfirmedAnswer = async () => {
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
      setConfirming(false);
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
        if (data.submission_state === "SUBMITTED") void loadLearningState();
      } catch (error) {
        setActionError(error instanceof Error ? error.message : "Assessment could not be submitted.");
      } finally {
        setIsSubmitting(false);
      }
    } else {
      setConfirming(false);
      setCurrentStep((step) => step + 1);
    }
  };

  const continueAfterResults = async () => {
    if (continueBusy) return;
    setContinueBusy(true);
    setActionError(null);
    try {
      const response = await fetch(`/api/v1/courses/${courseId}/activities/next`, { method: "POST", cache: "no-store" });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || "The next saved activity could not be opened.");
      const activity = payload as components["schemas"]["LearningActivityOut"];
      if (activity.experience_availability === "UNAVAILABLE") {
        throw new Error(activity.unavailable_reason || "The selected activity is unavailable in this course version.");
      }
      if (activity.assessment_session_id) {
        router.push(`/courses/${courseId}/assessment?type=activity&sessionId=${activity.assessment_session_id}`);
        return;
      }
      if (["PREREQUISITE_REMEDIATION", "TARGETED_PRACTICE", "CHALLENGE"].includes(activity.activity_type)) {
        router.push(`/courses/${courseId}/activities/${activity.id}`);
        return;
      }
      if (activity.activity_type === "DIAGNOSTIC") {
        router.push(`/courses/${courseId}/assessment?type=diagnostic&activityId=${activity.id}`);
        return;
      }
      const structureResponse = await fetch(`/api/v1/courses/${courseId}/structure`, { cache: "no-store" });
      if (!structureResponse.ok) throw new Error("The selected activity was saved, but its course outline could not be loaded.");
      const structure: components["schemas"]["StructureOut"] = await structureResponse.json();
      const href = selectedActivityHref(courseId, activity, structure);
      if (!href) throw new Error("The selected activity was saved, but no supported learning screen is available.");
      router.push(href);
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "The next saved activity could not be opened.");
    } finally {
      setContinueBusy(false);
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
      if (data.submission_state === "SUBMITTED") void loadLearningState();
    } catch (error) {
      throw error instanceof Error ? error : new Error("Grading could not be retried.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const reportGradingIssue = async (questionId: string) => {
    if (!session) return;
    setReportSending(questionId);
    setReportError((current) => ({ ...current, [questionId]: "" }));
    try {
      const response = await fetch(
        `/api/v1/courses/${courseId}/assessment-sessions/${session.id}/questions/${questionId}/grading-issue`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ report_text: reportText[questionId] || "" }),
        },
      );
      const data = await response.json();
      if (!response.ok) throw new Error(data?.detail || "Your report could not be saved.");
      setSession((current) => current ? {
        ...current,
        questions: current.questions.map((question) => question.question_id === questionId
          ? { ...question, grading_issue_report: data }
          : question),
      } : current);
    } catch (error) {
      setReportError((current) => ({
        ...current,
        [questionId]: error instanceof Error ? error.message : "Your report could not be saved.",
      }));
    } finally {
      setReportSending(null);
    }
  };

  if (isLoading) {
    return <main className="nl-screen grid place-items-center p-6"><div className="text-center"><Loader2 className="mx-auto mb-4 size-10 animate-spin text-[#5B2EFF]" /><h1 className="text-2xl font-bold">Loading saved assessment…</h1><p className="mt-2 text-zinc-700">Your question order and confirmed answers are being restored.</p></div></main>;
  }

  if (loadError || !session || !currentQuestion) {
    return <main className="nl-screen grid place-items-center px-5 py-14 text-center"><section className="nl-card nl-card-raised w-full max-w-xl"><h1 className="text-2xl font-bold">Could not load assessment</h1>{loadError && <p className="mt-3 text-zinc-700">{loadError}</p>}<Link href="/dashboard" className="nl-button mt-5">Return to dashboard</Link></section></main>;
  }

  if (isSubmitted) {
    const resultSources = [...new Set(session.questions.flatMap((question) => [
      ...(question.result?.source_chunk_ids || []),
      ...(question.result?.rubric_feedback || []).flatMap((criterion) => criterion.source_chunk_ids),
    ]))];
    const mcqQuestions = session.questions.filter((question) => question.question_type === "MCQ");
    const mcqCorrect = mcqQuestions.filter((question) => typeof question.result?.correctness === "number" && question.result.correctness >= 0.5).length;
    const correctedCount = session.questions.filter((question) => question.result?.grade_corrected).length;
    const gradingLabel = allGraded ? "Grading finished" : isGradingActive ? "Grading in progress" : session.grading_state === "RETRY_REQUIRED" ? "Grading needs a retry" : session.grading_state === "EXHAUSTED" ? "Grading retries exhausted" : "Awaiting grading";
    const gradingTone = allGraded ? "nl-chip-mint" : isGradingActive ? "nl-chip-yellow" : "nl-chip-coral";

    return <div className="nl-screen">
      <nav className="nl-topbar" aria-label="Assessment results navigation"><Link href="/dashboard" className="nl-button"><ArrowLeft className="size-4" /><span className="hidden sm:inline">Return to dashboard</span></Link><div className="nl-crumb"><small>{session.assessment_type === "DIAGNOSTIC" ? "Course diagnostic" : "Saved assessment"}</small><strong>Your results</strong></div><span className="nl-spacer" /><span className={`nl-chip ${gradingTone}`}>{gradingLabel}</span></nav>
      <main className="nl-shell">
        <header className="mb-6 flex flex-wrap items-end justify-between gap-4"><div><p className="nl-kicker">Submitted assessment</p><h1 className="nl-title">Your results</h1><p className="mt-3 max-w-3xl text-zinc-700">{session.graded_answer_count} of {session.questions.length} answers graded · {session.unresolved_answer_count} unresolved. Pending answers are not counted as incorrect.</p><p className="mt-2 text-sm font-semibold">{mcqQuestions.length} multiple-choice questions · {mcqCorrect} correct{correctedCount ? ` · ${correctedCount} judgment${correctedCount === 1 ? "" : "s"} corrected` : ""}</p></div><span className="nl-chip">{questionTypeSummary}</span></header>
        {session.linked_sources_unavailable && <p className="mb-6 border-2 border-amber-800 bg-amber-50 p-4 text-sm text-amber-950" role="status">This saved assessment remains available, but source passages from {session.linked_source_course_title} are no longer available because that course was deleted.</p>}
        {session.questions.some((question) => question.result?.automated_grading) && <p className="mb-6 border-2 border-black bg-white p-4 text-sm"><strong>Automated grading.</strong> Short answers are checked against their saved rubrics. Criterion feedback is separate from binary understanding evidence.</p>}

        <div className="nl-grid">
          <div className="nl-stack min-w-0">
            {session.questions.map((question) => {
              const result = question.result;
              const pending = typeof result?.correctness !== "number";
              const isShort = result?.automated_grading;
              const outcome = pending ? "Awaiting grading" : result.correctness! >= 0.5 ? "Correct" : "Not yet correct";
              return <article key={question.question_id} className={`nl-card ${pending ? "border-dashed" : ""}`}>
                <div className="nl-row justify-between"><span className={`nl-chip ${pending ? "nl-chip-yellow" : result.correctness! >= 0.5 ? "nl-chip-mint" : "nl-chip-coral"}`}>{outcome}</span><span className="nl-chip">{question.question_type === "MCQ" ? "Multiple choice" : "Short answer"}</span></div>
                <h2 className="mt-3 text-xl font-bold">{question.position + 1}. {question.prompt}</h2>
                <div className="mt-4"><p className="text-xs font-bold uppercase tracking-wide text-zinc-600">Your saved answer</p><p className="mt-1 whitespace-pre-wrap">{answerText(question.answer?.given_answer)}</p></div>
                {pending && <div className="mt-4 border-2 border-amber-700 bg-amber-50 p-3 text-sm text-amber-950" role="status">{gradingFailureMessage(question.answer?.grading_failure) ?? "Grading is unresolved. This answer is saved, is not counted as incorrect, and contributes no evidence yet."}</div>}
                {isShort && result && !pending && <div className="mt-4 grid gap-3 sm:grid-cols-2"><div className="border-2 border-black p-3"><small className="block font-bold text-zinc-600">Rubric feedback</small><strong>{result.rubric_score} of {result.rubric_feedback?.length ?? result.rubric?.length ?? 0} criteria met</strong></div><div className="border-2 border-black p-3"><small className="block font-bold text-zinc-600">Binary evidence</small><strong>{result.correctness! >= 0.5 ? "Correct" : "Incorrect"}</strong></div></div>}
                {result?.grade_corrected && <div className="mt-4 grid gap-3 sm:grid-cols-2"><div className="border-2 border-black bg-zinc-50 p-3"><p className="font-bold">Original automated judgment</p><p className="mt-1 text-sm">{result.original_rubric_score} of {result.original_rubric_feedback?.length ?? result.rubric?.length ?? 0} criteria · {typeof result.original_correctness === "number" && result.original_correctness >= 0.5 ? "correct" : "not correct"} evidence</p></div><div className="border-2 border-black bg-[#D5FFF1] p-3"><p className="font-bold">Corrected judgment</p><p className="mt-1 text-sm">{result.rubric_score} of {result.rubric_feedback?.length ?? 0} criteria · {result.correctness! >= 0.5 ? "correct" : "not correct"} evidence</p><p className="mt-1 text-xs text-zinc-700">Later progress views use the approved correction. Work already in progress is not reset.</p>{result.correction_reason && <p className="mt-2 text-sm">{result.correction_reason}</p>}</div></div>}
                {result?.expected_reasoning && <p className="mt-4 text-sm"><strong>Supported reasoning:</strong> {result.expected_reasoning}</p>}
                {result?.rubric_feedback && <div className="mt-4 border-2 border-black"><h3 className="border-b-2 border-black bg-zinc-100 px-3 py-2 font-bold">Criterion feedback</h3>{result.rubric_feedback.map((criterion, index) => <div key={`${question.question_id}-${index}`} className="border-b-2 border-black p-3 last:border-b-0"><p className="font-bold">{criterion.met ? "Met" : "Not met"}: {criterion.criterion}</p><p className="mt-1 text-sm text-zinc-700">{criterion.expected_reasoning}</p><div className="mt-2 flex flex-wrap gap-2">{criterion.source_chunk_ids.map((chunkId, sourceIndex) => <button key={`${chunkId}-${sourceIndex}`} type="button" className="nl-chip hover:bg-[#FFD23F]" onClick={() => openSource(chunkId)}>Criterion source {sourceIndex + 1}</button>)}</div></div>)}</div>}
                {result?.explanation && <p className="mt-4 text-sm"><strong>Why:</strong> {result.explanation}</p>}
                {(result?.expected_answer !== undefined || result?.correctness != null) && result?.expected_answer !== undefined && <p className="mt-3 text-sm"><strong>Expected answer:</strong> {answerText(result.expected_answer)}</p>}
                {(result?.source_chunk_ids?.length ?? 0) > 0 && <div className="mt-3 flex flex-wrap gap-2">{result?.source_chunk_ids?.map((chunkId, index) => <button key={`${chunkId}-${index}`} type="button" className="nl-chip hover:bg-[#FFD23F]" onClick={() => openSource(chunkId)}>Supporting source {index + 1}</button>)}</div>}
                {question.answer?.retry_available && <GradingRetryButton onRetry={() => retryGrading(question.question_id)} disabled={isSubmitting} />}
                {isShort && result && (question.grading_issue_report ? <p className="mt-4 border-2 border-black bg-[#D5FFF1] p-3 text-sm font-semibold" role="status">Your report was received. This acknowledgment does not promise immediate review.</p> : <div className="mt-5 border-t-2 border-black pt-4"><label className="font-bold" htmlFor={`grading-report-${question.question_id}`}>Report a grading judgment</label><textarea id={`grading-report-${question.question_id}`} value={reportText[question.question_id] || ""} onChange={(event) => setReportText((current) => ({ ...current, [question.question_id]: event.target.value }))} maxLength={2000} rows={3} className="nl-form-control mt-2" placeholder="Tell us which part of the judgment you believe is wrong." />{reportError[question.question_id] && <p role="alert" className="mt-2 text-sm text-red-800">{reportError[question.question_id]}</p>}<button type="button" onClick={() => void reportGradingIssue(question.question_id)} disabled={!reportText[question.question_id]?.trim() || reportSending === question.question_id} className="nl-button mt-3">{reportSending === question.question_id ? "Sending report…" : "Report this judgment"}</button></div>)}
              </article>;
            })}
          </div>

          <aside className="nl-stack">
            {actionError && <p role="alert" className="border-2 border-red-800 bg-red-50 p-3 text-sm text-red-900">{actionError}</p>}
            <section className="nl-card nl-card-raised"><h2 className="text-xl font-bold">Concept changes</h2><p className="mt-2 text-sm text-zinc-700">Before and after use this assessment’s saved submission time.</p><div className="mt-4 space-y-3">{session.concept_progress?.length ? session.concept_progress.map((progress) => <article key={progress.concept_id} className="border-t-2 border-black pt-3 first:border-t-0 first:pt-0"><h3 className="font-bold">{progress.concept_name}</h3><p className="mt-1 text-sm">Understanding: {progress.before_band} → {progress.after_band}</p><p className="text-xs text-zinc-700">Evidence: {progress.before_evidence_strength} → {progress.after_evidence_strength}</p><p className="mt-1 text-xs text-zinc-700">{describeProgressChange(progress)}</p></article>) : <p className="text-sm text-zinc-700">No graded concept evidence is available for this session yet.</p>}</div><p className="mt-4 text-xs text-zinc-600">These labels summarize course evidence. They do not prove real-world mastery or learning gain.</p></section>
            <section className="nl-card"><h2 className="text-lg font-bold">Lesson coverage</h2>{learningState ? <><p className="mt-2 font-bold">{learningState.lesson_coverage.lessons_covered} of {learningState.lesson_coverage.lessons_total} lessons covered</p><div className="nl-progress mt-2"><span style={{ width: `${learningState.lesson_coverage.lessons_total ? learningState.lesson_coverage.lessons_covered / learningState.lesson_coverage.lessons_total * 100 : 0}%` }} /></div></> : coverageError ? <p role="status" className="mt-2 text-sm text-red-800">{coverageError}</p> : <p role="status" className="mt-2 text-sm text-zinc-700">Loading saved lesson coverage…</p>}<p className="mt-2 text-xs text-zinc-700">Reading coverage is separate from concept understanding.</p></section>
            {allGraded ? <section className="nl-card nl-card-raised"><p className="nl-kicker">Next activity</p><h2 className="text-xl font-bold">Continue studying</h2><p className="mt-2 text-sm text-zinc-700">The course will resume unfinished work or save one next activity when you continue.</p><button type="button" onClick={() => void continueAfterResults()} disabled={continueBusy} className="nl-button nl-button-primary mt-4 w-full">{continueBusy && <Loader2 className="size-4 animate-spin" />}Continue<ChevronRight className="size-4" /></button><Link href="/dashboard" className="nl-button mt-3 w-full">Return to dashboard</Link></section> : <section className="nl-card"><h2 className="text-lg font-bold">Continue when grading is complete</h2><p className="mt-2 text-sm text-zinc-700">{session.unresolved_answer_count} unresolved answers remain. You can leave now; your saved work will be available from the dashboard.</p><Link href="/dashboard" className="nl-button mt-4 w-full">Return to dashboard</Link></section>}
            <LearningSidePanel courseId={courseId} contextLessonId={session.lesson_id} decisionId={session.decision_id} sourceIds={resultSources} initialSourceChunkId={sourceToOpen} sourceOpenRequestId={sourceOpenRequestId} conversationStorageKey={`results:${session.id}`} assessmentSubmitted />
          </aside>
        </div>
      </main>
    </div>;
  }

  const currentOptions = currentQuestion.options || [];
  const currentIsMcq = currentQuestion.question_type === "MCQ" && currentOptions.length > 0;
  const currentTypeLabel = currentIsMcq ? "Multiple choice" : "Short answer";

  return <div className="nl-screen">
    <nav className="nl-topbar" aria-label="Assessment navigation"><Link href="/dashboard" className="nl-button"><ArrowLeft className="size-4" /><span className="hidden sm:inline">Dashboard</span></Link><div className="nl-crumb"><small>{session.assessment_type === "DIAGNOSTIC" ? "Course diagnostic" : questionTypeSummary}</small><strong>Question {currentQuestion.position + 1} of {session.questions.length}</strong></div><span className="nl-spacer" /><span className="nl-chip">Tutor unavailable during questions</span></nav>
    {session.linked_sources_unavailable && <p className="mx-auto mt-4 max-w-3xl border-2 border-amber-800 bg-amber-50 p-4 text-sm text-amber-950" role="status">This fixed assessment remains available, but source passages from {session.linked_source_course_title} are no longer available because that course was deleted.</p>}
    <main className="nl-narrow">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3"><span className="nl-chip nl-chip-violet">{currentTypeLabel}</span><span className="text-sm font-bold">{questionTypeSummary}</span></div>
      <div className="nl-progress mb-6" role="img" aria-label={`Question ${currentStep + 1} of ${session.questions.length}`}><span style={{ width: `${((currentStep + 1) / session.questions.length) * 100}%` }} /></div>
      <article className="nl-card nl-card-accent">
        <p className="nl-kicker">Question {currentQuestion.position + 1}</p>
        <h1 className="mb-6 text-2xl font-bold md:text-3xl">{currentQuestion.prompt}</h1>
        {currentIsMcq ? <fieldset disabled={isLocked || confirming || isSubmitting} className="space-y-3">
          <legend className="mb-3 text-sm font-semibold text-zinc-700">Choose one answer</legend>
          {currentOptions.map((option, index) => <label key={`${currentQuestion.question_id}-${index}`} className={`flex cursor-pointer items-start gap-3 border-2 border-black p-4 font-semibold ${draftAnswer === option ? "bg-[#ECE7FF]" : "bg-white hover:bg-zinc-100"}`}>
            <input type="radio" name={`answer-${currentQuestion.question_id}`} value={option} checked={draftAnswer === option} onChange={() => setDraftAnswer(option)} className="mt-1 size-4 accent-[#5B2EFF]" />
            <span className="grid size-7 shrink-0 place-items-center border-2 border-black bg-white text-sm">{String.fromCharCode(65 + index)}</span><span>{option}</span>
          </label>)}
        </fieldset> : <div><label htmlFor={`answer-${currentQuestion.question_id}`} className="mb-2 block text-sm font-semibold text-zinc-700">Your answer</label><textarea id={`answer-${currentQuestion.question_id}`} value={draftAnswer} onChange={(event) => setDraftAnswer(event.target.value)} disabled={isLocked || confirming || isSubmitting} rows={6} maxLength={3000} className="nl-form-control resize-y disabled:bg-zinc-100" placeholder="Write your answer from what you have learned." /><p className="mt-2 text-sm text-zinc-600">Your answer is saved before it is locked. Short answers are graded after you submit the full set.</p></div>}

        {isLocked && <p className="mt-5 border-2 border-black bg-[#D5FFF1] p-3 text-sm font-semibold" role="status">Answer saved and locked. {currentQuestion.question_type === "SHORT_TEXT" ? "Grading status is saved; feedback appears after the full set is submitted." : "Feedback appears after the full set is submitted."}</p>}
        {actionError && <p role="alert" className="mt-4 border-2 border-red-800 bg-red-50 p-3 text-sm text-red-900">{actionError}</p>}

        <div className="mt-7 border-t-2 border-black pt-5">
          {confirming && !isLocked ? <section className="max-w-xl border-2 border-black bg-[#FFF3BA] p-4" aria-labelledby="confirm-lock-heading"><h2 id="confirm-lock-heading" className="text-lg font-bold">Lock this answer?</h2><p className="mt-2 text-sm">You cannot change it after the saved answer is confirmed.</p><div className="nl-row mt-4"><button type="button" onClick={() => void lockConfirmedAnswer()} disabled={isSubmitting} className="nl-button nl-button-primary">{isSubmitting && <Loader2 className="size-4 animate-spin" />}{isSubmitting ? "Saving answer…" : "Yes, save and lock"}</button><button type="button" onClick={() => setConfirming(false)} disabled={isSubmitting} className="nl-button">Change answer</button></div></section>
            : !isLocked ? <button type="button" disabled={!draftAnswer.trim() || isSubmitting} onClick={() => setConfirming(true)} className="nl-button nl-button-primary nl-button-large">Confirm answer</button>
              : <button type="button" disabled={isSubmitting} onClick={() => void advance()} className="nl-button nl-button-primary nl-button-large">{isSubmitting ? "Submitting…" : isLastStep ? "Finish and see results" : "Next question"}<ChevronRight className="size-5" /></button>}
        </div>
      </article>
      <p className="mt-4 text-sm text-zinc-700">Leaving saves your place. Correctness, explanations, and rubric feedback stay hidden until the complete question set is submitted.</p>
    </main>
  </div>;
}
