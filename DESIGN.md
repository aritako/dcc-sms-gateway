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
- Refunds. At most, we only charge one `REFUND` per failed message, which is safeguarded by the table's unique constraint. Only the insert statement that goes first credits the tenant, so two workers finalizing at the same time cannot both refund. Refunds only happen for message status `FAILED` before carrier acceptance because of either (1) a `400` from the carrier, or all attempts (6) for worker retries failed.
- Ledger entries. The ledger tracks every debit and credit to the tenant's balance exactly once. Like the charge and refund, the database enforces it a unique constraint means one message can only ever have one `CHARGE` and one `REFUND`.

## How does your fairness work? What happens with 50 tenants instead of 2?

We do not have a separate scheduling data structure, as much as it is the ideal solution. Instead, fairness is decided inside the claim query at `scheduler.py`, because that is the only place where the three workers can agree on what to pick next.

## What breaks first if the traffic grows to 1,000 messages per second? What would you change?

Out right, our gateway and workers will be first affected, since the carrier will be relatively safe due to the rate limit and the throttled work submits (50 messages/s). At 1,000 messages/s we’re roughly 20x over capacity, so the queue grows by about 950 messages every second. A batch send will take a considerable amount of time to drain before every single message is successfully sent.

If we want to remove the rate limit and throttle of 50 messages/s, then the carrier will fail next. Because we only store the `message_id` mapped to a `carrier_id` in memory (via hashmap), that is vulnerable to OOM due to the sheer amount of messages it is receiving. Not to mention, any carrier service crash will completely wipe out its current memory even if it recovers.

The write paths on the gateway and workers will also suffer. Each accepted message is a message row, an ACCEPTED event, a CHARGE ledger row, sometimes an idempotency row, plus an UPDATE on the tenant balance. That’s 3–4k inserts/s at 1,000/s, so our database connection pools will fill up severely. Since we also rely on direct DB transactions for the scheduling and queueing of messages for workers to claim, our single database instance cannot handle all of that.

To scale our services, I would fix the following:
- Have a dedicated queuing service such as SQS or Kafka. Accepted messages by the gateway API layer will route them to this queue, and then dedicated consumers (such as a specialized "message worker") will process the messages in queue. This takes load not only from the gateway service, which was originally responsible for both API layer + message processing, but also takes load off the database, which currently relies on table scans and locks for the queue.
- Multiple carriers with routing and failover, or a higher carrier contract to handle all the incoming throughput.
- Horizontally scaling our database. Since these are write-heavy processes, sharding our database will allow write operations to run in parallel across multiple independent servers, breaking past the CPU, disk I/O, and locking limits of a single primary database.