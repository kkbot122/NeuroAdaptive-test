"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { components } from "@/lib/generated/api";
import Link from "next/link";

type ReviewItem = components["schemas"]["GradingReviewItemOut"];
type ReviewPage = components["schemas"]["GradingReviewPageOut"];
type QueueView = "all" | "open" | "resolved";
type ReviewStatus = "OPEN" | "IN_REVIEW" | "RETAINED" | "CORRECTED";

const viewStatuses: Record<QueueView, Array<ReviewStatus | null>> = {
  all: [null],
  open: ["OPEN", "IN_REVIEW"],
  resolved: ["RETAINED", "CORRECTED"],
};

function reportLabel(id: string) {
  return `R-${id.replaceAll("-", "").slice(0, 8).toUpperCase()}`;
}

function statusLabel(status: string) {
  if (status === "OPEN") return "New";
  return status.replaceAll("_", " ");
}

function statusClass(status: string) {
  if (status === "OPEN") return "is-new";
  if (status === "IN_REVIEW") return "is-review";
  if (status === "CORRECTED") return "is-corrected";
  return "is-retained";
}

function relativeTime(date: string) {
  const elapsed = Date.now() - new Date(date).getTime();
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 31_536_000_000],
    ["month", 2_592_000_000],
    ["week", 604_800_000],
    ["day", 86_400_000],
    ["hour", 3_600_000],
    ["minute", 60_000],
  ];
  const formatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  for (const [unit, duration] of units) {
    if (elapsed >= duration) return formatter.format(-Math.floor(elapsed / duration), unit);
  }
  return "just now";
}

function eventLabel(eventType: string) {
  if (eventType === "IN_REVIEW") return "Review started";
  if (eventType === "RETAINED") return "Judgment retained";
  if (eventType === "CORRECTED") return "Correction recorded";
  return eventType.replaceAll("_", " ").toLowerCase();
}

function dateTime(date: string) {
  return new Date(date).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export default function GradingReviewsPage() {
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [criteriaDrafts, setCriteriaDrafts] = useState<Record<string, boolean[]>>({});
  const [reasonDrafts, setReasonDrafts] = useState<Record<string, string>>({});
  const [queueView, setQueueView] = useState<QueueView>("all");
  const [error, setError] = useState<string | null>(null);
  const [queueLoading, setQueueLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [authorization, setAuthorization] = useState<"checking" | "allowed" | "restricted" | "failed">("checking");
  const [notice, setNotice] = useState<string | null>(null);
  const itemsRef = useRef<ReviewItem[]>([]);
  const requestSequence = useRef(0);
  const requestController = useRef<AbortController | null>(null);
  const pageOffsets = useRef<Record<string, number>>({});
  const pageHasMore = useRef<Record<string, boolean>>({});
  const saveLock = useRef(false);

  const load = useCallback(async (append = false) => {
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    const sequence = ++requestSequence.current;
    setQueueLoading(true);
    setError(null);
    setNotice(null);
    if (!append) {
      pageOffsets.current = {};
      pageHasMore.current = {};
    }

    try {
      const statuses = viewStatuses[queueView];
      const pages = await Promise.all(statuses.map(async (status) => {
        const key = status ?? "ALL";
        const offset = append ? pageOffsets.current[key] ?? 0 : 0;
        const params = new URLSearchParams({ offset: String(offset) });
        if (status) params.set("status", status);
        const response = await fetch(`/api/v1/grading-reviews?${params.toString()}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (response.status === 404) return { restricted: true as const };
        if (!response.ok) throw new Error("Reports could not be loaded.");
        const page: ReviewPage = await response.json();
        pageOffsets.current[key] = page.next_offset ?? offset + page.limit;
        pageHasMore.current[key] = page.has_more;
        return { restricted: false as const, page };
      }));

      if (sequence !== requestSequence.current || controller.signal.aborted) return;
      if (pages.some((result) => result.restricted)) {
        setAuthorization("restricted");
        itemsRef.current = [];
        setItems([]);
        setSelectedId(null);
        setHasMore(false);
        return;
      }

      const pageItems = pages.flatMap((result) => result.restricted ? [] : result.page.items);
      const nextItems = append
        ? [...itemsRef.current, ...pageItems.filter((item) => !itemsRef.current.some((existing) => existing.id === item.id))]
        : pageItems;
      itemsRef.current = nextItems;
      setAuthorization("allowed");
      setItems(nextItems);
      setHasMore(statuses.some((status) => pageHasMore.current[status ?? "ALL"]));
      setSelectedId((current) => current && nextItems.some((item) => item.id === current)
        ? current
        : nextItems[0]?.id ?? null);
    } catch (loadError) {
      if (controller.signal.aborted || sequence !== requestSequence.current) return;
      setAuthorization((current) => current === "allowed" ? current : "failed");
      setError(loadError instanceof Error ? loadError.message : "Reports could not be loaded.");
    } finally {
      if (sequence === requestSequence.current) setQueueLoading(false);
    }
  }, [queueView]);

  useEffect(() => {
    void load();
    return () => {
      requestController.current?.abort();
      requestSequence.current += 1;
    };
  }, [load]);

  const selected = items.find((item) => item.id === selectedId) ?? null;
  const criteriaMet = selected
    ? criteriaDrafts[selected.id] ?? selected.latest_effective_criteria_met
    : [];
  const reason = selected ? reasonDrafts[selected.id] ?? "" : "";
  const criteriaChanged = selected
    ? criteriaMet.some((value, index) => value !== selected.latest_effective_criteria_met[index])
    : false;
  const currentCorrection = selected?.corrections[selected.corrections.length - 1] ?? null;
  const currentRubricScore = currentCorrection?.rubric_score ?? selected?.original_rubric_score ?? 0;
  const currentCorrect = selected
    ? selected.latest_effective_correctness === 1
    : false;

  const setReason = (value: string) => {
    if (!selected) return;
    setReasonDrafts((current) => ({ ...current, [selected.id]: value }));
  };

  const setCriterion = (index: number, value: boolean) => {
    if (!selected) return;
    setCriteriaDrafts((current) => {
      const next = current[selected.id] ?? [...selected.latest_effective_criteria_met];
      return {
        ...current,
        [selected.id]: next.map((criterion, itemIndex) => itemIndex === index ? value : criterion),
      };
    });
  };

  const saveAction = async (action: "start" | "retain" | "correct") => {
    if (!selected || saveLock.current || !reason.trim()) return;
    saveLock.current = true;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const body = action === "correct"
        ? {
            reason: reason.trim(),
            criteria_met: criteriaMet,
            expected_correction_version: selected.latest_correction_version,
          }
        : { reason: reason.trim() };
      const response = await fetch(`/api/v1/grading-reviews/${selected.id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const result: ReviewItem | { detail?: string } = await response.json();
      if (!response.ok) {
        const detail = "detail" in result ? result.detail : null;
        throw new Error(detail || "The review decision could not be saved.");
      }
      const updated = result as ReviewItem;
      const nextItems = itemsRef.current.map((item) => item.id === updated.id ? updated : item);
      itemsRef.current = nextItems;
      setItems(nextItems);
      if (action === "correct") {
        setCriteriaDrafts((current) => ({ ...current, [updated.id]: [...updated.latest_effective_criteria_met] }));
      }
      setReasonDrafts((current) => ({ ...current, [updated.id]: "" }));
      const updatedScore = updated.latest_effective_criteria_met.filter(Boolean).length;
      setNotice(action === "start"
        ? "Review started. The saved judgment is unchanged."
        : action === "retain"
          ? "Judgment retained. The original judgment stands."
          : `Correction recorded. Current judgment: ${updatedScore} of ${updated.rubric.length} criteria met, ${updated.latest_effective_correctness === 1 ? "correct" : "not correct"}.`);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "The review decision could not be saved.");
    } finally {
      saveLock.current = false;
      setBusy(false);
    }
  };

  const queue = authorization === "allowed" && (
    <aside className="nl-reviewer-queue" aria-label="Grading reports">
      <div className="nl-reviewer-queue-head">
        <h1>Reports</h1>
        <div className="nl-reviewer-tabs" role="group" aria-label="Filter reports">
          {(["all", "open", "resolved"] as const).map((view) => (
            <button
              key={view}
              type="button"
              aria-pressed={queueView === view}
              disabled={queueLoading}
              onClick={() => setQueueView(view)}
            >
              {view === "all" ? "All" : view === "open" ? "Open" : "Resolved"}
            </button>
          ))}
        </div>
      </div>
      <ul className="nl-reviewer-list">
        {items.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              className="nl-reviewer-item"
              onClick={() => setSelectedId(item.id)}
              aria-current={selectedId === item.id ? "true" : undefined}
            >
              <span className="nl-reviewer-item-head">
                <span>{reportLabel(item.id)}</span>
                <span className={`nl-reviewer-chip ${statusClass(item.status)}`}>{statusLabel(item.status)}</span>
              </span>
              <span className="nl-reviewer-item-prompt">{item.prompt}</span>
              <time className="nl-reviewer-item-time" dateTime={item.created_at} title={dateTime(item.created_at)}>
                Reported {relativeTime(item.created_at)}
              </time>
            </button>
          </li>
        ))}
        {items.length === 0 && !queueLoading && (
          <li className="nl-reviewer-list-empty">No reports in this view.</li>
        )}
        {queueLoading && items.length === 0 && (
          <li className="nl-reviewer-list-empty" role="status">Loading reports…</li>
        )}
      </ul>
      {hasMore && (
        <button type="button" className="nl-reviewer-load-more" onClick={() => void load(true)} disabled={queueLoading}>
          {queueLoading ? "Loading…" : "Load more reports"}
        </button>
      )}
    </aside>
  );

  const detail = selected && authorization === "allowed" ? (
    <section className="nl-reviewer-detail" aria-label={`Review ${reportLabel(selected.id)}`}>
      <div className="nl-reviewer-detail-head">
        <h2>{reportLabel(selected.id)}</h2>
        <span className={`nl-reviewer-chip ${statusClass(selected.status)}`}>{statusLabel(selected.status)}</span>
      </div>
      <div className="nl-reviewer-versions">
        <span className="nl-reviewer-chip">Question version {selected.question_version}</span>
        <span className="nl-reviewer-chip">Rubric frozen with question v{selected.question_version}</span>
      </div>
      <p className="nl-reviewer-lock">
        The question and rubric are fixed at the versions above. A learner report does not change the grade or create another attempt.
      </p>

      {error && <div className="nl-reviewer-error" role="alert">{error}<button type="button" onClick={() => void load()}>Reload reports</button></div>}
      {notice && <p className="nl-reviewer-notice" role="status">{notice}</p>}

      <section className="nl-reviewer-block">
        <h3>Question</h3>
        <div className="nl-reviewer-block-body">{selected.prompt}</div>
      </section>
      <section className="nl-reviewer-block">
        <h3>Saved answer</h3>
        <div className="nl-reviewer-block-body nl-reviewer-prewrap">{selected.answer}</div>
      </section>
      <section className="nl-reviewer-report">
        <b>Learner report</b>
        <p>{selected.report_text}</p>
      </section>

      <section className="nl-reviewer-block">
        <h3>Original judgment</h3>
        <div className="nl-reviewer-block-body">
          <div className="nl-reviewer-summary">
            <div><small>Rubric</small><b>{selected.original_rubric_score} of {selected.rubric.length} criteria met</b></div>
            <div><small>Result</small><b>{selected.original_evidence_correctness === 1 ? "Correct" : "Not correct"}</b></div>
          </div>
        </div>
        {selected.rubric.map((criterion, index) => (
          <div className="nl-reviewer-criterion" key={`${selected.id}-original-${index}`}>
            <div className="nl-reviewer-criterion-head">
              <b>{criterion}</b>
              <span className={`nl-reviewer-chip ${selected.original_criteria_met[index] ? "is-corrected" : "is-reported"}`}>
                {selected.original_criteria_met[index] ? "Met" : "Not met"}
              </span>
            </div>
          </div>
        ))}
      </section>

      {selected.status === "OPEN" && (
        <div className="nl-reviewer-start-row">
          <div className="nl-reviewer-start-copy">
            <label htmlFor="review-start-reason">Review note</label>
            <input
              id="review-start-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              maxLength={2000}
              placeholder="Why are you starting this review?"
              disabled={busy}
            />
          </div>
          <button type="button" className="nl-reviewer-button nl-reviewer-button-primary nl-reviewer-button-large" onClick={() => void saveAction("start")} disabled={busy || !reason.trim()}>
            {busy ? "Saving…" : "Start review"}
          </button>
          <span className="nl-reviewer-hint">Starting records your note in the history. It does not change the grade.</span>
        </div>
      )}

      {(selected.status === "IN_REVIEW" || selected.status === "CORRECTED") && (
        <section className="nl-reviewer-decision">
          <h3>Your decision</h3>
          <div className="nl-reviewer-decision-criteria">
            {selected.rubric.map((criterion, index) => (
              <div className="nl-reviewer-criterion" key={`${selected.id}-decision-${index}`}>
                <div className="nl-reviewer-criterion-head">
                  <b>{criterion}</b>
                  <div className="nl-reviewer-toggle" role="group" aria-label={`Criterion ${index + 1}`}>
                    <button type="button" className="is-met" aria-pressed={criteriaMet[index] === true} disabled={busy} onClick={() => setCriterion(index, true)}>Met</button>
                    <button type="button" className="is-not-met" aria-pressed={criteriaMet[index] === false} disabled={busy} onClick={() => setCriterion(index, false)}>Not met</button>
                  </div>
                </div>
              </div>
            ))}
          </div>
          <div className="nl-reviewer-decision-body">
            <label htmlFor="review-reason"><b>Reason</b> <span>Required and added to the history.</span></label>
            <textarea
              id="review-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              maxLength={2000}
              rows={3}
              placeholder="Explain your decision"
              disabled={busy}
            />
            <div className="nl-reviewer-actions">
              <button type="button" className="nl-reviewer-button" onClick={() => void saveAction("retain")} disabled={busy || !reason.trim()}>
                Retain judgment
              </button>
              <button type="button" className="nl-reviewer-button nl-reviewer-button-primary" onClick={() => void saveAction("correct")} disabled={busy || !reason.trim() || !criteriaChanged || criteriaMet.length !== selected.rubric.length}>
                {busy ? "Saving…" : "Record correction"}
              </button>
              <span className="nl-reviewer-hint">
                {criteriaChanged
                  ? "You changed a criterion, so you can record a correction."
                  : "No criteria changed, so you can retain the judgment."}
              </span>
            </div>
          </div>
        </section>
      )}

      {selected.status === "RETAINED" && (
        <p className="nl-reviewer-result is-retained"><b>Decision: judgment retained.</b> The original judgment stands.</p>
      )}
      {selected.status === "CORRECTED" && (
        <p className="nl-reviewer-result"><b>Decision: correction recorded.</b> Corrected judgment: {currentRubricScore} of {selected.rubric.length} criteria, {currentCorrect ? "correct" : "not correct"}. The original judgment is kept in the history.</p>
      )}

      <section className="nl-reviewer-block">
        <h3>Fixed rubric and source context</h3>
        <div className="nl-reviewer-block-body">
          <p className="nl-reviewer-reasoning"><b>Expected reasoning:</b> {selected.expected_reasoning}</p>
          {selected.sources.length > 0 ? selected.sources.map((source) => (
            <article className="nl-reviewer-source" key={source.chunk_id}>
              <small>Source context{source.heading_path ? ` · ${source.heading_path}` : ""}</small>
              <p>{source.text}</p>
            </article>
          )) : <p className="nl-reviewer-hint">No source passages were attached to this report.</p>}
        </div>
      </section>

      <section className="nl-reviewer-block">
        <h3>Review history</h3>
        {selected.history.length > 0 ? (
          <ol className="nl-reviewer-history">
            {selected.history.map((event, index) => (
              <li key={`${event.event_type}-${event.created_at}-${index}`}>
                <time dateTime={event.created_at}>{dateTime(event.created_at)}</time>
                <span><b>{eventLabel(event.event_type)}</b>{event.reason ? ` · ${event.reason}` : ""}</span>
              </li>
            ))}
          </ol>
        ) : <p className="nl-reviewer-no-history">No review decisions have been recorded.</p>}
        <p className="nl-reviewer-history-note">History is append-only. Entries cannot be edited or removed.</p>
      </section>
    </section>
  ) : null;

  const emptyView = authorization === "allowed" && !queueLoading && items.length === 0 && (
    <section className="nl-reviewer-empty">
      <h1>{queueView === "resolved" ? "No resolved reports" : "No reports waiting"}</h1>
      <p>{queueView === "open"
        ? "When a learner reports a short-answer judgment, it appears here for review."
        : queueView === "resolved"
          ? "Resolved reports will appear here once a review decision has been recorded."
          : "When a learner reports a short-answer judgment, it appears here for review."}</p>
    </section>
  );

  return (
    <main className="nl-reviewer-screen">
      <header className="nl-reviewer-bar">
        <Link href="/dashboard" className="nl-reviewer-logo" aria-label="NeuroLearn dashboard">
          <i aria-hidden="true" />NeuroLearn
        </Link>
        <span className="nl-reviewer-chip is-restricted">Reviewer access</span>
        <span className="nl-reviewer-spacer" />
        <div className="nl-reviewer-me"><b>R</b><span>Reviewer</span></div>
        <Link className="nl-reviewer-button nl-reviewer-exit" href="/dashboard">Exit</Link>
      </header>

      {authorization === "checking" && <section className="nl-reviewer-empty" role="status">Checking reviewer access…</section>}
      {authorization === "failed" && (
        <section className="nl-reviewer-empty" role="alert">
          <h1>Reports could not be loaded</h1>
          <p>{error || "Try again to reload the reviewer queue."}</p>
          <button type="button" className="nl-reviewer-button nl-reviewer-button-primary" onClick={() => void load()}>Try again</button>
        </section>
      )}
      {authorization === "restricted" && (
        <section className="nl-reviewer-deny" role="alert">
          <h1>You do not have reviewer access</h1>
          <p>This area is limited to approved reviewers. If you think you should have access, ask the course administrator to add you.</p>
          <Link className="nl-reviewer-button nl-reviewer-button-primary" href="/dashboard">Return to dashboard</Link>
        </section>
      )}
      {authorization === "allowed" && items.length === 0 && <>{queueView !== "all" && queue}{emptyView}</>}
      {authorization === "allowed" && items.length > 0 && (
        <div className="nl-reviewer-wrap">
          {queue}
          {detail ?? <section className="nl-reviewer-detail nl-reviewer-detail-empty">Select a report to review.</section>}
        </div>
      )}
    </main>
  );
}
