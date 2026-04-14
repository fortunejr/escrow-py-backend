# Escrow Backend Workflow

## 1) System Overview

This backend is a Django + DRF API-first escrow system where a buyer funds a specific listing-based escrow, and funds are later either:
- released to the seller,
- refunded to the buyer, or
- routed through dispute resolution.

Apps:
- `accounts` (JWT auth + profile)
- `listings` (seller listings)
- `escrow` (escrow creation/release/refund)
- `payments` (Paystack init/verify/webhook + payout execution)
- `disputes` (open/list/detail + admin resolution)
- `core` (audit logs + shared exception formatting)

Base API prefix: `/api/v1/`

---

## 2) Response Contract

Custom endpoints use a consistent envelope:

```json
{
  "success": true,
  "message": "Human-readable message",
  "data": {},
  "errors": null
}
```

Validation/auth failures return:

```json
{
  "success": false,
  "message": "Failure message",
  "data": null,
  "errors": { "field": ["error detail"] }
}
```

---

## 3) Core Domain Models

- `User` (custom user, email login)
- `Listing`
- `EscrowTransaction`
- `PaymentRecord`
- `PayoutRecord`
- `RefundRecord`
- `Dispute`
- `AuditLog`
- `PaystackWebhookEvent` (webhook idempotency/dedup)
- `SellerPayoutDetail` (seller transfer destination)

---

## 4) State Machines

### Escrow status
- `pending`
- `payment_pending`
- `funded`
- `released`
- `refunded`
- `disputed`
- `cancelled`
- `completed`

### Payment status
- `initialized`
- `pending`
- `success`
- `failed`
- `reversed`

### Key transition intent
- `pending -> payment_pending -> funded`
- `funded -> released -> completed` (after payout success)
- `funded -> refunded`
- `funded/released -> disputed` (when dispute is opened)

Invalid transitions are blocked by explicit checks in views.

---

## 5) Endpoints by Domain

## Accounts (`/api/v1/accounts/`)
- `POST register/`
- `POST login/`
- `POST token/` (alias of login in this project)
- `POST token/refresh/`
- `GET profile/`

## Listings (`/api/v1/listings/`)
- `GET /` (public active listings)
- `POST create/` (auth, seller creates)
- `GET <listing_id>/` (public; inactive hidden from non-owner)
- `PATCH <listing_id>/update/` (owner only)
- `POST <listing_id>/deactivate/` (owner only)

## Escrow (`/api/v1/escrow/`)
- `POST create/` (auth buyer creates escrow from `listing_id`)
- `POST <escrow_id>/release/` (buyer only; funded only; blocked if disputed)
- `POST <escrow_id>/refund/` (buyer/admin; funded only; blocked if disputed/released)

## Payments (`/api/v1/payments/`)
- `POST initialize/` (buyer only; escrow must be payable)
- `POST verify/` (buyer only; backend verify by reference)
- `POST webhooks/paystack/` (signature-verified webhook)
- `POST payout-details/` (create/upsert seller payout destination)
- `PATCH payout-details/update/` (update seller payout destination)
- `POST payouts/<payout_id>/execute/` (seller/admin executes provider transfer)

## Disputes (`/api/v1/disputes/`)
- `POST create/` (buyer or seller on escrow)
- `GET /` (own disputes unless admin)
- `GET <dispute_id>/` (participant/admin only)
- `POST <dispute_id>/resolve/` (admin only; outcome `release` or `refund`)

---

## 6) Full Workflow (Happy Path)

1. User registers/logs in and gets JWT.
2. Seller creates listing.
3. Buyer picks listing and creates escrow from that listing.
   - Escrow snapshots listing title/description/amount/seller.
4. Buyer initializes payment for that escrow.
   - `PaymentRecord` created/updated with Paystack init data.
   - Escrow moves to `payment_pending` when appropriate.
5. Payment is verified (buyer verify endpoint or Paystack webhook).
   - Amount/currency/reference validated.
   - `PaymentRecord` updated.
   - Escrow moves to `funded` only on verified success.
6. Buyer releases escrow.
   - Creates `PayoutRecord` (queued/intent).
   - Escrow moves to `released`.
7. Seller/admin executes payout via Paystack transfer.
   - Uses seller payout details.
   - On transfer success, escrow moves `released -> completed`.

---

## 7) Refund Workflow

1. Escrow must be `funded`.
2. Buyer/admin triggers refund endpoint.
3. Backend ensures successful original Paystack payment exists.
4. Creates `RefundRecord`.
5. Executes Paystack refund call.
6. On confirmed refund success:
   - `RefundRecord` set success with provider refs/metadata.
   - Escrow moves to `refunded`.

Blocked cases:
- already released/completed,
- already refunded,
- disputed escrow,
- duplicate refund intent/status.

---

## 8) Dispute Workflow

1. Buyer or seller on escrow opens dispute (eligible statuses only).
2. Escrow moves to `disputed`.
3. While disputed, release/refund operations are blocked.
4. Admin resolves dispute via:
   - `release` outcome: create payout intent + escrow to `released`
   - `refund` outcome: create/execute refund + escrow to `refunded` on success
5. Dispute marked `resolved` with resolution outcome/notes.

Duplicate resolution is blocked (only open disputes can be resolved).

---

## 9) Permission Rules (Financial Safety)

- Listing update/deactivate: owner only.
- Escrow creation: authenticated non-owner buyer.
- Payment initialize/verify: escrow buyer only.
- Release: escrow buyer only.
- Refund: escrow buyer or admin.
- Payout execution: escrow seller or admin.
- Dispute creation: escrow buyer or seller only.
- Dispute detail/list: involved users only, except admin sees all.
- Dispute resolution: admin only.

---

## 10) Idempotency & Double-Action Protection

- Duplicate webhook protection:
  - by payload hash,
  - and webhook identity (`event + event_id + reference`) where available.
- Double funding protection:
  - escrow funding requires valid pre-funding states,
  - already funded escrows are handled idempotently.
- Payment status regression hardening:
  - a previously `success` payment is not downgraded by later inconsistent responses.
- Double release/payout/refund protection:
  - existing pending/processing/success records block repeats.

---

## 11) Audit Logging Coverage

Critical events logged in `AuditLog` with actor, action, object reference, timestamp, metadata:
- listing created
- escrow created
- payment initialized
- payment verified
- escrow funded
- release triggered
- payout executed
- refund triggered
- dispute opened
- dispute resolved

This supports traceability and post-incident review.

---

## 12) Testing Coverage Summary

Tests cover:
- permissions and ownership checks,
- valid/invalid state transitions,
- duplicate operation prevention,
- webhook signature + dedup behavior,
- escrow/payment/release/refund/dispute flows,
- admin dispute resolution paths.

Current suites pass with financial guardrails enforced.
