"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { components } from "@/lib/generated/api";
import { ArrowLeft, ArrowRight, LoaderCircle } from "lucide-react";

type Course = components["schemas"]["CourseOut"];
type LearningState = components["schemas"]["LearningStateOut"];

export function DiagnosticIntro({ course, learningState }: { course: Course; learningState: LearningState }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = learningState.active_activity;
  const activeIsDiagnostic = active?.activity_type === "DIAGNOSTIC";

  const openDiagnostic = async () => {
    if (busy || (active && !activeIsDiagnostic)) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`/api/v1/courses/${course.id}/diagnostic`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || "The diagnostic is unavailable right now.");
      const session = payload as components["schemas"]["AssessmentSessionOut"];
      router.push(`/courses/${course.id}/assessment?type=diagnostic&sessionId=${session.id}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The diagnostic is unavailable right now.");
    } finally {
      setBusy(false);
    }
  };

  return <div className="nl-screen">
    <nav className="nl-topbar nl-diagnostic-topbar" aria-label="Diagnostic navigation">
      <Link href={`/courses/${course.id}/learn`} className="nl-button"><ArrowLeft aria-hidden="true" />Back to course</Link>
      <div className="nl-crumb"><small>{course.title}</small><strong>Starting point</strong></div>
      <span className="nl-spacer" />
      <Link href="/dashboard" className="nl-brand nl-diagnostic-brand"><span className="nl-brand-mark" aria-hidden="true" /><span className="nl-brand-label">NeuroLearn</span></Link>
    </nav>
    <main className="nl-diagnostic-main">
      <article className="nl-diagnostic-card">
        <header className="nl-diagnostic-head">
          <span className="nl-chip nl-chip-yellow">Diagnostic</span>
          <h1>Check what you already know</h1>
          <p>A short set of questions to record a starting point and help choose a supported place to begin.</p>
        </header>
        <section className="nl-diagnostic-row">
          <b>The set</b>
          <p>A fixed set of multiple-choice questions for this course. The same questions and order are saved if you leave and resume.</p>
        </section>
        <section className="nl-diagnostic-row">
          <b>How it works</b>
          <ul>
            <li>Answer one question at a time.</li>
            <li>You cannot change an answer after it has been saved and locked.</li>
            <li>Correct answers and feedback appear after the complete set is submitted.</li>
            <li>Tutor assistance stays off during the diagnostic.</li>
          </ul>
        </section>
        <section className="nl-diagnostic-row">
          <b>If you skip</b>
          <p>Skipping adds no diagnostic evidence. Concepts remain <span className="nl-label nl-label-not-assessed">Not assessed</span> and you start with lessons. The diagnostic remains available from the course overview when no activity is in progress.</p>
        </section>
        {active && !activeIsDiagnostic && <div className="nl-diagnostic-alert" role="status"><strong>Unfinished activity saved.</strong><p>Resume it before starting another course activity.</p><Link href={`/courses/${course.id}/learn`} className="nl-button mt-3">Back to course overview</Link></div>}
        {error && <p role="alert" className="nl-diagnostic-error">{error}</p>}
        <footer className="nl-diagnostic-footer">
          <button type="button" className="nl-button nl-button-primary nl-button-large" onClick={() => void openDiagnostic()} disabled={busy || Boolean(active && !activeIsDiagnostic)}>
            {busy && <LoaderCircle className="size-5 animate-spin" />}{activeIsDiagnostic ? "Resume the diagnostic" : "Take the diagnostic"}<ArrowRight aria-hidden="true" />
          </button>
          <Link href={`/courses/${course.id}/learn`} className="nl-button">{activeIsDiagnostic ? "Back to course" : "Skip for now"}</Link>
        </footer>
      </article>
    </main>
  </div>;
}
