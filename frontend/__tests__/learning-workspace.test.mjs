import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

const read = (path) => readFile(new URL(path, import.meta.url), "utf8");
const [dashboard, sidebar, newCourse, uploadHelper, landing, overview, overviewPage, intro, diagnosticPage, study, activity, assessment, sidePanel, tutor, reviewer, courseWorkspace] = await Promise.all([
  read("../app/(pages)/dashboard/page.tsx"),
  read("../components/CourseSidebar.tsx"),
  read("../app/(pages)/courses/new/page.tsx"),
  read("../lib/upload-course-document.ts"),
  read("../app/page.tsx"),
  read("../components/CourseOverview.tsx"),
  read("../app/(pages)/courses/[courseId]/learn/page.tsx"),
  read("../components/DiagnosticIntro.tsx"),
  read("../app/(pages)/courses/[courseId]/diagnostic/page.tsx"),
  read("../app/(pages)/courses/[courseId]/study/[lessonId]/page.tsx"),
  read("../app/(pages)/courses/[courseId]/activities/[activityId]/page.tsx"),
  read("../app/(pages)/courses/[courseId]/assessment/page.tsx"),
  read("../components/LearningSidePanel.tsx"),
  read("../app/(pages)/courses/[courseId]/tutor/page.tsx"),
  read("../app/(pages)/grading-reviews/page.tsx"),
  read("../app/(pages)/courses/[courseId]/workspace/page.tsx"),
]);

test("dashboard follows the sidebar reference and only selects work after Continue", () => {
  const loadFlow = dashboard.match(/const load = useCallback\(async \(\) => \{[\s\S]*?setCourses\(cards\);/)?.[0] || "";
  assert.match(sidebar, /nl-dashboard-sidebar/);
  assert.match(dashboard, /nl-dashboard-featured/);
  assert.match(dashboard, /nl-dashboard-rows/);
  assert.match(dashboard, /learning-state/);
  assert.match(dashboard, /activities\/next/);
  assert.match(dashboard, /selectedActivityHref\(/);
  assert.match(dashboard, /onClick=\{\(\) => void continueStudying\(\)\}/);
  assert.doesNotMatch(loadFlow, /activities\/next/);
  assert.doesNotMatch(dashboard, /Distributed Systems|Raft leader election|data-s=/);
});

test("landing follows its reference sections while keeping sign-in real", () => {
  for (const section of ["How it works", "Why it is different", "Progress", "FAQ"]) assert.match(landing, new RegExp(section));
  assert.match(landing, /Study from your own material, not someone else/);
  assert.match(landing, /href=\"\/signin\"/);
  assert.doesNotMatch(landing, /data-s=|setTimeout\(/);
});

test("course setup uses the sidebar form, real source upload, and preserves recovery", () => {
  assert.match(newCourse, /CourseSidebar/);
  assert.match(newCourse, /nl-course-setup-review/);
  assert.match(newCourse, /CourseCreate/);
  assert.match(newCourse, /uploadCourseDocument\(course\.id, source\.file\)/);
  assert.match(newCourse, /createdCourseId.*workspace\$\{hasUploadedSources \? "\?startProcessing=1"/s);
  assert.match(uploadHelper, /documents\/upload-intents/);
  assert.match(uploadHelper, /documents\/finalize/);
  assert.match(uploadHelper, /storage-not-configured/);
  assert.doesNotMatch(newCourse, /Build on an earlier course|Subject/);
});

test("uploaded courses open the saved processing and outline workspace", () => {
  assert.match(courseWorkspace, /new URLSearchParams\(window\.location\.search\).*startProcessing/s);
  assert.match(courseWorkspace, /courses\/\$\{courseId\}\/jobs\/latest/);
  assert.match(courseWorkspace, /currentDocuments\.length > 0/);
  assert.match(courseWorkspace, /autoStartAttemptedRef\.current/);
  assert.match(courseWorkspace, /courses\/\$\{courseId\}\/process/);
  assert.match(courseWorkspace, /job\?\.stages/);
  assert.match(courseWorkspace, /stage\.status === "SUCCEEDED"/);
  assert.match(courseWorkspace, /stage\.status === "FAILED"/);
  assert.match(courseWorkspace, /processingStageCopy/);
  assert.match(courseWorkspace, /structure\?\.modules\?\.map/);
  assert.match(courseWorkspace, /lesson\.objective/);
  assert.match(courseWorkspace, /lesson\.concepts/);
  assert.match(courseWorkspace, /publish-structure/);
  assert.match(courseWorkspace, /nl-processing-shell/);
  assert.doesNotMatch(courseWorkspace, /setTimeout\(\(\) => \{[^}]*setActiveTab|dangerouslySetInnerHTML/);
});

test("overview selection is explicit and dashboard links leave the learning flow", () => {
  assert.doesNotMatch(overviewPage, /activities\/next/);
  assert.match(overview, /method: "POST"/);
  assert.match(overview, /onClick=\{\(\) => void continueStudying\(\)\}/);
  assert.match(overview, /href="\/dashboard"/);
  assert.match(overview, /aria-label="Current activity"/);
});

test("diagnostic interruption resumes its fixed session and skip stays read-only", () => {
  assert.match(diagnosticPage, /\$\{courseUrl\}\/learning-state/);
  assert.match(intro, /activeIsDiagnostic/);
  assert.match(intro, /method: "POST"/);
  assert.match(intro, /session\.id/);
  assert.match(intro, /Skip for now/);
  assert.match(intro, /Resume the diagnostic/);
  assert.match(intro, /The set/);
  assert.match(intro, /How it works/);
  assert.match(intro, /If you skip/);
  assert.match(assessment, /assessment-sessions\/\$\{sessionId\}/);
  assert.match(assessment, /findIndex\(\(question\) => !question\.answer\)/);
});

test("lessons restore and save reading position and selected format before assessment", () => {
  assert.match(study, /setSavedReadingPosition\(\{ identity: workspaceIdentity, position: savedActivity\.reading_position \}\)/);
  assert.match(study, /reading_position: position, presentation_format: selectedFormat/);
  assert.match(study, /method: "PATCH"/);
  assert.match(study, /reading-complete/);
  assert.match(study, /presentation-affinity\/switch/);
  assert.match(study, /canCommitWorkspaceResponse/);
  assert.match(study, /visibleWorkspaceContent/);
  assert.match(study, /StudyPositionRestorer/);
});

test("only remediation uses the reading gate; practice and challenge start their fixed sets directly", () => {
  assert.match(activity, /const isQuestionFirst = activity\?\.activity_type === "TARGETED_PRACTICE" \|\| activity\?\.activity_type === "CHALLENGE"/);
  assert.match(activity, /if \(isRemediation && !activity\.reading_completed_at\)/);
  assert.match(activity, /reading_position: position, presentation_format: selectedFormat/);
  assert.match(activity, /savedActivity\.assessment_session_id/);
  assert.match(activity, /reading-complete/);
  assert.match(activity, /Retry preparation/);
  assert.match(activity, /saved explanation is still available/i);
});

test("remediation identifies the selected lesson format", () => {
  assert.match(activity, /aria-pressed=\{format === item\}/);
  assert.match(activity, /format === item \? "bg-\[#FFD23F\] text-black"/);
});

test("format and source/tutor panels keep saved course activity context", () => {
  assert.match(sidePanel, /sessionStorage\.getItem\(key\)/);
  assert.match(sidePanel, /context_lesson_id: contextLessonId/);
  assert.match(sidePanel, /decision_id: decisionId/);
  assert.match(sidePanel, /conversation_id: conversationId/);
  assert.match(sidePanel, /courses\/\$\{courseId\}\/chunks\/\$\{selectedChunkId\}/);
  assert.match(sidePanel, /chunk\.page_start/);
  assert.match(sidePanel, /chunk\.heading_path/);
  assert.doesNotMatch(sidePanel, /dangerouslySetInnerHTML/);
  for (const page of [study, activity, assessment]) {
    assert.match(page, /setSourceOpenRequestId\(\(requestId\) => requestId \+ 1\)/);
    assert.match(page, /sourceOpenRequestId=\{sourceOpenRequestId\}/);
  }
  assert.match(study, /onOpenSource=\{openSource\}/);
  assert.match(activity, /onOpenSource=\{openSource\}/);
  assert.match(assessment, /onClick=\{\(\) => openSource\(chunkId\)\}/);
  assert.match(study, /conversationStorageKey=\{activityId \? `course:\$\{courseId\}:activity:\$\{activityId\}`/);
});

test("assessment answers lock only after the fixed-session save succeeds", () => {
  const lockHandler = assessment.match(/const lockConfirmedAnswer = async \(\) => \{[\s\S]*?\n  \};/)?.[0] || "";
  assert.match(lockHandler, /assessment-sessions\/\$\{session\.id\}\/questions/);
  assert.match(lockHandler, /if \(!response\.ok\) throw/);
  assert.match(lockHandler, /setSession\(data\)/);
  assert.match(lockHandler, /setConfirming\(false\)/);
  assert.match(assessment, /Answer saved and locked/);
  assert.match(assessment, /feedback appears after the full set is submitted/i);
});

test("submitted results distinguish pending, retryable, exhausted, reports, and corrected judgments", () => {
  assert.match(assessment, /RETRIES_EXHAUSTED/);
  assert.match(assessment, /ALLOWANCE_UNAVAILABLE/);
  assert.match(assessment, /grading_state === "AWAITING_GRADING"/);
  assert.match(assessment, /if \(updated\.grading_state === "AWAITING_GRADING"\)/);
  assert.match(assessment, /retry-grading/);
  assert.match(assessment, /grading-issue/);
  assert.match(assessment, /grade_corrected/);
  assert.match(assessment, /original_rubric_score/);
  assert.match(assessment, /Rubric feedback/);
  assert.match(assessment, /Binary evidence/);
  assert.match(assessment, /Lesson coverage/);
});

test("assessment results retain lesson and decision context for tutor clarification", () => {
  assert.match(assessment, /contextLessonId=\{session\.lesson_id\}/);
  assert.match(assessment, /decisionId=\{session\.decision_id\}/);
  assert.match(assessment, /conversationStorageKey=\{`results:\$\{session\.id\}`\}/);
  assert.match(assessment, /assessmentSubmitted/);
  assert.match(tutor, /LearningSidePanel/);
  assert.match(tutor, /submission_state === "SUBMITTED"/);
});

test("results Continue is the only next-activity mutation and dashboard exit is available", () => {
  assert.match(assessment, /activities\/next/);
  assert.match(assessment, /onClick=\{\(\) => void continueAfterResults\(\)\}/);
  assert.match(assessment, /href="\/dashboard"/);
  assert.match(reviewer, /api\/v1\/grading-reviews/);
  assert.match(reviewer, /api\/v1\/grading-reviews\/\$\{selected\.id\}\/\$\{action\}/);
});

test("reviewer actions stay hidden until the P5 allowlist grants access", () => {
  assert.match(reviewer, /response\.status === 404/);
  assert.match(reviewer, /setAuthorization\("restricted"\)/);
  assert.match(reviewer, /You do not have reviewer access/);
  assert.match(reviewer, /authorization === "allowed" && \(/);
});
