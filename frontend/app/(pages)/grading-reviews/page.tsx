"use client";

import { useCallback, useEffect, useState } from "react";
import type { components } from "@/lib/generated/api";

type ReviewItem = components["schemas"]["GradingReviewItemOut"];
type ReviewPage = components["schemas"]["GradingReviewPageOut"];

export default function GradingReviewsPage() {
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [criteriaMet, setCriteriaMet] = useState<boolean[]>([]);
  const [reason, setReason] = useState("");
  const [statusFilter, setStatusFilter] = useState("OPEN");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [hasMore, setHasMore] = useState(false);

  const load = useCallback(async (offset = 0, append = false) => {
    setError(null);
    try {
      const params = new URLSearchParams({ offset: String(offset) });
      if (statusFilter) params.set("status", statusFilter);
      const query = `?${params.toString()}`;
      const response = await fetch(`/api/v1/grading-reviews${query}`, { cache: "no-store" });
      if (!response.ok) throw new Error(response.status === 404 ? "This review area is restricted." : "Reports could not be loaded.");
      const page: ReviewPage = await response.json();
      setItems((current) => append ? [...current, ...page.items] : page.items);
      setHasMore(page.has_more);
      if (!append) {
        setSelectedId((current) => current && page.items.some((item) => item.id === current) ? current : page.items[0]?.id || null);
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Reports could not be loaded.");
    }
  }, [statusFilter]);

  useEffect(() => { void load(); }, [load]);

  const selected = items.find((item) => item.id === selectedId) || null;

  useEffect(() => {
    if (selected) setCriteriaMet([...selected.latest_effective_criteria_met]);
  }, [selected]);

  const saveAction = async (action: "start" | "retain" | "correct") => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const body = action === "correct"
        ? { reason, criteria_met: criteriaMet, expected_correction_version: selected.latest_correction_version }
        : { reason };
      const response = await fetch(`/api/v1/grading-reviews/${selected.id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result?.detail || "The review decision could not be saved.");
      setItems((current) => current.map((item) => item.id === result.id ? result : item));
      if (action === "start") setStatusFilter("IN_REVIEW");
      setReason("");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "The review decision could not be saved.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="min-h-screen bg-[#F4F1EA] px-4 py-8 text-black">
      <div className="mx-auto max-w-6xl">
        <header className="mb-6 rounded-2xl border-4 border-black bg-white p-6">
          <h1 className="text-3xl font-black">Short-answer review</h1>
          <p className="mt-2 text-gray-700">Review reported automated judgments against the saved answer, fixed rubric, and cited course sources.</p>
          <p className="mt-1 text-sm text-gray-600">Approved corrections change current evidence and later progress views. Previously saved recommendation decisions remain unchanged.</p>
              <button onClick={() => void load()} className="mt-3 rounded-lg border-2 border-black px-3 py-1 text-sm font-bold">Refresh reports</button>
        </header>

        {error && <p className="mb-4 rounded-lg bg-red-50 p-3 font-bold text-red-800" role="status">{error}</p>}

        <div className="grid gap-6 lg:grid-cols-[300px_1fr]">
          <aside className="rounded-2xl border-4 border-black bg-white p-4">
            <label className="block text-sm font-bold" htmlFor="review-status">Report status</label>
            <select id="review-status" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="mt-2 w-full rounded-lg border-2 border-black p-2">
              <option value="OPEN">Open</option>
              <option value="IN_REVIEW">In review</option>
              <option value="RETAINED">Retained</option>
              <option value="CORRECTED">Corrected</option>
              <option value="">All reports</option>
            </select>
            <ul className="mt-4 space-y-2">
              {items.map((item) => (
                <li key={item.id}>
                  <button onClick={() => setSelectedId(item.id)} className={`w-full rounded-lg border-2 border-black p-3 text-left ${selectedId === item.id ? "bg-[#CBF3F0]" : "bg-white"}`}>
                    <span className="block font-bold">{item.status.replaceAll("_", " ")}</span>
                    <span className="mt-1 block text-xs text-gray-600">{new Date(item.created_at).toLocaleString()}</span>
                    <span className="mt-1 block truncate text-sm">{item.prompt}</span>
                  </button>
                </li>
              ))}
              {items.length === 0 && <li className="py-4 text-sm text-gray-600">No reports in this view.</li>}
            </ul>
            {hasMore && <button onClick={() => void load(items.length, true)} className="mt-4 w-full rounded-lg border-2 border-black px-3 py-2 text-sm font-bold">Load more reports</button>}
          </aside>

          {selected ? (
            <section className="space-y-5 rounded-2xl border-4 border-black bg-white p-5 md:p-7">
              <div>
                <p className="text-sm font-bold uppercase tracking-wide text-gray-600">{selected.status.replaceAll("_", " ")} · course {selected.course_id}</p>
                <h2 className="mt-2 text-2xl font-black">{selected.prompt}</h2>
                <p className="mt-3 rounded-lg bg-amber-50 p-3"><span className="font-bold">Student report:</span> {selected.report_text}</p>
              </div>

              <div className="rounded-xl border-2 border-gray-300 p-4">
                <h3 className="font-black">Saved answer · question version {selected.question_version}</h3>
                <p className="mt-2 whitespace-pre-wrap">{selected.answer}</p>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="rounded-xl border-2 border-gray-300 p-4">
                  <h3 className="font-black">Original automated judgment</h3>
                  <p className="mt-2">Rubric: {selected.original_rubric_score} of {selected.rubric.length}; at least {selected.rubric_passing_criteria} criteria met counts as correct evidence; saved label: {selected.original_evidence_correctness ? "Correct" : "Incorrect"}.</p>
                  <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">
                    {selected.rubric.map((criterion, index) => <li key={criterion}>{selected.original_criteria_met[index] ? "Met" : "Not met"}: {criterion}</li>)}
                  </ul>
                </div>
                <div className="rounded-xl border-2 border-gray-300 p-4">
                  <h3 className="font-black">Current effective judgment</h3>
                  <p className="mt-2">Rubric: {selected.corrections[selected.corrections.length - 1]?.rubric_score ?? selected.original_rubric_score} of {selected.rubric.length}; evidence label: {selected.latest_effective_correctness ? "Correct" : "Incorrect"}.</p>
                  <p className="mt-1 text-sm text-gray-600">Correction record version {selected.latest_correction_version || "none"}</p>
                </div>
              </div>

              <div className="rounded-xl border-2 border-gray-300 p-4">
                <h3 className="font-black">Fixed rubric and source context</h3>
                <p className="mt-2 text-sm text-gray-700">Expected reasoning: {selected.expected_reasoning}</p>
                <ul className="mt-3 space-y-3">
                  {selected.rubric.map((criterion, index) => (
                    <li key={`${index}-${criterion}`} className="rounded-lg bg-gray-50 p-3">
                      <label className="flex items-start gap-3 font-bold">
                        <input
                          type="checkbox"
                          checked={criteriaMet[index] ?? false}
                          disabled={busy || selected.status === "RETAINED"}
                          onChange={(event) => setCriteriaMet((current) => current.map((value, itemIndex) => itemIndex === index ? event.target.checked : value))}
                          className="mt-1 h-4 w-4"
                        />
                        Criterion {index + 1}: {criterion}
                      </label>
                    </li>
                  ))}
                </ul>
                <div className="mt-4 space-y-3">
                  {selected.sources.map((source) => (
                    <article key={source.chunk_id} className="rounded-lg border border-gray-300 p-3">
                      <h4 className="font-bold">{source.heading_path || "Course source"}</h4>
                      <p className="mt-1 whitespace-pre-wrap text-sm text-gray-700">{source.text}</p>
                    </article>
                  ))}
                </div>
              </div>

              {selected.history.length > 0 && (
                <div className="rounded-xl border-2 border-gray-300 p-4">
                  <h3 className="font-black">Review history</h3>
                  <ol className="mt-2 space-y-2 text-sm">
                    {selected.history.map((event, index) => <li key={`${event.event_type}-${index}`}>{event.event_type.replaceAll("_", " ")} · {new Date(event.created_at).toLocaleString()} · {event.reason}</li>)}
                  </ol>
                </div>
              )}

              {selected.status !== "RETAINED" && (
                <div className="border-t-2 border-gray-300 pt-4">
                  <label htmlFor="review-reason" className="block font-bold">Review reason</label>
                  <textarea id="review-reason" value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} rows={3} className="mt-2 w-full rounded-lg border-2 border-black p-3" />
                  <div className="mt-3 flex flex-wrap gap-3">
                    {selected.status === "OPEN" && <button onClick={() => void saveAction("start")} disabled={busy || !reason.trim()} className="rounded-lg border-2 border-black bg-[#CBF3F0] px-4 py-2 font-bold disabled:opacity-50">Start review</button>}
                    {(selected.status === "OPEN" || selected.status === "IN_REVIEW") && <button onClick={() => void saveAction("retain")} disabled={busy || !reason.trim()} className="rounded-lg border-2 border-black px-4 py-2 font-bold disabled:opacity-50">Retain judgment</button>}
                    {(selected.status === "OPEN" || selected.status === "IN_REVIEW" || selected.status === "CORRECTED") && <button onClick={() => void saveAction("correct")} disabled={busy || !reason.trim() || criteriaMet.length !== selected.rubric.length} className="rounded-lg border-2 border-black bg-purple-200 px-4 py-2 font-bold disabled:opacity-50">Approve correction</button>}
                  </div>
                </div>
              )}
            </section>
          ) : (
            <section className="rounded-2xl border-4 border-black bg-white p-8 text-gray-600">Select a report to review.</section>
          )}
        </div>
      </div>
    </main>
  );
}
