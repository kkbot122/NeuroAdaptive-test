import assert from "node:assert/strict";
import test from "node:test";
import { sendLearningEvents } from "../lib/telemetry";

const originalWindow = globalThis.window;
const originalFetch = globalThis.fetch;

function setConsent(value: string | null) {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      localStorage: {
        getItem: () => value,
        setItem: () => undefined,
      },
    },
  });
}

test("minimal consent stops optional frontend events without a request", async () => {
  setConsent("minimal");
  const calls: string[] = [];
  globalThis.fetch = async (input) => {
    calls.push(String(input));
    return new Response("{}", { status: 202 });
  };

  assert.equal(await sendLearningEvents([{ event_type: "paragraph_view", seconds: 5 }]), true);
  assert.deepEqual(calls, []);
});

test("unavailable consent lookup fails closed before optional collection", async () => {
  setConsent(null);
  const calls: string[] = [];
  globalThis.fetch = async (input) => {
    calls.push(String(input));
    return new Response("{}", { status: 503 });
  };

  assert.equal(await sendLearningEvents([{ event_type: "paragraph_view", seconds: 5 }]), true);
  assert.deepEqual(calls, ["/api/v1/me/settings"]);
});

test("full consent sends optional events", async () => {
  setConsent("full");
  const calls: string[] = [];
  globalThis.fetch = async (input) => {
    calls.push(String(input));
    return new Response("{}", { status: 202 });
  };

  assert.equal(await sendLearningEvents([{ event_type: "paragraph_view", seconds: 5 }]), true);
  assert.deepEqual(calls, ["/api/events"]);
});

test.after(() => {
  globalThis.fetch = originalFetch;
  if (originalWindow === undefined) delete (globalThis as { window?: Window }).window;
  else Object.defineProperty(globalThis, "window", { configurable: true, value: originalWindow });
});
