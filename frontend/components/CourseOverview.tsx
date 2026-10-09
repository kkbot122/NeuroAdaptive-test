"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { components } from "@/lib/generated/api";
import { ArrowLeft, ArrowRight, LoaderCircle } from "lucide-react";
import { selectedActivityHref } from "@/lib/learning-route";

type Course = components["schemas"]["CourseOut"];
type Structure = components["schemas"]["StructureOut"];
type LearningState = components["schemas"]["LearningStateOut"];
type Graph = components["schemas"]["GraphOut"];
type Activity = components["schemas"]["LearningActivityOut"];
type LinkedMatch = components["schemas"]["LinkedConceptMatchOut"];

const BANDS = ["Not assessed", "Needs attention", "Developing", "Proficient", "Mastered"] as const;

function bandClass(band: string): string {
  const key = band.toLowerCase().replaceAll(" ", "-");
  return `nl-label nl-label-${key}`;
}

function activityName(activity: Activity, lessons: Map<string, string>, conceptNames: Map<string, string>): string {
  if (activity.is_optional_linked_check) {
    return `Optional prerequisite check: ${conceptNames.get(activity.target_concept_ids[0]) || "possible earlier knowledge"}`;
  }
  if (activity.activity_type === "PREREQUISITE_REMEDIATION") {
    return `Review ${conceptNames.get(activity.target_concept_ids[0]) || "a selected concept"}`;
  }
  if (activity.activity_type === "TARGETED_PRACTICE") {
    return conceptNames.get(activity.target_concept_ids[0]) || "Selected concept";
  }
  if (activity.activity_type === "CHALLENGE") {
    const names = activity.target_concept_ids.map((id) => conceptNames.get(id)).filter((name): name is string => Boolean(name));
    return names.length ? names.join(" and ") : "Concept application";
  }
  if (activity.activity_type === "DIAGNOSTIC") return "Course diagnostic";
  return lessons.get(activity.lesson_id || "") || "Continue the selected lesson";
}

function activityHeading(activity: Activity, lessons: Map<string, string>, conceptNames: Map<string, string>): string {
  const name = activityName(activity, lessons, conceptNames);
  if (activity.activity_type === "TARGETED_PRACTICE") return `Practice: ${name}`;
  if (activity.activity_type === "CHALLENGE") return `Challenge: ${name}`;
  if (activity.activity_type === "DIAGNOSTIC") return "Course diagnostic";
  return name;
}

export function CourseOverview({ course, structure, learningState, graph }: {
  course: Course;
  structure: Structure;
  learningState: LearningState;
  graph: Graph;
}) {
  const router = useRouter();
  const [tab, setTab] = useState<"outline" | "concepts">("outline");
  const [continuing, setContinuing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [linkedMatches, setLinkedMatches] = useState<LinkedMatch[]>([]);
  const [linkedMatchesLoaded, setLinkedMatchesLoaded] = useState(false);
  const [linkedMatchError, setLinkedMatchError] = useState<string | null>(null);
  const [startingLinkedCheck, setStartingLinkedCheck] = useState<string | null>(null);
  const conceptNames = useMemo(() => new Map(graph.concepts.map((concept) => [concept.id, concept.name])), [graph.concepts]);
  const lessonNames = useMemo(() => new Map(structure.modules.flatMap((module) => module.lessons.map((lesson) => [lesson.id, lesson.title] as const))), [structure.modules]);
  const understanding = useMemo(() => new Map(learningState.concept_understanding.map((row) => [row.concept_id, row])), [learningState.concept_understanding]);
  const covered = useMemo(() => new Set(learningState.lesson_coverage.covered_lesson_ids), [learningState.lesson_coverage.covered_lesson_ids]);
  const active = learningState.active_activity;
  const counts = BANDS.map((band) => learningState.concept_understanding.filter((row) => row.band === band).length);
  const knownConceptCount = Math.max(graph.concepts.length, 1);
  const coverage = learningState.lesson_coverage;
  const coveragePercent = coverage.lessons_total ? Math.round(coverage.lessons_covered / coverage.lessons_total * 100) : 0;

  useEffect(() => {
    setLinkedMatches([]);
    setLinkedMatchesLoaded(false);
    setLinkedMatchError(null);
    if (!course.builds_on_course_id || (course.uncertain_linked_match_count < 1 && course.unsupported_linked_match_count < 1)) return;
    const controller = new AbortController();
    void fetch(`/api/v1/courses/${course.id}/linked-matches`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Linked concept checks could not be loaded.");
        const matches: LinkedMatch[] = await response.json();
        if (!controller.signal.aborted) {
          setLinkedMatches(matches.filter((match) => match.status === "UNCERTAIN" || (match.status === "UNSUPPORTED" && !match.has_source_support)));
          setLinkedMatchesLoaded(true);
        }
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setLinkedMatchError(cause instanceof Error ? cause.message : "Linked concept checks could not be loaded.");
      });
    return () => controller.abort();
  }, [course.builds_on_course_id, course.id, course.uncertain_linked_match_count, course.unsupported_linked_match_count]);

  const startLinkedCheck = async (match: LinkedMatch) => {
    if (startingLinkedCheck || active) return;
    setStartingLinkedCheck(match.id);
    setLinkedMatchError(null);
    try {
      const response = await fetch(`/api/v1/courses/${course.id}/linked-matches/${match.id}/optional-check`, { method: "POST" });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || "This optional check could not be prepared.");
      const activity = payload as Activity;
      const href = selectedActivityHref(course.id, activity, structure);
      if (!href) throw new Error(activity.unavailable_reason || "This optional check could not be opened.");
      router.push(href);
    } catch (cause) {
      setLinkedMatchError(cause instanceof Error ? cause.message : "This optional check could not be prepared.");
    } finally {
      setStartingLinkedCheck(null);
    }
  };

  const continueStudying = async () => {
    if (continuing) return;
    setContinuing(true);
    setError(null);
    try {
      const response = await fetch(`/api/v1/courses/${course.id}/activities/next`, { method: "POST", cache: "no-store" });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || "Your saved activity could not be opened. Try again.");
      const activity = payload as Activity;
      const href = selectedActivityHref(course.id, activity, structure);
      if (!href) {
        setError(activity.unavailable_reason || "The selected activity is saved, but its supported learning screen is unavailable.");
        return;
      }
      router.push(href);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Your saved activity could not be opened. Try again.");
    } finally {
      setContinuing(false);
    }
  };

  const currentTargets = active?.target_concept_ids.map((id) => conceptNames.get(id) || "Selected concept") || [];

  return (
    <div className="nl-screen">
      <nav className="nl-topbar nl-course-overview-topbar" aria-label="Course navigation">
        <Link href="/dashboard" className="nl-button"><ArrowLeft className="size-4" />Back to dashboard</Link>
        <span className="nl-spacer" />
        <Link href="/dashboard" className="nl-brand"><span className="nl-brand-mark" aria-hidden="true" /><span className="nl-brand-label">NeuroLearn</span></Link>
      </nav>

      <main className="nl-course-overview-main">
        <header className="nl-course-overview-heading">
          <h1>{course.title}</h1>
          {course.goal && <p>{course.goal}</p>}
          {(course.subject_name || course.builds_on_course_title) && <div className="nl-course-overview-relationship" aria-label="Course relationships">
            {course.subject_name && <span>Subject: <strong>{course.subject_name}</strong></span>}
            {course.builds_on_course_title && <span>Builds on: <strong>{course.builds_on_course_title}</strong>{course.builds_on_sources_available && course.builds_on_version_number ? ` · Published version ${course.builds_on_version_number}` : " · Earlier sources are unavailable"}</span>}
          </div>}
          {course.builds_on_sources_available && course.reliable_linked_match_count > 0 && <p className="mt-3 max-w-3xl border-2 border-black bg-[#fffbe0] p-3 text-sm">
            For {course.reliable_linked_match_count} reliably matched concept{course.reliable_linked_match_count === 1 ? "" : "s"}, corrected evidence from {course.builds_on_course_title} can inform prerequisite readiness when this course has no graded evidence yet. Mastery and attempts shown here remain attributed to this course.
          </p>}
        </header>

        {error && <div role="alert" className="border-2 border-red-800 bg-red-50 p-4 text-red-950"><p>{error}</p><button type="button" className="nl-button mt-3" onClick={() => void continueStudying()} disabled={continuing}>Try again</button></div>}

        {course.builds_on_sources_available && (course.uncertain_linked_match_count > 0 || course.unsupported_linked_match_count > 0) && <section className="nl-card" aria-label="Earlier-course concept matches">
          <h2 className="text-xl font-bold">Earlier-course concept matches</h2>
          <p className="mt-2 text-sm text-zinc-700">Possible matches have not been treated as equivalent. When both source sets support a possible match, an optional check can test the current course concept. Skipping records no negative evidence.</p>
          {linkedMatchError && <p className="mt-3 text-sm text-red-800" role="alert">{linkedMatchError}</p>}
          {linkedMatches.length > 0 ? <ul className="mt-4 grid gap-3">{linkedMatches.map((match) => <li key={match.id} className="border-2 border-black bg-white p-4">
            {match.status === "UNCERTAIN" ? <>
              <p><strong>{match.current_concept_name}</strong> may build on <strong>{match.linked_concept_name}</strong> from {course.builds_on_course_title}.</p>
              {match.has_source_support ? <button type="button" className="nl-button mt-3" disabled={Boolean(active) || Boolean(startingLinkedCheck)} onClick={() => void startLinkedCheck(match)}>{startingLinkedCheck === match.id ? "Preparing check…" : active ? "Finish the current activity first" : "Take optional check"}</button> : <p className="mt-2 text-sm text-zinc-700">There is no usable explanation in both source sets, so no check is available. You can continue with this course.</p>}
            </> : <p><strong>{match.current_concept_name}</strong> and <strong>{match.linked_concept_name}</strong> could not be compared because there is no usable supporting material in both courses. No earlier evidence is reused for this match; you can continue with this course.</p>}
          </li>)}</ul> : <p className="mt-3 text-sm text-zinc-700" role="status">{linkedMatchError ? "Retry by refreshing the overview." : linkedMatchesLoaded ? "No optional checks or missing-source limitations were found. You can continue studying." : "Loading possible concept matches…"}</p>}
        </section>}

        {active ? (
          <section className="nl-course-overview-hero" aria-label="Current activity">
            <div>
              <span className="nl-course-overview-label">Continue studying</span>
              <h2>{activityHeading(active, lessonNames, conceptNames)}</h2>
              {active.reason && <p>{active.reason}</p>}
              {currentTargets.length > 0 && <ul className="nl-course-overview-targets" aria-label="Selected concepts">{currentTargets.map((name, index) => <li key={`${name}-${index}`} className="nl-chip nl-chip-violet">{name}</li>)}</ul>}
            </div>
            <button type="button" className="nl-button nl-button-primary nl-button-large shrink-0" onClick={() => void continueStudying()} disabled={continuing}>{continuing ? <LoaderCircle className="size-5 animate-spin" /> : null}Continue <ArrowRight className="size-5" /></button>
          </section>
        ) : (
          <section className="nl-course-overview-hero" aria-label="No unfinished activity">
            <div><span className="nl-course-overview-label">Continue studying</span><h2>Your course is ready</h2><p>Continue selects one supported next activity and saves it so you can resume later.</p><Link href={`/courses/${course.id}/diagnostic`} className="nl-course-overview-diagnostic">Take an optional baseline diagnostic</Link></div>
            <button type="button" className="nl-button nl-button-primary nl-button-large shrink-0" onClick={() => void continueStudying()} disabled={continuing}>{continuing ? <LoaderCircle className="size-5 animate-spin" /> : null}Continue <ArrowRight className="size-5" /></button>
          </section>
        )}

        <div className="nl-course-overview-stats">
          <section className="nl-course-overview-panel">
            <h2>Lesson coverage</h2>
            <div className="nl-course-overview-big">{coverage.lessons_covered} of {coverage.lessons_total} lessons</div>
            <div className="nl-progress" role="img" aria-label={`${coverage.lessons_covered} of ${coverage.lessons_total} lessons covered`}><span style={{ width: `${coveragePercent}%` }} /></div>
            <p className="nl-course-overview-hint">Counts lessons you finished reading. It says nothing about how well you understand them.</p>
          </section>
          <section className="nl-course-overview-panel">
            <h2>Concept understanding</h2>
            <div className="nl-course-overview-strip" role="img" aria-label={BANDS.map((band, index) => `${counts[index]} ${band}`).join(", ")}>
              {counts.map((count, index) => count > 0 ? <span key={BANDS[index]} className={["bg-zinc-200", "bg-[#FF6B5E]", "bg-[#FFD23F]", "bg-[#4FE0B0]", "bg-[#5B2EFF]"][index]} style={{ width: `${count / knownConceptCount * 100}%` }} /> : null)}
            </div>
            <div className="nl-course-overview-hint">{BANDS.map((band, index) => <span key={band}>{counts[index]} {band}</span>)}</div>
            <p className="nl-course-overview-hint nl-course-overview-hint-bottom">Counts use graded concept evidence, separately from lesson reading coverage.</p>
          </section>
        </div>

        <div className="nl-tabs nl-course-overview-tabs" role="tablist" aria-label="Course details">
          <button type="button" role="tab" id="overview-outline-tab" aria-selected={tab === "outline"} aria-controls="overview-outline" className="nl-tab" onClick={() => setTab("outline")}>Outline</button>
          <button type="button" role="tab" id="overview-concepts-tab" aria-selected={tab === "concepts"} aria-controls="overview-concepts" className="nl-tab" onClick={() => setTab("concepts")}>Concepts</button>
        </div>

        {tab === "outline" ? (
          <section id="overview-outline" role="tabpanel" aria-labelledby="overview-outline-tab" className="nl-course-overview-outline">
            <p className="nl-course-overview-note">Open a lesson to inspect its objective and concept evidence. Browsing this outline does not start or select an activity.</p>
            {structure.modules.map((module, moduleIndex) => (
              <details key={module.id} className="nl-course-overview-module" open={Boolean(active?.lesson_id && module.lessons.some((lesson) => lesson.id === active.lesson_id))}>
                <summary>
                  <span className="nl-course-overview-caret" aria-hidden="true" />
                  <span>{moduleIndex + 1}. {module.title}</span><span className="nl-course-overview-module-count">{module.lessons.filter((lesson) => covered.has(lesson.id)).length} of {module.lessons.length} covered</span>
                </summary>
                <div className="nl-course-overview-lessons">
                  {module.lessons.map((lesson, lessonIndex) => {
                    const isCovered = covered.has(lesson.id);
                    const isCurrent = active?.lesson_id === lesson.id;
                    return <article key={lesson.id} className={`nl-course-overview-lesson ${isCurrent ? "is-current" : ""}`}>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <h3 className="text-lg font-bold">{moduleIndex + 1}.{lessonIndex + 1} {lesson.title}</h3>
                        <span className={`nl-chip ${isCovered ? "nl-chip-mint" : isCurrent ? "nl-chip-yellow" : ""}`}>{isCovered ? "Covered" : isCurrent ? "Current activity" : "Not started"}</span>
                      </div>
                      {lesson.objective && <p className="mt-2 max-w-4xl text-sm text-zinc-700"><strong>Objective:</strong> {lesson.objective}</p>}
                      {lesson.concepts.length > 0 && <ul className="mt-3 flex flex-wrap gap-2" aria-label={`Concept evidence for ${lesson.title}`}>
                        {lesson.concepts.map((link) => {
                          const row = understanding.get(link.concept_id);
                          const name = conceptNames.get(link.concept_id) || "Course concept";
                          return <li key={link.concept_id} className="flex flex-wrap items-center gap-1.5"><span className="nl-chip">{name}</span><span className={bandClass(row?.band || "Not assessed")}>{row?.band || "Not assessed"}</span><span className="text-xs text-zinc-700">{row?.evidence_strength || "Not assessed"} evidence</span></li>;
                        })}
                      </ul>}
                    </article>;
                  })}
                </div>
              </details>
            ))}
            {structure.modules.length === 0 && <p className="nl-card">This published course has no outline entries.</p>}
          </section>
        ) : (
          <section id="overview-concepts" role="tabpanel" aria-labelledby="overview-concepts-tab" className="nl-course-overview-concepts">
            <p className="nl-course-overview-concepts-note">These labels summarize saved evidence for this course. They are not a claim about real-world mastery or learning gain.</p>
            {graph.concepts.map((concept) => {
              const row = understanding.get(concept.id);
              return <article key={concept.id} className="nl-course-overview-concept-row">
                <div><h3>{concept.name}</h3><p>{concept.definition}</p>{row?.lesson_id && <small>{lessonNames.get(row.lesson_id) || "Related lesson"}</small>}</div>
                <span className={bandClass(row?.band || "Not assessed")}>{row?.band || "Not assessed"}</span>
                <span className="nl-course-overview-evidence">{row?.evidence_strength || "Not assessed"} evidence</span>
              </article>;
            })}
            {graph.concepts.length === 0 && <p className="nl-card">No concepts are available in this course structure.</p>}
          </section>
        )}
      </main>
    </div>
  );
}
