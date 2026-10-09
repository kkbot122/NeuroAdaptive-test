"use client";

import { useEffect, useState } from "react";
import type { components } from "@/lib/generated/api";

type Chunk = components["schemas"]["ChunkDetail"];

function SourcePassage({ courseId, chunkId, onOpenSource }: {
  courseId: string; chunkId: string; onOpenSource: (id: string) => void;
}) {
  const [chunk, setChunk] = useState<Chunk | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void fetch(`/api/v1/courses/${courseId}/chunks/${chunkId}`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(response.status === 404
          ? "This passage is unavailable for this course." : "This passage could not be loaded.");
        const payload: Chunk = await response.json();
        if (payload.chunk_id !== chunkId) throw new Error("The requested passage could not be verified.");
        if (!controller.signal.aborted) setChunk(payload);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError("This source passage could not be loaded. Your saved lesson is still available.");
      });
    return () => controller.abort();
  }, [courseId, chunkId, attempt]);

  return <section className="border-2 border-black bg-[#fffbe0] p-4" aria-label="Original source passage">
    {chunk ? <>
      <h3 className="font-bold">{chunk.filename}</h3>
      {chunk.page_start != null && <p className="text-sm">{chunk.page_start === chunk.page_end
        ? `Page ${chunk.page_start}` : `Pages ${chunk.page_start}–${chunk.page_end ?? chunk.page_start}`}</p>}
      {chunk.heading_path && <p className="text-sm text-zinc-700">{chunk.heading_path}</p>}
      <blockquote className="mt-3 whitespace-pre-wrap border-l-4 border-black pl-4 leading-relaxed [overflow-wrap:anywhere]">{chunk.text}</blockquote>
      <button type="button" className="nl-chip mt-3" onClick={() => onOpenSource(chunkId)}>Open passage in Sources tab</button>
    </> : error ? <div role="alert"><p>{error}</p><button type="button" className="nl-button mt-3" onClick={() => {
      setChunk(null); setError(null); setAttempt((value) => value + 1);
    }}>Retry passage</button></div>
      : <p role="status">Loading original passage…</p>}
  </section>;
}

export function LessonSourcePassages({ courseId, artifactId, sourceIds, onOpenSource }: {
  courseId: string; artifactId: string; sourceIds: string[]; onOpenSource: (id: string) => void;
}) {
  return <section className="mt-4 space-y-4" aria-label="Course source passages">
    <h2 className="text-xl font-bold">Read the original passages</h2>
    <p className="text-sm text-zinc-700">These are the uploaded passages cited by this lesson. The source-grounded explanation follows below.</p>
    {[...new Set(sourceIds)].map((id) => <SourcePassage key={`${courseId}:${artifactId}:${id}`} courseId={courseId} chunkId={id} onOpenSource={onOpenSource} />)}
  </section>;
}
