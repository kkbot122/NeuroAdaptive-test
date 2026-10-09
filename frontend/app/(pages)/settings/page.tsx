"use client";

import { useCallback, useEffect, useState } from "react";
import { signOut, useSession } from "next-auth/react";
import { useRouter } from "next/navigation";
import { CourseSidebar } from "@/components/CourseSidebar";
import type { components } from "@/lib/generated/api";

type Settings = components["schemas"]["SettingsOut"];

const DEFAULT_SETTINGS: Settings = {
  tracking_consent: "full",
  default_presentation_format: "detailed",
  tutor_panel_open: true,
};

const FORMAT_LABELS: Record<Settings["default_presentation_format"], string> = {
  detailed: "Detailed",
  concise: "Concise",
  worked_example: "Worked example",
  analogy: "Analogy",
};

function responseMessage(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== "object") return fallback;
  const body = payload as { detail?: unknown; title?: unknown };
  return typeof body.detail === "string" ? body.detail
    : typeof body.title === "string" ? body.title : fallback;
}

export default function SettingsPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const [draft, setDraft] = useState<Settings>(DEFAULT_SETTINGS);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState("");
  const [resetState, setResetState] = useState<"idle" | "resetting" | "reset" | "error">("idle");
  const [resetError, setResetError] = useState("");
  const [showDelete, setShowDelete] = useState(false);
  const [deleteText, setDeleteText] = useState("");
  const [deleteState, setDeleteState] = useState<"idle" | "deleting" | "error">("idle");
  const [deleteError, setDeleteError] = useState("");

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const response = await fetch("/api/v1/me/settings", { cache: "no-store" });
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok) throw new Error(responseMessage(payload, "Settings could not be loaded."));
      const current = payload as Settings;
      if (!FORMAT_LABELS[current.default_presentation_format]
        || !["full", "minimal"].includes(current.tracking_consent)
        || typeof current.tutor_panel_open !== "boolean") {
        throw new Error("Your saved settings could not be verified.");
      }
      setDraft(current);
      window.localStorage.setItem("neurolearn:tracking-consent", current.tracking_consent);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Settings could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (status === "unauthenticated") router.replace("/signin");
    if (status === "authenticated") void loadSettings();
  }, [status, router, loadSettings]);

  const save = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (saveState === "saving") return;
    setSaveState("saving");
    setSaveError("");
    try {
      const response = await fetch("/api/v1/me/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      });
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok) throw new Error(responseMessage(payload, "Your settings could not be saved. Try again."));
      const next = payload as Settings;
      setDraft(next);
      window.localStorage.setItem("neurolearn:tracking-consent", next.tracking_consent);
      setSaveState("saved");
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "Your settings could not be saved. Try again.");
      setSaveState("error");
    }
  };

  const resetPreferences = async () => {
    if (resetState === "resetting") return;
    setResetState("resetting");
    setResetError("");
    try {
      const response = await fetch("/api/v1/me/preferences/reset", { method: "POST" });
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok) throw new Error(responseMessage(payload, "Presentation preferences could not be reset."));
      const reset = payload as Pick<components["schemas"]["PreferencesResetOut"], "default_presentation_format" | "tutor_panel_open">;
      const next = { ...draft, ...reset };
      setDraft(next);
      setResetState("reset");
      setSaveState("idle");
      setSaveError("");
    } catch (error) {
      setResetError(error instanceof Error ? error.message : "Presentation preferences could not be reset.");
      setResetState("error");
    }
  };

  const cancelDelete = () => {
    setShowDelete(false);
    setDeleteText("");
    setDeleteError("");
    setDeleteState("idle");
  };

  const deleteAccount = async () => {
    if (deleteText !== "DELETE" || deleteState === "deleting") return;
    setDeleteState("deleting");
    setDeleteError("");
    let signInUrl = "/signin?account-deletion=complete";
    try {
      const response = await fetch("/api/v1/me", { method: "DELETE" });
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok) throw new Error(responseMessage(payload, "Your account could not be deleted. Try again."));
      const deletion = payload as Partial<components["schemas"]["AccountDeletionOut"]> | null;
      const pending = typeof deletion?.pending_storage_cleanups === "number"
        ? deletion.pending_storage_cleanups : deletion?.status === "deleted" ? 0 : 1;
      signInUrl = pending > 0
        ? "/signin?account-deletion=cleanup-pending"
        : "/signin?account-deletion=complete";

      for (const storage of [window.localStorage, window.sessionStorage]) {
        for (const key of Object.keys(storage)) {
          if (key.startsWith("neurolearn:")) storage.removeItem(key);
        }
      }
    } catch (error) {
      setDeleteState("error");
      setDeleteError(error instanceof Error ? error.message : "Your account could not be deleted. Try again.");
      return;
    }
    try {
      await signOut({ callbackUrl: signInUrl });
    } catch {
      window.location.assign(signInUrl);
    }
  };

  const userName = session?.user?.name?.trim() || session?.user?.email?.split("@")[0] || "Learner";
  if (status === "loading" || loading) return <main className="nl-screen grid place-items-center p-6"><p role="status">Loading your settings…</p></main>;
  if (!session) return <main className="nl-screen grid place-items-center p-6"><p role="status">Returning to sign in…</p></main>;

  return <div className="nl-dashboard-shell">
    <CourseSidebar name={userName} />
    <main className="nl-settings-main">
      <h1>Settings</h1>
      <p className="nl-settings-lead">Control what is tracked and how lessons look. Your courses are not affected by changes here.</p>

      {loadError && <section className="nl-settings-error" role="alert"><p>{loadError}</p><button type="button" className="nl-button" onClick={() => void loadSettings()}>Try again</button></section>}

      {!loadError && <form onSubmit={(event) => void save(event)}>
        <section className="nl-settings-set" aria-labelledby="settings-records-heading">
          <h2 id="settings-records-heading">Learning records</h2>
          <div className="nl-settings-row is-required">
            <div><b>Answers, progress, and mastery evidence</b><p>Required to keep learning working. Answers, lesson completion, resume position, mastery evidence, and learning decisions power your course and next activity. You cannot turn these off. They are removed if you delete your account.</p></div>
            <span className="nl-chip">Required</span>
          </div>
          <div className="nl-settings-row">
            <div><b>Reading time and interaction tracking</b><p>Optional analytics record time spent and interactions such as navigation events. Turning this off stops optional collection; reading completion and your saved resume position still work. Changing this setting keeps existing telemetry history.</p></div>
            <label className="nl-settings-switch"><input type="checkbox" checked={draft.tracking_consent === "full"} aria-label="Reading time and interaction tracking" onChange={(event) => { setDraft((current) => ({ ...current, tracking_consent: event.target.checked ? "full" : "minimal" })); setSaveState("idle"); }} /><span aria-hidden="true" /></label>
          </div>
        </section>

        <section className="nl-settings-set" aria-labelledby="settings-presentation-heading">
          <h2 id="settings-presentation-heading">Presentation</h2>
          <div className="nl-settings-row">
            <div><label htmlFor="default-format"><b>Default lesson format</b></label><p>The format new teaching activities open in. You can still switch while studying. This explicit default sets the initial view; adaptive activity selection keeps its current policy.</p></div>
            <select id="default-format" className="nl-settings-select" value={draft.default_presentation_format} onChange={(event) => { setDraft((current) => ({ ...current, default_presentation_format: event.target.value as Settings["default_presentation_format"] })); setSaveState("idle"); }}>
              {Object.entries(FORMAT_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
          <div className="nl-settings-row">
            <div><b>Open tutor panel by default</b><p>Show the tutor and source panel when a new teaching activity opens.</p></div>
            <label className="nl-settings-switch"><input type="checkbox" checked={draft.tutor_panel_open} aria-label="Open tutor panel by default" onChange={(event) => { setDraft((current) => ({ ...current, tutor_panel_open: event.target.checked })); setSaveState("idle"); }} /><span aria-hidden="true" /></label>
          </div>
          <div className="nl-settings-actions nl-settings-reset-row">
            <button type="button" className="nl-button" onClick={() => void resetPreferences()} disabled={resetState === "resetting"}>{resetState === "resetting" ? "Resetting…" : "Reset preferences"}</button>
            <span className="nl-settings-hint">Resets presentation preferences only. Tracking stays as set.</span>
          </div>
          {resetState === "reset" && <p className="nl-settings-notice" role="status">Presentation preferences reset to Detailed with the tutor panel open. Format affinity was cleared for future recommendations. Tracking, answers, progress, and mastery were not changed.</p>}
          {resetState === "error" && <p className="nl-settings-error-inline" role="alert">{resetError}</p>}
        </section>

        <div className="nl-settings-save-row">
          <button type="submit" className="nl-button nl-button-primary nl-button-large" disabled={saveState === "saving"}>{saveState === "saving" ? "Saving changes…" : "Save changes"}</button>
          <span aria-live="polite">{saveState === "saved" && <span className="nl-chip nl-chip-mint">Saved</span>}</span>
          {saveState === "error" && <p className="nl-settings-error-inline" role="alert">{saveError}</p>}
        </div>

        <section className="nl-settings-set nl-settings-danger" aria-labelledby="settings-account-heading">
          <h2 id="settings-account-heading">Account</h2>
          <div className="nl-settings-row">
            <div><b>Sign out</b><p>You will return to the sign-in page. Your work is saved.</p></div>
            <button type="button" className="nl-button" onClick={() => void signOut({ callbackUrl: "/signin" })}>Sign out</button>
          </div>
          <div className="nl-settings-row">
            <div><b>Delete account</b><p>Permanently deletes your courses, source files, answers, progress, mastery evidence, preparation records, grading and review records, tutor history, and preferences. Private originals and unfinished uploads are also scheduled for cleanup. This cannot be undone.</p></div>
            {!showDelete && <button type="button" className="nl-button nl-button-danger" onClick={() => { setShowDelete(true); setDeleteError(""); }}>Delete account</button>}
          </div>
          {showDelete && <div className="nl-settings-confirm">
            <label htmlFor="confirm-delete"><b>Type DELETE to confirm</b></label>
            <div className="nl-settings-confirm-controls">
              <input id="confirm-delete" className="nl-form-control" value={deleteText} onChange={(event) => setDeleteText(event.target.value)} autoComplete="off" />
              <button type="button" className="nl-button nl-button-danger" disabled={deleteText !== "DELETE" || deleteState === "deleting"} onClick={() => void deleteAccount()}>{deleteState === "deleting" ? "Deleting…" : "Delete everything"}</button>
              <button type="button" className="nl-button" disabled={deleteState === "deleting"} onClick={cancelDelete}>Cancel</button>
            </div>
            {deleteError && <p className="nl-settings-error-inline" role="alert">{deleteError}</p>}
          </div>}
        </section>
      </form>}
    </main>
  </div>;
}
