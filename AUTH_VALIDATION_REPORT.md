# Authentication validation report

| Check | Result | Evidence |
|---|---:|---|
| Landing TypeScript + production build | PASS | `npm run build` in `tradexa-landing` |
| Dashboard TypeScript + production build | PASS | `npm run build:check` in `automation-hub-dashboard` |
| FastAPI import | PASS | Temporary Python 3.9 test environment |
| Auth boundary / legacy regression tests | PASS | `24 passed` — Supabase token/profile cache, cookie bridge, unverified access guard, CSRF origin guard, legacy auth/JWT/migration tests |
| SQL migration static review | PASS | Idempotent profile, audit, user-id/RLS migration files present |
| Docker Compose config | NOT RUN | Docker CLI is not installed on this Mac |
| Docker image/runtime health | NOT RUN | Docker CLI is not installed on this Mac |
| Live Supabase signup/OAuth/email reset | NOT RUN | Requires the production Supabase project, SMTP and OAuth credentials |

Run the commands in `DEPLOYMENT_VPS.md` after configuring Supabase on the VPS.
