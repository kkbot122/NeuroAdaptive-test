"use server";

import { auth } from "@/auth";
import { requireInternalToken } from "@/lib/internal-auth";

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000";



// ─────────────────────────────────────────────────────────────────────────────
// Auth helper
// ─────────────────────────────────────────────────────────────────────────────

async function getAuthHeaders() {
  const session = await auth();
  if (!session?.user?.email) throw new Error("Unauthorized: No active session.");
  return {
    "Content-Type": "application/json",
    "x-user-email": session.user.email,
    "x-internal-token": requireInternalToken(),
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Profile actions
// ─────────────────────────────────────────────────────────────────────────────

export async function getMyProfileAction() {
  const headers = await getAuthHeaders();
  const res = await fetch(`${BACKEND_URL}/api/v1/profile/me`, {
    method: "GET",
    headers,
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Failed to fetch profile: ${await res.text()}`);
  return res.json();
}

export async function submitCalibrationAction(dimension: string, points: number) {
  const headers = await getAuthHeaders();
  const res = await fetch(`${BACKEND_URL}/api/v1/profile/calibrate`, {
    method: "POST",
    headers,
    body: JSON.stringify({ paragraph_id: 0, dimension, points, calibration: true }),
  });
  if (!res.ok) throw new Error(`Failed to calibrate: ${await res.text()}`);
  return res.json();
}

// ─────────────────────────────────────────────────────────────────────────────
// FSLSM actions (Phase 4)
// ─────────────────────────────────────────────────────────────────────────────

export interface FSLSMVector {
  processing: number;
  perception: number;
  reception: number;
  understanding: number;
}

export interface FSLSMVectorResponse extends FSLSMVector {
  labels: Record<string, string>;
}

/** Fetch the current FSLSM vector for the logged-in user. */
export async function getFSLSMVectorAction(): Promise<FSLSMVectorResponse> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${BACKEND_URL}/api/v1/profile/fslsm`, {
    method: "GET",
    headers,
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Failed to fetch FSLSM vector: ${await res.text()}`);
  return res.json();
}

/** Apply delta nudges to the user's FSLSM vector. */
export async function nudgeFSLSMVectorAction(
  deltas: Partial<FSLSMVector>
): Promise<FSLSMVectorResponse> {
  const headers = await getAuthHeaders();
  const body: FSLSMVector = {
    processing: deltas.processing ?? 0,
    perception: deltas.perception ?? 0,
    reception: deltas.reception ?? 0,
    understanding: deltas.understanding ?? 0,
  };
  const res = await fetch(`${BACKEND_URL}/api/v1/profile/fslsm/nudge`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Failed to nudge FSLSM: ${await res.text()}`);
  return res.json();
}

/** Submit behavioral signal names to update FSLSM vector. */
export async function applyBehavioralSignalsAction(
  signals: string[]
): Promise<FSLSMVectorResponse> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${BACKEND_URL}/api/v1/profile/fslsm/signals`, {
    method: "POST",
    headers,
    body: JSON.stringify({ signals }),
  });
  if (!res.ok) throw new Error(`Failed to apply signals: ${await res.text()}`);
  return res.json();
}

/** Reset FSLSM vector to neutral (0.0). */
export async function resetFSLSMVectorAction(): Promise<FSLSMVectorResponse> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${BACKEND_URL}/api/v1/profile/fslsm/reset`, {
    method: "POST",
    headers,
  });
  if (!res.ok) throw new Error(`Failed to reset FSLSM: ${await res.text()}`);
  return res.json();
}
