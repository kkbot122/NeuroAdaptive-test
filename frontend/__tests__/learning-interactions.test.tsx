import assert from "node:assert/strict";
import test, { afterEach, before } from "node:test";
import { JSDOM } from "jsdom";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, PathParamsContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { SessionContext } from "next-auth/react";
import type { components } from "../lib/generated/api";
import { studyWorkspaceIdentity, updateWorkspaceState } from "../lib/study-workspace-state.mjs";

const originalFetch = globalThis.fetch;
let cleanup: () => void;
let fireEvent: typeof import("@testing-library/react").fireEvent;
let render: typeof import("@testing-library/react").render;
let screen: typeof import("@testing-library/react").screen;
let waitFor: typeof import("@testing-library/react").waitFor;
let LearningSidePanel: typeof import("../components/LearningSidePanel").LearningSidePanel;
let PreparedLessonContent: typeof import("../components/PreparedLessonContent").PreparedLessonContent;
let GradingRetryButton: typeof import("../components/GradingRetryButton").GradingRetryButton;
let StudyPositionRestorer: typeof import("../components/StudyPositionRestorer").StudyPositionRestorer;
let DiagnosticIntro: typeof import("../components/DiagnosticIntro").DiagnosticIntro;
let EmptyLessonContentState: typeof import("../components/EmptyLessonContentState").EmptyLessonContentState;
let AdaptiveActivityPage: typeof import("../app/(pages)/courses/[courseId]/activities/[activityId]/page").default;
let StudyLessonPage: typeof import("../app/(pages)/courses/[courseId]/study/[lessonId]/page").default;
let WorkspacePage: typeof import("../app/(pages)/courses/[courseId]/workspace/page").default;
let uploadCourseDocument: typeof import("../lib/upload-course-document").uploadCourseDocument;

before(async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost" });
  Object.defineProperties(globalThis, {
    window: { configurable: true, value: dom.window },
    self: { configurable: true, value: dom.window },
    document: { configurable: true, value: dom.window.document },
    navigator: { configurable: true, value: dom.window.navigator },
    HTMLElement: { configurable: true, value: dom.window.HTMLElement },
    IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: true, writable: true },
  });
  Object.defineProperty(dom.window.crypto, "randomUUID", { configurable: true, value: () => "test-conversation-id" });
  ({ cleanup, fireEvent, render, screen, waitFor } = await import("@testing-library/react"));
  ({ LearningSidePanel } = await import("../components/LearningSidePanel"));
  ({ PreparedLessonContent } = await import("../components/PreparedLessonContent"));
  ({ GradingRetryButton } = await import("../components/GradingRetryButton"));
  ({ StudyPositionRestorer } = await import("../components/StudyPositionRestorer"));
  ({ DiagnosticIntro } = await import("../components/DiagnosticIntro"));
  ({ EmptyLessonContentState } = await import("../components/EmptyLessonContentState"));
  ({ default: AdaptiveActivityPage } = await import("../app/(pages)/courses/[courseId]/activities/[activityId]/page"));
  ({ default: StudyLessonPage } = await import("../app/(pages)/courses/[courseId]/study/[lessonId]/page"));
  ({ default: WorkspacePage } = await import("../app/(pages)/courses/[courseId]/workspace/page"));
  ({ uploadCourseDocument } = await import("../lib/upload-course-document"));
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
  window.sessionStorage.clear();
  window.localStorage.clear();
});

function presentationContent(format: components["schemas"]["PreparedLessonContentOut"]["presentation_format"]): components["schemas"]["PreparedLessonContentOut"] {
  const statement = (text: string) => ({ text, concept_ids: ["concept-1"], citation_chunk_ids: ["chunk-1"] });
  return { artifact_id: "format-artifact", course_version_id: "version-1", presentation_format: format,
    source_chunk_ids: ["chunk-1", "chunk-1"], sections: {
      objective: [statement("Learn to explain Retry delays.")],
      explanation: [statement("Jitter spreads retries over time."), statement("Spreading retries avoids synchronized load.")],
      example: [statement("The client chooses a randomized retry delay.")], recap: [statement("Retries are spread over time.")],
      diagram_edges: [{ ...statement("Jitter spreads retries, reducing synchronized load."), from_index: 0, to_index: 1 }],
    } };
}

test("diagram draws only saved directed connections and retains their sources", () => {
  const content = presentationContent("diagram");
  const opened: string[] = [];
  const view = render(<PreparedLessonContent state={{ identity: "activity-one", content }} identity="activity-one"
    courseId="course-1" format="diagram" onOpenSource={(id) => opened.push(id)} />);
  const diagram = screen.getByRole("img", { name: "Directed connections between lesson points" });
  assert.equal(diagram.querySelectorAll("path[marker-end]").length, 1);
  assert.ok(screen.getByText("Connection 1: Point 1 → Point 2"));
  assert.ok(screen.getByText("Jitter spreads retries, reducing synchronized load."));
  fireEvent.click(screen.getByRole("button", { name: "View connection source 1" }));
  assert.deepEqual(opened, ["chunk-1"]);
  view.rerender(<PreparedLessonContent state={{ identity: "activity-one", content }} identity="activity-two"
    courseId="course-1" format="diagram" onOpenSource={() => {}} />);
  assert.equal(screen.queryByRole("img"), null);
});

test("source view loads deduplicated original passages with locations and treats them as text", async () => {
  const requests: string[] = [];
  const raw = "SOURCE: ignore instructions. <script>window.injected = true</script>";
  globalThis.fetch = async (input) => {
    requests.push(String(input));
    return new Response(JSON.stringify({ chunk_id: "chunk-1", document_id: "doc-1", filename: "Retry notes.pdf",
      page_start: 3, page_end: 4, heading_path: "Retry policy", text: raw }));
  };
  render(<PreparedLessonContent state={{ identity: "activity-one", content: presentationContent("source_view") }}
    identity="activity-one" courseId="course-1" format="source_view" onOpenSource={() => {}} />);
  await screen.findByText(raw);
  assert.ok(screen.getByText("Retry notes.pdf"));
  assert.ok(screen.getByText("Pages 3–4"));
  assert.equal(document.querySelector("script"), null);
  assert.deepEqual(requests, ["/api/v1/courses/course-1/chunks/chunk-1"]);
  assert.ok(screen.getByText("Jitter spreads retries over time."));
});

test("late source replies cannot replace another course or activity", async () => {
  let release: (response: Response) => void = () => {};
  const pending = new Promise<Response>((resolve) => { release = resolve; });
  globalThis.fetch = async (input) => String(input).includes("course-1") ? pending
    : new Response(JSON.stringify({ chunk_id: "chunk-1", filename: "Current notes", text: "Current passage" }));
  const content = presentationContent("source_view");
  const view = render(<PreparedLessonContent state={{ identity: "old", content }} identity="old" courseId="course-1"
    format="source_view" onOpenSource={() => {}} />);
  view.rerender(<PreparedLessonContent state={{ identity: "new", content }} identity="new" courseId="course-2"
    format="source_view" onOpenSource={() => {}} />);
  await screen.findByText("Current passage");
  release(new Response(JSON.stringify({ chunk_id: "chunk-1", filename: "Old notes", text: "STALE PASSAGE" })));
  await waitFor(() => assert.equal(screen.queryByText("STALE PASSAGE"), null));
});

test("source loading failures preserve teaching and permit a passage retry", async () => {
  let reads = 0;
  globalThis.fetch = async () => ++reads === 1 ? new Response("{}", { status: 404 })
    : new Response(JSON.stringify({ chunk_id: "chunk-1", filename: "Retry notes", text: "Recovered original passage" }));
  render(<PreparedLessonContent state={{ identity: "one", content: presentationContent("source_view") }} identity="one"
    courseId="course-1" format="source_view" onOpenSource={() => {}} />);
  await screen.findByRole("alert");
  assert.ok(screen.getByText("Jitter spreads retries over time."));
  fireEvent.click(screen.getByRole("button", { name: "Retry passage" }));
  await screen.findByText("Recovered original passage");
});

test("quiz-first hides explanations until an attempt and restores browser answers without assessment calls", async () => {
  const readiness: boolean[] = [];
  globalThis.fetch = async (input) => { throw new Error(`Warm-up must not request assessment or tutor: ${input}`); };
  const content = presentationContent("quiz_first");
  const props = { state: { identity: "one", content }, identity: "one", courseId: "course-1", format: "quiz_first" as const,
    onOpenSource() {}, onQuizReadyChange(ready: boolean) { readiness.push(ready); } };
  const first = render(<PreparedLessonContent {...props} />);
  const answer = await screen.findByRole("textbox", { name: "Your answer to warm-up question 1" });
  assert.equal(screen.queryByText("Jitter spreads retries over time."), null);
  assert.equal((screen.getByRole("button", { name: "Compare with course explanation" }) as HTMLButtonElement).disabled, true);
  fireEvent.change(answer, { target: { value: "Random delays spread retries." } });
  fireEvent.click(screen.getByRole("button", { name: "Compare with course explanation" }));
  await screen.findByText("Jitter spreads retries over time.");
  assert.equal(readiness.at(-1), true);
  first.unmount();
  render(<PreparedLessonContent {...props} />);
  await screen.findByText("Jitter spreads retries over time.");
  assert.equal((screen.getByRole("textbox", { name: "Your answer to warm-up question 1" }) as HTMLTextAreaElement).value, "Random delays spread retries.");
});

test("quiz-first permits uncertainty and resets on another activity or artifact", async () => {
  const content = presentationContent("quiz_first");
  const view = render(<PreparedLessonContent state={{ identity: "one", content }} identity="one"
    courseId="course-1" format="quiz_first" onOpenSource={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "I’m not sure — show explanation" }));
  await screen.findByText("Jitter spreads retries over time.");
  view.rerender(<PreparedLessonContent state={{ identity: "two", content }} identity="two"
    courseId="course-1" format="quiz_first" onOpenSource={() => {}} />);
  await screen.findByRole("button", { name: "I’m not sure — show explanation" });
  assert.equal(screen.queryByText("Jitter spreads retries over time."), null);
  view.rerender(<PreparedLessonContent state={{ identity: "two", content: { ...content, artifact_id: "fresh-artifact" } }} identity="two"
    courseId="course-1" format="quiz_first" onOpenSource={() => {}} />);
  assert.equal(screen.queryByText("Jitter spreads retries over time."), null);
});

for (const kind of ["lesson", "remediation"] as const) {
  test(`${kind} quiz-first completes its warm-up before the saved reading and assessment handoff`, async () => {
    const mutations: string[] = [];
    const navigations: string[] = [];
    const content = presentationContent("quiz_first");
    const activity: components["schemas"]["LearningActivityOut"] = { id: "activity-1", status: "READY", decision_id: null, reason: null,
      activity_type: kind === "lesson" ? "NEW_LESSON" : "PREREQUISITE_REMEDIATION",
      course_version_id: "version-1", lesson_id: "lesson-1", presentation_format: "quiz_first", target_concept_ids: ["concept-1"],
      reading_position: 0, reading_completed_at: null, question_count: 5, experience_availability: "SUPPORTED",
      preparation: { id: "prep-1", status: "READY", stage: "COMPLETE", content_ready: true, assessment_ready: true,
        progress: 100, updated_at: "2026-10-09T00:00:00Z" } };
    globalThis.fetch = async (input, init) => {
      const url = String(input);
      if (init?.method && init.method !== "GET") {
        mutations.push(`${init.method} ${url}`);
        if (url.endsWith("/reading-complete")) return new Response(JSON.stringify({ ...activity, assessment_session_id: "fixed-session" }));
        if (init.method === "PATCH") return new Response(JSON.stringify(activity));
        throw new Error(`Unexpected mutation: ${url}`);
      }
      if (url.includes("/tutor/history")) return new Response(JSON.stringify({ conversation_id: "test-conversation-id", available: true, turns: [], has_more: false }));
      if (url.includes("/content?")) return new Response(JSON.stringify({ status: "READY", preparation: activity.preparation, content }));
      if (url.endsWith("/activities/activity-1")) return new Response(JSON.stringify(activity));
      if (url.endsWith("/structure")) return new Response(JSON.stringify({ modules: [{ lessons: [{ id: "lesson-1", title: "Retries", concepts: [] }] }] }));
      if (url.endsWith("/graph")) return new Response(JSON.stringify({ concepts: [{ id: "concept-1", name: "Retry delays" }] }));
      if (url.endsWith("/courses/course-1")) return new Response(JSON.stringify({ id: "course-1", title: "Distributed systems" }));
      throw new Error(`Unexpected request: ${url}`);
    };
    const router = { back() {}, forward() {}, refresh() {}, prefetch() {}, replace() {}, push(href: string) { navigations.push(href); } };
    const originalScroll = window.scrollTo;
    window.scrollTo = () => {};
    try {
      render(<AppRouterContext.Provider value={router}>
        <PathParamsContext.Provider value={{ courseId: "course-1", activityId: "activity-1", lessonId: "lesson-1" }}>
          <SearchParamsContext.Provider value={new URLSearchParams("activityId=activity-1&format=quiz_first")}>
            {kind === "lesson" ? <StudyLessonPage /> : <AdaptiveActivityPage />}
          </SearchParamsContext.Provider>
        </PathParamsContext.Provider>
      </AppRouterContext.Provider>);
      const reveal = await screen.findByRole("button", { name: "I’m not sure — show explanation" });
      const continueButton = screen.getByRole("button", { name: /^Ready for questions/ }) as HTMLButtonElement;
      assert.equal(continueButton.disabled, true);
      assert.equal(mutations.length, 0);
      fireEvent.click(reveal);
      await waitFor(() => assert.equal(continueButton.disabled, false));
      assert.equal(mutations.length, 0);
      fireEvent.click(continueButton);
      await waitFor(() => assert.deepEqual(navigations, ["/courses/course-1/assessment?type=activity&sessionId=fixed-session"]));
      assert.equal(mutations.filter((request) => request.includes("reading-complete")).length, 1);
      assert.equal(mutations.filter((request) => request.includes("/assessment-sessions") || request.includes("/tutor")).length, 0);
    } finally { window.scrollTo = originalScroll; }
  });
}

function sidePanelProps(overrides = {}) {
  return {
    courseId: "course-1",
    contextLessonId: "lesson-1",
    conversationStorageKey: "course:course-1:activity:activity-1",
    ...overrides,
  };
}

test("the source replacement input stays mounted in processing and outline review", async () => {
  const course = {
    id: "course-1", title: "Distributed Systems", status: "PROCESSING", created_at: null, goal: null,
    source_count: 1, source_revision: 3, sources_finalized_at: "2026-10-08T12:00:00Z", starting_confidence: null,
  };
  const document = {
    id: "document-1", course_id: "course-1", filename: "consistency.txt", role: "STUDY", source_kind: "UPLOAD",
    status: "EXTRACTED", size_bytes: 120, page_count: null, needs_input_reason: null, created_at: null,
  };
  const job = {
    id: "job-1", course_id: "course-1", status: "PAUSED", current_stage: "EXTRACTING",
    error_category: "PROVIDER_UNAVAILABLE", error_detail: "Processing is paused.", retry_available: true, retry_count: 0, source_revision: 3, stages: [],
  };
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.endsWith("/courses/course-1")) return new Response(JSON.stringify(course));
    if (url.endsWith("/courses/course-1/documents")) return new Response(JSON.stringify([document]));
    if (url.endsWith("/courses/course-1/jobs/latest")) return new Response(JSON.stringify(job));
    if (url.endsWith("/jobs/job-1")) return new Response(JSON.stringify(job));
    if (url.endsWith("/courses/course-1/structure")) return new Response(JSON.stringify({ version_id: "version-1", modules: [] }));
    if (url.endsWith("/courses/course-1/graph")) return new Response(JSON.stringify({ concepts: [] }));
    throw new Error(`Unexpected request: ${url}`);
  };
  const router = { back() {}, forward() {}, refresh() {}, push() {}, replace() {}, prefetch() {} };
  const session = { user: { name: "Learner", email: "learner@example.test" }, expires: "2099-01-01T00:00:00.000Z" };
  const mountWorkspace = () => render(<AppRouterContext.Provider value={router}>
      <PathnameContext.Provider value="/courses/course-1/workspace">
        <PathParamsContext.Provider value={{ courseId: "course-1" }}>
          <SessionContext.Provider value={{ data: session, status: "authenticated", update: async () => session }}>
            <WorkspacePage />
          </SessionContext.Provider>
        </PathParamsContext.Provider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>);

  const view = mountWorkspace();
  const replace = await screen.findByRole("button", { name: "Replace consistency.txt" });
  const fileInput = view.container.querySelector<HTMLInputElement>('input[type="file"]');
  assert.ok(fileInput, "the hidden replacement input remains mounted outside the setup views");
  let inputClicks = 0;
  fileInput.click = () => { inputClicks += 1; };
  fireEvent.click(replace);
  assert.equal(inputClicks, 1);

  view.unmount();
  job.status = "READY";
  const outline = mountWorkspace();
  const outlineReplace = await screen.findByRole("button", { name: "Replace consistency.txt" });
  const outlineInput = outline.container.querySelector<HTMLInputElement>('input[type="file"]');
  assert.ok(outlineInput, "the same hidden input remains mounted during outline review");
  let outlineInputClicks = 0;
  outlineInput.click = () => { outlineInputClicks += 1; };
  fireEvent.click(outlineReplace);
  assert.equal(outlineInputClicks, 1);
});

test("source replacement keeps the selected document role through local upload fallback", async () => {
  const sentBodies: BodyInit[] = [];
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url.endsWith("/documents/upload-intents")) return new Response(JSON.stringify({
      type: "https://neurolearn.internal/problems/storage-not-configured",
    }), { status: 503 });
    if (url.endsWith("/documents")) {
      if (init?.body) sentBodies.push(init.body);
      return new Response(JSON.stringify({ source_changed: true, cleanup_pending: true }));
    }
    throw new Error(`Unexpected request: ${url}`);
  };

  await uploadCourseDocument("course-1", new File(["syllabus replacement text"], "plan.txt", { type: "text/plain" }), "syllabus-1", "SYLLABUS");

  assert.equal(sentBodies.length, 1);
  assert.ok(sentBodies[0] instanceof FormData);
  assert.equal((sentBodies[0] as FormData).get("role"), "SYLLABUS");
  assert.equal((sentBodies[0] as FormData).get("replaces_document_id"), "syllabus-1");
});

test("an open assessment locks the rendered tutor panel and blocks submit events", async () => {
  let tutorRequests = 0;
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url.includes("/tutor/history")) return new Response(JSON.stringify({ conversation_id: "test-conversation-id", available: false, turns: [], has_more: false }));
    if (url.endsWith("/learning-state")) {
      return new Response(JSON.stringify({ active_activity: { assessment_session_id: "session-1" } }));
    }
    if (url.endsWith("/assessment-sessions/session-1")) {
      return new Response(JSON.stringify({ submission_state: "OPEN" }));
    }
    if (url.endsWith("/tutor") && init?.method === "POST") {
      tutorRequests += 1;
      return new Response(JSON.stringify({ detail: "Tutor assistance is unavailable during an active assessment." }), { status: 409 });
    }
    throw new Error(`Unexpected request: ${url}`);
  };

  render(<LearningSidePanel {...sidePanelProps()} />);
  assert.match((await screen.findByText(/unavailable while you have an active assessment/i)).textContent || "", /active assessment/i);
  const input = screen.getByRole("textbox", { name: "Ask the tutor" });
  assert.equal((input as HTMLInputElement).disabled, true);
  const form = input.closest("form");
  assert.ok(form);
  fireEvent.submit(form!);
  await waitFor(() => assert.equal(tutorRequests, 0));
});

test("an activity switch removes the previous saved explanation and its source button", () => {
  const firstIdentity = studyWorkspaceIdentity("course-1", "activity-1");
  const secondIdentity = studyWorkspaceIdentity("course-1", "activity-2");
  const content = {
    artifact_id: "artifact-1",
    course_version_id: "version-1",
    presentation_format: "detailed",
    sections: {
      objective: [],
      explanation: [{ text: "Saved explanation for activity one.", concept_ids: [], citation_chunk_ids: ["chunk-1"] }],
      example: [],
      recap: [],
    },
    source_chunk_ids: ["chunk-1"],
  } satisfies components["schemas"]["PreparedLessonContentOut"];
  const savedState = updateWorkspaceState(null, firstIdentity, { content, loading: false });
  const openedSources: string[] = [];

  const view = render(<PreparedLessonContent state={savedState} identity={firstIdentity} format="detailed" onOpenSource={(id) => openedSources.push(id)} />);
  assert.ok(screen.getByText("Saved explanation for activity one."));
  fireEvent.click(screen.getByRole("button", { name: "Open supporting source passage 1 in the Sources tab" }));
  assert.deepEqual(openedSources, ["chunk-1"]);

  view.rerender(<PreparedLessonContent state={savedState} identity={secondIdentity} format="detailed" onOpenSource={(id) => openedSources.push(id)} />);
  assert.equal(screen.queryByText("Saved explanation for activity one."), null);
  assert.equal(screen.queryByRole("button", { name: "Open supporting source passage 1 in the Sources tab" }), null);

  view.rerender(<PreparedLessonContent state={savedState} identity={firstIdentity} format="concise" onOpenSource={(id) => openedSources.push(id)} />);
  assert.equal(screen.queryByText("Saved explanation for activity one."), null);
});

test("results keep tutor clarification available after assessment submission", async () => {
  let tutorRequests = 0;
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url.includes("/tutor/history")) return new Response(JSON.stringify({ conversation_id: "test-conversation-id", available: true, turns: [], has_more: false }));
    if (url.endsWith("/tutor") && init?.method === "POST") {
      tutorRequests += 1;
      return new Response(
        'event: retrieval\ndata: {"chunk_ids":[]}\n\nevent: token\ndata: {"text":"A source grounded clarification."}\n\nevent: done\ndata: {"message_id":"message-1","grounding_mode":"source_only"}\n\n',
      );
    }
    throw new Error(`Unexpected request: ${url}`);
  };

  render(<LearningSidePanel {...sidePanelProps({ assessmentSubmitted: true })} />);
  const input = await screen.findByRole("textbox", { name: "Ask the tutor" });
  await waitFor(() => assert.equal((input as HTMLInputElement).disabled, false));
  fireEvent.change(input, { target: { value: "Clarify my results" } });
  fireEvent.click(screen.getByRole("button", { name: "Send question" }));
  await screen.findByText("A source grounded clarification.");
  assert.equal(tutorRequests, 1);
});

test("saved tutor turns restore on remount and keep the conversation after session storage clears", async () => {
  const savedId = "saved-conversation-id";
  window.sessionStorage.setItem("neurolearn:tutor:course:course-1:activity:activity-1", savedId);
  const requested: string[] = [];
  globalThis.fetch = async (input) => {
    const url = String(input); requested.push(url);
    if (url.includes("/tutor/history")) return new Response(JSON.stringify({
      conversation_id: savedId, available: true, has_more: false,
      turns: [{ id: "turn-1", question: "What is jitter?", answer_markdown: "Jitter spreads retries over time.", grounding_mode: "source_only", citations: [] }],
    }));
    if (url.endsWith("/learning-state")) return new Response(JSON.stringify({ active_activity: null }));
    throw new Error(`Unexpected request: ${url}`);
  };
  const first = render(<LearningSidePanel {...sidePanelProps()} />);
  await screen.findByText("Jitter spreads retries over time.");
  first.unmount(); window.sessionStorage.clear();
  render(<LearningSidePanel {...sidePanelProps()} />);
  await screen.findByText("Jitter spreads retries over time.");
  assert.equal(requested.filter((url) => url.includes(`conversation_id=${savedId}`)).length, 2);
});

test("submitted results still obey an assessment lock elsewhere in the account", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ conversation_id: "test-conversation-id", available: false, turns: [], has_more: false }));
  render(<LearningSidePanel {...sidePanelProps({ assessmentSubmitted: true })} />);
  await screen.findByText(/unavailable while you have an active assessment/i);
  assert.equal((screen.getByRole("textbox", { name: "Ask the tutor" }) as HTMLInputElement).disabled, true);
});

test("selecting a cited passage opens a panel collapsed by the saved preference", async () => {
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.endsWith("/api/v1/me/settings")) return new Response(JSON.stringify({ tutor_panel_open: false }));
    if (url.includes("/tutor/history")) return new Response(JSON.stringify({
      conversation_id: "test-conversation-id", available: false, turns: [], has_more: false,
    }));
    if (url.endsWith("/chunks/chunk-1")) return new Response(JSON.stringify({
      id: "chunk-1", filename: "course-notes.txt", text: "The cited passage is visible.",
      page_start: null, page_end: null, heading_path: "Unit 1",
    }));
    throw new Error(`Unexpected request: ${url}`);
  };

  const view = render(<LearningSidePanel {...sidePanelProps()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Open tutor and sources" }));
  await screen.findByRole("tab", { name: /Tutor/ });

  view.rerender(<LearningSidePanel {...sidePanelProps({ initialSourceChunkId: "chunk-1" })} />);

  assert.equal((await screen.findByRole("tab", { name: /Sources/ })).getAttribute("aria-selected"), "true");
  await screen.findByText("The cited passage is visible.");
  assert.ok(screen.getByRole("heading", { name: "course-notes.txt" }));
});

test("a late closed-panel preference cannot hide a citation selected by the learner", async () => {
  let resolveSettings: (response: Response) => void = () => {};
  const settings = new Promise<Response>((resolve) => { resolveSettings = resolve; });
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.endsWith("/api/v1/me/settings")) return settings;
    if (url.includes("/tutor/history")) return new Response(JSON.stringify({
      conversation_id: "test-conversation-id", available: false, turns: [], has_more: false,
    }));
    if (url.endsWith("/chunks/chunk-1")) return new Response(JSON.stringify({
      id: "chunk-1", filename: "course-notes.txt", text: "The selected citation stays visible.",
      page_start: null, page_end: null, heading_path: "Unit 1",
    }));
    throw new Error(`Unexpected request: ${url}`);
  };

  const view = render(<LearningSidePanel {...sidePanelProps()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Close tutor and sources panel" }));
  assert.ok(await screen.findByRole("button", { name: "Open tutor and sources" }));
  view.rerender(<LearningSidePanel {...sidePanelProps({ initialSourceChunkId: "chunk-1" })} />);
  await screen.findByText("The selected citation stays visible.");
  resolveSettings(new Response(JSON.stringify({ tutor_panel_open: false })));
  await waitFor(() => assert.equal((screen.getByRole("tab", { name: /Sources/ })).getAttribute("aria-selected"), "true"));
  assert.ok(screen.getByText("The selected citation stays visible."));
});

test("closing the panel and reopening the same citation emits a fresh open request", async () => {
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.endsWith("/api/v1/me/settings")) return new Response(JSON.stringify({ tutor_panel_open: false }));
    if (url.includes("/tutor/history")) return new Response(JSON.stringify({
      conversation_id: "test-conversation-id", available: false, turns: [], has_more: false,
    }));
    if (url.endsWith("/chunks/chunk-1")) return new Response(JSON.stringify({
      id: "chunk-1", filename: "course-notes.txt", text: "The same citation opens again.",
      page_start: null, page_end: null, heading_path: "Unit 1",
    }));
    throw new Error(`Unexpected request: ${url}`);
  };

  const view = render(<LearningSidePanel {...sidePanelProps({ initialSourceChunkId: "chunk-1", sourceOpenRequestId: 1 })} />);
  await screen.findByText("The same citation opens again.");
  fireEvent.click(screen.getByRole("button", { name: "Close tutor and sources panel" }));
  assert.ok(await screen.findByRole("button", { name: "Open tutor and sources" }));

  view.rerender(<LearningSidePanel {...sidePanelProps({ initialSourceChunkId: "chunk-1", sourceOpenRequestId: 2 })} />);

  assert.equal((await screen.findByRole("tab", { name: /Sources/ })).getAttribute("aria-selected"), "true");
  assert.ok(screen.getByText("The same citation opens again."));
});

test("late history from a previous activity cannot replace the current conversation", async () => {
  let release: (response: Response) => void = () => {};
  const late = new Promise<Response>((resolve) => { release = resolve; });
  let historyReads = 0;
  globalThis.fetch = async (input) => {
    if (String(input).endsWith("/api/v1/me/settings")) return new Response(JSON.stringify({ tutor_panel_open: true }));
    historyReads += 1;
    if (historyReads === 1) return late;
    return new Response(JSON.stringify({ conversation_id: "test-conversation-id", available: true, has_more: false,
      turns: [{ id: "new-turn", question: "New activity question", answer_markdown: "Current activity answer", citations: [], grounding_mode: "source_only" }] }));
  };
  const view = render(<LearningSidePanel {...sidePanelProps()} />);
  await waitFor(() => assert.equal(historyReads, 1));
  view.rerender(<LearningSidePanel {...sidePanelProps({ conversationStorageKey: "activity-2" })} />);
  await screen.findByText("Current activity answer");
  release(new Response(JSON.stringify({ conversation_id: "test-conversation-id", available: true, has_more: false,
    turns: [{ id: "old-turn", question: "Old question", answer_markdown: "STALE ACTIVITY ANSWER", citations: [], grounding_mode: "source_only" }] })));
  await waitFor(() => assert.equal(screen.queryByText("STALE ACTIVITY ANSWER"), null));
});

test("older messages load ahead of the restored page without discarding recent replies", async () => {
  globalThis.fetch = async (input) => {
    const older = String(input).includes("before=new-turn");
    return new Response(JSON.stringify({ conversation_id: "test-conversation-id", available: true, has_more: !older,
      turns: [{ id: older ? "old-turn" : "new-turn", question: older ? "Earlier question" : "Recent question",
        answer_markdown: older ? "Earlier answer" : "Recent answer", citations: [], grounding_mode: "source_only" }] }));
  };
  render(<LearningSidePanel {...sidePanelProps()} />);
  await screen.findByText("Recent answer");
  fireEvent.click(screen.getByRole("button", { name: "Load earlier messages" }));
  await screen.findByText("Earlier answer");
  assert.ok(screen.getByText("Recent answer"));
  assert.equal(screen.queryByRole("button", { name: "Load earlier messages" }), null);
});

test("a tutor outage keeps the question and saved history without labeling the sources insufficient", async () => {
  globalThis.fetch = async (input) => {
    if (String(input).includes("/tutor/history")) return new Response(JSON.stringify({ conversation_id: "test-conversation-id", available: true, turns: [], has_more: false }));
    if (String(input).endsWith("/learning-state")) return new Response(JSON.stringify({ active_activity: null }));
    return new Response(JSON.stringify({ detail: "The tutor could not verify an answer right now.", error_category: "PROVIDER_UNAVAILABLE" }), { status: 503 });
  };
  render(<LearningSidePanel {...sidePanelProps({ assessmentSubmitted: true })} />);
  const input = screen.getByRole("textbox", { name: "Ask the tutor" }) as HTMLInputElement;
  await waitFor(() => assert.equal(input.disabled, false));
  fireEvent.change(input, { target: { value: "Explain that again" } });
  fireEvent.click(screen.getByRole("button", { name: "Send question" }));
  await screen.findByText("The tutor could not verify an answer right now.");
  assert.equal(input.value, "Explain that again");
  assert.equal(screen.queryByText(/available sources did not support/i), null);
});

test("reading position restores once per activity and does not rewind on format updates", () => {
  const scrollCalls: number[][] = [];
  window.scrollTo = ((x: number, y: number) => { scrollCalls.push([x, y]); }) as typeof window.scrollTo;
  const first = {
    identity: "course-1:activity-1",
    readingPosition: 420,
    hasActivity: true,
    isLoading: false,
    isContentLoading: true,
    contentFormat: undefined,
    format: "detailed",
  };
  const view = render(<StudyPositionRestorer {...first} />);
  view.rerender(<StudyPositionRestorer {...first} isContentLoading={false} contentFormat="detailed" />);
  assert.deepEqual(scrollCalls, [[0, 420]]);

  view.rerender(<StudyPositionRestorer {...first} readingPosition={880} isContentLoading={false} contentFormat="concise" format="concise" />);
  assert.deepEqual(scrollCalls, [[0, 420]]);

  view.rerender(<StudyPositionRestorer {...first} identity="course-1:activity-2" readingPosition={120} isContentLoading={false} contentFormat="detailed" />);
  assert.deepEqual(scrollCalls, [[0, 420], [0, 120]]);
});

test("grading retry failure is visible while submitted answers remain unresolved", async () => {
  let retries = 0;
  render(
    <section aria-label="Submitted results while grading is incomplete">
      <p>Grading is still pending for one saved answer.</p>
      <GradingRetryButton onRetry={async () => {
        retries += 1;
        throw new Error("The grading service is still unavailable.");
      }} />
    </section>,
  );

  fireEvent.click(screen.getByRole("button", { name: "Retry grading" }));
  assert.match((await screen.findByRole("alert")).textContent || "", /grading service is still unavailable/i);
  assert.equal(retries, 1);
});

for (const failedVariant of [true, false]) test(failedVariant
  ? "remediation retries the selected failed format while retaining its main question set"
  : "remediation retries failed questions while reading a ready alternate format", async () => {
  const retries: string[] = [];
  const mainPreparation = {
    id: "main-preparation", status: failedVariant ? "READY" : "RECOVERABLE_FAILURE",
    stage: failedVariant ? "COMPLETE" : "QUESTIONS", progress: 100,
    content_ready: true, assessment_ready: failedVariant,
  };
  const variantPreparation = {
    id: "variant-preparation", status: "RECOVERABLE_FAILURE", stage: "CONTENT", progress: 0,
    content_ready: false, assessment_ready: true, error_category: "INSUFFICIENT_SOURCE_SUPPORT",
  };
  const activity = {
    id: "activity-1", activity_type: "PREREQUISITE_REMEDIATION", target_concept_ids: ["concept-1"],
    presentation_format: "worked_example", reading_position: 0, question_count: 5,
    preparation: mainPreparation, experience_availability: "AVAILABLE",
  };
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url.includes("/tutor/history")) return new Response(JSON.stringify({ conversation_id: "test-conversation-id", available: true, turns: [], has_more: false }));
    if (url.includes("/preparation/retry")) {
      retries.push(url);
      return new Response(JSON.stringify({ ...activity, preparation: mainPreparation }));
    }
    if (url.includes("/content?format=")) {
      const format = new URL(url, "http://localhost").searchParams.get("format");
      return new Response(JSON.stringify(format === "concise" && failedVariant
        ? { status: "RECOVERABLE_FAILURE", preparation: variantPreparation, content: null }
        : { status: "READY", preparation: mainPreparation, content: {
          presentation_format: format, source_chunk_ids: [], sections: {
            objective: [], explanation: [{ text: `Saved ${format} explanation.`, citation_chunk_ids: [] }],
            example: [], recap: [],
          },
        } }));
    }
    if (url.endsWith("/graph")) return new Response(JSON.stringify({ concepts: [] }));
    if (url.endsWith("/learning-state")) return new Response(JSON.stringify({ active_activity: null }));
    if (url.endsWith("/activities/activity-1")) return new Response(JSON.stringify(activity));
    throw new Error(`Unexpected request: ${url}`);
  };
  const router = { back() {}, forward() {}, refresh() {}, push() {}, replace() {}, prefetch() {} };
  render(<AppRouterContext.Provider value={router}>
    <PathParamsContext.Provider value={{ courseId: "course-1", activityId: "activity-1" }}>
      <AdaptiveActivityPage />
    </PathParamsContext.Provider>
  </AppRouterContext.Provider>);
  await screen.findByText("Saved worked_example explanation.");
  fireEvent.click(screen.getByRole("button", { name: "concise" }));
  if (!failedVariant) await screen.findByText("Saved concise explanation.");
  await screen.findByRole("button", { name: "Retry preparation" });
  fireEvent.click(screen.getByRole("button", { name: "Retry preparation" }));
  await waitFor(() => assert.deepEqual(retries, [
    `/api/v1/courses/course-1/activities/activity-1/preparation/retry${failedVariant ? "?format=concise" : ""}`,
  ]));
});

test("lesson workspace shows active preparation progress when validated content is not ready", () => {
  const preparation: components["schemas"]["PreparationOut"] = {
    id: "preparation-1",
    status: "RUNNING",
    stage: "CONTENT",
    progress: 42,
    content_ready: false,
    assessment_ready: false,
    updated_at: "2026-10-08T12:00:00Z",
  };
  render(<EmptyLessonContentState preparation={preparation} isLoading={false} />);
  assert.match(screen.getByRole("status").textContent || "", /preparing this saved format.*42%/i);
  assert.equal(screen.queryByText(/validated content is not ready yet/i), null);
});

test("diagnostic introduction follows the starting-point reference without fake session details", () => {
  const course: components["schemas"]["CourseOut"] = {
    id: "course-1",
    title: "Distributed Systems",
    status: "PUBLISHED",
    created_at: null,
    goal: null,
    source_count: 3,
    source_revision: 0,
    sources_finalized_at: null,
    starting_confidence: null,
  };
  const learningState: components["schemas"]["LearningStateOut"] = {
    active_activity: null,
    concept_understanding: [],
    lesson_coverage: {
      course_version_id: "version-1",
      covered_lesson_ids: [],
      lessons_covered: 0,
      lessons_total: 3,
    },
  };
  const router = { back() {}, forward() {}, refresh() {}, push() {}, replace() {}, prefetch() {} };

  render(
    <AppRouterContext.Provider value={router}>
      <DiagnosticIntro course={course} learningState={learningState} />
    </AppRouterContext.Provider>,
  );

  assert.ok(screen.getByRole("link", { name: "Back to course" }));
  assert.ok(screen.getByText("Starting point"));
  assert.ok(screen.getByRole("heading", { name: "Check what you already know" }));
  assert.ok(screen.getByText("The set"));
  assert.ok(screen.getByText("How it works"));
  assert.ok(screen.getByText("If you skip"));
  assert.ok(screen.getByRole("button", { name: "Take the diagnostic" }));
  assert.ok(screen.getByRole("link", { name: "Skip for now" }));
  assert.equal(screen.queryByText(/12 questions|10 minutes|short answer/i), null);
});

test("diagnostic session is created only after the learner explicitly starts it", async () => {
  const course: components["schemas"]["CourseOut"] = {
    id: "course-1",
    title: "Distributed Systems",
    status: "PUBLISHED",
    created_at: null,
    goal: null,
    source_count: 3,
    source_revision: 0,
    sources_finalized_at: null,
    starting_confidence: null,
  };
  const learningState: components["schemas"]["LearningStateOut"] = {
    active_activity: null,
    concept_understanding: [],
    lesson_coverage: {
      course_version_id: "version-1",
      covered_lesson_ids: [],
      lessons_covered: 0,
      lessons_total: 3,
    },
  };
  const navigations: string[] = [];
  const requests: Array<{ url: string; method: string | undefined }> = [];
  globalThis.fetch = async (input, init) => {
    requests.push({ url: String(input), method: init?.method });
    return new Response(JSON.stringify({ id: "session-1" }));
  };
  const router = { back() {}, forward() {}, refresh() {}, push(href: string) { navigations.push(href); }, replace() {}, prefetch() {} };

  render(
    <AppRouterContext.Provider value={router}>
      <DiagnosticIntro course={course} learningState={learningState} />
    </AppRouterContext.Provider>,
  );

  assert.deepEqual(requests, []);
  fireEvent.click(screen.getByRole("button", { name: "Take the diagnostic" }));
  await waitFor(() => assert.deepEqual(navigations, ["/courses/course-1/assessment?type=diagnostic&sessionId=session-1"]));
  assert.deepEqual(requests, [{ url: "/api/v1/courses/course-1/diagnostic", method: "POST" }]);
});
