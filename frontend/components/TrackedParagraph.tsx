"use client";

import { useEffect } from "react";
import { useTrackVisibility } from "@/hooks/useTrackVisibility";
import { sendLearningEvent } from "@/lib/telemetry";

interface Props {
  text: string;
  paragraphId: number;
}

export default function TrackedParagraph({ text, paragraphId }: Props) {
  const { elementRef, secondsViewed } = useTrackVisibility(0.6);

  useEffect(() => {
    // Only send a pulse every 5 seconds
    if (secondsViewed > 0 && secondsViewed % 5 === 0) {
      
      sendLearningEvent({
        event_type: "paragraph_view",
        dimension: "textual",
        seconds: 5,
        target_id: String(paragraphId),
      });
    }
  }, [secondsViewed, paragraphId]);

  return (
    <div ref={elementRef} className="transition-colors duration-500 rounded p-2 hover:bg-blue-50/50">
      <p className="text-lg text-gray-800 leading-relaxed">{text}</p>
    </div>
  );
}