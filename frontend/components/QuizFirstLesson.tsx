"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { components } from "@/lib/generated/api";

type Content = components["schemas"]["PreparedLessonContentOut"];
type Attempt = { answer: string; revealed: boolean };
type Attempts = Record<string, Attempt>;

function questionFor(objective: string): string {
  const match = /^Learn to (identify|explain|apply|compare|analyze) (.+)\.$/.exec(objective);
  if (!match) return "What is your current understanding of this objective?";
  const [, action, concept] = match;
  if (action === "identify") return `What features help you identify ${concept}?`;
  if (action === "apply") return `How would you apply ${concept} using a situation from your course material?`;
  if (action === "compare") return `How would you compare ${concept} with a related idea in your course material?`;
  return `How would you ${action} ${concept}?`;
}

export function QuizFirstLesson({ content, identity, renderExplanation, onReadyChange }: {
  content: Content; identity: string;
  renderExplanation: (conceptIds: string[]) => ReactNode;
  onReadyChange?: (ready: boolean) => void;
}) {
  const storageKey = `neurolearn:warmup:${identity}:${content.artifact_id}`;
  const [attempts, setAttempts] = useState<Attempts>({});
  const [restored, setRestored] = useState(false);
  const [saveError, setSaveError] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        const saved: unknown = JSON.parse(window.localStorage.getItem(storageKey) || "{}");
        if (saved && typeof saved === "object" && !Array.isArray(saved)) {
          const clean: Attempts = {};
          for (let index = 0; index < content.sections.objective.length; index++) {
            const value = (saved as Record<string, unknown>)[index];
            if (value && typeof value === "object" && "answer" in value && "revealed" in value
              && typeof value.answer === "string" && typeof value.revealed === "boolean") {
              clean[index] = { answer: value.answer.slice(0, 2000), revealed: value.revealed };
            }
          }
          setAttempts(clean);
        }
      } catch { setSaveError(true); }
      setRestored(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [storageKey, content.sections.objective.length]);

  const complete = restored && content.sections.objective.length > 0
    && content.sections.objective.every((_objective, index) => attempts[index]?.revealed);
  useEffect(() => { onReadyChange?.(complete); }, [complete, onReadyChange]);

  const update = (index: number, change: Partial<Attempt>) => {
    const previous = attempts[index] ?? { answer: "", revealed: false };
    const next = { ...attempts, [index]: { ...previous, ...change } };
    setAttempts(next);
    try { window.localStorage.setItem(storageKey, JSON.stringify(next)); setSaveError(false); }
    catch { setSaveError(true); }
  };

  return <section className="space-y-5" aria-label="Quiz-first warm-up">
    <h2 className="text-xl font-bold">Try before you read</h2>
    <p className="text-sm text-zinc-700">These warm-up questions are ungraded. Try an answer, then compare it with your course explanation. They do not change mastery or replace the assessment.</p>
    <p className="text-xs text-zinc-600">Warm-up answers are saved on this browser.</p>
    {saveError && <p role="alert">Your warm-up could not be saved on this browser. You can keep studying, but it may not restore after leaving.</p>}
    {!restored ? <p role="status">Restoring your warm-up…</p> : content.sections.objective.map((objective, index) => {
      const attempt = attempts[index] || { answer: "", revealed: false };
      return <section key={index} className="border-2 border-black p-4">
        <h3 className="font-bold">Question {index + 1}: {questionFor(objective.text)}</h3>
        <p className="mt-2 text-sm text-zinc-700">{objective.text}</p>
        <form className="mt-3 space-y-3" onSubmit={(event) => {
          event.preventDefault();
          if (attempt.answer.trim()) update(index, { revealed: true });
        }}>
          <label className="block text-sm font-bold" htmlFor={`warmup-${index}`}>Your answer to warm-up question {index + 1}</label>
          <textarea id={`warmup-${index}`} className="nl-input min-h-24 w-full" value={attempt.answer}
            maxLength={2000} onChange={(event) => update(index, { answer: event.target.value })} />
          {!attempt.revealed && <div className="flex flex-wrap gap-3">
            <button type="submit" className="nl-button nl-button-primary" disabled={!attempt.answer.trim()}>Compare with course explanation</button>
            <button type="button" className="nl-button" onClick={() => update(index, { revealed: true })}>I’m not sure — show explanation</button>
          </div>}
        </form>
        {attempt.revealed && <div className="mt-4 border-t-2 border-black pt-4" aria-label={`Explanation for warm-up question ${index + 1}`}>
          <h4 className="font-bold">Compare your answer</h4>
          <p className="mt-2 text-sm text-zinc-700">What would you add or change after reading the supported explanation?</p>
          {renderExplanation(objective.concept_ids)}
        </div>}
      </section>;
    })}
    {complete && <p role="status" className="border-2 border-black bg-[#E8F5DE] p-3 font-semibold">Warm-up complete. You can continue to the assessment when its questions are ready.</p>}
  </section>;
}
