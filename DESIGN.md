## Core design

We run three services and two datastores. The gateway and workers share one codebase and image, the carrier is separate, Postgres stores the messages and the queue, and Redis only holds the rate limiter.

### Carrier

The carrier follows the spec. It adds 20 to 300 ms of latency, enforces 50 submits per second for the whole process, returns 503 about 3% of the time and 400 about 1%, and hangs for 5 seconds on about 1% while still accepting the message. It sends a DLR 0.5 to 10 seconds later, sometimes twice, and sometimes before the submit response. It maps `message_id` to `carrier_id`, so a gateway retry with the same id gets the original `carrier_id` and no second SMS. A restart clears that map, and that is the remaining way a customer could get two SMS.

### Gateway

This is the API the tenants call. It checks `X-API-Key`, E.164 numbers and sender IDs, quotes the encoding, segments and price, and rejects a destination with no price. Accepting a message writes the message, the `ACCEPTED` event, the `CHARGE` row and the balance debit in one transaction, so a message cannot exist without its charge. An `Idempotency-Key` reserves the tenant and key pair and replays the stored 2xx for the same body, or returns 409 for a different body. A tenant read only returns that tenant's own rows, and another tenant's message is 404.

### Workers

Three replicas claim due messages with pessimistic locking of a message row (`FOR UPDATE SKIP LOCKED`) and a 30 second "lease," split evenly between multiple tenants. A worker takes a Redis permit for each submit, at about 45 per second, below the carrier's 50. A 200 moves the message to `SUBMITTED` and reconciles any parked receipt. A 400 marks it `FAILED` and refunds the charge. The worker retries a 429, a 5xx or a network error with backoff. It treats a timeout as ambiguous and retries the same `message_id`. The maximum is six attempts, and each status change is conditional, so the first terminal status wins and a late event cannot move it back.

### Postgres or Redis

Postgres is our main database for persistent data such as `messages` and `api_keys`, etc. It is also the queue because accepting a message, charging it and enqueueing it must be one transaction. `SKIP LOCKED` lets the workers claim rows without a broker. Redis holds the shared limiter to throttle worker submits under the 50 messages/s limit of the carrier.

### What I would add or improve

- A dedicated queue such as SQS or Kafka. Right now, we rely on multiple queries, table scans and pessimistic locking to queue a message for sending. This puts the burden on our database, when we could probably decouple queuing logic to a dedicated service instead. Workers would consume from it and stay idempotent on `message_id`.
- The dashboard and SSE, [#17](https://github.com/aritako/dcc-sms-gateway/issues/17) and [#18](https://github.com/aritako/dcc-sms-gateway/issues/18), and tenant webhooks with retries and HMAC, [#16](https://github.com/aritako/dcc-sms-gateway/issues/16). I cut both since I ran out of time, and I wanted to focus on the core functionality of the services.
- A second carrier with routing and failover, which I was unable to do in [#23](https://github.com/aritako/dcc-sms-gateway/issues/23) . Scheduled sends, metrics and templates are also missing, [#25](https://github.com/aritako/dcc-sms-gateway/issues/25), [#26](https://github.com/aritako/dcc-sms-gateway/issues/26), [#27](https://github.com/aritako/dcc-sms-gateway/issues/27).


## Data Models

| Table | Purpose |
|---|---|
| tenants | One row per tenant; holds the prepaid balance and currency. |
| api_keys | Hashed tenant API keys used to authenticate requests. |
| prices | Per-prefix price per segment used for quoting a send. |
| messages | The message/queue row: content, pricing, status, and retry timing. |
| message_events | Status history for a message, sourced from the gateway or a DLR. |
| submit_attempts | One row per real carrier submit, with its outcome. |
| dlr_inbox | Durable log of every received receipt, including parked early ones. |
| ledger_entries | Append-only charge/refund/credit rows for the tenant balance. |
| idempotency_keys | Per-tenant request keys and the stored response to replay. |

Messages belong to a tenant and are the queue and status row; message events and ledger entries are append-only, with ledger rows unique per message and kind. Idempotency keys are unique per tenant and key, and `dlr_inbox` dedupes carrier receipts by `(carrier, carrier_id, status, occurred_at)`.

## Questions

## A worker submits a message, the carrier accepts it, and the worker crashes before it saves the result. What happens? Can the customer get the SMS twice? 

When a worker claims a message to send, it only "reserves" it from the other workers, and does not ultimately mark it "done" not until we get a `SUBMITTED` status from the carrier that is sucessful saved by the worker. So in this case, it receives a 200 from the carrier, but does not save the result. The message is still at a nonterminal state (`ACCEPTED`, `QUEUED`), so another worker picks up again and retries sending to the carrier endpoint.

But no duplication should happen because of our dedupe logic on the carrier endpoint. The first submit should have stored the message_id in the carrier's accepted store, mapped to its carrier_id. So when another worker reclaims the message for sending, we do a preliminary check whether that message_id is already in the store, and return the original carrier_id early, without sending a second SMS.

Now the new worker receives the carrier_id, but it still has to reconcile the message status. If the DLR arrived while the message had no carrier_id, it is "parked" in `dlr_inbox` as pending, and the `record_submitted` checks and reconciles it in the same transaction, writing SUBMITTED first and then the terminal status (`DELIVERED`, `UNDELIVERED`, etc.). If the DLR has not arrived yet, we mark SUBMITTED and the receipt applies normally later, with the 5 second sweep as a backstop. So the customer does not get the SMS twice, as long as the carrier dedupes on message_id. 

The remaining risk is that the mock keeps the store in memory: if the carrier restarts between the accept and the retry it forgets, and a real carrier without an idempotency key has the same problem. Billing is unaffected because we charge at accept time, so a retry never charges again. The cost of a crash is up to 30 seconds of delay and one duplicate submit, not a duplicate SMS.

## Where is your system "at least once", and where is it "exactly once"? How do you prove it?

At least once
- Worker retries. The same message can be submitted more than once: a 503, a timeout, or a worker crash before it saves the result all lead to another claim and another submit with the same `message_id`. However, we have safeguards for deduplication of messages which allows us to have multiple worker retries.
- DLR arrival. The carrier can send the same receipt twice (about 10% of them, by default), and a receipt can arrive before the submit response, so arrival is at-least-once.
- Gateway API layer accept, when the client does not send an `Idempotency-Key`. A client retry creates a new message and a new charge. That is correct behaviour, but it means the API is at-least-once unless the header is used.

Exactly once.
- Terminal status. A message status can only reach a terminal status once: DELIVERED, UNDELIVERED, FAILED, EXPIRED. Even if there are additional DLRs for the same `message_id`, once it reaches any of the four statuses, it cannot be updated any more.
- Charge. One `CHARGE` per accepted message, written in the same transaction as the message insert and guarded by `UNIQUE(message_id, kind)`. With `Idempotency-Key`, a duplicate request replays the stored response instead of charging again.
- Refunds. At most, we only charge one `REFUND` per failed message, which is safeguarded by the table's unique constraint `UNIQUE(message_id, kind)`. Only the insert statement that goes first credits the tenant, so two workers finalizing at the same time cannot both refund. Refunds only happen for message status `FAILED` before carrier acceptance because of either (1) a `400` from the carrier, or all attempts (6) for worker retries failed.
- Ledger entries. The ledger tracks every debit and credit to the tenant's balance exactly once. Like the charge and refund, the database enforces it a unique constraint, meaning one message can only ever have one `CHARGE` and one `REFUND`.

## How does your fairness work? What happens with 50 tenants instead of 2?

There is no separate scheduler process. The claim query in `scheduler.py` decides fairness, because that is the only place where the three workers can agree on what to take next.

Each worker claims 20 messages at a time. It ranks the tenants with due work by their oldest due message. With N active tenants and K = 20 slots, each tenant gets `K // N` slots and the first `K % N` tenants get one more. The worker then locks each tenant's share in FIFO order by `due_at` and `id` with `FOR UPDATE SKIP LOCKED`. If a tenant has fewer messages than its share, the unused slots go to the globally oldest due messages. With ShopFast and Bank both backed up that is 10 and 10. With one Bank OTP and 2,000 ShopFast messages it is 1 and 19, so the OTP does not sit behind the campaign. A retry enters at `next_attempt_at`, so it does not jump ahead of new work. A message that keeps failing takes one turn and then backs off. There is no extra table and no in-process state, so all three workers run the same query, and `SKIP LOCKED` keeps their batches disjoint. With one tenant, the query can return up to 20 of its messages, so throughput does not drop.

With 50 tenants, 20 is smaller than 50. Each claim serves the 20 tenants with the oldest due message, one message each, and the other 30 wait for a later claim. A claim takes about 0.45 seconds at 45 submits per second, so with one worker a tenant waits at most three rounds, about 1.4 seconds. With three workers it is faster, because they claim in parallel. Every tenant gets served, because a tenant that missed a round has an older due message next time and moves into the top 20.

The limitation is that these are equal shares per claim, not persistent deficit round-robin, so the shares reset each claim. Ranking by the oldest due message means a tenant with a large old backlog keeps getting its slot each round, ahead of tenants with newer messages, until that backlog drains. That is bounded and eventually fair, but it is not strict rotation. To make it strict I would store a per-tenant `last_scheduled_at`, or a claim-round sequence, and order by that. For weighted shares I would add a share column per tenant. The query keeps the same shape.

Not to mention, this puts tremendous load on the database with increasing messages per second. We do at least 6 queries for a single message + queue transaction.

## What breaks first if the traffic grows to 1,000 messages per second? What would you change?

Out right, our gateway and workers will be first affected, since the carrier will be relatively safe due to the rate limit and the throttled work submits (50 messages/s). At 1,000 messages/s we’re roughly 20x over capacity, so the queue grows by about 950 messages every second. A batch send will take a considerable amount of time to drain before every single message is successfully sent.

If we want to remove the rate limit and throttle of 50 messages/s, then the carrier will fail next. Because we only store the `message_id` mapped to a `carrier_id` in memory (via hashmap), that is vulnerable to OOM due to the sheer amount of messages it is receiving. Not to mention, any carrier service crash will completely wipe out its current memory even if it recovers.

The write paths on the gateway and workers will also suffer. Each accepted message is a message row, an ACCEPTED event, a CHARGE ledger row, sometimes an idempotency row, plus an UPDATE on the tenant balance. That’s 3–4k inserts/s at 1,000/s, so our database connection pools will fill up severely. Since we also rely on direct DB transactions for the scheduling and queueing of messages for workers to claim, our single database instance cannot handle all of that.

To scale our services, I would fix the following:
- Have a dedicated queuing service such as SQS or Kafka. Accepted messages by the gateway API layer will route them to this queue, and then dedicated consumers (such as a specialized "message worker") will process the messages in queue. This takes load not only from the gateway service, which was originally responsible for both API layer + message processing, but also takes load off the database, which currently relies on table scans and locks for the queue.
- Multiple carriers with routing and failover, or a higher carrier contract to handle all the incoming throughput.
- Horizontally scaling our database. Since these are write-heavy processes, sharding our database will allow write operations to run in parallel across multiple independent servers, breaking past the CPU, disk I/O, and locking limits of a single primary database.

## AI Usage

I have included in the `transcript/` my AI usage for this project. To start with, I asked my agent to break down this spec into actionable tickets, which you can see in my Github [Issues](https://github.com/aritako/dcc-sms-gateway/issues) tab. For each ticket, I primarily was spearheading the design and consulting it with my agent, though for certain design decisions such as the fair scheduling, I let my agent take the lead while we discuss the plan. In the interest of time, I also asked it to scaffold some of the menial stuff, like the READMEs, docker compose files, etc.