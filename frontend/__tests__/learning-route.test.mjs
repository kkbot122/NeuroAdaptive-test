import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { loadTs } from "./load-ts.mjs";
const { studyHrefForRecommendation, selectedActivityHref, describeQuestionTypes, preparationFailureMessage } = await loadTs("../lib/learning-route.ts");
const activityPage = await readFile(
  new URL("../app/(pages)/courses/[courseId]/activities/[activityId]/page.tsx", import.meta.url),
  "utf8",
);

const courseId = "course-1";
const structure = {
  modules: [
    {
      lessons: [
        { id: "lesson-1", concepts: [{ concept_id: "concept-1" }] },
        { id: "lesson-2", concepts: [{ concept_id: "concept-2" }] },
      ],
    },
  ],
};

test("a recommended lesson opens its study page", () => {
  assert.equal(
    studyHrefForRecommendation(courseId, { lesson_id: "lesson-2", concept_ids: ["concept-2"] }, structure),
    "/courses/course-1/study/lesson-2",
  );
});

test("a concept recommendation opens the lesson containing that concept", () => {
  assert.equal(
    studyHrefForRecommendation(courseId, { lesson_id: null, concept_ids: ["concept-2"] }, structure),
    "/courses/course-1/study/lesson-2",
  );
});

test("an unmapped recommendation does not silently choose a different lesson", () => {
  assert.equal(
    studyHrefForRecommendation(courseId, { lesson_id: null, concept_ids: ["missing"] }, structure),
    null,
  );
});

test("remediation recovery remains visible when saved teaching content exists", () => {
  assert.match(activityPage, /const displayedContent = isRemediation \? visibleWorkspaceContent\(contentState, identity, format\)/);
  assert.match(activityPage, /\{hasFailure && <section className="border-2 border-amber-800/);
  assert.match(activityPage, /isRemediation && displayedContent && hasFailure/);
});

const savedActivity = (overrides = {}) => ({
  activity_type: "NEW_LESSON",
  assessment_session_id: null,
  course_version_id: "version-1",
  decision_id: "decision-1",
  experience_availability: "SUPPORTED",
  id: "activity-1",
  lesson_id: "lesson-2",
  presentation_format: "analogy",
  question_count: 5,
  reading_completed_at: null,
  reading_position: 0,
  reason: "Saved selection reason",
  status: "READY",
  target_concept_ids: ["concept-2"],
  ...overrides,
});

test("Continue routes the saved activity identity and selected format", () => {
  assert.equal(
    selectedActivityHref(courseId, savedActivity(), structure),
    "/courses/course-1/study/lesson-2?activityId=activity-1&format=analogy",
  );
});

test("Continue resumes a fixed assessment session before opening another screen", () => {
  assert.equal(
    selectedActivityHref(courseId, savedActivity({ assessment_session_id: "session-1" }), structure),
    "/courses/course-1/assessment?type=activity&sessionId=session-1",
  );
});

test("remediation, practice, and challenge keep their selected activity route", () => {
  for (const activity_type of ["PREREQUISITE_REMEDIATION", "TARGETED_PRACTICE", "CHALLENGE"]) {
    assert.equal(
      selectedActivityHref(courseId, savedActivity({ activity_type }), structure),
      "/courses/course-1/activities/activity-1",
    );
  }
});

test("an unsupported selection is reported instead of silently choosing another lesson", () => {
  assert.equal(selectedActivityHref(courseId, savedActivity({ experience_availability: "UNAVAILABLE" }), structure), null);
  assert.equal(selectedActivityHref(courseId, savedActivity({ lesson_id: null, target_concept_ids: ["missing"] }), structure), null);
});

test("assessment descriptions reflect saved MCQ-only and mixed question sets", () => {
  assert.equal(describeQuestionTypes([{ question_type: "MCQ" }, { question_type: "MCQ" }]), "2 multiple choice");
  assert.equal(describeQuestionTypes([{ question_type: "MCQ" }, { question_type: "SHORT_TEXT" }]), "1 multiple choice · 1 short answer");
});

test("lesson preparation reports source validation and provider failures accurately", () => {
  assert.match(preparationFailureMessage("INSUFFICIENT_SOURCE_SUPPORT"), /did not pass its course source checks/i);
  assert.match(preparationFailureMessage("CONTENT_SCHEMA_OR_GROUNDING_FAILED"), /no content from this attempt was saved/i);
  assert.match(preparationFailureMessage("AI_ALLOWANCE_EXHAUSTED"), /allowance is currently unavailable/i);
  assert.match(preparationFailureMessage(null), /Preparation paused/);
});

test("overview browsing does not select an activity; the explicit button owns selection", async () => {
  const overview = await readFile(new URL("../app/(pages)/courses/[courseId]/learn/page.tsx", import.meta.url), "utf8");
  const overviewClient = await readFile(new URL("../components/CourseOverview.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(overview, /activities\/next/);
  assert.match(overviewClient, /onClick=\{\(\) => void continueStudying\(\)\}/);
  assert.match(overviewClient, /method: "POST"/);
});

test("assessment answers require a second confirmation and are written through the fixed-session route", async () => {
  const assessment = await readFile(new URL("../app/(pages)/courses/[courseId]/assessment/page.tsx", import.meta.url), "utf8");
  assert.match(assessment, /setConfirming\(true\)/);
  assert.match(assessment, /onClick=\{\(\) => void lockConfirmedAnswer\(\)\}/);
  assert.match(assessment, /assessment-sessions\/\$\{session\.id\}\/questions/);
});
