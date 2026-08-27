/**
 * Returns the shared secret that proves a request to the FastAPI backend came
 * from this Next.js server and not from a browser.
 *
 * Throws when unset. That is deliberate: every previous call site fell back to
 * either a hardcoded literal ("dev_secret_key_123", which is in the git
 * history and therefore public) or to an empty string via `as string` / `|| ""`.
 * Both fail open — the request is sent anyway and the failure surfaces later,
 * somewhere else, as a confusing 403.
 *
 * SERVER USE ONLY. INTERNAL_API_KEY carries no NEXT_PUBLIC_ prefix, so Next.js
 * never inlines it into a client bundle; calling this from a client component
 * throws rather than leaking. Consider adding the `server-only` package to turn
 * that runtime failure into a build-time one.
 */
export function requireInternalToken(): string {
  const token = process.env.INTERNAL_API_KEY;

  if (!token) {
    throw new Error(
      "INTERNAL_API_KEY is not set. The backend will reject every request. " +
        "Set it in frontend/.env.local to the same value as backend/.env.",
    );
  }

  return token;
}

/**
 * Standard headers for a server-to-server call that acts on behalf of a user.
 * The backend derives identity from x-user-email and trusts it only because
 * x-internal-token proves the caller is this server.
 */
export function internalHeaders(userEmail: string): Record<string, string> {
  return {
    "x-user-email": userEmail,
    "x-internal-token": requireInternalToken(),
  };
}
