/** @typedef {{ identity: string, content: object | null, preparation: object | null, error: string | null, loading: boolean }} StudyWorkspaceState */

export function studyWorkspaceIdentity(courseId, activityId) {
  return JSON.stringify([courseId, activityId || null]);
}

export function workspaceStateForIdentity(state, identity) {
  return state?.identity === identity ? state : null;
}

/** @param {StudyWorkspaceState | null} current @param {string} identity @param {Partial<StudyWorkspaceState>} changes */
export function updateWorkspaceState(current, identity, changes) {
  const existing = workspaceStateForIdentity(current, identity);
  return {
    identity,
    content: existing?.content ?? null,
    preparation: existing?.preparation ?? null,
    error: existing?.error ?? null,
    loading: existing?.loading ?? true,
    ...changes,
  };
}

export function visibleWorkspaceContent(state, identity, format) {
  const current = workspaceStateForIdentity(state, identity);
  return current?.content?.presentation_format === format ? current.content : null;
}

export function canCommitWorkspaceResponse({ requestIdentity, activeIdentity, requestedFormat, activeFormat, aborted }) {
  return !aborted && requestIdentity === activeIdentity && requestedFormat === activeFormat;
}

export function shouldRestoreReadingPosition({
  identity,
  restoredIdentity,
  hasActivity,
  isLoading,
  isContentLoading,
  contentFormat,
  format,
}) {
  return Boolean(
    hasActivity
    && identity !== restoredIdentity
    && !isLoading
    && !isContentLoading
    && contentFormat === format,
  );
}
