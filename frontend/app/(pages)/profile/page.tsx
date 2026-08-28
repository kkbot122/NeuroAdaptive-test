"use client";

import React, { useEffect, useState, useTransition } from "react";
import {
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Cell,
  ReferenceLine,
  Tooltip,
} from "recharts";
import { useSession } from "next-auth/react";
import { Brain, RefreshCw, Zap } from "lucide-react";
import {
  getMyProfileAction,
  getFSLSMVectorAction,
  resetFSLSMVectorAction,
  type FSLSMVectorResponse,
} from "@/app/actions/profile";

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface ProfileData {
  primary_archetype: string;
  raw_scores: {
    visual: number;
    structural: number;
    active: number;
    logic: number;
  };
  learning_sessions_count: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// FSLSM bar config
// ─────────────────────────────────────────────────────────────────────────────

const FSLSM_DIMS = [
  {
    key: "processing" as const,
    leftLabel: "Active",
    rightLabel: "Reflective",
    color: "#FF6B6B",
  },
  {
    key: "perception" as const,
    leftLabel: "Sensing",
    rightLabel: "Intuitive",
    color: "#FF9F1C",
  },
  {
    key: "reception" as const,
    leftLabel: "Visual",
    rightLabel: "Verbal",
    color: "#6C63FF",
  },
  {
    key: "understanding" as const,
    leftLabel: "Sequential",
    rightLabel: "Global",
    color: "#2ECC71",
  },
];

// ─────────────────────────────────────────────────────────────────────────────
// Profile page
// ─────────────────────────────────────────────────────────────────────────────

export default function ProfileDashboard() {
  const { data: session } = useSession();
  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [fslsm, setFslsm] = useState<FSLSMVectorResponse | null>(null);
  const [mounted, setMounted] = useState(false);
  const [isPending, startTransition] = useTransition();

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!session?.user?.email) return;

    Promise.all([getMyProfileAction(), getFSLSMVectorAction()])
      .then(([profileData, fslsmData]) => {
        setProfile(profileData);
        setFslsm(fslsmData);
      })
      .catch(console.error);
  }, [session]);

  const handleResetFSLSM = () => {
    startTransition(async () => {
      try {
        const updated = await resetFSLSMVectorAction();
        setFslsm(updated);
      } catch (e) {
        console.error(e);
      }
    });
  };

  if (!mounted || !profile) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[#F4F1EA] font-(family-name:--font-kodchasan)">
        <div className="flex items-center gap-3 bg-white border-2 border-black p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] animate-pulse font-bold">
          <Brain className="w-6 h-6" />
          LOADING NEURO-PROFILE...
        </div>
      </div>
    );
  }

  const radarData = [
    { subject: "Visual", value: profile.raw_scores.visual || 0 },
    { subject: "Structural", value: profile.raw_scores.structural || 0 },
    { subject: "Active", value: profile.raw_scores.active || 0 },
    { subject: "Logic", value: profile.raw_scores.logic || 0 },
  ];

  return (
    <div className="min-h-screen bg-[#F4F1EA] text-black font-(family-name:--font-kodchasan) py-10 px-4 md:px-6 pb-20">
      <div className="max-w-5xl mx-auto space-y-10">

        {/* ── Header ─────────────────────────────────────────────── */}
        <div className="text-center">
          <div className="inline-block bg-[#FF9F1C] border-2 border-black px-4 py-1 text-sm font-bold mb-4 shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] rotate-1">
            BRAIN SCANNED
          </div>
          <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-4">
            Cognitive Profile
          </h1>
          <div className="inline-flex items-center gap-2 px-6 py-2 bg-white border-4 border-black shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] -rotate-1">
            <Zap className="w-5 h-5" />
            <p className="text-black font-extrabold text-lg md:text-xl tracking-widest uppercase">
              {profile.primary_archetype.replace(/_/g, " ")}
            </p>
          </div>
          <p className="mt-4 text-gray-500 font-medium">
            {profile.learning_sessions_count} learning session
            {profile.learning_sessions_count !== 1 ? "s" : ""} logged
          </p>
        </div>

        {/* ── Legacy Radar + Stat Bars ────────────────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          {/* Radar Chart */}
          <div className="bg-white border-4 border-black p-6 shadow-[8px_8px_0px_0px_rgba(0,0,0,1)]">
            <h2 className="text-lg font-extrabold uppercase mb-4 border-b-2 border-black pb-2">
              Cognitive Radar
            </h2>
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart cx="50%" cy="50%" outerRadius="70%" data={radarData}>
                  <PolarGrid stroke="#000" strokeWidth={1.5} />
                  <PolarAngleAxis
                    dataKey="subject"
                    tick={{ fill: "#000", fontSize: 13, fontWeight: 800 }}
                  />
                  <PolarRadiusAxis domain={[0, 50]} tick={false} axisLine={false} />
                  <Radar
                    name="Scores"
                    dataKey="value"
                    stroke="#6C63FF"
                    strokeWidth={3}
                    fill="#CBF3F0"
                    fillOpacity={0.85}
                  />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Legacy stat bars */}
          <div className="bg-white border-4 border-black p-6 shadow-[8px_8px_0px_0px_rgba(0,0,0,1)]">
            <h2 className="text-lg font-extrabold uppercase mb-6 border-b-2 border-black pb-2">
              Dimension Breakdown
            </h2>
            <div className="space-y-6">
              <StatBar label="VISUAL AFFINITY" value={profile.raw_scores.visual || 0} color="bg-[#FF6B6B]" />
              <StatBar label="STRUCTURAL OVERVIEWS" value={profile.raw_scores.structural || 0} color="bg-[#FF9F1C]" />
              <StatBar label="ACTIVE ENGAGEMENT" value={profile.raw_scores.active || 0} color="bg-[#CBF3F0]" />
              <StatBar label="LOGICAL REASONING" value={profile.raw_scores.logic || 0} color="bg-[#6C63FF]" />
            </div>
          </div>
        </div>

        {/* ── FSLSM Vector Panel (Phase 4) ───────────────────────── */}
        <div className="bg-white border-4 border-black p-6 md:p-8 shadow-[8px_8px_0px_0px_rgba(0,0,0,1)]">
          <div className="flex items-center justify-between mb-6 border-b-2 border-black pb-4">
            <div>
              <h2 className="text-xl font-extrabold uppercase">FSLSM Learning Style Vector</h2>
              <p className="text-sm text-gray-500 font-medium mt-1">
                Felder-Silverman Model — evolves with every session
              </p>
            </div>
            <button
              onClick={handleResetFSLSM}
              disabled={isPending}
              className="flex items-center gap-2 px-4 py-2 border-2 border-black rounded-lg font-bold text-sm hover:bg-gray-100 transition-all shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] disabled:opacity-50"
            >
              <RefreshCw className={`w-4 h-4 ${isPending ? "animate-spin" : ""}`} />
              Reset
            </button>
          </div>

          {fslsm ? (
            <div className="space-y-6">
              {FSLSM_DIMS.map((dim) => {
                const value = fslsm[dim.key];
                const label = fslsm.labels?.[dim.key] ?? "Balanced";
                return (
                  <FSLSMBar
                    key={dim.key}
                    leftLabel={dim.leftLabel}
                    rightLabel={dim.rightLabel}
                    value={value}
                    color={dim.color}
                    label={label}
                  />
                );
              })}

              {/* Recharts bi-directional bar chart (Phase 4) */}
              <div className="mt-8 border-t-2 border-dashed border-gray-200 pt-6">
                <h3 className="text-sm font-extrabold uppercase mb-4 text-gray-500">
                  Vector Summary Chart
                </h3>
                <div className="h-55">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={FSLSM_DIMS.map((d) => ({
                        name: d.key.charAt(0).toUpperCase() + d.key.slice(1),
                        value: fslsm[d.key],
                        fill: d.color,
                      }))}
                      margin={{ top: 5, right: 10, left: 10, bottom: 5 }}
                    >
                      <XAxis
                        dataKey="name"
                        tick={{ fontSize: 11, fontWeight: 700 }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <YAxis domain={[-1, 1]} tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
                      <Tooltip
                        formatter={(val: number | string | any) => typeof val === "number" ? val.toFixed(3) : val}
                        contentStyle={{
                          border: "2px solid black",
                          borderRadius: "8px",
                          fontWeight: 700,
                        }}
                      />
                      <ReferenceLine y={0} stroke="#000" strokeWidth={2} />
                      <Bar dataKey="value" radius={[4, 4, 0, 0]} maxBarSize={60}>
                        {FSLSM_DIMS.map((d, i) => (
                          <Cell key={i} fill={d.color} stroke="#000" strokeWidth={1.5} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <div className="flex justify-between text-xs text-gray-400 font-bold mt-2 px-2">
                  <span>← Left pole (e.g. Active, Visual, Sensing, Sequential)</span>
                  <span>Right pole (Reflective, Verbal, Intuitive, Global) →</span>
                </div>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center py-12 text-gray-400 font-bold">
              FSLSM data loading...
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────────────

function StatBar({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  const pct = Math.min((value / 50) * 100, 100);
  return (
    <div>
      <div className="flex justify-between text-xs mb-1.5 font-extrabold uppercase">
        <span>{label}</span>
        <span className="bg-black text-white px-2 py-0.5 text-xs">{value} pts</span>
      </div>
      <div className="w-full bg-gray-100 border-2 border-black h-5 overflow-hidden shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]">
        <div
          className={`${color} h-full border-r-2 border-black transition-all duration-700`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function FSLSMBar({
  leftLabel,
  rightLabel,
  value,
  color,
  label,
}: {
  leftLabel: string;
  rightLabel: string;
  value: number;  // -1.0 to +1.0
  color: string;
  label: string;
}) {
  // Convert -1..+1 to 0..100% for left side and right side
  const pctFromCenter = Math.abs(value) * 50; // 0 to 50
  const isLeft = value < 0;
  const isNeutral = Math.abs(value) < 0.05;

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className={`text-xs font-extrabold uppercase ${isLeft && !isNeutral ? "text-black" : "text-gray-400"}`}>
          {leftLabel}
        </span>
        <span className="text-xs font-bold text-gray-500 px-2 border border-gray-200 rounded">
          {label}
        </span>
        <span className={`text-xs font-extrabold uppercase ${!isLeft && !isNeutral ? "text-black" : "text-gray-400"}`}>
          {rightLabel}
        </span>
      </div>
      {/* Bi-directional bar */}
      <div className="relative w-full h-5 bg-gray-100 border-2 border-black flex shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]">
        {/* Center line */}
        <div className="absolute left-1/2 top-0 bottom-0 w-0.5 bg-black z-10" />
        {/* Bar fill */}
        {isNeutral ? null : isLeft ? (
          <div
            className="absolute top-0 bottom-0 right-1/2 transition-all duration-700"
            style={{ width: `${pctFromCenter}%`, backgroundColor: color, border: "1px solid rgba(0,0,0,0.3)" }}
          />
        ) : (
          <div
            className="absolute top-0 bottom-0 left-1/2 transition-all duration-700"
            style={{ width: `${pctFromCenter}%`, backgroundColor: color, border: "1px solid rgba(0,0,0,0.3)" }}
          />
        )}
        {/* Value label */}
        <div className="absolute inset-0 flex items-center justify-center z-20">
          <span className="text-xs font-black text-black mix-blend-multiply">
            {value >= 0 ? "+" : ""}{value.toFixed(2)}
          </span>
        </div>
      </div>
    </div>
  );
}
