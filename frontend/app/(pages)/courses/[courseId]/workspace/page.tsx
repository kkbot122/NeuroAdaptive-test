"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import Link from "next/link";
import type { components } from "@/lib/generated/api";
import { StateWrapper } from "@/components/StateWrapper";
import { CourseSidebar } from "@/components/CourseSidebar";
import { ArrowLeft, ArrowRight, Upload, File, Loader2, CheckCircle, RefreshCcw, Pencil, Check, X } from "lucide-react";
import { uploadCourseDocument } from "@/lib/upload-course-document";

const processingStageCopy: Record<string, { title: string; detail: string }> = {
  VALIDATING: { title: "Checking files", detail: "Checking the uploaded source files" },
  EXTRACTING: { title: "Reading content", detail: "Extracting text and document structure" },
  INTERPRETING_VISUALS: { title: "Interpreting visuals", detail: "Visual interpretation is not enabled for these source types" },
  CHUNKING: { title: "Preparing source passages", detail: "Organizing sections for grounded learning" },
  INDEXING: { title: "Indexing course sources", detail: "Making source passages available to the course" },
  EXTRACTING_CONCEPTS: { title: "Finding concepts", detail: "Identifying concepts present in your files" },
  BUILDING_GRAPH: { title: "Connecting concepts", detail: "Organizing relationships between concepts" },
  GENERATING_STRUCTURE: { title: "Drafting outline", detail: "Building modules and lessons from your sources" },
  VALIDATING_COURSE: { title: "Checking the outline", detail: "Validating the course structure" },
};

function formatStageName(name: string): string {
  return processingStageCopy[name]?.title || name.replaceAll("_", " ").toLowerCase();
}

export default function WorkspacePage() {
  const params = useParams();
  const router = useRouter();
  const { data: session } = useSession();
  const courseId = params.courseId as string;
  const learnerName = session?.user?.name?.trim() || session?.user?.email?.split("@")[0] || "Learner";

  const [activeTab, setActiveTab] = useState<"upload" | "outline" | "diagnostic">("upload");
  
  // States for StateWrapper
  const [isLoading, setIsLoading] = useState(true);
  const [isError, setIsError] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  const [course, setCourse] = useState<components["schemas"]["CourseOut"] | null>(null);
  const [documents, setDocuments] = useState<components["schemas"]["DocumentOut"][]>([]);
  const [job, setJob] = useState<components["schemas"]["JobOut"] | null>(null);
  // Separate from job.status === "RUNNING": that only becomes true once the
  // first poll response comes back. Without this, clicking "Generate
  // Curriculum" gave zero visual feedback for as long as the POST took to
  // resolve -- reproduced live taking several minutes -- so a learner
  // clicked it again (and again), sending overlapping /process requests
  // for the same course that raced each other and crashed the backend.
  const [isGenerating, setIsGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  // True only while a PAUSED job's cause is a provider issue (quota/rate
  // limit/outage -- jobs/service.py's frozen-scope-mandated behavior for
  // that case), never a FAILED job: "paused" is retryable in place, "failed"
  // needs a fresh attempt. Drives whether the button below reads "Retry"
  // (calls /jobs/{id}/retry, resuming from the stage that paused) instead
  // of "Generate Curriculum" (starts an entirely new job from scratch).
  const [isRetrying, setIsRetrying] = useState(false);

  // Matches curriculum/router.py's _version_out() exactly: nested
  // modules[].lessons[], and a lesson's concepts are {concept_id, role,
  // weight} -- no concept name. Names come from the separate graph
  // endpoint, joined in below via conceptNames.
  const [structure, setStructure] = useState<components["schemas"]["StructureOut"] | null>(null);
  const [conceptNames, setConceptNames] = useState<Record<string, string>>({});
  const [openModules, setOpenModules] = useState<Record<string, boolean>>({});

  // Lesson renaming: the only edit PUT /courses/{id}/structure supports
  // this phase (curriculum/router.py's StructureUpdateIn docstring --
  // dropping a concept or reordering a module is a documented, deferred
  // gap, not something to fake a control for here).
  const [editingLessonId, setEditingLessonId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [isSavingRename, setIsSavingRename] = useState(false);

  const [uploading, setUploading] = useState(false);
  const [removingDocumentId, setRemovingDocumentId] = useState<string | null>(null);
  const [sourceChangeNotice, setSourceChangeNotice] = useState<string | null>(null);
  const [sourceChangeKind, setSourceChangeKind] = useState<"changed" | "unchanged" | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const replacementDocumentIdRef = useRef<string | null>(null);

  // Fetch Structure for Review. The structure response has no concept
  // names (curriculum/router.py's _version_out only returns concept_id per
  // lesson) -- the graph endpoint is fetched alongside it to build the
  // concept_id -> name map the outline needs to display anything readable.
  const fetchStructure = useCallback(async () => {
    try {
      const [structureRes, graphRes] = await Promise.all([
        fetch(`/api/v1/courses/${courseId}/structure`),
        fetch(`/api/v1/courses/${courseId}/graph`),
      ]);
      if (structureRes.ok) {
        const savedStructure: components["schemas"]["StructureOut"] = await structureRes.json();
        setStructure(savedStructure);
        setOpenModules(Object.fromEntries(savedStructure.modules.map((module, index) => [module.id, index === 0])));
        setActiveTab("outline");
        setGenerateError(null);
      } else {
        throw new Error("The saved course outline could not be loaded. Try again.");
      }
      if (graphRes.ok) {
        const graph = await graphRes.json();
        const names: Record<string, string> = {};
        for (const c of graph.concepts || []) names[c.id] = c.name;
        setConceptNames(names);
      }
    } catch (err) {
      console.error(err);
      setGenerateError(err instanceof Error ? err.message : "The course outline could not be loaded. Try again.");
    }
  }, [courseId]);

  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollingJobIdRef = useRef<string | null>(null);
  const pollInFlightRef = useRef(false);
  const processingPostInFlightRef = useRef(false);
  const processingAttemptedRef = useRef(false);
  const autoStartRequestedRef = useRef(false);
  const autoStartAttemptedRef = useRef(false);
  useEffect(() => () => {
    if (pollingRef.current) clearInterval(pollingRef.current);
    pollingJobIdRef.current = null;
  }, []);

  const pollJob = useCallback((jobId: string) => {
    if (pollingRef.current) clearInterval(pollingRef.current);
    pollingJobIdRef.current = jobId;
    const poll = async () => {
      if (pollingJobIdRef.current !== jobId || pollInFlightRef.current) return;
      pollInFlightRef.current = true;
      try {
        const res = await fetch(`/api/v1/jobs/${jobId}`);
        if (!res.ok) throw new Error("Failed to fetch job status");
        const jobData: components["schemas"]["JobOut"] = await res.json();
        if (pollingJobIdRef.current !== jobId) return;
        setJob(jobData);

        // Backend job statuses are READY/FAILED/PAUSED (uppercase --
        // app/modules/jobs/models.py's JobStatus enum), not "completed"/
        // "failed": this comparison never matched, so the interval never
        // cleared and the UI never advanced past step 1 even once the job
        // had actually finished.
        if (jobData.status === "READY") {
          if (pollingRef.current) clearInterval(pollingRef.current);
          pollingRef.current = null;
          pollingJobIdRef.current = null;
          setIsGenerating(false);
          fetchStructure();
        } else if (jobData.retry_available || jobData.status === "FAILED" || jobData.status === "PAUSED") {
          if (pollingRef.current) clearInterval(pollingRef.current);
          pollingRef.current = null;
          pollingJobIdRef.current = null;
          setIsGenerating(false);
          // PAUSED used to fall through here with no message at all -- the
          // spinner just vanished and the button went back to idle,
          // indistinguishable from having done nothing. Reproduced live
          // against a real exhausted Gemini quota.
          const fallback = jobData.status === "PAUSED"
            ? "Processing paused. The AI provider may be temporarily unavailable -- try Retry below."
            : "Processing failed. Try again.";
          setGenerateError(jobData.error_detail || fallback);
        }
      } catch (err) {
        console.error(err);
      } finally {
        pollInFlightRef.current = false;
      }
    };
    const interval = setInterval(() => { void poll(); }, 2000);
    pollingRef.current = interval;
    void poll();
  }, [fetchStructure]);

  const startProcessingRequest = useCallback(async () => {
    if (processingPostInFlightRef.current) return;
    processingPostInFlightRef.current = true;
    processingAttemptedRef.current = true;
    setIsGenerating(true);
    setGenerateError(null);
    try {
      const res = await fetch(`/api/v1/courses/${courseId}/process`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || body.error || "Failed to start processing");
      }
      const jobData: components["schemas"]["JobOut"] = await res.json();
      setJob(jobData);
      pollJob(jobData.id);
    } catch (err) {
      console.error(err);
      setGenerateError(err instanceof Error ? err.message : "Failed to start processing");
      setIsGenerating(false);
    } finally {
      processingPostInFlightRef.current = false;
    }
  }, [courseId, pollJob]);

  const fetchCourseData = useCallback(async (autoStart = false) => {
    setIsLoading(true);
    setIsError(false);
    try {
      const courseRes = await fetch(`/api/v1/courses/${courseId}`);
      if (!courseRes.ok) {
        if (courseRes.status === 404) {
          setIsError(true);
          setErrorMsg("Unauthorized or Not Found");
          return;
        }
        throw new Error("Failed to load course");
      }
      const currentCourse = await courseRes.json();
      if (currentCourse.status === "PUBLISHED") {
        router.replace(`/courses/${courseId}/learn`);
        return;
      }
      setCourse(currentCourse);

      const docsRes = await fetch(`/api/v1/courses/${courseId}/documents`);
      let currentDocuments: components["schemas"]["DocumentOut"][] = [];
      if (docsRes.ok) {
        currentDocuments = await docsRes.json();
        setDocuments(currentDocuments);
      }

      // Recover an in-flight/paused/failed job across a reload or a
      // navigate-away-and-back -- without this, the only place a job id
      // ever lived was React state, so returning to this page showed a
      // fresh "Generate Curriculum" button with no memory of a job that
      // was paused (e.g. by a provider quota hit) or still running.
      const jobRes = await fetch(`/api/v1/courses/${courseId}/jobs/latest`);
      if (jobRes.ok) {
        const latestJob: components["schemas"]["JobOut"] | null = await jobRes.json();
        if (latestJob) {
          setJob(latestJob);
          if (currentCourse.status === "PROCESSING" && latestJob.source_revision !== currentCourse.source_revision) {
            // Recover the narrow crash window between committing a source
            // replacement and creating its new durable processing job.
            setStructure(null);
            setActiveTab("upload");
            void startProcessingRequest();
          } else if ((latestJob.status === "RUNNING" || latestJob.status === "PENDING") && !latestJob.retry_available) {
            setIsGenerating(true);
            pollJob(latestJob.id);
          } else if (latestJob.retry_available || latestJob.status === "PAUSED" || latestJob.status === "FAILED") {
            const fallback = latestJob.status === "PAUSED"
              ? "Processing paused. The AI provider may be temporarily unavailable -- try Retry below."
              : "Processing failed. Try again.";
            setGenerateError(latestJob.error_detail || fallback);
          } else if (latestJob.status === "READY") {
            fetchStructure();
            setActiveTab("outline");
          }
        } else if ((autoStart || currentCourse.status === "PROCESSING") && currentDocuments.length > 0 && !autoStartAttemptedRef.current) {
          autoStartAttemptedRef.current = true;
          void startProcessingRequest();
        }
      } else if (autoStart) {
        setGenerateError("The current processing status could not be checked. Use Prepare course to continue safely.");
      }
    } catch (err: unknown) {
      console.error(err);
      setIsError(true);
      setErrorMsg(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setIsLoading(false);
    }
  }, [courseId, pollJob, fetchStructure, router, startProcessingRequest]);

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("startProcessing") === "1") {
      autoStartRequestedRef.current = true;
      window.history.replaceState(window.history.state, "", `/courses/${courseId}/workspace`);
    }
    const timer = window.setTimeout(() => {
      if (courseId) void fetchCourseData(autoStartRequestedRef.current);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [courseId, fetchCourseData]);

  // Upload Document
  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0) {
      replacementDocumentIdRef.current = null;
      return;
    }
    const file = e.target.files[0];
    const replacementDocumentId = replacementDocumentIdRef.current;
    const replacing = replacementDocumentId ? documents.find((doc) => doc.id === replacementDocumentId) : null;
    replacementDocumentIdRef.current = null;
    setUploading(true);
    setGenerateError(null);
    try {
      const result = await uploadCourseDocument(courseId, file, replacementDocumentId || undefined, replacing?.role || "STUDY");
      
      const newDocs = await fetch(`/api/v1/courses/${courseId}/documents`).then(r => r.json());
      setDocuments(newDocs);
      if (result.source_changed) {
        setSourceChangeKind("changed");
        setStructure(null);
        setActiveTab("upload");
        setSourceChangeNotice(result.replaced_filename || replacing
          ? `Replaced ${result.replaced_filename || replacing?.filename} with ${file.name}.`
          : `Added ${file.name}.`);
        setJob(null);
        if (result.rebuild_job_id) {
          const jobResponse = await fetch(`/api/v1/jobs/${result.rebuild_job_id}`);
          if (jobResponse.ok) {
            const jobData: components["schemas"]["JobOut"] = await jobResponse.json();
            setJob(jobData);
            setIsGenerating(jobData.status === "PENDING" || jobData.status === "RUNNING");
            if (jobData.status === "PENDING" || jobData.status === "RUNNING") pollJob(jobData.id);
          }
        } else {
          setIsGenerating(false);
        }
        if (result.cleanup_pending) setSourceChangeNotice((notice) => `${notice} Cleanup of the retired file has been queued.`);
      } else if (replacing) {
        setSourceChangeKind("unchanged");
        setSourceChangeNotice(`${file.name} matches the existing source. ${replacing.filename} and its saved extraction were kept.`);
      }
    } catch (err) {
      console.error(err);
      setGenerateError(err instanceof Error ? err.message : "The source could not be uploaded. Check the file type and try again.");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const handleRemoveSource = async (documentId: string, filename: string) => {
    if (removingDocumentId) return;
    setRemovingDocumentId(documentId);
    setGenerateError(null);
    try {
      const response = await fetch(`/api/v1/courses/${courseId}/documents/${documentId}`, { method: "DELETE" });
      const payload: components["schemas"]["DocumentMutationOut"] | null = await response.json().catch(() => null);
      if (!response.ok || !payload) {
        const detail = (payload as unknown as { detail?: unknown } | null)?.detail;
        throw new Error(typeof detail === "string" ? detail : "This source could not be removed.");
      }
      const currentDocuments: components["schemas"]["DocumentOut"][] = await fetch(`/api/v1/courses/${courseId}/documents`).then((r) => r.json());
      setDocuments(currentDocuments);
      if (payload.source_changed) {
        setSourceChangeKind("changed");
        setStructure(null);
        setActiveTab("upload");
        setSourceChangeNotice(`Removed ${filename}.`);
        setJob(null);
        if (payload.rebuild_job_id) {
          const jobResponse = await fetch(`/api/v1/jobs/${payload.rebuild_job_id}`);
          if (jobResponse.ok) {
            const jobData: components["schemas"]["JobOut"] = await jobResponse.json();
            setJob(jobData);
            setIsGenerating(jobData.status === "PENDING" || jobData.status === "RUNNING");
            if (jobData.status === "PENDING" || jobData.status === "RUNNING") pollJob(jobData.id);
          }
        } else {
          setIsGenerating(false);
        }
        if (payload.cleanup_pending) setSourceChangeNotice((notice) => `${notice} Cleanup of the retired file has been queued.`);
      }
    } catch (error) {
      setGenerateError(error instanceof Error ? error.message : "This source could not be removed.");
    } finally {
      setRemovingDocumentId(null);
    }
  };

  // Generate Curriculum
  const handleGenerate = () => { void startProcessingRequest(); };

  // Resume a PAUSED/FAILED job in place (jobs/router.py's /jobs/{id}/retry)
  // instead of starting a whole new pipeline run via handleGenerate --
  // stages that already succeeded are not re-run.
  const handleRetryJob = async () => {
    if (!job || isRetrying) return;
    setIsRetrying(true);
    setGenerateError(null);
    try {
      const res = await fetch(`/api/v1/jobs/${job.id}/retry`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || body.error || "Failed to retry processing");
      }
      const jobData: components["schemas"]["JobOut"] = await res.json();
      setJob(jobData);
      setIsGenerating(true);
      pollJob(job.id);
    } catch (err) {
      console.error(err);
      setGenerateError(err instanceof Error ? err.message : "Failed to retry processing");
    } finally {
      setIsRetrying(false);
    }
  };

  const handlePublishStructure = async () => {
    try {
      const res = await fetch(`/api/v1/courses/${courseId}/publish-structure`, {
        method: "POST"
      });
      if (!res.ok) throw new Error("Failed to publish");
      setActiveTab("diagnostic");
    } catch (err) {
      console.error(err);
      setGenerateError("The course could not be published. Refresh the review and try again.");
    }
  };

  const startRenaming = (lessonId: string, currentTitle: string) => {
    setEditingLessonId(lessonId);
    setEditingTitle(currentTitle);
  };

  const cancelRenaming = () => {
    setEditingLessonId(null);
    setEditingTitle("");
  };

  const saveRename = async (lessonId: string) => {
    if (!editingTitle.trim()) return;
    setIsSavingRename(true);
    try {
      const res = await fetch(`/api/v1/courses/${courseId}/structure`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lesson_renames: [{ lesson_id: lessonId, title: editingTitle.trim() }],
        }),
      });
      if (!res.ok) throw new Error("Failed to rename lesson");
      const updated = await res.json();
      setStructure(updated);
      cancelRenaming();
    } catch (err) {
      console.error(err);
      setGenerateError("The lesson name could not be saved. Try again.");
    } finally {
      setIsSavingRename(false);
    }
  };

  const renderUploadTab = () => (
    <section className="nl-processing-box nl-processing-upload">
      <Upload aria-hidden="true" />
      <h2>Upload source material</h2>
      <p>Choose PDF, Markdown, or text files to build this course from.</p>
      <button onClick={() => { replacementDocumentIdRef.current = null; fileInputRef.current?.click(); }} disabled={uploading} className="nl-processing-button nl-processing-primary">
        {uploading ? <><Loader2 className="nl-processing-spin" /> Uploading…</> : "Select a file"}
      </button>
      {renderSourceControls()}
      {documents.length > 0 && <button onClick={handleGenerate} disabled={isGenerating} className="nl-processing-button nl-processing-primary">
        {isGenerating ? "Starting processing…" : "Prepare course"}<ArrowRight aria-hidden="true" />
      </button>}
      {generateError && <p className="nl-processing-error" role="alert">{generateError}</p>}
    </section>
  );

  const renderSourceControls = () => documents.length > 0 && <section className="nl-processing-source-controls" aria-label="Course source files">
    <h3>Course source files</h3>
    <ul>{documents.map((doc) => <li key={doc.id}>
      <span><File aria-hidden="true" />{doc.filename}</span>
      <span className="nl-processing-source-actions">
        <button type="button" onClick={() => { replacementDocumentIdRef.current = doc.id; fileInputRef.current?.click(); }} disabled={uploading || removingDocumentId !== null} aria-label={`Replace ${doc.filename}`}>Replace</button>
        <button type="button" onClick={() => void handleRemoveSource(doc.id, doc.filename)} disabled={uploading || removingDocumentId !== null} aria-label={`Remove ${doc.filename}`}>
          {removingDocumentId === doc.id ? "Removing…" : "Remove"}
        </button>
      </span>
    </li>)}</ul>
  </section>;

  const renderProcessingTab = () => {
    const activeStage = job?.current_stage;
    const stages = job?.stages || [];
    const failedStage = stages.find((stage) => stage.status === "FAILED");
    const failed = Boolean(generateError || job?.status === "FAILED" || job?.status === "PAUSED" || job?.retry_available);
    const canRetry = Boolean(job?.retry_available);
    const currentJobStage = activeStage ? formatStageName(activeStage) : null;

    return <>
      {renderSourceControls()}
      <div className="nl-processing-grid">
      <section className="nl-processing-box" aria-label="Course processing stages">
        {stages.length > 0 ? <ol className="nl-processing-stages">
          {stages.map((stage) => {
            const copy = processingStageCopy[stage.name] || { title: formatStageName(stage.name), detail: "Processing this course stage" };
            const rowState = stage.status === "SUCCEEDED" ? "done" : stage.status === "FAILED" ? "failed" : stage.status === "RUNNING" ? "active" : stage.status === "SKIPPED" ? "skipped" : "waiting";
            const statusText = rowState === "done" ? "Complete" : rowState === "failed" ? "Needs attention" : rowState === "active" ? "In progress" : rowState === "skipped" ? "Skipped" : "Waiting";
            return <li key={stage.name} className={`nl-processing-stage is-${rowState}`} aria-label={`${copy.title}: ${statusText}`} aria-current={rowState === "active" ? "step" : undefined}>
              <i aria-hidden="true">{rowState === "done" ? "✓" : rowState === "failed" ? "!" : rowState === "active" ? <span className="nl-processing-stage-dot" /> : rowState === "skipped" ? "–" : ""}</i>
              <div><b>{copy.title}</b><small>{stage.status === "FAILED" ? (generateError || job?.error_detail || copy.detail) : copy.detail}</small></div>
              {rowState === "failed" && <span className="nl-processing-chip is-failed">Needs attention</span>}
            </li>;
          })}
        </ol> : <div className="nl-processing-stage-empty" role="status">
          <Loader2 className="nl-processing-spin" aria-hidden="true" />
          <div><b>{isGenerating ? "Starting course processing" : "Processing status unavailable"}</b><small>{isGenerating ? "The course job is being created." : "Retry the status check or start processing again."}</small></div>
        </div>}

        {failed && <div className="nl-processing-failure" role="alert">
          <h2>{failedStage ? `${formatStageName(failedStage.name)} needs attention` : "Processing stopped"}</h2>
          <p>{generateError || job?.error_detail || "The course could not be prepared. Your uploaded sources are still saved."}</p>
          <div className="nl-processing-actions">
            <button onClick={canRetry ? handleRetryJob : handleGenerate} disabled={isGenerating || isRetrying} className="nl-processing-button nl-processing-primary">
              {isRetrying ? "Retrying…" : <><RefreshCcw aria-hidden="true" /> {canRetry ? "Retry processing" : "Try processing again"}</>}
            </button>
          </div>
          <p className="nl-processing-note">Your uploaded sources and completed processing stages are kept.</p>
        </div>}
      </section>

      <aside className="nl-processing-ready" aria-live="polite">
        <h2>What is ready</h2>
        <ul>
          <li>Course outline <span className={`nl-processing-chip ${job?.status === "READY" ? "is-ready" : failed ? "is-failed" : ""}`}>{job?.status === "READY" ? "Ready" : failed ? "Needs attention" : "Preparing"}</span></li>
          <li>Source files <span className="nl-processing-chip is-ready">{documents.length} added</span></li>
          <li>Lessons and questions <span className="nl-processing-chip">When you begin</span></li>
        </ul>
        {job?.status === "READY" ? <button onClick={() => { void fetchStructure(); }} className="nl-processing-button nl-processing-primary nl-processing-wide">
          Review outline <ArrowRight aria-hidden="true" />
        </button> : !failed && <button disabled className="nl-processing-button nl-processing-primary nl-processing-wide">
          {currentJobStage ? `Processing · ${currentJobStage}` : "Review outline"}<ArrowRight aria-hidden="true" />
        </button>}
        <Link href="/dashboard" className="nl-processing-button nl-processing-wide">Back to dashboard</Link>
        <p className="nl-processing-note">Your outline is built from the files you uploaded. Lesson content and questions are prepared as you begin studying.</p>
      </aside>
      </div>
    </>;
  };

  const renderOutlineTab = () => (
    <div className="nl-processing-grid">
      <section className="nl-processing-box nl-processing-outline" aria-label="Generated course outline">
        {structure?.modules?.map((module, moduleIndex) => <details
          key={module.id}
          className="nl-processing-module"
          open={openModules[module.id] ?? moduleIndex === 0}
          onToggle={(event) => {
            const expanded = event.currentTarget.open;
            setOpenModules((current) => current[module.id] === expanded ? current : { ...current, [module.id]: expanded });
          }}
        >
          <summary><span className="nl-processing-caret" aria-hidden="true" /><b>{module.title}</b><span>{module.lessons.length} lessons</span></summary>
          <div className="nl-processing-lessons">
            {module.lessons.map((lesson, lessonIndex) => <article key={lesson.id} className="nl-processing-lesson">
              {editingLessonId === lesson.id ? <div className="nl-processing-rename">
                <input type="text" value={editingTitle} onChange={(event) => setEditingTitle(event.target.value)} autoFocus aria-label={`New title for ${lesson.title}`} />
                <button onClick={() => void saveRename(lesson.id)} disabled={isSavingRename} className="nl-processing-button nl-processing-small nl-processing-primary" aria-label={`Save title for ${lesson.title}`}><Check aria-hidden="true" />Save</button>
                <button onClick={cancelRenaming} disabled={isSavingRename} className="nl-processing-button nl-processing-small" aria-label={`Cancel renaming ${lesson.title}`}><X aria-hidden="true" />Cancel</button>
              </div> : <div className="nl-processing-lesson-head">
                <b>Lesson {lessonIndex + 1}: {lesson.title}</b>
                <button onClick={() => startRenaming(lesson.id, lesson.title)} className="nl-processing-edit" aria-label={`Rename ${lesson.title}`}><Pencil aria-hidden="true" />Rename</button>
              </div>}
              {lesson.objective && <p>{lesson.objective}</p>}
              {lesson.concepts.some((concept) => conceptNames[concept.concept_id]) && <div className="nl-processing-concepts" aria-label="Lesson concepts">
                {lesson.concepts.filter((concept) => conceptNames[concept.concept_id]).map((concept) => <span key={concept.concept_id}>{conceptNames[concept.concept_id]}</span>)}
              </div>}
            </article>)}
          </div>
        </details>)}
        {generateError && <p className="nl-processing-error" role="alert">{generateError}</p>}
      </section>

      <aside className="nl-processing-ready">
        <h2>Ready to publish?</h2>
        {renderSourceControls()}
        <div className="nl-processing-stats" aria-label="Outline counts">
          <div><b>{structure?.modules.length || 0}</b><span>modules</span></div>
          <div><b>{structure?.modules.reduce((total, module) => total + module.lessons.length, 0) || 0}</b><span>lessons</span></div>
          <div><b>{documents.length}</b><span>sources</span></div>
        </div>
        <ul>
          <li>Course outline <span className="nl-processing-chip is-ready">Ready</span></li>
          <li>Lesson content <span className="nl-processing-chip">When you begin</span></li>
        </ul>
        <button onClick={handlePublishStructure} disabled={!structure || isSavingRename} className="nl-processing-button nl-processing-primary nl-processing-wide">
          Publish course <ArrowRight aria-hidden="true" />
        </button>
        <p className="nl-processing-note">Publishing fixes the uploaded sources. Lesson content and questions are prepared as you begin studying.</p>
        <Link href="/dashboard" className="nl-processing-button nl-processing-wide">Back to dashboard</Link>
      </aside>
    </div>
  );

  const renderDiagnosticTab = () => (
    <section className="nl-processing-box nl-processing-published">
      <CheckCircle aria-hidden="true" />
      <h2>Your course is ready</h2>
      <p>Your outline is published. Take the optional diagnostic to record a baseline, or continue to the course overview.</p>
      <div className="nl-processing-actions">
        <Link href={`/courses/${courseId}/diagnostic`} className="nl-processing-button nl-processing-primary">Take diagnostic <ArrowRight aria-hidden="true" /></Link>
        <Link href={`/courses/${courseId}/learn`} prefetch={false} className="nl-processing-button">Course overview</Link>
      </div>
    </section>
  );

  const processingContext = Boolean(job || isGenerating || (generateError && processingAttemptedRef.current));
  const pageTitle = activeTab === "diagnostic"
    ? "Your course is ready"
    : structure
      ? "Review your outline"
      : processingContext
        ? (job?.status === "FAILED" || job?.status === "PAUSED" ? "Preparing stopped" : "Preparing your course")
        : "Add course materials";
  const pageLead = activeTab === "diagnostic"
    ? "Your outline is published. Take the optional diagnostic to record a baseline, or continue to the course overview."
    : structure
      ? "Rename anything that reads wrong. Everything else is built from your files."
      : processingContext
        ? (job?.status === "FAILED" || job?.status === "PAUSED"
          ? "Processing stopped. Your uploaded sources and completed stages are still saved."
          : "You can leave safely. Processing progress is saved, and your dashboard shows the course status.")
        : "Upload source material to build this course outline.";
  const sourceRebuildStatus = sourceChangeKind !== "changed" ? ""
    : documents.length === 0 ? "Add a source to prepare a new outline."
      : job?.status === "READY" ? "The updated outline is ready for review."
        : job?.status === "FAILED" || job?.status === "PAUSED" || job?.retry_available
          ? "The rebuild stopped. Review the status below and retry processing."
          : job?.status === "PENDING" ? "The rebuild is queued."
            : job?.status === "RUNNING"
              ? `Rebuilding${job.current_stage ? ` · ${formatStageName(job.current_stage)}` : ""}.`
              : course?.status === "PROCESSING" ? "The current outline is stale while rebuild status is being recovered."
                : "The source set is saved. Prepare the course to build its outline.";

  return <div className="nl-processing-shell">
    <CourseSidebar name={learnerName} />
    <main className="nl-processing-main">
      <Link href="/dashboard" className="nl-processing-back"><ArrowLeft aria-hidden="true" />Back to dashboard</Link>
      {!isLoading && <header className="nl-processing-heading">
        <h1>{pageTitle}</h1>
        <p>{pageLead}</p>
        {course?.title && <span className="nl-processing-course-name">{course.title}</span>}
      </header>}
      <input type="file" ref={fileInputRef} onChange={handleUpload} className="sr-only" accept=".pdf,.md,.txt" />
      {sourceChangeNotice && <p className="nl-processing-source-notice" role="status">{sourceChangeNotice}{sourceRebuildStatus ? ` ${sourceRebuildStatus}` : ""}</p>}
      <StateWrapper
        isLoading={isLoading}
        isError={isError}
        errorMessage={errorMsg}
        isUnauthorized={errorMsg.includes("Unauthorized")}
        isEmpty={false}
        onRetry={() => { void fetchCourseData(); }}
      >
        {activeTab === "diagnostic" ? renderDiagnosticTab()
          : structure ? renderOutlineTab()
            : processingContext ? renderProcessingTab()
              : renderUploadTab()}
      </StateWrapper>
    </main>
  </div>;
}
