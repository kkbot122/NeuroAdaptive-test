/**
 * Client-side learning telemetry.
 *
 * Posts to this app's own /api/events route handler, which validates the
 * session server-side and forwards to the backend with the internal token.
 * The browser never holds a secret and never talks to FastAPI directly.
 *
 * Replaces three near-identical copies of a fetch that posted to a hardcoded
 * http://localhost:8000/api/v1/profile/pulse — an endpoint that did not exist,
 * with no auth headers, and which logged "✅ Pulse saved" on the 404 because
 * fetch does not reject on HTTP status.
 */
export type LearningDimension = "textual" | "visual" | "logic" | "structural";

export interface LearningEvent {
  event_type: string;
  dimension?: LearningDimension;
  seconds?: number;
  target_id?: string;
  payload?: Record<string, unknown>;
}

/**
 * Send one or more events. Returns whether they were accepted.
 *
 * Never throws: telemetry must not break the page it is measuring. But it does
 * report failure honestly rather than claiming success.
 */
export async function sendLearningEvents(
  events: LearningEvent[],
): Promise<boolean> {
  if (events.length === 0) return true;

  try {
    const response = await fetch("/api/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events }),
      // Let the browser finish this even if the user navigates away.
      keepalive: true,
    });

    if (!response.ok) {
      console.warn(
        `Telemetry rejected: ${response.status} ${response.statusText}`,
      );
      return false;
    }
    return true;
  } catch (error) {
    console.warn("Telemetry failed to send:", error);
    return false;
  }
}

export function sendLearningEvent(event: LearningEvent): Promise<boolean> {
  return sendLearningEvents([event]);
}
