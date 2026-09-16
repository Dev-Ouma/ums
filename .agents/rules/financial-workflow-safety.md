---
trigger: always_on
---

# Financial Workflow Safety Rules — UMS Project

## Absolute Rules (No Exceptions)

1. **GET endpoints are read-only** — Any view under `/fees/`, `/payments/`, or
   `/billing/` that uses GET must NEVER initiate, update, or cancel a payment.
   Refreshing a status page must always be safe.

2. **Payment initiation is POST-only** — Use `@require_POST` on all views that
   call any payment provider API.

3. **Idempotency is mandatory** — Every payment record must have a unique
   constraint on the provider's reference/transaction ID. Use `get_or_create()`
   to prevent duplicate processing.

4. **Webhooks return HTTP 200 on duplicates** — Payment providers retry webhooks.
   If the payment is already recorded as PAID, return 200 — do not re-process
   and do not return 4xx (which triggers retries).

5. **All financial mutations are atomic** — Any write to `FeePayment`,
   `FeeAccount`, or related ledger tables must be inside `@transaction.atomic`.

6. **Audit log on every financial mutation** — Every create, update, or reversal
   of a fee record must be logged with the actor, timestamp, and delta.

7. **No fee mutation without authorization** — All fee-related mutations must
   check `has_perm("university.change_feeaccount")` or equivalent before acting.
