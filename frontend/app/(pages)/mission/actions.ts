"use server";

import { auth } from "@/auth";
import { requireInternalToken } from "@/lib/internal-auth";
import { revalidatePath } from "next/cache";

interface CalibrationPayload {
  accumulated_scores: Record<string, number>;
  ab_choice: "visual" | "logical" | "neutral";
}

export async function submitCalibration(payload: CalibrationPayload) {
  try {
    const session = await auth();
    if (!session?.user?.email) {
      return { success: false, error: "Unauthorized. Please log in." };
    }

    const apiUrl =
      process.env.INTERNAL_API_URL ||
      process.env.NEXT_PUBLIC_API_URL ||
      "http://backend:8000";

    const response = await fetch(`${apiUrl}/api/v1/profile/calibrate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-user-email": session.user.email,
        "x-internal-token": requireInternalToken(),
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error("Calibration failed:", response.status, errorText);
      return { success: false, error: "Failed to save profile." };
    }

    const data = await response.json();
    revalidatePath("/dashboard");
    return { success: true, archetype: data.archetype as string };
  } catch (error) {
    console.error("submitCalibration error:", error);
    return { success: false, error: "An unexpected error occurred." };
  }
}

export async function overrideUserArchetype(newArchetype: string) {
  try {
    const session = await auth();
    if (!session?.user?.email) return { success: false, error: "Unauthorized" };

    const apiUrl =
      process.env.INTERNAL_API_URL ||
      process.env.NEXT_PUBLIC_API_URL ||
      "http://backend:8000";

    const response = await fetch(`${apiUrl}/api/v1/profile/override`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "x-user-email": session.user.email,
        "x-internal-token": requireInternalToken(),
      },
      body: JSON.stringify({ primary_archetype: newArchetype }),
    });

    if (!response.ok) return { success: false, error: "Failed to override archetype." };

    revalidatePath("/dashboard");
    return { success: true };
  } catch (error) {
    return { success: false, error: "Network error." };
  }
}
