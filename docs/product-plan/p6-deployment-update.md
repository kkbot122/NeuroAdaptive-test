# Deploying the workspace and AI accounting update

Deployment source branch: `feat/p0-deployed-foundation`.

## Railway API and worker

- Keep the API pre-deploy command `alembic upgrade head`. The new ledger migration is
  `9fd2c74a6e11`, following `f5a1c9d2e7b4`. It adds the provider-call table without
  changing existing learning records. Deploy the API/migration successfully before
  starting the updated worker; the new worker needs the ledger table.
- Both services must use the same PostgreSQL database and the exact same
  `CELERY_BROKER_URL`, including its Redis database number. Shared AI admission and
  capacity use that broker's Redis database. Keep Redis reachable through Railway's
  private network. `CELERY_RESULT_BACKEND` retains its existing role.
- `AI_SHARED_LIMITS_ENABLED=true` is the production default. Coordination failure
  blocks new AI calls rather than multiplying limits across processes.
- Existing `GEMINI_API_KEY`, `INTERNAL_API_KEY`, storage, and auth settings remain the
  service configuration. No new API credentials are required.
- New operational defaults are built into the code; copy them from `backend/.env.example`
  only when explicit environment settings are preferred. Keep API and worker settings
  consistent, particularly provider/worker concurrency and retry limits.

| Setting | Default |
| --- | --- |
| `GEMINI_GENERATION_TIMEOUT_SECONDS_V1` | `45` (accepted range: 1–90) |
| `GEMINI_EMBEDDING_TIMEOUT_SECONDS_V1` | `30` |
| `GEMINI_MAX_RETRIES_V1` | `1` |
| `GEMINI_RETRY_BACKOFF_SECONDS_V1` | `2` |
| `AI_VALIDATION_CONCURRENCY_V1` | `2` |
| `AI_PROVIDER_MAX_CONCURRENT_V1` | `4` |
| `AI_WORKER_MAX_CONCURRENT_V1` | `2`; must be below the provider maximum |
| `AI_PROVIDER_MAX_CONCURRENT_PER_USER_V1` | `4` |
| `AI_PROVIDER_SLOT_WAIT_SECONDS_V1` | `2` |
| `AI_WORKER_SLOT_WAIT_SECONDS_V1` | `60` |
| `AI_INTERACTIVE_DEADLINE_SECONDS_V1` | `120` |
| `AI_ACCOUNTING_RESERVATION_TIMEOUT_SECONDS_V1` | `3` |

Leave `AI_BUDGET_EXEMPT_EMAILS` empty for normal production accounts unless an explicit
developer exemption is intended. The generation allowance remains 200 attempts per UTC
day; provider retries now count individually. Embedding attempts and returned token
usage are recorded separately. `GET /api/v1/ai/usage` provides an authenticated usage
view; old reservations without ledger rows remain explicitly unitemized.

Railway's pre-deploy command runs before its service starts and prevents that service
deploying if the command fails. See [Railway pre-deploy documentation](https://docs.railway.com/deployments/pre-deploy-command).

## Vercel

No new environment variable is required. Keep root directory `frontend`, the existing
build command, public Railway `INTERNAL_API_URL`, matching `INTERNAL_API_KEY`, and the
existing NextAuth/Google OAuth configuration.

Use a current Node.js runtime: the new test dependency requires Node 20.19+,
Node 22.13+, or Node 24+. Local verification used Node 24; the existing Node 20
Docker/CI targets are compatible when using a current minor release.

The `/api/v1/[...path]` proxy must be allowed to run longer than the API's 120-second
interactive deadline plus network overhead: use at least 180 seconds. With Fluid
compute, Vercel's documented default duration is 300 seconds, which covers the default
backend deadline. Check any project-specific function timeout override; increase it
or enable Fluid compute if it is shorter. If the backend deadline is customized,
adjust the proxy allowance accordingly. See [Vercel function-duration documentation](https://vercel.com/docs/functions/configuring-functions/duration).

Frontend changes require a fresh Vercel deployment. API and worker changes require fresh
Railway deployments. Repository changes do not modify hosted dashboard settings.
