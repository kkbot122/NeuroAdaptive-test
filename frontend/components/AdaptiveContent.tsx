"use client";

import React, { useState } from "react";
import TrackedParagraph from "./TrackedParagraph";
import TrackedCodeBlock from "./TrackedCodeBlock";

interface Paragraph {
  id: number;
  original_text: string;
  adapted_text: string;
}

interface AdaptiveContentProps {
  paragraphs: Paragraph[];
}

export default function AdaptiveContent({ paragraphs }: AdaptiveContentProps) {
  const [isAdapted, setIsAdapted] = useState(true);
  const [isTransitioning, setIsTransitioning] = useState(false);

  // Triggered when the toggle is clicked
  const handleToggle = () => {
    setIsTransitioning(true);
    
    // Artificial delay to simulate "AI Processing"
    setTimeout(() => {
      setIsAdapted((prev) => !prev);
      setIsTransitioning(false);
    }, 600); 
  };

  return (
    <div className="space-y-8">
      {/* --- NEURO-ADAPTIVE TOGGLE CONTROLLER --- */}
      <div className="flex items-center justify-between p-5 bg-gradient-to-r from-blue-50 to-indigo-50 rounded-2xl border border-blue-100 shadow-sm transition-all">
        <div className="flex flex-col">
          <div className="flex items-center gap-2">
            {/* Status Dot Logic */}
            <div className={`w-2.5 h-2.5 rounded-full transition-colors duration-300 ${
              isTransitioning ? 'bg-yellow-400 animate-ping' : isAdapted ? 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]' : 'bg-gray-400'
            }`} />
            
            <h3 className="font-bold text-blue-900 text-sm tracking-tight uppercase">
              {isTransitioning ? "AI Re-Processing..." : "Neuro-Adaptive View"}
            </h3>
          </div>
          <p className="text-xs text-blue-700 mt-1">
            {isTransitioning 
              ? "Analyzing cognitive profile and restructuring data..." 
              : isAdapted 
                ? "Content optimized for your unique learning archetype." 
                : "Showing raw, uncompressed source material."}
          </p>
        </div>

        <button
          onClick={handleToggle}
          disabled={isTransitioning}
          aria-label="Toggle Adaptive Content"
          className={`relative inline-flex h-7 w-12 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-blue-400 focus:ring-offset-2 ${
            isAdapted ? "bg-blue-600" : "bg-gray-300"
          } ${isTransitioning ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
        >
          <span
            className={`inline-block h-5 w-5 transform rounded-full bg-white shadow-md transition-transform duration-300 ease-in-out ${
              isAdapted ? "translate-x-6" : "translate-x-1"
            }`}
          />
        </button>
      </div>

      {/* --- CONTENT AREA --- */}
      <div className={`transition-all duration-500 ease-in-out ${isTransitioning ? "opacity-40 blur-[1px]" : "opacity-100 blur-0"}`}>
        {paragraphs.map((p, index) => (
          <div key={p.id} className="relative mb-6 last:mb-0">
            {/* SKELETON LOADER OVERLAY */}
            {isTransitioning && (
              <div className="absolute inset-0 z-10 space-y-3">
                <div className="h-4 bg-gray-200 rounded w-full animate-pulse"></div>
                <div className="h-4 bg-gray-200 rounded w-5/6 animate-pulse"></div>
                <div className="h-4 bg-gray-200 rounded w-4/6 animate-pulse shadow-sm"></div>
              </div>
            )}

            <div className={isTransitioning ? "invisible" : "visible"}>
              <TrackedParagraph
                paragraphId={p.id}
                text={isAdapted ? p.adapted_text : p.original_text}
              />

              {/* Logic Tracker Example */}
              {index === 0 && (
                <div className="mt-8 mb-8">
                  <TrackedCodeBlock
                    id={999}
                    language="system-logic"
                    code={`// Current UI Configuration\n{\n  "mode": "${isAdapted ? "ADAPTIVE" : "ORIGINAL"}",\n  "ai_processing": ${isTransitioning}\n}`}
                  />
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
