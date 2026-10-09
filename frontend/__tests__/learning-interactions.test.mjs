import assert from "node:assert/strict";
import test from "node:test";
import {
  canCommitWorkspaceResponse,
  shouldRestoreReadingPosition,
  studyWorkspaceIdentity,
  updateWorkspaceState,
  visibleWorkspaceContent,
  workspaceStateForIdentity,
} from "../lib/study-workspace-state.mjs";

test("changing the course or activity immediately hides old lesson content and citations", () => {
  const firstIdentity = studyWorkspaceIdentity("course-a", "activity-a");
  const secondIdentity = studyWorkspaceIdentity("course-a", "activity-b");
  const firstContent = {
    presentation_format: "detailed",
    source_chunk_ids: ["source-a"],
    sections: { explanation: [{ text: "Saved explanation for activity A." }] },
  };
  const firstState = updateWorkspaceState(null, firstIdentity, { content: firstContent, loading: false });

  assert.equal(visibleWorkspaceContent(firstState, firstIdentity, "detailed"), firstContent);
  assert.deepEqual(visibleWorkspaceContent(firstState, secondIdentity, "detailed"), null);

  const secondState = updateWorkspaceState(firstState, secondIdentity, {
    preparation: { status: "PENDING" },
    loading: true,
  });
  assert.equal(secondState.content, null);
  assert.deepEqual(visibleWorkspaceContent(secondState, secondIdentity, "detailed"), null);
  assert.equal(workspaceStateForIdentity(secondState, firstIdentity), null);
});

test("a late response from another activity or format cannot replace the current workspace", () => {
  const activityA = studyWorkspaceIdentity("course-a", "activity-a");
  const activityB = studyWorkspaceIdentity("course-a", "activity-b");
  assert.equal(canCommitWorkspaceResponse({
    requestIdentity: activityA,
    activeIdentity: activityB,
    requestedFormat: "detailed",
    activeFormat: "detailed",
    aborted: false,
  }), false);
  assert.equal(canCommitWorkspaceResponse({
    requestIdentity: activityB,
    activeIdentity: activityB,
    requestedFormat: "concise",
    activeFormat: "detailed",
    aborted: false,
  }), false);
  assert.equal(canCommitWorkspaceResponse({
    requestIdentity: activityB,
    activeIdentity: activityB,
    requestedFormat: "detailed",
    activeFormat: "detailed",
    aborted: false,
  }), true);
});

test("reading position restores once after current content loads, not on format changes", () => {
  const identity = studyWorkspaceIdentity("course-a", "activity-a");
  const initial = {
    identity,
    restoredIdentity: null,
    hasActivity: true,
    isLoading: false,
    isContentLoading: true,
    contentFormat: null,
    format: "detailed",
  };
  assert.equal(shouldRestoreReadingPosition(initial), false);

  const contentReady = { ...initial, isContentLoading: false, contentFormat: "detailed" };
  assert.equal(shouldRestoreReadingPosition(contentReady), true);

  const afterRestore = { ...contentReady, restoredIdentity: identity };
  assert.equal(shouldRestoreReadingPosition({
    ...afterRestore,
    contentFormat: "concise",
    format: "concise",
  }), false);
  assert.equal(shouldRestoreReadingPosition({ ...afterRestore, isContentLoading: true }), false);
});
