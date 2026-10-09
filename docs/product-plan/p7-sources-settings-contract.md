# P7 source changes, settings, and privacy contract

Status: implemented locally on `codex/p7-sources-settings`. Existing course creation,
outline review, diagnostic, and recovery flows remain the accepted baseline. P7 adds
pre-publication source mutation and persisted account settings around those flows.
Hosted storage/worker behavior is not verified here.

## Source mutation before publication

- The owner may replace or remove a source while the course is unpublished. Course,
  document, and upload-intent operations are owner scoped. Private originals are read and
  removed only through the existing private-storage service.
- A replacement is uploaded, checked against its authorized size and SHA-256, and checked
  against its file signature before the original is retired. Invalid or failed uploads
  leave the original row, bytes, and extraction usable. Repeating finalization returns the
  same document and mutation state.
- Every actual source-set mutation has a monotonic `source_revision`. A source mutation increments
  the revision, cancels older pending/running jobs, and invalidates the draft outline,
  concept mappings, generated lesson content, preparations, and questions. Unchanged
  documents retain extracted text, chunks, and reusable embeddings; chunks for a replaced
  or removed document are deleted.
- The current revision uses the existing asynchronous processing pipeline. The workspace
  names the file changed and displays that job's durable stage and recovery state. A
  previous outline is marked stale; publication and outline edits require the current
  rebuilt review outline and a READY job for the same revision.
- Source mutation, publication, worker artifact commits, course deletion, and account
  deletion serialize through the course row and source-revision checks. A stale worker
  cannot commit artifacts after replacement/publication/deletion. Repeated process requests
  for a current pending or running job reuse that job.
- Once published, a course's sources are immutable at both upload-intent creation and
  finalization. A different source set requires a new course.
- Retired originals, duplicate upload objects, abandoned upload intents, and deletion
  originals are recorded in `storage_cleanup_tasks` before source rows are removed. Worker
  failures leave a durable pending row and a cleanup-pending response; operations can
  re-enqueue that row after storage or queue recovery. Account deletion never reports
  complete while tracked file cleanup remains.

## Settings and preference precedence

- `tracking_consent` remains the existing `full`/`minimal` event-ingestion gate. Full
  collects optional reading-time/interaction events; minimal rejects that telemetry in
  both the frontend sender and backend ingestion route. Answers, progress, mastery,
  required reading completion, resume position, and learning decisions remain available.
  Saving consent neither edits historical telemetry nor changes learning records.
- A saved default lesson format (`detailed`, `concise`, `worked_example`, or `analogy`)
  takes precedence over the adaptive format choice for the initial format of a newly
  created teaching activity. The adaptive policy still selects the activity and target
  concepts; its ranking/recommendation policy is unchanged. Resumed activities keep their
  saved format and position. Assessment/practice tutor access remains governed by the
  existing backend restrictions.
- `tutor_panel_open` controls the initial panel state when a teaching experience opens.
  Existing activities keep their saved format and reading position when resumed; learners
  can still switch presentation format while studying.
- “Reset preferences” sets the default format to `detailed`, opens the tutor panel by
  default, and deletes only the user's `PresentationAffinity` rows. It does not change
  tracking consent, telemetry history, answers, progress, or mastery evidence. This is the
  existing affinity reset extended to the new settings, not a reset of learning history.

## Account deletion and recovery

- Deletion removes the learner's owned courses and learning, preparation/provenance,
  grading/review, tutor, event, profile, adaptation, preference, and AI-usage records.
  When a deleted account is a reviewer, corrections and review history belonging to other
  learners remain and reviewer identity is cleared according to the existing attribution
  policy.
- Private originals and unfinished upload objects are queued for cleanup before their
  database rows or owner are deleted. `storage_cleanup_tasks` has no user foreign key so it
  survives account deletion. Failed storage calls return the task to `PENDING`, record an
  error category and attempt count, and can be retried by the worker/operations. The API
  reports `cleanup_pending` and its outstanding count until every tracked target is gone.
- Processing workers fence each artifact commit on the course revision and existence. A
  worker finishing after deletion cannot recreate user-owned rows.
- On success the Settings page clears NeuroLearn browser storage, signs out, and shows
  whether file cleanup is pending. The destructive action requires typing `DELETE` and
  provides a cancel action.

## Migration and verification boundary

Additive migration `3c7a1e9d5b42_p7_source_settings_cleanup` adds source revisions, persisted
presentation preferences, replacement/idempotency metadata, and the durable cleanup queue.
Apply it through the normal controlled migration process; this task does not run it against
a persistent or production database. Deterministic tests cover source replacement and
invalidation, stale job/publication gates, ownership, settings/minimal tracking, independent
reset, and deletion cleanup recovery. Local checks do not prove hosted Celery or private
storage behavior.
