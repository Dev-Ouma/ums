# UMS Payment Go-Live Certification

## Required payment channels

Each live M-Pesa Paybill, Buy Goods/Till, card gateway, bank feed, or other provider must have an active `FeeAccount`, an owner, a production identifier, an external secret-manager reference, a tested callback URL, and a recorded connection test. Sandbox channels must not be used for public go-live.

## Verified application controls

- Payment intent, student, amount, currency and internal reference are created server-side.
- Successful settlement requires a successful provider callback result; failed/cancelled callbacks do not allocate funds.
- Callback amount, currency, payment reference and student account reference are checked against the server-created intent.
- Production callbacks require an HMAC-SHA256 `X-UMS-Webhook-Signature` using `UMS_PAYMENT_WEBHOOK_SECRET`.
- M-Pesa STK callbacks match the original provider checkout reference; the system no longer settles the newest pending payment as a fallback.
- Confirmation is atomic and row-locked; duplicate callbacks return the existing receipt and cannot allocate twice.
- Allocation, balance update, receipt, reconciliation, notification attempt and audit event are handled by the confirmation workflow.
- Original payments are preserved during reversals/refunds, with a required reason and audit record.
- Provider credentials are not persisted in payment-account configuration JSON; the database stores only a non-sensitive external-secret marker.
- Hosted/tokenized card architecture is required; raw PAN, CVV and PIN are not accepted or stored by UMS.

## Finance test evidence required

Test each configured provider in a production-like environment for successful, failed, cancelled, pending, timeout, duplicate, wrong amount, wrong student, wrong currency, replay, reversal, refund, provider outage, callback outage, and browser interruption scenarios. Attach provider transaction evidence, callback payload identifiers, reconciliation result, receipt number, and audit record to the readiness item.

The certification screen deliberately marks independent live-provider API verification as a warning until each adapter implements and evidences the provider's authoritative verification API. Parsing a browser return or unsigned callback is not payment certification.
