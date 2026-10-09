import type { components } from "@/lib/generated/api";

type UploadIntent = components["schemas"]["UploadIntentOut"];
export type UploadMutation = Pick<components["schemas"]["DocumentMutationOut"], "source_changed" | "cleanup_pending"> & {
  replaced_filename: string | null;
  rebuild_job_id: string | null;
};

function problemMessage(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  return typeof detail === "string" ? detail : null;
}

export async function uploadCourseDocument(
  courseId: string,
  file: File,
  replacesDocumentId?: string,
  role = "STUDY",
): Promise<UploadMutation> {
  const bytes = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const checksum = Array.from(new Uint8Array(digest))
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  const intentResponse = await fetch(`/api/v1/courses/${courseId}/documents/upload-intents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      filename: file.name,
      size_bytes: file.size,
      checksum_sha256: checksum,
      content_type: file.type || null,
      role,
      replaces_document_id: replacesDocumentId,
    }),
  });

  if (intentResponse.ok) {
    const intent: UploadIntent = await intentResponse.json();
    const uploadResponse = await fetch(intent.upload_url, {
      method: "PUT",
      headers: intent.required_headers,
      body: file,
    });
    if (!uploadResponse.ok) throw new Error("Private storage rejected the upload.");
    const finalized = await fetch(`/api/v1/courses/${courseId}/documents/finalize/${intent.intent_id}`, {
      method: "POST",
    });
    if (!finalized.ok) {
      const body: unknown = await finalized.json().catch(() => null);
      throw new Error(problemMessage(body) || "The uploaded source could not be finalized.");
    }
    const result = await finalized.json() as Partial<UploadMutation>;
    return {
      source_changed: result.source_changed === true,
      replaced_filename: typeof result.replaced_filename === "string" ? result.replaced_filename : null,
      cleanup_pending: result.cleanup_pending === true,
      rebuild_job_id: typeof result.rebuild_job_id === "string" ? result.rebuild_job_id : null,
    };
  }

  const failure: unknown = await intentResponse.json().catch(() => null);
  const isLocalStorageFallback = intentResponse.status === 503
    && failure !== null
    && typeof failure === "object"
    && typeof (failure as { type?: unknown }).type === "string"
    && (failure as { type: string }).type.endsWith("/storage-not-configured");
  if (!isLocalStorageFallback) {
    throw new Error(problemMessage(failure) || "Private upload could not be authorized. Please retry.");
  }

  // Local installations without S3-compatible storage use the authenticated
  // multipart endpoint. This is the same fallback used by the course workspace.
  const formData = new FormData();
  formData.append("file", file);
  formData.append("role", role);
  if (replacesDocumentId) formData.append("replaces_document_id", replacesDocumentId);
  const legacyResponse = await fetch(`/api/v1/courses/${courseId}/documents`, {
    method: "POST",
    body: formData,
  });
  const legacyPayload: unknown = await legacyResponse.json().catch(() => null);
  if (!legacyResponse.ok) {
    const legacyFailure: unknown = legacyPayload;
    throw new Error(problemMessage(legacyFailure) || "The source could not be uploaded.");
  }
  const result = legacyPayload as Partial<UploadMutation> | null;
  return {
    source_changed: result?.source_changed === true,
    replaced_filename: typeof result?.replaced_filename === "string" ? result.replaced_filename : null,
    cleanup_pending: result?.cleanup_pending === true,
    rebuild_job_id: typeof result?.rebuild_job_id === "string" ? result.rebuild_job_id : null,
  };
}
