"use client";

import { useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useSession } from "next-auth/react";
import type { components } from "@/lib/generated/api";
import { CourseSidebar } from "@/components/CourseSidebar";
import { uploadCourseDocument } from "@/lib/upload-course-document";
import { ArrowLeft, Check, Loader2, X } from "lucide-react";

type SourceDraft = {
  id: number;
  file: File;
  issue: string | null;
  status: "selected" | "uploading" | "uploaded" | "failed";
};

const maximumSourceBytes = 25 * 1024 * 1024;

function sourceIssue(file: File): string | null {
  const extension = file.name.split(".").pop()?.toLowerCase();
  if (!extension || !["pdf", "txt", "md"].includes(extension)) {
    return "Unsupported file type. Use PDF, TXT, or Markdown.";
  }
  if (file.size > maximumSourceBytes) return "This file is larger than 25 MB.";
  return null;
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function responseError(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") return body.detail;
  if (body && typeof body === "object" && "error" in body && typeof body.error === "string") return body.error;
  return fallback;
}

export default function NewCoursePage() {
  const router = useRouter();
  const { data: session } = useSession();
  const name = session?.user?.name?.trim() || session?.user?.email?.split("@")[0] || "Learner";
  const fileInputRef = useRef<HTMLInputElement>(null);
  const nextSourceId = useRef(0);

  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [startingConfidence, setStartingConfidence] = useState("3");
  const [sources, setSources] = useState<SourceDraft[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [createdCourseId, setCreatedCourseId] = useState<string | null>(null);

  const addSources = (files: FileList | File[]) => {
    const incoming = Array.from(files).map((file): SourceDraft => ({
      id: nextSourceId.current++,
      file,
      issue: sourceIssue(file),
      status: "selected",
    }));
    setSources((current) => [...current, ...incoming]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleFileSelection = (event: ChangeEvent<HTMLInputElement>) => {
    if (event.target.files?.length) addSources(event.target.files);
  };

  const handleDrop = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault();
    setIsDragging(false);
    if (event.dataTransfer.files.length) addSources(event.dataTransfer.files);
  };

  const hasInvalidSource = sources.some((source) => source.issue !== null);
  const titleReady = title.trim().length > 0;
  const selectedCount = sources.filter((source) => !source.issue).length;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!titleReady || hasInvalidSource || isLoading || createdCourseId) return;

    setIsLoading(true);
    setError("");
    try {
      const coursePayload: components["schemas"]["CourseCreate"] = {
        title: title.trim(),
        goal: goal.trim() || null,
        starting_confidence: Number(startingConfidence),
      };
      const response = await fetch("/api/v1/courses", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(coursePayload),
      });
      if (!response.ok) {
        const body: unknown = await response.json().catch(() => null);
        throw new Error(responseError(body, "The course could not be created. Try again."));
      }

      const course: components["schemas"]["CourseOut"] = await response.json();
      setCreatedCourseId(course.id);
      for (const source of sources) {
        if (source.issue) continue;
        setSources((current) => current.map((item) => item.id === source.id ? { ...item, status: "uploading" } : item));
        try {
          await uploadCourseDocument(course.id, source.file);
          setSources((current) => current.map((item) => item.id === source.id ? { ...item, status: "uploaded" } : item));
        } catch (cause) {
          setSources((current) => current.map((item) => item.id === source.id ? { ...item, status: "failed" } : item));
          throw cause;
        }
      }

      const hasUploadedSources = sources.some((source) => !source.issue);
      router.push(`/courses/${course.id}/workspace${hasUploadedSources ? "?startProcessing=1" : ""}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The course could not be created. Try again.");
    } finally {
      setIsLoading(false);
    }
  };

  const checklist = [
    [titleReady, titleReady ? "Course name added" : "Add a course name"],
    [goal.trim().length > 0, goal.trim() ? "Learning goal added" : "Learning goal is optional"],
    [selectedCount > 0, selectedCount > 0 ? `${selectedCount} source${selectedCount === 1 ? "" : "s"} selected` : "Sources can be added here or next"],
    [sources.length === 0 || !hasInvalidSource, hasInvalidSource ? "Remove or replace unsupported sources" : "Selected files are within supported limits"],
  ] as const;

  return <div className="nl-course-setup-shell">
    <CourseSidebar name={name} />
    <main className="nl-course-setup-main">
      <Link href="/dashboard" className="nl-course-setup-back"><ArrowLeft aria-hidden="true" />Back to dashboard</Link>
      <h1 className="nl-course-setup-title">Create course</h1>
      <p className="nl-course-setup-lead">Add your own material and say what you want to learn. The course is built from these sources only.</p>

      <div className="nl-course-setup-grid">
        <form id="course-setup-form" className="nl-course-setup-form" onSubmit={handleSubmit}>
          <section className="nl-course-setup-block">
            <label className="nl-course-setup-label" htmlFor="course-title">Course name <span aria-hidden="true">*</span></label>
            <input
              id="course-title"
              type="text"
              autoComplete="off"
              placeholder="For example, Distributed Systems"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              className="nl-course-setup-field"
              required
              disabled={isLoading || Boolean(createdCourseId)}
            />
          </section>

          <section className="nl-course-setup-block">
            <label className="nl-course-setup-label" htmlFor="course-goal">What do you want to be able to do?</label>
            <p className="nl-course-setup-hint">One or two sentences. The course and your practice are shaped around this.</p>
            <textarea
              id="course-goal"
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              className="nl-course-setup-field nl-course-setup-goal"
              placeholder="Describe what you want to understand, explain, or do."
              disabled={isLoading || Boolean(createdCourseId)}
            />
          </section>

          <section className="nl-course-setup-block">
            <div className="nl-course-setup-label" id="source-label">Sources</div>
            <p className="nl-course-setup-hint">PDF, TXT, or Markdown, up to 25 MB each. You can add more sources in the course workspace.</p>
            <input
              ref={fileInputRef}
              type="file"
              className="sr-only"
              accept=".pdf,.txt,.md"
              multiple
              aria-labelledby="source-label"
              onChange={handleFileSelection}
              disabled={isLoading || Boolean(createdCourseId)}
            />
            <button
              type="button"
              className={`nl-course-setup-drop${isDragging ? " is-over" : ""}`}
              aria-label="Drop files here or choose files"
              aria-describedby="source-help"
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(event) => { event.preventDefault(); setIsDragging(true); }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              disabled={isLoading || Boolean(createdCourseId)}
            >
              <strong>Drop files here or choose files</strong>
              <span id="source-help">Files are checked as soon as you add them</span>
            </button>

            {sources.length > 0 && <ul className="nl-course-setup-files" aria-label="Selected source files">
              {sources.map((source) => {
                const extension = source.file.name.split(".").pop()?.toUpperCase() || "FILE";
                return <li key={source.id} className={`nl-course-setup-file${source.issue ? " is-invalid" : ""}`}>
                  <span className="nl-course-setup-filetype" aria-hidden="true">{extension.slice(0, 4)}</span>
                  <span>
                    <strong className="nl-course-setup-filename">{source.file.name}</strong>
                    <small className="nl-course-setup-filesub">{source.issue || formatSize(source.file.size)}</small>
                  </span>
                  <span className={`nl-course-setup-chip${source.issue || source.status === "failed" ? " is-invalid" : source.status === "uploaded" ? " is-uploaded" : " is-selected"}`}>
                    {source.issue ? "Can’t upload" : source.status === "failed" ? "Upload failed" : source.status === "uploaded" ? "Uploaded" : source.status === "uploading" ? "Uploading…" : "Selected"}
                  </span>
                  {!isLoading && !createdCourseId && <button type="button" className="nl-course-setup-small-button" onClick={() => setSources((current) => current.filter((item) => item.id !== source.id))} aria-label={`Remove ${source.file.name}`}><X className="size-4" /><span className="sr-only">Remove</span></button>}
                </li>;
              })}
            </ul>}
          </section>

          <section className="nl-course-setup-block">
            <details className="nl-course-setup-confidence">
              <summary><span className="nl-course-setup-caret" aria-hidden="true" /><span className="nl-course-setup-label">Starting confidence <span className="nl-muted">Optional</span></span></summary>
              <div className="nl-course-setup-confidence-inner">
                <span>Beginner</span>
                <input aria-label="Starting confidence level" type="range" min="1" max="5" step="1" value={startingConfidence} onChange={(event) => setStartingConfidence(event.target.value)} disabled={isLoading || Boolean(createdCourseId)} />
                <span>Expert</span>
              </div>
            </details>
          </section>
        </form>

        <aside className="nl-course-setup-review" aria-live="polite">
          <h2>Before you prepare</h2>
          <ul className="nl-course-setup-checklist">
            {checklist.map(([complete, label]) => <li key={label} className={`nl-course-setup-check${complete ? " is-complete" : ""}`}>
              <i aria-hidden="true">{complete ? <Check className="size-3" /> : null}</i>{label}
            </li>)}
          </ul>

          {error && <p className="nl-course-setup-error" role="alert">{error}</p>}
          {createdCourseId ? <Link href={`/courses/${createdCourseId}/workspace`} className="nl-button nl-button-primary nl-button-large nl-course-setup-submit">Continue in workspace</Link> : <button type="submit" form="course-setup-form" className="nl-button nl-button-primary nl-button-large nl-course-setup-submit" disabled={isLoading || !titleReady || hasInvalidSource}>
            {isLoading ? <><Loader2 className="size-5 animate-spin" aria-hidden="true" />{sources.length > 0 ? "Creating and uploading…" : "Creating…"}</> : "Create course"}
          </button>}
          <p className="nl-course-setup-review-note">You can leave the workspace while course preparation runs. The outline will be ready for your review before publishing.</p>
        </aside>
      </div>
    </main>
  </div>;
}
