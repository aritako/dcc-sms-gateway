# Session 01 — Assignment breakdown and ticket setup

- **Date:** 2026-11-18
- **Agent/model:** opencode-go/deepseek-v4.1-flash (T3 Code / OpenCode)
- **Goal:** Read the DCC Mini SMS Gateway take-home, inspect the scaffold, turn the spec into tracer-bullet GitHub issues, and set up project agent docs and transcript logging.
- **Outcome:** 27 issues created (#1–#27, 22 Required + 5 Bonus) with domain/priority/difficulty labels and native blocked-by edges; `AGENTS.md`; transcript convention; first transcript.
- **Issues/commits:** [#1–#27](https://github.com/aritako/dcc-sms-gateway/issues); commit `docs: add agent guide and first session transcript` on `main`.

> This is a structured session log. User messages are reproduced; assistant messages and tool output are condensed where they were long. The issue bodies on GitHub are the full acceptance criteria, and the sub-agent findings are summarized in the appendices.

## Transcript

### 1. Initial request

**User**

> I am tasked with this assignment: [DCC_SMS_Gateway_Take_Home_Assignment-v2.docx.pdf] . I have already scaffolded the carrier and gateway services in this github repo: https://github.com/aritako/dcc-sms-gateway
>
> # Process
> GOAL: Organize each required task into workable tickets as a Github Issue on the same repo where a human or agent can pick it up and work on.
>
> Read the entire document spec first, then decide how we can break down a certain task for a particular domain in the spec.
>
> Break the work into **tracer bullet** tickets.
>
> <vertical-slice-rules>
> - Each slice cuts a narrow but COMPLETE path through every layer (schema, API, UI, tests): vertical, NOT a horizontal slice of one layer
> - A completed slice is demoable or verifiable on its own
> - Each slice is sized to fit in a single fresh context window
> - Any prefactoring should be done first
> </vertical-slice-rules>
>
> For the purposes of this assignment, triage tickets based on their importance: "Required", "Bonus", etc. And difficulty, a scale from 1 to 10. Use also the appropriate labels for each ticket, i.e. "Carrier", "Testing", etc.
>
> Use sub-agents whenever necessary.
>
> # Output
> Present the proposed breakdown as a numbered list. For each ticket, show:
> - Title: short descriptive name
> - Blocked by: which other tickets (if any) must complete first
> - What it delivers: the end-to-end behaviour this ticket makes work. mention specific API contracts, constraints, etc, as required by the spec.

The assignment PDF was attached. The harness could not pass PDFs directly to the model, and `pdftotext` was not installed, so I installed `pymupdf` in a temporary `/tmp/opencode/pdfvenv` and extracted all six pages to `/tmp/opencode/assignment.txt`.

### 2. Spec summary

The assignment is a mini CPaaS SMS gateway with two tenants: Acme Bank (low-volume, urgent OTPs) and ShopFast (campaigns up to 10,000 messages). Both share one carrier capped at 50 submits/second and pay per SMS segment from a prepaid balance.

The required parts:

1. **Mock carrier** — `POST /submit` returning `{carrier_id}`, configurable 20–300 ms latency, 50/s limit, 3% 503, 1% 400, 1% 5-second timeout that still accepts, signed DLRs 0.5–10 s later, 90/7/3 status mix, 10% duplicate DLRs, 5% early DLRs.
2. **Gateway** — API-key auth, tenant isolation, single and batch send, strict E.164, idempotency, GSM-7/UCS-2 detection, segment counting, prefix pricing, prepaid billing and ledger, Postgres/Redis queue, global rate limit, retries, timeout handling, fairness, crash safety, signed DLRs with monotonic statuses, webhooks.
3. **Dashboard** — message list with tenant/status/date filters, detail with event history, live updates, per-tenant balance and status counts, send form with live encoding/segment/price preview, loading/empty/error states.
4. **Load script** — 10,000 ShopFast messages plus one Acme OTP/second, reporting time, submit rate, OTP p50/p95, final-state counts and a billing reconciliation.
5. **Required tests** — encoding/segments, idempotency, early/duplicate DLR, concurrent overdraw, 429/503 vs 400, bad DLR signature, cross-tenant 404.
6. **Submission** — `DESIGN.md` answering four questions, README with load output, migrations/seed, assumptions/trade-offs, AI transcript, improvement notes.
7. **Bonus** — second carrier/failover, webhook HMAC, scheduled messages, Prometheus metrics, templates.

The evaluated weights: correctness under failure 25%, billing integrity 20%, throughput/fairness 15%, API/tenant isolation 10%, dashboard 10%, tests 10%, design/README 10%.

### 3. Scaffold inspection

I inspected the repo source excluding `.venv` and `.git`.

Existing at the start:

- `gateway/` — FastAPI hello-world; SQLAlchemy async engine; Redis client; Alembic initialized with an empty migration; placeholder `seed.py`; placeholder worker that logs every five seconds; Dockerfile without a `CMD`.
- `carrier/` — FastAPI hello-world only.
- No schema/models, no compose file, no tests, no frontend, no load script, no `DESIGN.md`, no API routes beyond `GET /`.

Key scaffold risks found:

- `Settings()` and the SQLAlchemy/Redis clients were constructed at import time, so tests and Alembic failed without live environment variables.
- Alembic read raw `os.environ["DATABASE_URL"]`, had `target_metadata = None`, and the only migration was empty.
- Both projects build the Python module `app`, so co-installing them in one venv can shadow one another.
- No test dependencies or pytest configuration.
- The root `.gitignore` had `pytest_cache/` instead of `.pytest_cache/`.

At the end of the session the user's working tree also had an untracked `docker-compose.yaml` and two feature-branch Dockerfile commits. Those were left untouched.

### 4. Independent decomposition

Two sub-agents ran in parallel:

1. A scaffold/gap audit that confirmed the missing schema, compose, tests, frontend and docs, and identified the import-time settings and Alembic issues.
2. An independent tracer-bullet decomposition. It proposed 19 required + 5 bonus tickets and highlighted several traps:
   - DLRs carry only `carrier_id`, not `message_id`; early DLRs need to be parked and reconciled.
   - HMAC must be over the raw request bytes.
   - GSM-7 extension characters count as two septets but remain GSM-7.
   - UCS-2 length is UTF-16 code units, so an emoji counts as two.
   - A capacity-50 token bucket can burst across the carrier's one-second boundary; use a sliding window with headroom.
   - Retries must also pass through the global limiter.
   - A retry must not move a message back from `SUBMITTED` to `QUEUED`.
   - Ambiguous timeouts need a separate refund policy.
   - Duplicate DLRs must not fan out a second webhook.
   - The dashboard is cross-tenant, so it needs a server-side admin seam.
   - `QUEUED` is easy to skip and must be recorded.
   - Migrations and seed must run before gateway/workers.
   - Tests need deterministic carrier controls, not random 3%/1% behavior.
   - The load script must include `FAILED` and refunds.
   - Strict E.164 and longest-prefix pricing must not guess.

I refined that draft into **22 Required + 5 Bonus** slices, splitting the carrier submit and DLR behavior, the timeout policy from the retry policy, and the dashboard into shell, detail/live, and send form.

### 5. Issue creation

Labels created:

- Priority: `Required`, `Bonus`
- Domains: `Infra`, `Testing`, `Gateway`, `Billing`, `Carrier`, `DLR`, `Dispatch`, `Security`, `API`, `Frontend`, `Realtime`, `Docs`, `Observability`, `Scheduling`, `Webhooks`, `Load`
- Difficulty: `Difficulty: 3` through `Difficulty: 9`

Issues were created sequentially, so the plan number matches the GitHub issue number. Native blocked-by edges were added with `gh issue edit --add-blocked-by` — 75 edges total (one added while testing the command, 74 in the bulk pass).

| # | Title | Priority | Difficulty | Blocked by |
|---|---|---|---|---|
| 1 | Walking skeleton: config, dev stack, health and test harness | Required | 4 | — |
| 2 | Domain schema, constraints, migrations and idempotent seed | Required | 6 | #1 |
| 3 | Mock carrier: /submit contract with deterministic fault injection | Required | 6 | #1 |
| 4 | Mock carrier: signed, delayed, duplicated and early DLRs | Required | 5 | #3 |
| 5 | Encoding, segmentation, pricing and quote endpoint | Required | 5 | #2 |
| 6 | Send one message: auth, validation, atomic charge and enqueue | Required | 7 | #2, #5 |
| 7 | Idempotency-Key for message sends | Required | 6 | #6 |
| 8 | Batch send up to 1,000 messages | Required | 6 | #6, #7 |
| 9 | Read APIs: list, detail, ledger, stats and tenant isolation | Required | 6 | #2, #6, #8 |
| 10 | Worker dispatch: claim, submit, SUBMITTED and crash recovery | Required | 7 | #2, #3, #6 |
| 11 | DLR ingestion: HMAC, monotonic state machine, duplicates and early DLRs | Required | 8 | #3, #4, #10 |
| 12 | Global 50/s carrier rate limiter | Required | 6 | #10 |
| 13 | Retry policy: classification, backoff, max attempts and refunds | Required | 7 | #10, #11, #12 |
| 14 | Timeout ambiguity: safe retries without duplicate SMS | Required | 6 | #11, #13 |
| 15 | Fair scheduling: OTP latency during a 10k campaign | Required | 7 | #10, #12, #13, #14 |
| 16 | Tenant webhooks: final-state delivery, retries and isolation | Required | 6 | #11, #13, #14 |
| 17 | Dashboard shell: message list, filters, balance and status counts | Required | 6 | #9 |
| 18 | Dashboard detail: event history and live SSE updates | Required | 7 | #9, #11, #17 |
| 19 | Dashboard send form with live encoding, segments and price | Required | 5 | #5, #6, #17, #18 |
| 20 | Load script: 10k campaign, OTP latency and billing checks | Required | 6 | #3, #8, #9, #11, #13, #14, #15 |
| 21 | One-command Docker Compose stack with migrations and seed | Required | 6 | #2, #3, #4, #10, #12, #15, #16, #17, #18, #19 |
| 22 | DESIGN.md, README, test matrix and submission artifacts | Required | 4 | #20, #21 |
| 23 | Bonus: second carrier, prefix routing and failover | Bonus | 8 | #3, #4, #10, #13, #21 |
| 24 | Bonus: HMAC signatures on tenant webhooks | Bonus | 3 | #16 |
| 25 | Bonus: scheduled messages | Bonus | 5 | #6, #10, #15, #16, #17 |
| 26 | Bonus: Prometheus metrics for queue, submit rate and DLR delay | Bonus | 4 | #10, #11, #16 |
| 27 | Bonus: message templates with variables | Bonus | 4 | #5, #6, #7 |

### 6. Audit and corrections

An independent audit checked coverage, dependency order, vertical-slice quality, consistency and labels. It found no missing spec behavior and no cycles. It did find interface seams, which were fixed in the issue bodies:

- The dashboard uses an admin key, so the quote and balance read paths now allow `X-Admin-Key` + `tenant_id`; tenant API keys cannot cross tenants.
- The carrier and gateway API now share the wire enum `GSM7`/`UCS2` (no hyphen).
- Event rows use `UNIQUE(message_id, status)` as a dedupe guard with `ON CONFLICT DO NOTHING`, so reclaims/retries and DLR-sourced `SUBMITTED` events cannot collide.
- `messages` and `dlr_inbox` are scoped by `(carrier, carrier_id)`, and `(carrier, carrier_id)` is unique when set.
- Batch items use the same `{to,from,text,client_ref?}` shape as single send and return encoding/segment/price fields.
- Webhooks are one logical outbox row with a stable `event_id`; HTTP delivery is at-least-once, so tenants dedupe.
- The load script declared its dependency on the carrier stats endpoint.
- Compose gained the limiter and fairness blockers and a higher difficulty; the 10k benchmark moved to the load script, leaving a 2k controlled fairness benchmark.
- Scheduled messages now define `SCHEDULED`/`CANCELLED`, the cancel-vs-submit race, tenant scope, and the webhook extension.
- Templates declared their idempotency dependency.

The final validation confirmed 27 issues, no broken blocker references, and matching priority/domain/difficulty labels.

### 7. Agent guide and transcript convention

**User**

> Got it, let's also create an AGENTS.md about this project and its scope, since we haven't really written anything yet, let's keep it minimal. Though I will add this: Whenever I initiate a session and we conclude it (if this thread closes, or if I explicitly say so), let's log the transcript for documentation, as per the tech spec. save it in root as /transcript , with a numbering system eg. 01-setup-tickets, though I am not sure whether it should be md or text file, maybe you know what would be optimal.

I created:

- `AGENTS.md` — project scope, layout, issue/dependency source of truth, core invariants, definition of done, and the transcript rule.
- `transcript/README.md` — Markdown naming convention (`NN-kebab-case.md`), metadata, content rules, and template.

Markdown was chosen over `.txt` because it renders on GitHub, preserves code blocks and links, and diffs cleanly. A raw export can be appended later if needed.

### 8. Session close

**User**

> alright, i guess we can now close this thread and write the first transcript, though I think it would be best if you did it in main first, ill just rebase on my current working branch

The transcript was written on `main`, committed with `AGENTS.md` and the transcript convention. The user's feature branch (`feat/walking-skeleton-01`) and its untracked `docker-compose.yaml` were left untouched. After the commit, the user can run:

```bash
git switch feat/walking-skeleton-01
git rebase main
```

## Artifacts

- [AGENTS.md](../AGENTS.md) — project scope, invariants and transcript rule.
- [transcript/README.md](README.md) — transcript format and template.
- [GitHub issues #1–#27](https://github.com/aritako/dcc-sms-gateway/issues) — full acceptance criteria and native blocked-by edges.
- No implementation code was changed in this session.

## Key decisions

- **Prefactoring first.** #1 fixes config/import/test/compose seams; #2 establishes schema, constraints, migrations and seed before feature slices.
- **PostgreSQL is the queue.** `messages.available_at` / `next_attempt_at` with `FOR UPDATE SKIP LOCKED` makes accept + charge + enqueue one transaction. Redis is reserved for the global rate limiter and SSE fan-out.
- **Global limiter uses a Redis Lua sliding window** with Redis `TIME`, default 45/s and a hard ceiling of 50/s; retries also pass through it.
- **Money is integer cents** with a ledger constraint/invariant and a non-negative balance check; refunds are unique per message.
- **The dashboard uses a server-side admin key** because the public API is tenant-scoped and the dashboard must filter across tenants.
- **DLRs are carrier-scoped.** The carrier sends only `carrier_id`; early DLRs are parked in `dlr_inbox` and reconciled when the worker saves the id. `(carrier, carrier_id)` prevents collisions with the bonus second carrier.
- **Timeout retries use the same `message_id`.** The mock carrier deduplicates it; the residual risk with a real carrier that lacks idempotency is documented in #14/#22.
- **Wire encoding is `GSM7`/`UCS2`.** The API and carrier payload use the same enum, while the docs can still say GSM-7/UCS-2.
- **Seed balances are load-test sized:** Acme 100,000 cents, ShopFast 1,000,000 cents, `+880` 3 cents/segment, `+971` 5 cents/segment.

## Coverage check

| Mandated test | Owner |
|---|---|
| Encoding detection and segment count, including extension chars and UCS-2/Bangla/emoji | #5 |
| Same idempotent request twice → one message/charge | #7 |
| Early DLR before submit response + duplicate DLR | #11 |
| Concurrent sends exceeding balance → never below zero | #6 |
| 429/503 retried; 400 not retried | #13 |
| Missing/wrong DLR signature → 401 and no change | #11 |
| Cross-tenant message access → 404 | #9 |

Minimum API surface: `POST /v1/messages` (#6), `POST /v1/messages/batch` (#8), `GET /v1/messages` and `GET /v1/messages/{id}` (#9), `GET /v1/balance` (#6/#9), `POST /carrier/dlr` (#11).

## Follow-ups

- Start with #1, then #2; #3–#4 can run in parallel with #5.
- Rebase `feat/walking-skeleton-01` onto `main` to pick up `AGENTS.md` and this transcript.
- Keep issue acceptance criteria as the definition of done; update `DESIGN.md` and `README.md` as decisions land.
- Log the next session as `transcript/02-<slug>.md`.

## Appendix A — Sub-agent scaffold findings

The scaffold audit confirmed:

- `gateway/app/main.py` only served `GET /`.
- `gateway/app/settings.py` instantiated settings at import, requiring `DATABASE_URL` and `REDIS_URL`.
- `gateway/app/db/database.py` and `redis.py` created clients at import.
- `gateway/app/seed.py` and `worker/worker.py` were placeholders.
- `gateway/migrations/env.py` used raw `os.environ` and `target_metadata = None`; the single migration was empty.
- `gateway/Dockerfile` had no `CMD`.
- `carrier/app/main.py` was hello-world and the README was empty.
- No schema, compose, tests, frontend, load script or submission docs existed.
- Both services build a module named `app`, so sharing one venv would shadow one package.
- No pytest/pytest-asyncio/fakeredis/respx dependencies were installed.

## Appendix B — Sub-agent decomposition highlights

The independent decomposition recommended 19 required + 5 bonus tickets and a critical path of `1 → 2 → 5 → 9 → 10 → 12 → 13 → 15 → 18 → 19`. Its most useful warnings were:

- The DLR contains only `carrier_id`; early DLRs need parking and reconciliation.
- HMAC must be computed over the raw bytes actually sent.
- GSM-7 extension characters count as 2; UCS-2 uses UTF-16 code units.
- A token bucket can burst across the carrier's window; use a sliding window with headroom.
- Retries must not bypass the limiter or regress status.
- Ambiguous timeout exhaustion must not silently refund if the carrier may have charged.
- Duplicate DLRs must not fan out duplicate webhooks.
- The dashboard needs a cross-tenant admin seam.
- `QUEUED` must be recorded explicitly.
- Migrations and seed must precede gateway/worker startup.
- The carrier needs deterministic controls for tests.
- The load script must include `FAILED` and refunds.

The final plan adopted those findings and split the carrier, timeout, fairness and dashboard work into smaller slices.

## Appendix C — Original ticket templates

The initial request included these templates (kept here for completeness):

```markdown
# <NN>: <Ticket title>

**What to build:** the end-to-end behaviour this ticket makes work, from the user's perspective, not a layer-by-layer implementation list.

**Blocked by:** the numbers/titles of the tickets that gate this one, or "None (can start immediately)".
**Domain**: Carrier
**Priority**: Required
**Difficulty**: 1/10

- [ ] Acceptance criterion 1
- [ ] Acceptance criterion 2
```

```markdown
## Parent

A reference to the parent issue on the tracker (if the source was an existing issue, otherwise omit this section).

## What to build

The end-to-end behaviour this ticket makes work, from the user's perspective, not layer-by-layer implementation.

## Acceptance criteria

- [ ] Criterion 1
- [ ] Criterion 2

## Blocked by

- A reference to each blocking ticket, or "None (can start immediately)". Omit this section when blockers were set as native edges.
```
