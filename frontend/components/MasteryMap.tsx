import React from "react";
import { Brain, Star, CheckCircle, ShieldAlert } from "lucide-react";

// Matches backend/app/modules/mastery/engine.py's classify_band() literally
// -- these exact strings (Title Case, "Not assessed" is two words), not an
// invented UPPER_SNAKE_CASE vocabulary.
export type MasteryBand = "Not assessed" | "Needs attention" | "Developing" | "Proficient" | "Mastered";

export interface MasteryRow {
  concept_id: string;
  concept_name: string;
  band: MasteryBand;
  // Only present when the caller requests ?include_raw=true
  // (mastery/schemas.py's MasteryReportRow) -- nested, not flat fields.
  raw?: {
    mastery: number;
    uncertainty: number;
    evidence_weight_total: number;
  } | null;
}

interface MasteryMapProps {
  data: MasteryRow[];
  showRawValues?: boolean;
}

const bandConfig: Record<MasteryBand, { label: string; color: string; icon: React.ReactNode; bg: string }> = {
  "Not assessed": {
    label: "Not Started",
    color: "text-gray-500",
    bg: "bg-gray-100",
    icon: <Brain className="w-5 h-5 text-gray-500" />,
  },
  "Needs attention": {
    label: "Needs Review",
    color: "text-red-600",
    bg: "bg-red-100",
    icon: <ShieldAlert className="w-5 h-5 text-red-600" />,
  },
  Developing: {
    label: "Developing",
    color: "text-yellow-600",
    bg: "bg-yellow-100",
    icon: <Star className="w-5 h-5 text-yellow-600" />,
  },
  Proficient: {
    label: "Proficient",
    color: "text-blue-600",
    bg: "bg-blue-100",
    icon: <CheckCircle className="w-5 h-5 text-blue-600" />,
  },
  Mastered: {
    label: "Mastered",
    color: "text-purple-600",
    bg: "bg-purple-100",
    icon: <Brain className="w-5 h-5 text-purple-600" fill="currentColor" />,
  },
};

export function MasteryMap({ data, showRawValues = false }: MasteryMapProps) {
  if (!data || data.length === 0) {
    return (
      <div className="p-8 text-center bg-gray-50 rounded-xl border-2 border-dashed border-gray-300">
        <p className="text-gray-500 font-medium">No mastery data available yet.</p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {data.map((row) => {
        const config = bandConfig[row.band] || bandConfig["Not assessed"];
        return (
          <div
            key={row.concept_id}
            className="flex items-center p-4 bg-white border-2 border-black rounded-xl shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-transform hover:-translate-y-1"
          >
            <div className={`p-3 rounded-lg border-2 border-black mr-4 ${config.bg}`}>
              {config.icon}
            </div>
            <div className="flex-1">
              <h4 className="font-bold text-gray-900 truncate">{row.concept_name}</h4>
              <p className={`text-sm font-bold mt-1 ${config.color}`}>{config.label}</p>
              
              {showRawValues && row.raw && (
                <div className="mt-2 text-xs text-gray-500 font-mono">
                  M: {row.raw.mastery.toFixed(2)} | U: {row.raw.uncertainty.toFixed(2)}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
