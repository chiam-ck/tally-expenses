# Tally — self-hosted expense & liquid-cash tracker

> *Your money, counted.* One private FastAPI service: a dashboard (liquid-cash hero,
> charts), balance capture, a natural-language transaction logger, multi-currency with
> live FX, recurring charges, history with filters, and account settings — backed by your
> own Postgres. A self-hosted alternative to a finance spreadsheet.

**Topology:** the app runs on one host (`app-host` in these docs) and reaches Postgres on
another (`db-host`) over a Tailscale tailnet via `DATABASE_URL`. Designed to live entirely
inside the tailnet (no public exposure) with a single-user login gate on top.

> **Note:** `seed.sql` ships **dummy sample data**, not real finances. Replace it with your own.

## Core model — stocks vs flows (do not violate)

- **`balances`** = *stocks*: how much money exists, captured by the user (one row per
  account per day). Source of truth for net cash.
- **`transactions`** = *flows*: where money went (categorization), append-only ledger.

The recurring poster and the manual logger write to **`transactions` only** — they never
touch `balances`. A recurring bill posting does not change a balance; the next snapshot
already reflects it. This avoids double-counting.

## Stack

Python 3.12 · FastAPI + Uvicorn · psycopg 3 (`ConnectionPool`, sync) · Jinja2 ·
APScheduler (started in the FastAPI lifespan) · httpx. Timezone **Asia/Singapore** for
all date logic. Money is `numeric(14,2)`, formatted to 2 dp.

## Layout

```
app/
  main.py        FastAPI app, routes, auth middleware, lifespan (pool + scheduler)
  auth.py        single-user login cookie + API_KEY gate for /api/*
  db.py          connection pool + query helpers (normalizes the DSN)
  queries.py     all SQL + dashboard metric computation
  jobs.py        recurring poster, monthly rollover, weekly digest, FX update
  fx.py          currency list, live FX fetch, NL currency detection
  llm.py         LLM parse (any OpenAI-compatible endpoint) + regex fallback
  charts.py      dependency-free server-rendered SVG (line/donut/bar)
  templates/     base, dashboard, history, settings, login + Balance/Log modals
  static/        style.css (dark theme)
tally_mcp/       optional MCP sidecar for AI agents (Dockerfile.mcp → mcp service)
schema.sql  seed.sql  requirements.txt  Dockerfile  docker-compose.yml  .env.example
```

**Docs:** [`ARCHITECTURE.md`](ARCHITECTURE.md) — system design, components, request
flows, trust boundaries · [`SEMANTIC_LAYER.md`](SEMANTIC_LAYER.md) — domain model for
AI agents · [`SPEC.md`](SPEC.md) — original product spec.

## Pages & API

**Balance** and **Log** are in-screen modals (popups) reachable from the topbar on every
page — not separate pages — so capturing a balance or logging a spend never navigates away
from the dashboard. On success they refresh so the dashboard/history update.

| Route | What |
|---|---|
| `GET /` | Dashboard: liquid-cash hero + trend line, spend-by-category donut, daily-spend bars, stat tiles (total/days/burn/projection), latest balances & category tables. Charts are dependency-free server-rendered SVG. Responsive (mobile + desktop) |
| `GET /history` | Browse history with **filters** (search note/id, account, category, flow, date range) and **pagination** (25/50/100 per page); plus a liquid-cash-by-snapshot-date table |
| `GET /settings` | Settings area (tabs). Redirects to **Accounts** |
| `GET /settings/accounts` | Account maintenance: add/edit/activate/deactivate. Delete is guarded — accounts referenced by transactions/balances/recurring can't be hard-deleted (deactivate instead) |
| `GET /settings/recurring` | Manage recurring charges (add/edit/delete/pause). **Monthly** (every month on the day) or **yearly** (once, in the chosen month). Day 30/31 falls back to month-end |
| `GET /settings/fx` | Exchange-rates: every currency's `to_sgd` + inverse, source, last-updated, and a **Refresh now** button |
| `GET /balance` | Redirects to `/?open=balance` — opens the **Balance** modal (kept for deep links / Home-Screen shortcuts) |
| `GET /log` | Redirects to `/?open=log` — opens the **Log** modal |
| `POST /api/balance` | `{rows:[{account_id,balance,currency?}]}` → upsert today's snapshot → `{ok,rows}` |
| `POST /api/txn` | `{account_id,category,amount,currency?,note?}` → append → `{ok,txn_id}` |
| `POST /api/parse` | `{text}` → `{amount,currency,category,account,note}`; OpenAI-compatible LLM with regex fallback, never 5xx. Auto-detects currency from the text (symbols/codes/words; SGD default) |
| `GET /api/dashboard` | JSON of all dashboard metrics (SGD-equivalent) |
| `GET /api/fx` | Current FX rates (`to_sgd` per currency) + supported currency list |
| `GET /api/reference` | Live `accounts` + `categories` + `currencies` (valid values for writes; for agents) |
| `GET /api/transactions` | Filtered txn list for any date range + `expense_total_sgd` (`date_from`/`date_to`/`account`/`category`/`flow`/`q`/`limit`); per-day spend, since the dashboard is MTD-only |
| `POST /api/jobs/{name}` | Manually trigger `recurring_poster` / `monthly_rollover` / `weekly_digest` / `fx_update` (ops/smoke) |

(Old `/fx` and `/recurring` redirect to their `/settings/...` homes.)

## Agent / MCP access

The same JSON API doubles as an integration surface for external AI agents:

- **Auth:** set `API_KEY` in `.env`, then send `Authorization: Bearer <key>` (or
  `X-API-Key: <key>`) on `/api/*` — no browser cookie needed. See **Security** below.
- **MCP server:** [`tally_mcp/`](tally_mcp/) wraps the endpoints as MCP tools
  (`get_dashboard`, `list_reference`, `list_transactions`, `get_fx_rates`, `parse_expense`,
  `log_transaction`, `log_from_text`, `set_balance`) for Claude Desktop / Claude Code / any
  MCP host. It runs as the `mcp` compose service (Streamable HTTP on `:9000/mcp`), guarded
  by `MCP_AUTH_TOKEN` — agents connect with `Authorization: Bearer <token>` and the sidecar
  reaches the app with `API_KEY` internally. See `tally_mcp/README.md` for client config.
- **Semantic layer:** [`SEMANTIC_LAYER.md`](SEMANTIC_LAYER.md) is the domain context an
  agent should read first — stocks vs flows, liquid cash, burn, FX, the write contracts,
  and guardrails.

```bash
# smoke-test the API key (app)
curl -H "Authorization: Bearer $API_KEY" http://localhost:8000/api/reference
```

## Scheduled jobs (Asia/Singapore)

- **`recurring_poster`** — daily **00:10**. Posts each due recurring template to
  `transactions`. Idempotent via the partial unique index `(source, txn_date) WHERE
  source LIKE 'recurring:%'` (`ON CONFLICT DO NOTHING`).
- **`monthly_rollover`** — **1st** of month **00:05**. Carries each account's latest
  balance forward to a 1st-of-month snapshot (`source='rollover'`), upsert do-nothing.
- **`weekly_digest`** — **Mon 07:00**. Emails the dashboard summary via the Resend API.
  No-op if `RESEND_API_KEY` is unset. Read-only.
- **`fx_update`** — daily **00:20** (and once on boot). Fetches live FX rates from
  `open.er-api.com` (keyless, base SGD) into the `fx_rates` table. Keeps the last/fallback
  rates on any failure — never blocks startup or the app.

## Currencies & FX

Supported: SGD, MYR, USD, EUR, GBP, JPY, CNY, HKD, TWD, THB, IDR, AUD, KRW, INR, PHP, VND.

- Transactions can be logged in **any** supported currency (SGD default). The NL parser
  auto-detects a currency from the text — symbols (`$`/`¥`/`€`/`£`/`฿`/`RM`…), ISO codes
  (`usd`, `myr`…), or words (`ringgit`, `baht`, `yen`, `euro`…).
- Rates are stored as `to_sgd` in `fx_rates` (created + seeded automatically at startup —
  no `schema.sql` change). Dashboard liquid cash and spend totals are shown **SGD-equivalent**
  using these rates; the category table shows the native-currency breakdown where it differs.
- `app_config.myr_to_sgd` remains the static fallback; once `fx_update` runs, the live
  MYR→SGD rate is used (so the headline liquid cash reflects market FX, not the fixed 0.30).

## LLM parsing (any OpenAI-compatible provider)

The natural-language logger calls a standard **OpenAI-style `POST /v1/chat/completions`**
endpoint — it is **not** tied to LiteLLM (the `LITELLM_*` env names are just historical).
Point `LITELLM_URL` / `LITELLM_KEY` / `LITELLM_MODEL` at whatever you like:

| Provider | `LITELLM_URL` | `LITELLM_MODEL` (example) |
|---|---|---|
| OpenAI | `https://api.openai.com/v1/chat/completions` | `gpt-4o-mini` |
| OpenRouter | `https://openrouter.ai/api/v1/chat/completions` | `openai/gpt-4o-mini` |
| Groq | `https://api.groq.com/openai/v1/chat/completions` | `llama-3.3-70b-versatile` |
| Together | `https://api.together.xyz/v1/chat/completions` | `meta-llama/Llama-3.3-70B-Instruct-Turbo` |
| LiteLLM proxy | `http://<host>:4000/v1/chat/completions` | (whatever the proxy exposes) |
| Ollama (local, no key) | `http://<host>:11434/v1/chat/completions` | `llama3.1` |
| vLLM (self-hosted) | `http://<host>:8000/v1/chat/completions` | (served model id) |

The request uses `temperature:0` + `response_format:{type:"json_object"}`. If the endpoint
is unreachable or returns junk, parsing **falls back to a local regex** so `/log` never
blocks on the LLM. Leave `LITELLM_KEY` blank to skip the LLM entirely (regex only).

---

## Deploy

### 0. Prerequisite — Postgres on db-host

Create the DB/role and allow connections from app-host over the tailnet:

- `postgresql.conf`: `listen_addresses` includes the tailscale interface (or `*`).
- `pg_hba.conf`: a line permitting app-host's tailnet IP (or the `100.64.0.0/10`
  CGNAT range) to the `expenses` DB.

```sql
CREATE ROLE expenses LOGIN PASSWORD 'choose-a-strong-password';
CREATE DATABASE expenses OWNER expenses;
```

### 1. Configure env (on app-host)

```bash
cp .env.example .env
# edit .env: set DATABASE_URL to db-host's tailnet host, LITELLM_*, RESEND_*
```

`DATABASE_URL` must point at **db-host's tailnet host** (e.g.
`db-host.<tailnet>.ts.net:5432` or its `100.x` address) — never `localhost` /
`host.docker.internal` (Postgres is on a different machine).

### 2. Load schema + seed against db-host (run from app-host)

```bash
psql "$DATABASE_URL" -f schema.sql && psql "$DATABASE_URL" -f seed.sql
```

(If your `DATABASE_URL` carries a `+asyncpg` driver suffix for the app, strip it for
`psql`: use a plain `postgresql://...` DSN here.)

### 3. Run the app (on app-host)

```bash
docker compose up -d --build
```

`docker-compose.yml` runs **only the app** and uses `network_mode: host` so the container
reaches `*.ts.net` through app-host's tailscale interface. Uvicorn binds
`0.0.0.0:8000`.

```bash
docker compose logs -f app     # watch startup; confirms scheduler jobs registered
```

### 4. Access (already private to the tailnet)

Open from any tailnet device — **do NOT use `tailscale funnel`**:

```
http://app-host.<tailnet>.ts.net:8000/
# or the host's 100.x tailnet address:
http://100.x.y.z:8000/
```

**Point a phone at it:** connect the phone to the tailnet (Tailscale app), open the
app-host URL above, then **Share → Add to Home Screen** for an app-like icon.

**Optional HTTPS + clean hostname** (nice-to-have, not required):

```bash
tailscale serve https / http://localhost:8000
# → https://app-host.<tailnet>.ts.net/   (still tailnet-only; never `funnel`)
```

## Security

- **Login gate** (single-user). All pages and `/api/*` require a session except the
  login flow and `/static/*`. Unauthenticated HTML requests redirect to `/login`; API
  requests get `401`. Credentials come from env (`AUTH_USERNAME` / `AUTH_PASSWORD`); the
  session cookie is HMAC-signed with `SECRET_KEY` (HttpOnly, SameSite=Lax, 30-day). Sign
  out via the topbar **Sign out** link. Changing the password or `SECRET_KEY` invalidates
  existing sessions.
- **API key** (programmatic access). Set `API_KEY` in env to let non-browser clients
  (external AI agents, the [MCP server](tally_mcp/), scripts) reach `/api/*` without the
  login cookie — send it as `Authorization: Bearer <key>` or `X-API-Key: <key>`. Compared
  with constant-time HMAC. Blank `API_KEY` disables the bypass (cookie only). Browser pages
  still require the login cookie regardless.
- **MCP endpoint token** (agent surface). The optional `mcp` sidecar exposes the API as MCP
  tools on its own port (`:9000`). It authenticates to the app with `API_KEY` *internally*,
  and the endpoint itself is guarded by `MCP_AUTH_TOKEN` (Bearer), checked before any MCP
  traffic — so agents hold the MCP token and never see the API key. Two distinct secrets;
  rotate either via `.env` + `docker compose up -d`. Blank `MCP_AUTH_TOKEN` leaves the
  endpoint open (tailnet-only). Like the app, the sidecar is never Funnel-exposed.
- No public exposure (Serve, not Funnel). The tailnet is still the primary boundary; the
  login / API key / MCP token are layers on top.
- All SQL is parameterized (psycopg placeholders); no string interpolation into queries.
- `account_id` and `category` are validated against the DB on every write; unknown values
  are rejected with 400.
- `LITELLM_KEY` / `RESEND_API_KEY` live in env only.

## Acceptance (spec §13)

1. Seed loads clean; with latest snapshot 2026-06-03 the dashboard shows Net cash SGD
   **11,320.00**, MYR **5,150.00**, Combined **12,865.00** at the seed FX 0.30. (Once
   `fx_update` runs, Combined uses the live MYR→SGD rate instead of 0.30.) *(`seed.sql`
   is dummy sample data, not real finances.)*
2. Saving the balance form twice in one day → exactly one row per account per day (upsert).
3. `grab to office 12` → Transport / CASH_SGD / 12, appended to `transactions`.
4. Recurring poster posts exactly one row per due template per day; re-running posts nothing.
5. Avg daily burn uses discretionary categories only; projection = booked MTD + burn ×
   remaining days.
6. With the LLM endpoint down, `/log` still parses (regex) and logs.
7. Reachable from a tailnet device at app-host's address; not reachable publicly.

## License

[MIT](LICENSE) © 2026 Ck Chiam
