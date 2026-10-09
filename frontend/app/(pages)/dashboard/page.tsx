"use client";

import Link from "next/link";
import type { components } from "@/lib/generated/api";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRight, Plus } from "lucide-react";
import { CourseSidebar } from "@/components/CourseSidebar";
import { selectedActivityHref } from "@/lib/learning-route";

type Course = components["schemas"]["CourseOut"];
type LearningState = components["schemas"]["LearningStateOut"];
type Activity = components["schemas"]["LearningActivityOut"];
type Structure = components["schemas"]["StructureOut"];
type Graph = components["schemas"]["GraphOut"];
type CourseCard = { course: Course; learningState: LearningState | null; structure?: Structure; graph?: Graph };

function actionFor(course: Course) {
  if (course.status === "PUBLISHED") return { label: "Open course", href: `/courses/${course.id}/learn` };
  if (course.status === "REVIEW_READY") return { label: "Review outline", href: `/courses/${course.id}/workspace` };
  if (course.status === "PROCESSING") return { label: "View progress", href: `/courses/${course.id}/workspace` };
  if (course.status === "NEEDS_INPUT" || course.status === "FAILED") return { label: "View status", href: `/courses/${course.id}/workspace` };
  return { label: course.source_count ? "Finish setup" : "Add sources", href: `/courses/${course.id}/workspace` };
}

function activityKind(activity: Activity | null): string | null {
  if (!activity) return null;
  if (activity.activity_type === "PREREQUISITE_REMEDIATION") return "Concept review";
  if (activity.activity_type === "TARGETED_PRACTICE") return "Practice";
  if (activity.activity_type === "CHALLENGE") return "Challenge";
  if (activity.activity_type === "DIAGNOSTIC") return "Diagnostic";
  return "Lesson";
}

function activityName(card: CourseCard): string | null {
  const activity = card.learningState?.active_activity;
  if (!activity) return null;
  const lesson = card.structure?.modules.flatMap((module) => module.lessons).find((item) => item.id === activity.lesson_id);
  const conceptNames = new Map(card.graph?.concepts.map((concept) => [concept.id, concept.name]) ?? []);
  if (lesson) return lesson.title;
  const targets = activity.target_concept_ids.map((id) => conceptNames.get(id)).filter((name): name is string => Boolean(name));
  if (targets.length) return targets.join(" and ");
  return activityKind(activity);
}

function describeCourse(card: CourseCard): { message: string; chip: string; chipClass: string; loading?: boolean } {
  const { course, learningState } = card;
  if (course.status === "PUBLISHED") {
    const coverage = learningState?.lesson_coverage;
    if (learningState?.active_activity) {
      const kind = activityKind(learningState.active_activity)?.toLowerCase() ?? "activity";
      return { message: `Saved ${kind} in progress.`, chip: "Published", chipClass: "nl-dash-chip-mint" };
    }
    return {
      message: coverage ? `${coverage.lessons_covered} of ${coverage.lessons_total} lessons covered.` : "Learning progress is unavailable.",
      chip: "Published",
      chipClass: "nl-dash-chip-mint",
    };
  }
  if (course.status === "REVIEW_READY") return { message: `${course.source_count} source${course.source_count === 1 ? "" : "s"} saved. Review the course outline before publishing.`, chip: "Outline ready", chipClass: "nl-dash-chip-yellow" };
  if (course.status === "PROCESSING") {
    const stage = course.latest_job?.current_stage?.replaceAll("_", " ");
    return { message: stage ? `Preparing your course · ${stage}.` : "Your course sources are being processed.", chip: "Processing", chipClass: "nl-dash-chip-violet", loading: true };
  }
  if (course.status === "NEEDS_INPUT") return { message: "A source needs attention before preparation can continue.", chip: "Needs a source fix", chipClass: "nl-dash-chip-coral" };
  if (course.status === "FAILED") return { message: "Course preparation needs attention.", chip: "Needs attention", chipClass: "nl-dash-chip-coral" };
  return { message: `${course.source_count} source${course.source_count === 1 ? "" : "s"} saved. Course setup is unfinished.`, chip: "Setup unfinished", chipClass: "nl-dash-chip-yellow" };
}

export default function DashboardPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const [courses, setCourses] = useState<CourseCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [continuing, setContinuing] = useState(false);
  const [continueError, setContinueError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/v1/courses", { cache: "no-store" });
      if (!response.ok) throw new Error(response.status === 401 ? "Please sign in again to view your courses." : "Your courses could not be loaded.");
      const items: Course[] = await response.json();
      const cards = await Promise.all(items.map(async (course): Promise<CourseCard> => {
        if (course.status !== "PUBLISHED") return { course, learningState: null };
        try {
          const stateResponse = await fetch(`/api/v1/courses/${course.id}/learning-state`, { cache: "no-store" });
          return { course, learningState: stateResponse.ok ? await stateResponse.json() : null };
        } catch {
          return { course, learningState: null };
        }
      }));

      const featured = cards.find((card) => card.learningState?.active_activity) ?? cards.find((card) => card.course.status === "PUBLISHED");
      if (featured) {
        try {
          const [structureResponse, graphResponse] = await Promise.all([
            fetch(`/api/v1/courses/${featured.course.id}/structure`, { cache: "no-store" }),
            fetch(`/api/v1/courses/${featured.course.id}/graph`, { cache: "no-store" }),
          ]);
          if (structureResponse.ok && graphResponse.ok) {
            const [structure, graph]: [Structure, Graph] = await Promise.all([structureResponse.json(), graphResponse.json()]);
            featured.structure = structure;
            featured.graph = graph;
          }
        } catch {
          // The course list remains usable if the selected course outline is unavailable.
        }
      }
      setCourses(cards);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Your courses could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (status === "unauthenticated") router.replace("/signin");
    if (status === "authenticated") void load();
  }, [status, router, load]);

  const featured = useMemo(
    () => courses.find((card) => card.learningState?.active_activity) ?? courses.find((card) => card.course.status === "PUBLISHED") ?? null,
    [courses],
  );
  const otherCourses = useMemo(() => courses.filter((card) => card.course.id !== featured?.course.id), [courses, featured]);

  const continueStudying = async () => {
    if (!featured || continuing) return;
    setContinuing(true);
    setContinueError("");
    try {
      if (!featured.structure) {
        router.push(`/courses/${featured.course.id}/learn`);
        return;
      }
      const response = await fetch(`/api/v1/courses/${featured.course.id}/activities/next`, { method: "POST", cache: "no-store" });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || "The selected course activity could not be opened. Try again.");
      const activity = payload as Activity;
      const href = selectedActivityHref(featured.course.id, activity, featured.structure);
      if (!href) throw new Error(activity.unavailable_reason || "The selected activity is saved, but its learning screen is unavailable.");
      router.push(href);
    } catch (cause) {
      setContinueError(cause instanceof Error ? cause.message : "The selected course activity could not be opened. Try again.");
    } finally {
      setContinuing(false);
    }
  };

  const userName = session?.user?.name?.trim() || session?.user?.email?.split("@")[0] || "Learner";
  if (status === "loading" || !session) return <main className="nl-screen grid place-items-center p-6"><p role="status">Loading your account…</p></main>;

  return <div className="nl-dashboard-shell">
    <CourseSidebar name={userName} />
    <main className="nl-dashboard-main">
      <header className="nl-dashboard-head"><h1>Your courses</h1><Link href="/courses/new" className="nl-dashboard-button nl-dashboard-button-sm"><Plus aria-hidden="true" />Create course</Link></header>

      {loading ? <div role="status" aria-label="Loading courses"><div className="nl-dashboard-skeleton" /><div className="nl-dashboard-skeleton nl-dashboard-skeleton-tall" /></div>
        : error ? <section className="nl-dashboard-box nl-dashboard-box-error" role="alert"><h2>Courses did not load</h2><p>Your courses and progress are safe. Check your connection and try again.</p><button type="button" className="nl-dashboard-button nl-dashboard-button-primary" onClick={() => void load()}>Try again</button></section>
          : courses.length === 0 ? <section className="nl-dashboard-box nl-dashboard-box-empty"><h2>Create your first course</h2><p>Upload your notes, slides, or PDFs and say what you want to learn. The course is built from your files only.</p><Link href="/courses/new" className="nl-dashboard-button nl-dashboard-button-primary nl-dashboard-button-large">Create course</Link></section>
            : <>
              {featured && <article className="nl-dashboard-featured">
                <div className="nl-dashboard-featured-copy">
                  <span className="nl-dashboard-label">Continue studying</span>
                  <h2>{featured.course.title}</h2>
                  {featured.learningState?.active_activity ? <>
                    <div className="nl-dashboard-next">{activityKind(featured.learningState.active_activity)}: {activityName(featured) || "Selected activity"}</div>
                    {featured.learningState.active_activity.reason && <p className="nl-dashboard-why">{featured.learningState.active_activity.reason}</p>}
                  </> : <>
                    <div className="nl-dashboard-next">Your course is ready for the next activity</div>
                    <p className="nl-dashboard-why">Continue to resume saved work or select the next activity for this course.</p>
                  </>}
                  {featured.learningState ? <div className="nl-dashboard-progress"><span>{featured.learningState.lesson_coverage.lessons_covered} of {featured.learningState.lesson_coverage.lessons_total} lessons covered</span><div className="nl-dashboard-bar" role="img" aria-label={`${featured.learningState.lesson_coverage.lessons_covered} of ${featured.learningState.lesson_coverage.lessons_total} lessons covered`}><i style={{ width: `${featured.learningState.lesson_coverage.lessons_total ? featured.learningState.lesson_coverage.lessons_covered / featured.learningState.lesson_coverage.lessons_total * 100 : 0}%` }} /></div></div> : <p className="nl-dashboard-why">Saved lesson coverage is unavailable right now.</p>}
                </div>
                <button type="button" className="nl-dashboard-button nl-dashboard-button-primary nl-dashboard-button-large nl-dashboard-continue" onClick={() => void continueStudying()} disabled={continuing}>
                  {continuing ? "Opening…" : "Continue"}<ArrowRight aria-hidden="true" />
                </button>
              </article>}

              {continueError && <p className="nl-dashboard-inline-error" role="alert">{continueError}</p>}

              {otherCourses.length > 0 && <section className="nl-dashboard-list" aria-label="Other courses"><h2>Other courses</h2><div className="nl-dashboard-rows">{otherCourses.map((card) => {
                const description = describeCourse(card);
                const action = actionFor(card.course);
                return <article className="nl-dashboard-row" key={card.course.id}>
                  <div className="nl-dashboard-row-copy"><h3>{card.course.title}</h3><p>{description.message}</p></div>
                  <span className={`nl-dashboard-chip ${description.chipClass}`}>{description.loading && <i className="nl-dashboard-dot" aria-hidden="true" />}{description.chip}</span>
                  <Link href={action.href} prefetch={false} className="nl-dashboard-button nl-dashboard-button-sm">{action.label}</Link>
                </article>;
              })}</div></section>}

            </>}
    </main>
  </div>;
}
