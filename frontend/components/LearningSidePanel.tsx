"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { components } from "@/lib/generated/api";
import { MarkdownMessage } from "@/components/MarkdownMessage";
import { LoaderCircle, PanelRightClose, PanelRightOpen, Send } from "lucide-react";

type Citation = components["schemas"]["CitationOut"];
type Chunk = components["schemas"]["ChunkDetail"];
type TutorDone = components["schemas"]["TutorDone"];
type History = components["schemas"]["TutorHistoryOut"];

type Turn = {
  id: string;
  question: string;
  answer: string;
  citations: Citation[];
  insufficient: boolean;
};

type LearningSidePanelProps = {
  courseId: string;
  contextLessonId?: string | null;
  decisionId?: string | null;
  sourceIds?: string[];
  initialSourceChunkId?: string | null;
  sourceOpenRequestId?: number;
  conversationStorageKey: string;
  assessmentSubmitted?: boolean;
};

function locationFor(chunk: Chunk): string | null {
  if (chunk.page_start != null) {
    return chunk.page_start === chunk.page_end
      ? `Page ${chunk.page_start}`
      : `Pages ${chunk.page_start}–${chunk.page_end ?? chunk.page_start}`;
  }
  return chunk.heading_path || null;
}

export function LearningSidePanel({
  courseId,
  contextLessonId,
  decisionId,
  sourceIds = [],
  initialSourceChunkId,
  sourceOpenRequestId = 0,
  conversationStorageKey,
  assessmentSubmitted = false,
}: LearningSidePanelProps) {
  const [tab, setTab] = useState<"tutor" | "sources">("tutor");
  const [panelOpen, setPanelOpen] = useState(true);
  const [prompt, setPrompt] = useState("");
  const scope = JSON.stringify([courseId, contextLessonId ?? null, decisionId ?? null, conversationStorageKey]);
  const [conversation, setConversation] = useState<{ scope: string; id: string; turns: Turn[]; available: boolean; hasMore: boolean } | null>(null);
  const [historyState, setHistoryState] = useState<{ scope: string; loading: boolean; error: string | null } | null>(null);
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const currentConversation = conversation?.scope === scope ? conversation : null;
  const conversationId = currentConversation?.id ?? null;
  const turns = currentConversation?.available ? currentConversation.turns : [];
  const historyLoading = historyState?.scope !== scope || historyState.loading;
  const historyError = historyState?.scope === scope ? historyState.error : null;
  const assessmentAccess = historyError ? "unavailable" : historyLoading ? "checking" : currentConversation?.available ? "available" : "blocked";
  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(null);
  const [sourceState, setSourceState] = useState<{ scope: string; chunk: Chunk | null; error: string | null } | null>(null);
  const chunk = sourceState?.scope === scope ? sourceState.chunk : null;
  const chunkError = sourceState?.scope === scope ? sourceState.error : null;
  const [busy, setBusy] = useState(false);
  const [tutorError, setTutorError] = useState<string | null>(null);
  const [loadingChunk, setLoadingChunk] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const historyAbortRef = useRef<AbortController | null>(null);
  const panelPreferenceTouchedRef = useRef(false);
  const busyRef = useRef(busy);
  busyRef.current = busy;

  useEffect(() => {
    panelPreferenceTouchedRef.current = false;
    let current = true;
    void fetch("/api/v1/me/settings", { cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then((settings: { tutor_panel_open?: unknown } | null) => {
        if (current && !panelPreferenceTouchedRef.current && typeof settings?.tutor_panel_open === "boolean") {
          setPanelOpen(settings.tutor_panel_open);
        }
      })
      .catch(() => undefined);
    return () => { current = false; };
  }, [scope]);

  const loadHistory = useCallback(async (id: string, before?: string) => {
    const requestScope = scope;
    historyAbortRef.current?.abort();
    const controller = new AbortController();
    historyAbortRef.current = controller;
    setHistoryState({ scope: requestScope, loading: true, error: null });
    try {
      const query = new URLSearchParams({ conversation_id: id });
      if (contextLessonId) query.set("context_lesson_id", contextLessonId);
      if (decisionId) query.set("decision_id", decisionId);
      if (before) query.set("before", before);
      const response = await fetch(`/api/v1/courses/${courseId}/tutor/history?${query}`, { cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error("Your saved conversation could not be loaded. Try loading it again.");
      const history: History = await response.json();
      if (controller.signal.aborted || scopeRef.current !== requestScope) return;
      if (history.conversation_id !== id) throw new Error("Your saved conversation could not be verified.");
      const restored = history.turns.map((turn): Turn => ({ id: turn.id, question: turn.question,
        answer: turn.answer_markdown, citations: turn.citations, insufficient: turn.grounding_mode === "insufficient" }));
      setConversation((current) => {
        const existing = before && current?.scope === requestScope ? current.turns : [];
        const combined = [...restored, ...existing];
        return { scope: requestScope, id, available: history.available, hasMore: history.has_more,
          turns: history.available ? combined.filter((turn, index) => combined.findIndex((item) => item.id === turn.id) === index) : [] };
      });
      setHistoryState({ scope: requestScope, loading: false, error: null });
    } catch (error) {
      if (!controller.signal.aborted && scopeRef.current === requestScope) {
        setHistoryState({ scope: requestScope, loading: false, error: error instanceof Error ? error.message : "Saved conversation could not be loaded." });
      }
    }
  }, [contextLessonId, courseId, decisionId, scope]);

  useEffect(() => {
    const key = `neurolearn:tutor:${conversationStorageKey}`;
    let id: string | null = null;
    try {
      id = window.localStorage.getItem(key) || window.sessionStorage.getItem(key);
      if (!id) id = window.crypto.randomUUID();
      window.localStorage.setItem(key, id);
      window.sessionStorage.setItem(key, id);
    } catch {
      id = window.crypto.randomUUID();
    }
    setConversation({ scope, id, turns: [], available: false, hasMore: false });
    setPrompt(""); setTutorError(null); setTab("tutor"); setSelectedChunkId(null); setSourceState(null);
    requestIdRef.current += 1;
    abortRef.current?.abort(); setBusy(false);
    void loadHistory(id);
    const recheck = () => { if (!busyRef.current) void loadHistory(id!); };
    window.addEventListener("focus", recheck);
    return () => {
      window.removeEventListener("focus", recheck);
      historyAbortRef.current?.abort(); abortRef.current?.abort();
    };
  }, [conversationStorageKey, loadHistory, scope]);

  useEffect(() => {
    if (!initialSourceChunkId) return;
    panelPreferenceTouchedRef.current = true;
    setPanelOpen(true);
    setSelectedChunkId(initialSourceChunkId);
    setTab("sources");
  }, [initialSourceChunkId, sourceOpenRequestId, scope]);

  useEffect(() => {
    if (tab !== "sources" || !selectedChunkId) return;
    const controller = new AbortController();
    const requestScope = scope;
    setLoadingChunk(true);
    setSourceState({ scope: requestScope, chunk: null, error: null });
    void fetch(`/api/v1/courses/${courseId}/chunks/${selectedChunkId}`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null);
        if (!response.ok) throw new Error(response.status === 404
          ? "This source passage is unavailable for this course."
          : "The source passage could not be loaded.");
        if (!controller.signal.aborted && scopeRef.current === requestScope) setSourceState({ scope: requestScope, chunk: payload as Chunk, error: null });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || scopeRef.current !== requestScope) return;
        setSourceState({ scope: requestScope, chunk: null, error: error instanceof Error ? error.message : "The source passage could not be loaded." });
      })
      .finally(() => { if (!controller.signal.aborted && scopeRef.current === requestScope) setLoadingChunk(false); });
    return () => controller.abort();
  }, [courseId, selectedChunkId, tab, scope]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const referencedIds = useMemo(() => {
    const ids = new Set(sourceIds);
    for (const turn of turns) for (const citation of turn.citations) ids.add(citation.chunk_id);
    return [...ids];
  }, [sourceIds, turns]);

  const askTutor = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const question = prompt.trim();
    if (!question || busy || !conversationId || assessmentAccess !== "available") return;

    const requestScope = scope;
    const requestId = ++requestIdRef.current;
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;
    setBusy(true);
    setTutorError(null);
    const turn: Turn = { id: `${Date.now()}-${requestId}`, question, answer: "", citations: [], insufficient: false };
    setConversation((current) => current?.scope === requestScope ? { ...current, turns: [...current.turns, turn] } : current);

    try {
      const response = await fetch(`/api/v1/courses/${courseId}/tutor`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          context_lesson_id: contextLessonId || undefined,
          decision_id: decisionId || undefined,
          conversation_id: conversationId,
        }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        if (response.status === 409) setConversation((current) => current?.scope === requestScope ? { ...current, available: false, turns: [], hasMore: false } : current);
        throw new Error(typeof detail?.detail === "string" ? detail.detail : "The tutor could not answer from this course right now.");
      }
      if (!response.body) throw new Error("The tutor response was empty.");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let answer = "";
      let citations: Citation[] = [];
      let insufficient = false;
      let completed = false;
      let savedMessageId: string | null = null;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() || "";
        for (const frame of frames) {
          const event = frame.match(/^event: ([^\n]+)$/m)?.[1];
          const data = frame.match(/^data: (.+)$/m)?.[1];
          if (!event || !data) continue;
          let payload: unknown;
          try { payload = JSON.parse(data); } catch { throw new Error("The tutor response was interrupted. Refresh the conversation to check for a saved answer."); }
          if (event === "token" && typeof (payload as { text?: unknown }).text === "string") {
            answer = (payload as { text: string }).text;
          } else if (event === "citation") {
            citations = [...citations, payload as Citation];
          } else if (event === "insufficient") {
            insufficient = true;
            savedMessageId = (payload as { message_id: string }).message_id;
            answer = (payload as { text: string }).text;
          } else if (event === "done") {
            completed = (payload as TutorDone).grounding_mode === "source_only";
            savedMessageId = (payload as TutorDone).message_id;
          }
        }
      }
      if (controller.signal.aborted || requestId !== requestIdRef.current || scopeRef.current !== requestScope) return;
      if ((!completed && !insufficient) || !savedMessageId) throw new Error("The tutor response was interrupted. Refresh the conversation to check for a saved answer.");
      setConversation((current) => current?.scope === requestScope ? { ...current, turns: current.turns.map((item) => item.id === turn.id
        ? { ...item, id: savedMessageId!, answer, citations, insufficient } : item) } : current);
      setPrompt("");
    } catch (error) {
      if (controller.signal.aborted || requestId !== requestIdRef.current || scopeRef.current !== requestScope) return;
      setConversation((current) => current?.scope === requestScope ? { ...current, turns: current.turns.filter((item) => item.id !== turn.id) } : current);
      const message = error instanceof Error ? error.message : "The tutor could not answer right now.";
      if (message.includes("unavailable during an active assessment")) {
        setConversation((current) => current?.scope === requestScope ? { ...current, available: false, turns: [], hasMore: false } : current);
        setTutorError(null);
      } else {
        setTutorError(message);
      }
    } finally {
      if (!controller.signal.aborted && requestId === requestIdRef.current) setBusy(false);
    }
  }, [assessmentAccess, busy, contextLessonId, conversationId, courseId, decisionId, prompt, scope]);

  const openSource = (chunkId: string) => {
    panelPreferenceTouchedRef.current = true;
    setPanelOpen(true);
    setSelectedChunkId(chunkId);
    setTab("sources");
  };

  if (!panelOpen) return <aside className="nl-panel nl-panel-collapsed" aria-label="Tutor and course sources">
    <button type="button" className="nl-panel-open" onClick={() => {
      panelPreferenceTouchedRef.current = true;
      setPanelOpen(true);
    }}>
      <PanelRightOpen className="size-4" aria-hidden="true" />Open tutor and sources
    </button>
  </aside>;

  return (
    <aside className="nl-panel" aria-label="Tutor and course sources">
      <div className="nl-tabs" role="tablist" aria-label="Learning support">
        <button type="button" role="tab" aria-selected={tab === "tutor"} className="nl-tab" onClick={() => setTab("tutor")}>Tutor</button>
        <button type="button" role="tab" aria-selected={tab === "sources"} className="nl-tab" onClick={() => setTab("sources")}>Sources <span>({referencedIds.length})</span></button>
        <button type="button" className="nl-panel-close" aria-label="Close tutor and sources panel" onClick={() => {
          panelPreferenceTouchedRef.current = true;
          setPanelOpen(false);
        }}><PanelRightClose className="size-4" aria-hidden="true" /></button>
      </div>

      {tab === "tutor" ? (
        <section className="nl-panel-body flex flex-col" role="tabpanel" aria-label="Tutor">
          <div className="flex-1 space-y-4 overflow-y-auto p-4" aria-live="polite">
            {conversationId && <div className="flex flex-wrap gap-2">
              <button type="button" className="nl-button" disabled={busy || historyLoading} onClick={() => void loadHistory(conversationId)}>Refresh conversation</button>
              {currentConversation?.hasMore && turns[0] && <button type="button" className="nl-button" disabled={busy || historyLoading} onClick={() => void loadHistory(conversationId, turns[0].id)}>Load earlier messages</button>}
            </div>}
            {historyError && <p role="alert" className="border-2 border-red-700 bg-red-50 p-3 text-sm text-red-900">{historyError}</p>}
            {turns.length === 0 && (
              assessmentAccess === "blocked"
                ? <p className="border-2 border-amber-800 bg-amber-50 p-4 text-sm text-amber-950" role="status">Tutor assistance is unavailable while you have an active assessment. Return to the assessment to continue.</p>
                : assessmentAccess === "checking"
                  ? <p className="border-2 border-dashed border-zinc-500 bg-white p-4 text-sm text-zinc-700" role="status">Checking whether tutor help is available for this activity…</p>
                  : assessmentAccess === "unavailable"
                    ? <p className="border-2 border-red-700 bg-red-50 p-4 text-sm text-red-900" role="alert">Tutor availability could not be verified. Return to your course or assessment and try again.</p>
                    : <p className="border-2 border-black bg-white p-4 text-sm text-zinc-700">{assessmentSubmitted
                      ? "Ask for clarification about your submitted results. Answers are checked against this course’s sources."
                      : "Ask about this learning activity. Answers are checked against this course’s sources."}</p>
            )}
            {turns.map((turn) => (
              <div key={turn.id} className="space-y-2">
                <p className="ml-auto w-fit max-w-[92%] border-2 border-black bg-black px-3 py-2 text-sm text-white">{turn.question}</p>
                {turn.answer ? (
                  <div className="max-w-[96%] border-2 border-black bg-white p-3 text-sm">
                    {turn.insufficient && <p className="mb-2 font-bold text-amber-900">The available sources did not support an answer.</p>}
                    <MarkdownMessage content={turn.answer} />
                    {turn.citations.length > 0 && <div className="mt-3 flex flex-wrap gap-2 border-t-2 border-zinc-200 pt-2">
                      {turn.citations.map((citation, index) => (
                        <button key={`${citation.chunk_id}-${index}`} type="button" className="nl-chip nl-chip-yellow" onClick={() => openSource(citation.chunk_id)}>
                          Source {index + 1}
                        </button>
                      ))}
                    </div>}
                  </div>
                ) : busy && turn.id.endsWith(`-${requestIdRef.current}`) ? (
                  <p className="flex items-center gap-2 p-3 text-sm text-zinc-600" role="status"><LoaderCircle className="size-4 animate-spin" />Checking course sources…</p>
                ) : null}
              </div>
            ))}
          </div>
          {tutorError && <p className="mx-4 mb-2 border-2 border-red-700 bg-red-50 p-2 text-sm text-red-900" role="alert">{tutorError}</p>}
          <form className="flex gap-2 border-t-2 border-black p-3" onSubmit={(event) => void askTutor(event)}>
            <label className="sr-only" htmlFor="learning-tutor-question">Ask the tutor</label>
            <input id="learning-tutor-question" className="nl-form-control" value={prompt} onChange={(event) => setPrompt(event.target.value)} maxLength={2000} placeholder="Ask about the material" disabled={busy || !conversationId || assessmentAccess !== "available"} />
            <button className="nl-button nl-button-primary" type="submit" disabled={busy || !prompt.trim() || !conversationId || assessmentAccess !== "available"} aria-label="Send question"><Send className="size-4" /></button>
          </form>
        </section>
      ) : (
        <section className="nl-panel-body grid min-h-0 md:grid-cols-[minmax(130px,.75fr)_minmax(0,1.25fr)]" role="tabpanel" aria-label="Sources">
          <nav className="overflow-y-auto border-b-2 border-black md:border-b-0 md:border-r-2" aria-label="Cited passages">
            {referencedIds.length > 0 ? referencedIds.map((id, index) => (
              <button key={id} type="button" aria-current={selectedChunkId === id ? "true" : undefined} onClick={() => setSelectedChunkId(id)} className={`block w-full border-b-2 border-black px-3 py-3 text-left text-sm font-bold ${selectedChunkId === id ? "bg-[#FFD23F]" : "bg-white hover:bg-zinc-100"}`}>
                Source {index + 1}
              </button>
            )) : <p className="p-4 text-sm text-zinc-600">Source passages appear here when this activity cites them.</p>}
          </nav>
          <div className="overflow-auto p-4">
            {loadingChunk && <p className="flex items-center gap-2 text-sm" role="status"><LoaderCircle className="size-4 animate-spin" />Loading the cited passage…</p>}
            {chunkError && <p className="text-sm text-red-800" role="alert">{chunkError}</p>}
            {chunk && !loadingChunk && <article>
              <h2 className="text-lg font-bold">{chunk.filename}</h2>
              {locationFor(chunk) && <p className="mt-1 text-sm font-semibold text-zinc-700">{locationFor(chunk)}</p>}
              <blockquote className="mt-4 whitespace-pre-wrap border-l-4 border-black pl-4 text-sm leading-relaxed">{chunk.text}</blockquote>
            </article>}
            {!selectedChunkId && !chunkError && <p className="text-sm text-zinc-600">Choose a cited passage to read it beside your activity.</p>}
          </div>
        </section>
      )}
    </aside>
  );
}
