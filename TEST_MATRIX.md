# Test matrix

The seven mandated automated tests, mapped to the test that covers them and the
command that runs it. Test names were checked with `pytest --collect-only`
against the files on `main`; every command below has been run from this repo at
the commit that adds this file.

## Prerequisites

Postgres and Redis must be reachable on localhost. The stack from
`docker compose up -d` is enough, or start just the two services:

```bash
make dev    # docker compose up -d postgres redis
```

The gateway suite provisions and migrates an isolated `sms_test` database and
uses Redis db 15, truncating both after each test. It never touches the dev
database.

## Summary

| # | Mandated scenario | Test file |
| --- | --- | --- |
| 1 | Encoding and segment counting (`GSM7`/`UCS2`) | `gateway/tests/test_encoding.py` |
| 2 | Idempotency: one message, one charge | `gateway/tests/test_idempotency.py` |
| 3 | Early and duplicate DLR | `gateway/tests/test_dlr.py` |
| 4 | Concurrent overdraw cannot go below zero | `gateway/tests/test_send.py` |
| 5 | 429/503 retried vs 400 not retried | `gateway/tests/test_retry.py` (+ carrier 429 emission) |
| 6 | Missing or bad DLR signature is rejected | `gateway/tests/test_dlr.py` |
| 7 | Cross-tenant reads return 404 | `gateway/tests/test_messages_read.py` |

## Run everything

```bash
make test-all    # gateway suite, then carrier suite — the same commands CI runs
```

CI (`.github/workflows/ci.yml`) additionally runs `ruff check` and `mypy` for the
gateway and the load script before both suites. Last local run: 176 gateway
tests and 12 carrier tests passed.

## 1. Encoding and segments

**File:** `gateway/tests/test_encoding.py`

- `test_gsm_tables_are_the_full_03_38_alphabet` — 127 basic + 10 extension
  characters; escape byte excluded.
- `test_every_extension_character_stays_gsm7_and_costs_two_septets` — every
  extension character is `GSM7` at 2 septets.
- `test_single_segment_boundary_is_160_septets` — 160 chars = 1 segment, 161 = 2.
- `test_multi_segment_capacity_is_153_septets` — 306 = 2 segments, 307 = 3.
- `test_euro_costs_two_septets_so_the_boundary_is_eighty` — extension chars
  shift the multipart boundary.
- `test_characters_outside_the_table_force_ucs2` — Bengali text selects `UCS2`.
- `test_ucs2_boundary_is_70_utf16_units` — 70/71 and 134/135 unit boundaries.
- `test_emoji_counts_as_two_utf16_units` and
  `test_lone_surrogate_counts_as_one_unit_instead_of_raising` — UTF-16
  accounting.
- `test_empty_text_is_rejected`.

```bash
cd gateway && uv run pytest tests/test_encoding.py
```

Pricing by segment is covered next to this in `tests/test_quote.py`
(`test_segments_multiply_the_price`, `test_price_uses_the_longest_matching_prefix`).

## 2. Idempotency: one message, one charge

**File:** `gateway/tests/test_idempotency.py`

- `test_same_key_and_body_replays_and_writes_nothing` — same key and body returns
  the identical 202 body; one message, one event, one charge, one debit.
- `test_concurrent_duplicates_create_one_message_and_one_charge` — 10 parallel
  sends with one key produce one message and one charge.
- `test_concurrent_duplicates_replay_even_when_the_balance_runs_out` — replays
  succeed even when only one send's worth of balance remains.
- `test_same_key_with_different_body_is_409_and_writes_nothing` — reused key
  with a changed body is `409 IDEMPOTENCY_KEY_REUSED`.
- `test_same_key_is_independent_per_tenant` — keys are scoped per tenant.
- `test_402_is_not_stored_and_the_same_key_works_after_top_up` — only 2xx
  responses are stored.
- `test_invalid_idempotency_key_is_422_and_writes_nothing`.

Batch replay is covered in `tests/test_batch.py`
(`test_replay_returns_the_same_ids_and_charges_once`,
`test_concurrent_duplicate_batches_charge_once`).

```bash
cd gateway && uv run pytest tests/test_idempotency.py
```

## 3. Early and duplicate DLR

**File:** `gateway/tests/test_dlr.py`

- `test_early_dlr_is_parked_then_reconciled_by_the_submit_response` — a DLR that
  arrives before the submit response is parked `PENDING` in `dlr_inbox`, then
  reconciled when `carrier_id` is saved, leaving the ordered events
  `ACCEPTED → QUEUED → SUBMITTED → DELIVERED`.
- `test_a_duplicate_dlr_writes_no_second_event_and_keeps_final_at` — the second
  identical DLR returns `DUPLICATE`, writes no event, and keeps `final_at`.
- `test_sweep_reconciles_a_dlr_parked_before_the_carrier_id_was_saved` — the
  periodic sweep closes the same race when no submit response follows.
- `test_a_late_conflicting_terminal_is_rejected_and_does_not_regress` — a later
  conflicting terminal status is `REJECTED` and cannot rewrite history.
- `test_terminal_codes_mark_the_message_with_both_timestamps` — `DELIVRD`,
  `UNDELIV` and `EXPIRED` map to terminal statuses with carrier and gateway
  timestamps.

```bash
cd gateway && uv run pytest tests/test_dlr.py
```

## 4. Concurrent overdraw

**File:** `gateway/tests/test_send.py`

- `test_concurrent_sends_cannot_overdraw` — a 30-cent balance affords exactly 10
  three-cent messages; 50 parallel sends yield exactly 10 `202`, 40 `402`, a
  zero balance, 10 charge rows and a −30 ledger sum.
- `test_insufficient_balance_is_402_and_writes_nothing` — a single send with too
  little balance writes nothing.

```bash
cd gateway && uv run pytest tests/test_send.py
```

## 5. 429/503 retried vs 400 not retried

**File:** `gateway/tests/test_retry.py` (gateway side) and
`carrier/tests/test_submit.py` (429 emission)

- `test_transient_503_then_200_delivers_once_without_refund` — a 503 schedules a
  future `next_attempt_at`, the retry succeeds with the same `message_id`, the
  message is delivered once, and there is no refund.
- `test_exhausting_transient_failures_fails_and_refunds_once` — 503s up to the
  attempt cap end `FAILED` with exactly one refund and no extra submit.
- `test_concurrent_finalisation_cannot_double_refund` — two finalisers racing on
  the last attempt still produce one `REFUND` row.
- `test_permanent_400_fails_after_one_attempt_with_one_refund` — a 400 is
  `PERMANENT`: one submit, terminal `FAILED`, exactly one refund.
- `test_permanent_400_after_a_timeout_is_definitive_and_refunds` — a 400 after an
  ambiguous timeout is still treated as definitive.
- `test_network_error_is_transient_and_retried` — transport errors retry.
- `test_timeout_then_200_resubmits_the_same_message_id_and_delivers` and the
  timeout-exhaustion test cover the ambiguous path absorbed from #14.
- `carrier/tests/test_submit.py::test_limit_is_shared_by_every_caller` — the
  mock carrier enforces its 50/s limit and answers `429` beyond it.

The gateway classifies every non-400 HTTP status as transient
(`gateway/app/worker/dispatch.py:173`), so 429 and 503 share the retry branch and
the limiter is re-entered on every attempt. Issue #13 deferred a dedicated
429-limiter unit test; the captured load run exercised the real path with
0 × 429, 315 × 503 and 87 × 400 responses.

```bash
cd gateway && uv run pytest tests/test_retry.py
cd carrier && uv run pytest tests/test_submit.py
```

## 6. Bad or missing DLR signature

**File:** `gateway/tests/test_dlr.py`

- `test_bad_signature_is_401_and_writes_nothing` — parametrized over a missing
  `X-Signature`, a well-formed but wrong signature, and a signature made with
  the wrong secret. Each returns `401` with unchanged message/event/inbox row
  counts and an unchanged status.
- `test_invalid_dlr_is_400_and_writes_nothing` — a correctly signed but invalid
  body is `400` and writes nothing.

```bash
cd gateway && uv run pytest tests/test_dlr.py
```

## 7. Cross-tenant 404

**File:** `gateway/tests/test_messages_read.py` (with auth and schema checks)

- `test_another_tenants_message_and_unknown_ids_are_404` — another tenant's
  message id and a random UUID both return `404 NOT_FOUND`.
- `test_list_is_tenant_scoped` — list results only contain the caller's rows.
- `test_admin_detail_crosses_tenants_unless_tenant_id_scopes_it` — an admin
  scoping to a different tenant gets `404`, not data.
- `tests/test_auth.py::test_reads_reject_a_tenant_key_that_names_a_tenant` — a
  tenant key cannot widen its scope with `tenant_id`.
- `tests/test_schema.py::test_cross_tenant_ledger_is_rejected` — the database
  refuses a ledger row that points at another tenant's message.

```bash
cd gateway && uv run pytest tests/test_messages_read.py
```

## Supporting suites

| Area | File | Command |
| --- | --- | --- |
| Fair dispatch (up to 50 tenants) | `gateway/tests/test_dispatch.py` | `cd gateway && uv run pytest tests/test_dispatch.py` |
| Global limiter | `gateway/tests/test_limiter.py` | `cd gateway && uv run pytest tests/test_limiter.py` |
| Carrier contract and DLRs | `carrier/tests/` | `cd carrier && uv run pytest` |
| Load-script math and audit | `gateway/tests/test_load_script.py` | `cd gateway && uv run pytest tests/test_load_script.py` |
| Schema constraints and seed | `gateway/tests/test_schema.py` | `cd gateway && uv run pytest tests/test_schema.py` |
