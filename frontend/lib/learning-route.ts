import type { components } from "@/lib/generated/api";

type LearningActivity = components["schemas"]["LearningActivityOut"];
type CourseStructure = components["schemas"]["StructureOut"];

export function describeQuestionTypes(questions: readonly Pick<components["schemas"]["AssessmentQuestionOut"], "question_type">[]): string {
  const counts = new Map<string, number>();
  for (const question of questions) {
    const label = question.question_type === "MCQ" ? "multiple choice" : "short answer";
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  return [...counts].map(([label, count]) => `${count} ${label}`).join(" · ");
}

export function preparationFailureMessage(category?: string | null): string {
  if (["DIAGRAM_SOURCE_SUPPORT_FAILED", "INVALID_DIAGRAM_CONNECTION"].includes(category || "")) {
    return "A diagram with source-supported connections could not be prepared. Choose another format or retry; your saved activity and progress are safe.";
  }
  if ([
    "INSUFFICIENT_SOURCE_SUPPORT",
    "INSUFFICIENT_SOURCE_PROVENANCE",
    "CONTENT_SCHEMA_OR_GROUNDING_FAILED",
    "CONTENT_CONCEPT_COVERAGE_FAILED",
    "CONTENT_CONCEPT_MISMATCH",
  ].includes(category || "")) {
    return "The generated lesson did not pass its course source checks, so no content from this attempt was saved. Retry preparation to try a fresh draft.";
  }
  if (category === "AI_ALLOWANCE_EXHAUSTED") {
    return "The AI preparation allowance is currently unavailable. Your course and sources are saved; retry when the allowance is available.";
  }
  if (["PROVIDER_UNAVAILABLE", "VALIDATION_UNAVAILABLE", "PROVIDER_OR_SCHEMA_FAILURE"].includes(category || "")) {
    return "The content service could not prepare a validated lesson. Your course and sources are saved; retry preparation when the service is available.";
  }
  return "Preparation paused. Your saved course and progress are safe. Retry when you are ready.";
}

/** Resolve an API selected activity to a lesson in the published structure. */
export function studyHrefForRecommendation(
  courseId: string,
  recommended: unknown,
  structure: unknown,
): string | null {
  if (!recommended || typeof recommended !== "object") return null;
  if (!structure || typeof structure !== "object") return null;

  const activity = recommended as Record<string, unknown>;
  const courseStructure = structure as Record<string, unknown>;
  if (!Array.isArray(courseStructure.modules)) return null;

  const conceptIds = Array.isArray(activity.concept_ids)
    ? activity.concept_ids.filter((id): id is string => typeof id === "string")
    : [];

  for (const courseModule of courseStructure.modules) {
    if (!courseModule || typeof courseModule !== "object") continue;
    const lessons = (courseModule as Record<string, unknown>).lessons;
    if (!Array.isArray(lessons)) continue;

    for (const lesson of lessons) {
      if (!lesson || typeof lesson !== "object") continue;
      const row = lesson as Record<string, unknown>;
      if (typeof row.id !== "string") continue;
      if (row.id === activity.lesson_id) {
        return `/courses/${courseId}/study/${row.id}`;
      }
    }
  }

  for (const courseModule of courseStructure.modules) {
    if (!courseModule || typeof courseModule !== "object") continue;
    const lessons = (courseModule as Record<string, unknown>).lessons;
    if (!Array.isArray(lessons)) continue;

    for (const lesson of lessons) {
      if (!lesson || typeof lesson !== "object") continue;
      const row = lesson as Record<string, unknown>;
      if (typeof row.id !== "string" || !Array.isArray(row.concepts)) continue;
      if (row.concepts.some((concept: unknown) =>
        concept && typeof concept === "object" &&
        typeof (concept as Record<string, unknown>).concept_id === "string" &&
        conceptIds.includes((concept as Record<string, unknown>).concept_id as string)
      )) {
        return `/courses/${courseId}/study/${row.id}`;
      }
    }
  }

  return null;
}

/** Resolve only a server-selected activity; callers must not substitute an alternative. */
export function selectedActivityHref(
  courseId: string,
  activity: LearningActivity,
  structure: CourseStructure,
): string | null {
  if (activity.experience_availability === "UNAVAILABLE") return null;
  if (activity.assessment_session_id) {
    return `/courses/${courseId}/assessment?type=activity&sessionId=${activity.assessment_session_id}`;
  }
  if (["PREREQUISITE_REMEDIATION", "TARGETED_PRACTICE", "CHALLENGE"].includes(activity.activity_type)) {
    return `/courses/${courseId}/activities/${activity.id}`;
  }
  if (activity.activity_type === "DIAGNOSTIC") {
    return `/courses/${courseId}/assessment?type=diagnostic&activityId=${activity.id}`;
  }
  const studyHref = studyHrefForRecommendation(courseId, {
    lesson_id: activity.lesson_id,
    concept_ids: activity.target_concept_ids,
  }, structure);
  if (!studyHref) return null;
  const query = new URLSearchParams({ activityId: activity.id, format: activity.presentation_format });
  return `${studyHref}?${query.toString()}`;
}
