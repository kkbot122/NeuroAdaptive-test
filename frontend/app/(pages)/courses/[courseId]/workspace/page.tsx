"use client";

import { useState, useEffect, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { StateWrapper } from "@/components/StateWrapper";
import { Brain, ArrowLeft, Upload, File, Loader2, CheckCircle, RefreshCcw, Pencil, Check, X } from "lucide-react";

export default function WorkspacePage() {
  const params = useParams();
  const courseId = params.courseId as string;

  const [activeTab, setActiveTab] = useState<"upload" | "outline" | "diagnostic">("upload");
  
  // States for StateWrapper
  const [isLoading, setIsLoading] = useState(true);
  const [isError, setIsError] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  const [course, setCourse] = useState<{ title?: string } | null>(null);
  const [documents, setDocuments] = useState<{ filename: string }[]>([]);
  const [job, setJob] = useState<{ id: string; status: string; current_stage?: string } | null>(null);

  // Matches curriculum/router.py's _version_out() exactly: nested
  // modules[].lessons[], and a lesson's concepts are {concept_id, role,
  // weight} -- no concept name. Names come from the separate graph
  // endpoint, joined in below via conceptNames.
  interface LessonOut {
    id: string;
    title: string;
    objective: string | null;
    concepts: { concept_id: string; role: string; weight: number }[];
  }
  interface ModuleOut {
    id: string;
    title: string;
    lessons: LessonOut[];
  }
  const [structure, setStructure] = useState<{ modules: ModuleOut[] } | null>(null);
  const [conceptNames, setConceptNames] = useState<Record<string, string>>({});

  // Lesson renaming: the only edit PUT /courses/{id}/structure supports
  // this phase (curriculum/router.py's StructureUpdateIn docstring --
  // dropping a concept or reordering a module is a documented, deferred
  // gap, not something to fake a control for here).
  const [editingLessonId, setEditingLessonId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [isSavingRename, setIsSavingRename] = useState(false);

  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchCourseData = async () => {
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
      setCourse(await courseRes.json());

      const docsRes = await fetch(`/api/v1/courses/${courseId}/documents`);
      if (docsRes.ok) {
        setDocuments(await docsRes.json());
      }
    } catch (err: unknown) {
      console.error(err);
      setIsError(true);
      setErrorMsg(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (courseId) fetchCourseData();
  }, [courseId]);

  // Upload Document
  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0) return;
    const file = e.target.files[0];
    
    setUploading(true);
    const formData = new FormData();
    formData.append("file", file);
    
    try {
      const res = await fetch(`/api/v1/courses/${courseId}/documents`, {
        method: "POST",
        body: formData,
      });
      if (!res.ok) throw new Error("Upload failed");
      
      const newDocs = await fetch(`/api/v1/courses/${courseId}/documents`).then(r => r.json());
      setDocuments(newDocs);
    } catch (err) {
      console.error(err);
      alert("Failed to upload document");
    } finally {
      setUploading(false);
    }
  };

  // Generate Curriculum
  const handleGenerate = async () => {
    try {
      const res = await fetch(`/api/v1/courses/${courseId}/process`, {
        method: "POST"
      });
      if (!res.ok) throw new Error("Failed to start processing");
      const jobData = await res.json();
      setJob(jobData);
      pollJob(jobData.id);
    } catch (err) {
      console.error(err);
      alert("Failed to start generation");
    }
  };

  const pollJob = async (jobId: string) => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/v1/jobs/${jobId}`);
        if (!res.ok) throw new Error("Failed to fetch job status");
        const jobData = await res.json();
        setJob(jobData);
        
        // Backend job statuses are READY/FAILED/PAUSED (uppercase --
        // app/modules/jobs/models.py's JobStatus enum), not "completed"/
        // "failed": this comparison never matched, so the interval never
        // cleared and the UI never advanced past step 1 even once the job
        // had actually finished.
        if (jobData.status === "READY") {
          clearInterval(interval);
          fetchStructure();
        } else if (jobData.status === "FAILED" || jobData.status === "PAUSED") {
          clearInterval(interval);
        }
      } catch (err) {
        console.error(err);
      }
    }, 2000);
  };

  // Fetch Structure for Review. The structure response has no concept
  // names (curriculum/router.py's _version_out only returns concept_id per
  // lesson) -- the graph endpoint is fetched alongside it to build the
  // concept_id -> name map the outline needs to display anything readable.
  const fetchStructure = async () => {
    try {
      const [structureRes, graphRes] = await Promise.all([
        fetch(`/api/v1/courses/${courseId}/structure`),
        fetch(`/api/v1/courses/${courseId}/graph`),
      ]);
      if (structureRes.ok) {
        setStructure(await structureRes.json());
        setActiveTab("outline");
      }
      if (graphRes.ok) {
        const graph = await graphRes.json();
        const names: Record<string, string> = {};
        for (const c of graph.concepts || []) names[c.id] = c.name;
        setConceptNames(names);
      }
    } catch (err) {
      console.error(err);
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
      alert("Failed to publish outline");
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
      alert("Failed to rename lesson");
    } finally {
      setIsSavingRename(false);
    }
  };

  const renderUploadTab = () => (
    <div className="space-y-6">
      <div className="bg-white border-2 border-black rounded-xl p-8 text-center shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
        <Upload className="w-12 h-12 text-purple-600 mx-auto mb-4" />
        <h3 className="text-2xl font-bold mb-2">Upload Source Material</h3>
        <p className="text-gray-600 mb-6">PDFs, MDs, or TXT files that you want to learn from.</p>
        
        <input 
          type="file" 
          ref={fileInputRef}
          onChange={handleUpload}
          className="hidden" 
          accept=".pdf,.md,.txt"
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
          className="bg-[#FF9F1C] hover:bg-[#ff8c00] border-2 border-black px-6 py-3 rounded-lg font-bold shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-all active:translate-x-1 active:translate-y-1 active:shadow-none disabled:opacity-50"
        >
          {uploading ? "Uploading..." : "Select File"}
        </button>
      </div>

      {documents.length > 0 && (
        <div className="bg-white border-2 border-black rounded-xl p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
          <h3 className="font-bold text-xl mb-4">Uploaded Documents</h3>
          <ul className="space-y-3">
            {documents.map((doc, idx) => (
              <li key={idx} className="flex items-center gap-3 p-3 bg-gray-50 border-2 border-gray-200 rounded-lg">
                <File className="w-5 h-5 text-blue-500" />
                <span className="font-medium text-gray-800">{doc.filename}</span>
              </li>
            ))}
          </ul>
          
          <button
            onClick={handleGenerate}
            disabled={!!(job && job.status === "RUNNING")}
            className="w-full mt-6 flex items-center justify-center gap-2 bg-purple-600 text-white hover:bg-purple-700 border-2 border-black px-6 py-3 rounded-lg font-bold shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-all active:translate-x-1 active:translate-y-1 active:shadow-none disabled:opacity-50"
          >
            {(job && job.status === "RUNNING") ? (
              <><Loader2 className="w-5 h-5 animate-spin" /> Processing ({job.current_stage})...</>
            ) : "Generate Curriculum"}
          </button>
        </div>
      )}
    </div>
  );

  const renderOutlineTab = () => (
    <div className="bg-white border-2 border-black rounded-xl p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
      <h3 className="text-2xl font-bold mb-2">Review Course Outline</h3>
      <p className="text-gray-600 mb-6">Here is the generated structure. Please review before publishing.</p>
      
      {structure ? (
        <div className="space-y-6 mb-8">
          {structure.modules?.map((module) => (
            <div key={module.id}>
              <h3 className="font-bold text-xl mb-3">{module.title}</h3>
              <div className="space-y-4">
                {module.lessons.map((lesson, i) => (
                  <div key={lesson.id} className="p-4 bg-gray-50 border-2 border-black rounded-lg">
                    {editingLessonId === lesson.id ? (
                      <div className="flex items-center gap-2 mb-2">
                        <span className="font-bold text-lg">Lesson {i + 1}:</span>
                        <input
                          type="text"
                          value={editingTitle}
                          onChange={(e) => setEditingTitle(e.target.value)}
                          autoFocus
                          className="flex-1 border-2 border-black rounded px-2 py-1 font-bold"
                        />
                        <button
                          onClick={() => saveRename(lesson.id)}
                          disabled={isSavingRename}
                          className="p-1.5 bg-green-500 hover:bg-green-600 border-2 border-black rounded disabled:opacity-50"
                        >
                          <Check className="w-4 h-4 text-white" />
                        </button>
                        <button
                          onClick={cancelRenaming}
                          disabled={isSavingRename}
                          className="p-1.5 bg-gray-300 hover:bg-gray-400 border-2 border-black rounded"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2 mb-2 group">
                        <h4 className="font-bold text-lg">Lesson {i + 1}: {lesson.title}</h4>
                        <button
                          onClick={() => startRenaming(lesson.id, lesson.title)}
                          className="p-1 opacity-0 group-hover:opacity-100 hover:bg-gray-200 rounded transition-opacity"
                          title="Rename lesson"
                        >
                          <Pencil className="w-4 h-4 text-gray-500" />
                        </button>
                      </div>
                    )}
                    <ul className="list-disc pl-5 space-y-1 text-gray-700 font-medium">
                      {lesson.concepts.map((c) => (
                        <li key={c.concept_id}>{conceptNames[c.concept_id] || c.concept_id}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center p-8 text-gray-500">
          <RefreshCcw className="w-8 h-8 animate-spin mx-auto mb-2" />
          <p>Loading structure...</p>
        </div>
      )}

      <button
        onClick={handlePublishStructure}
        className="w-full bg-green-500 text-white hover:bg-green-600 border-2 border-black px-6 py-3 rounded-lg font-bold shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-all active:translate-x-1 active:translate-y-1 active:shadow-none"
      >
        Confirm & Publish Outline
      </button>
    </div>
  );

  const renderDiagnosticTab = () => (
    <div className="bg-white border-2 border-black rounded-xl p-8 text-center shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
      <CheckCircle className="w-16 h-16 text-green-500 mx-auto mb-4" />
      <h3 className="text-2xl font-bold mb-2">Course is Ready!</h3>
      <p className="text-gray-600 mb-8 max-w-md mx-auto">
        Your curriculum is generated. Take a quick diagnostic test so we can tailor the initial recommendations, or jump straight into learning.
      </p>
      
      <div className="flex gap-4 justify-center">
        <Link
          href={`/courses/${courseId}/assessment?type=diagnostic`}
          className="bg-[#FF9F1C] hover:bg-[#ff8c00] border-2 border-black px-6 py-3 rounded-lg font-bold shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-all active:translate-x-1 active:translate-y-1 active:shadow-none"
        >
          Take Diagnostic
        </Link>
        <Link
          href="/dashboard"
          className="bg-gray-100 hover:bg-gray-200 border-2 border-black px-6 py-3 rounded-lg font-bold transition-all"
        >
          Skip & Go to Dashboard
        </Link>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-[#F4F1EA] text-black font-[family-name:var(--font-kodchasan)] pb-28">
      <nav className="w-full bg-white border-b-2 border-black px-6 py-4 flex items-center gap-4 sticky top-0 z-50">
        <Link
          href="/dashboard"
          className="p-2 hover:bg-gray-100 rounded-full border-2 border-transparent hover:border-black transition-all"
        >
          <ArrowLeft className="w-5 h-5" />
        </Link>
        <div className="w-10 h-10 bg-purple-500 rounded-lg border-2 border-black flex items-center justify-center shadow-[3px_3px_0px_0px_rgba(0,0,0,1)]">
          <Brain className="w-6 h-6 text-white" strokeWidth={2.5} />
        </div>
        <span className="text-xl font-bold tracking-tight truncate max-w-[300px]">
          {course ? course.title : "Workspace"}
        </span>
      </nav>

      <main className="max-w-3xl mx-auto px-6 py-10">
        <StateWrapper
          isLoading={isLoading}
          isError={isError}
          errorMessage={errorMsg}
          isUnauthorized={errorMsg.includes("Unauthorized")}
          isEmpty={false}
          onRetry={fetchCourseData}
        >
          <div className="flex justify-center mb-8 gap-4 overflow-x-auto pb-4">
            <button 
              onClick={() => setActiveTab("upload")} 
              className={`px-4 py-2 font-bold rounded-lg border-2 border-black ${activeTab === "upload" ? "bg-purple-200 shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]" : "bg-white"}`}
            >
              1. Upload
            </button>
            <button
              onClick={() => { if(job?.status === "READY") { fetchStructure(); setActiveTab("outline"); } }}
              disabled={job?.status !== "READY" && activeTab !== "outline"}
              className={`px-4 py-2 font-bold rounded-lg border-2 border-black ${activeTab === "outline" ? "bg-purple-200 shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]" : "bg-white disabled:opacity-50"}`}
            >
              2. Review Outline
            </button>
            <button
              onClick={() => setActiveTab("diagnostic")}
              disabled={activeTab !== "diagnostic" && (!structure || job?.status !== "READY")}
              className={`px-4 py-2 font-bold rounded-lg border-2 border-black ${activeTab === "diagnostic" ? "bg-purple-200 shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]" : "bg-white disabled:opacity-50"}`}
            >
              3. Diagnostic
            </button>
          </div>

          {activeTab === "upload" && renderUploadTab()}
          {activeTab === "outline" && renderOutlineTab()}
          {activeTab === "diagnostic" && renderDiagnosticTab()}
        </StateWrapper>
      </main>
    </div>
  );
}
