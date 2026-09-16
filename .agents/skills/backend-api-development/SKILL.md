---
name: backend-api-development
description: >-
  Backend API and payment workflow safety skill for the UMS project.
  Covers command/query separation, idempotency, financial workflow read-only
  enforcement, M-Pesa STK Push integration, payment provider registry patterns,
  audit payload sanitization, and API endpoint design.
  Activate whenever working on payment, fee, or any provider-integrated endpoint.
---

# Backend API Development Skill — UMS Project

## Core Principle: Command–Query Separation (CQS)

Every endpoint must be either a **command** (mutation) or a **query** (read).
They must NEVER be mixed. This is especially critical in financial workflows.

```
GET  /fees/payment-status/   → QUERY only — zero side effects
POST /fees/payment/initiate/ → COMMAND — creates payment session
POST /fees/payment/verify/   → COMMAND — records payment outcome
```

---

## 1. Financial Workflow Safety (Observation #6)

### THE GOLDEN RULE
> **GET requests in any payment flow must NEVER initiate, update, or cancel
> payment state. Refreshing a status page must always be safe.**

### ❌ WRONG — GET initiates a payment:
```python
def payment_status(request, pk):
    payment = get_object_or_404(FeePayment, pk=pk)
    # DANGEROUS: calling provider on a GET!
    result = provider.initiate_checkout(payment)
    return render(request, "payment_status.html", {"result": result})
```

### ✓ CORRECT — Separation of command and query:
```python
# QUERY view — read-only, safe to refresh
def payment_status(request, pk):
    payment = get_object_or_404(FeePayment, pk=pk, student__user=request.user)
    return render(request, "fees/payment_status.html", {"payment": payment})

# COMMAND view — only called once via POST/redirect
@require_POST
def payment_initiate(request):
    form = PaymentInitiateForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            payment = FeePaymentService().initiate(request.user, form.cleaned_data)
        return redirect("fees:payment_status", pk=payment.pk)
    ...

# WEBHOOK view — provider calls this, idempotent
@csrf_exempt
@require_POST
def payment_webhook(request):
    payload = request.body
    # Idempotency: check if already processed
    if FeePayment.objects.filter(provider_ref=payload["id"], status="PAID").exists():
        return HttpResponse(status=200)  # Already processed — safe to return 200
    FeePaymentService().record_webhook(payload)
    return HttpResponse(status=200)
```

---

## 2. Idempotency Rules

All payment and state-change endpoints must be idempotent:

- Use a **unique constraint** on `provider_ref` or `transaction_id`
- Check before insert: `get_or_create()` with the provider reference
- Return HTTP 200 on duplicate webhook calls (don't error — providers retry)
- Log duplicate calls for audit trail but do not re-process

```python
payment, created = FeePayment.objects.get_or_create(
    provider_ref=provider_ref,
    defaults={"status": "PENDING", "student": student, "amount": amount}
)
if not created:
    logger.info("Duplicate payment ref received: %s", provider_ref)
    return payment  # Return existing, do not re-initiate
```

---

## 3. API Endpoint Design Rules

### URL Patterns
```
# List + Create
GET/POST  /manage/fees/                    → FeeListView / FeeCreateView

# Detail + Update + Delete
GET       /manage/fees/<pk>/               → FeeDetailView
POST      /manage/fees/<pk>/update/        → FeeUpdateView
POST      /manage/fees/<pk>/delete/        → FeeDeleteView (POST, not DELETE)

# Action endpoints (state transitions)
POST      /manage/fees/<pk>/waive/         → FeeWaiveView
POST      /manage/fees/<pk>/reverse/       → FeeReverseView

# Read-only status pages
GET       /fees/<pk>/status/               → PaymentStatusView (QUERY only)
```

### HTTP Method Enforcement:
```python
from django.views.decorators.http import require_POST, require_http_methods

@login_required
@require_POST  # NEVER allow GET for mutations
def fee_waive(request, pk):
    ...
```

---

## 4. Webhook Security

All provider webhooks must:
1. Verify the signature header (HMAC or provider-specific)
2. Be `@csrf_exempt` (they come from outside)
3. Process asynchronously via Celery if heavy
4. Store the raw payload before processing

```python
import hmac, hashlib

def verify_mpesa_signature(request, secret):
    signature = request.headers.get("X-Mpesa-Signature", "")
    expected = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)
```

---

## 5. Error Handling Standards

```python
from django.http import JsonResponse

def api_view(request):
    try:
        result = service.do_something(request.user, request.POST)
        return JsonResponse({"status": "ok", "data": result})
    except PermissionDenied as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=403)
    except ValueError as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)
    except Exception as e:
        logger.exception("Unexpected error in api_view")
        return JsonResponse({"status": "error", "message": "Internal error"}, status=500)
```

---

## 6. Payment Provider Architecture

The system uses a **provider registry pattern** — never call a payment provider directly from a view.

```
university/payment_providers/
├── base.py          ← BasePaymentProviderAdapter + PaymentResult dataclass
├── mpesa.py         ← M-Pesa STK Push / Paybill / Buy Goods / Pochi la Biashara
├── bank.py          ← Bank transfer instructions
├── card.py          ← Card payment gateway
└── registry.py      ← get_payment_adapter(fee_account) — resolves provider from FeeAccount type
```

### Provider Resolution:
```python
from university.payment_providers.registry import get_payment_adapter

# Never hardcode the provider — always resolve from the FeeAccount
adapter = get_payment_adapter(fee_account)
result = adapter.initiate_payment(payment, request, extra_data={"phone": phone})
```

### FeeAccount Types → Provider Mapping:
| FeeAccount.account_type | Provider | Flow |
|---|---|---|
| `MPESA_PAYBILL` | MpesaProviderAdapter | STK Push → Paybill + Account Ref |
| `MPESA_TILL` | MpesaProviderAdapter | Buy Goods / Till |
| `POCHI_LA_BIASHARA` | MpesaProviderAdapter | Pochi number |
| `BANK_TRANSFER` | BankProviderAdapter | Manual bank transfer instructions |
| `CARD` | CardProviderAdapter | Card gateway redirect |

---

## 7. M-Pesa Phone Number Sanitization

Always normalize phone before sending to M-Pesa (from `mpesa.py`):

```python
def sanitize_mpesa_phone(phone: str) -> str:
    """Convert any Kenyan format to 254XXXXXXXXX."""
    cleaned = phone.replace("+", "").replace(" ", "").replace("-", "")
    if cleaned.startswith("0") and len(cleaned) == 10:
        return "254" + cleaned[1:]
    if cleaned.startswith("7") and len(cleaned) == 9:
        return "254" + cleaned
    return cleaned  # already in 254... format
```

---

## 8. Payment Audit Payload Sanitization

From `payment_services.py` — never store raw PII in payment audit records:

```python
# These are the ONLY fields that may be stored in audit logs for payments
PAYMENT_AUDIT_FIELDS = {
    "transactiontype", "transid", "transtime", "transamount", "amount",
    "businessshortcode", "billrefnumber", "invoicenumber", "currency",
    "currencycode", "internal_reference", "reference", "provider_reference",
    "transaction_id", "status", "resultcode", "resultdesc",
}

def sanitize_payment_payload(payload):
    """Keep only non-personal verification facts."""
    return {k: v for k, v in (payload or {}).items() if k.lower() in PAYMENT_AUDIT_FIELDS}
```

---

## 9. Reconciliation and Reversal

The system has `PaymentReconciliation` and `PaymentReversal` models.

- **Reconciliation** — matches provider records to internal `Payment` records
- **Reversal** — creates a negative ledger entry, does NOT delete the original payment
- Both require `has_perm("university.change_feeaccount")` + `@transaction.atomic`

```python
# Pattern for reversal (never delete, always reverse)
with transaction.atomic():
    reversal = PaymentReversal.objects.create(
        payment=payment,
        reversed_by=request.user,
        reason=form.cleaned_data["reason"],
        amount=payment.amount,
    )
    payment.status = "REVERSED"
    payment.save(update_fields=["status"])
    log_activity(request.user, "payment_reversed", payment, delta={"amount": str(payment.amount)})
```

---

## 10. Pre-Release API Checklist

- [ ] All GET endpoints have zero side effects (safe to refresh/repeat)
- [ ] All POST endpoints have `@require_POST` decorator
- [ ] Payment provider resolved via registry, not hardcoded
- [ ] M-Pesa phone numbers sanitized to `254XXXXXXXXX` before sending
- [ ] Payment webhooks verify provider signature
- [ ] Idempotency implemented with unique constraint + `get_or_create`
- [ ] All mutations wrapped in `@transaction.atomic`
- [ ] Duplicate webhook calls return HTTP 200, not 4xx
- [ ] Audit payload sanitized — no PII in `PAYMENT_AUDIT_FIELDS`
- [ ] Reversal creates new record, never deletes original
- [ ] Error responses use consistent JSON structure
- [ ] Audit log entry created for all financial mutations
