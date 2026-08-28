"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { StateWrapper } from "@/components/StateWrapper";
import { ArrowLeft, FileText } from "lucide-react";

interface ChunkDetail {
  chunk_id: string;
  document_id: string;
  filename: string;
  text: string;
  heading_path: string | null;
  page_start: number | null;
  page_end: number | null;
}

export default function SourceViewerPage() {
  const params = useParams();
  const courseId = params.courseId as string;
  const chunkId = params.chunkId as string;

  const [chunk, setChunk] = useState<ChunkDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isError, setIsError] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  const fetchChunk = async () => {
    setIsLoading(true);
    setIsError(false);
    try {
      const res = await fetch(`/api/v1/courses/${courseId}/chunks/${chunkId}`);
      if (res.status === 404) {
        setIsError(true);
        setErrorMsg("This source chunk could not be found, or you don't have access to it.");
        return;
      }
      if (!res.ok) throw new Error("Failed to load source");
      setChunk(await res.json());
    } catch (err) {
      console.error(err);
      setIsError(true);
      setErrorMsg(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (courseId && chunkId) fetchChunk();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [courseId, chunkId]);

  return (
    <div className="min-h-screen bg-[#F4F1EA] text-black font-[family-name:var(--font-kodchasan)] pb-28">
      <nav className="w-full bg-white border-b-2 border-black px-6 py-4 flex items-center gap-4 sticky top-0 z-50">
        <button
          onClick={() => history.back()}
          className="p-2 hover:bg-gray-100 rounded-full border-2 border-transparent hover:border-black transition-all"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div className="w-10 h-10 bg-blue-500 rounded-lg border-2 border-black flex items-center justify-center shadow-[3px_3px_0px_0px_rgba(0,0,0,1)]">
          <FileText className="w-5 h-5 text-white" />
        </div>
        <span className="text-xl font-bold tracking-tight">Source</span>
      </nav>

      <main className="max-w-3xl mx-auto px-6 py-10">
        <StateWrapper
          isLoading={isLoading}
          isError={isError}
          errorMessage={errorMsg}
          isUnauthorized={false}
          isEmpty={false}
          onRetry={fetchChunk}
        >
          {chunk && (
            <div className="bg-white border-2 border-black rounded-xl p-8 shadow-[6px_6px_0px_0px_rgba(0,0,0,1)]">
              <div className="mb-6 pb-6 border-b-2 border-gray-100">
                <h1 className="text-2xl font-bold mb-2">{chunk.filename}</h1>
                <div className="flex flex-wrap gap-2 text-sm text-gray-500 font-medium">
                  {chunk.heading_path && (
                    <span className="bg-gray-100 border border-gray-300 rounded px-2 py-0.5">
                      {chunk.heading_path}
                    </span>
                  )}
                  {chunk.page_start != null && (
                    <span className="bg-gray-100 border border-gray-300 rounded px-2 py-0.5">
                      {chunk.page_start === chunk.page_end
                        ? `Page ${chunk.page_start}`
                        : `Pages ${chunk.page_start}-${chunk.page_end}`}
                    </span>
                  )}
                </div>
              </div>
              <p className="whitespace-pre-wrap text-gray-800 leading-relaxed">{chunk.text}</p>
            </div>
          )}
        </StateWrapper>
      </main>
    </div>
  );
}
