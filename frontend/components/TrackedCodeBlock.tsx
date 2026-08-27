"use client";

import { useEffect } from "react";
import { useTrackVisibility } from "@/hooks/useTrackVisibility";
import { sendLearningEvent } from "@/lib/telemetry";

interface Props {
  code: string;
  language: string;
  id: number;
}

export default function TrackedCodeBlock({ code, language, id }: Props) {
  // Logic tracking usually requires higher focus, so we use a 0.7 threshold
  const { elementRef, secondsViewed } = useTrackVisibility(0.7);

  useEffect(() => {
    if (secondsViewed > 0 && secondsViewed % 5 === 0) {
      sendLearningEvent({
        event_type: "code_view",
        dimension: "logic",
        seconds: 5,
        target_id: String(id),
      });
    }
  }, [secondsViewed, id]);

  return (
    <div ref={elementRef} className="my-6 rounded-lg bg-gray-900 p-4 shadow-lg border-l-4 border-blue-500">
      <div className="flex justify-between items-center mb-2">
        <span className="text-xs font-mono text-gray-400 uppercase">{language}</span>
        <span className="text-[10px] text-gray-600 font-mono">{secondsViewed}s analyzed</span>
      </div>
      <pre className="text-blue-300 font-mono text-sm overflow-x-auto">
        <code>{code}</code>
      </pre>
    </div>
  );
}