# Escrow Backend Readme

## 1. Overview

This project is a Django + DRF backend for a listing-based direct escrow payment system.

What it does:
- lets sellers create listings (`product` or `service`)
- lets buyers create escrow transactions from active listings
- initializes and verifies Paystack payments for a specific escrow
- moves escrow through strict financial states (`pending`, `funded`, `released`, `refunded`, etc.)
- supports disputes and admin-side dispute resolution
- keeps an audit trail for critical financial events

Key actors:
- `Buyer`: funds escrow, can release, can request refund (under rules), can raise dispute
- `Seller`: creates/manages listings, sets payout details, executes payout, can raise dispute
- `Admin`: can view all disputes and resolve disputes (`release` or `refund`)

---

## 2. Core Models

## Identity & Marketplace
- `User` (`accounts.User`)
  - Custom user model
  - Email is unique login identifier (`USERNAME_FIELD = email`)

- `Listing` (`listings.Listing`)
  - Owned by seller (`ForeignKey(User)`)
  - Fields: `title`, `description`, `listing_type`, `price`, `is_active`, timestamps
  - Source object used to create escrow snapshot

## Escrow Domain
- `EscrowTransaction` (`escrow.EscrowTransaction`)
  - Links one listing + buyer + seller
  - Stores immutable snapshots: `title_snapshot`, `description_snapshot`, `amount`
  - Holds escrow lifecycle `status`
  - Constraint prevents `buyer == seller`

## Payments & Money Movement
- `PaymentRecord` (`payments.PaymentRecord`)
  - Tied to an escrow
  - Stores provider data for funding (`paystack`, `reference`, `amount`, `currency`, `status`, gateway metadata)

- `SellerPayoutDetail` (`payments.SellerPayoutDetail`)
  - One-to-one seller payout destination
  - Holds bank account details and Paystack recipient references

- `PayoutRecord` (`payments.PayoutRecord`)
  - Tied to an escrow
  - Represents payout intent/execution status and provider references

- `RefundRecord` (`payments.RefundRecord`)
  - Tied to an escrow
  - Represents refund intent/execution status and provider references

- `PaystackWebhookEvent` (`payments.PaystackWebhookEvent`)
  - Stores incoming webhook identity/hash/payload for idempotency and traceability

## Disputes & Audit
- `Dispute` (`disputes.Dispute`)
  - Tied to escrow
  - Raised by buyer or seller
  - Tracks dispute status and resolution outcome

- `AuditLog` (`core.AuditLog`)
  - Immutable log for critical events (listing creation, escrow creation, payment lifecycle, release, payout, refund, dispute actions)

## Relationship Summary
- `User (seller) -> Listing`
- `Listing + buyer + seller -> EscrowTransaction`
- `EscrowTransaction -> PaymentRecord / PayoutRecord / RefundRecord / Dispute`
- `User (seller) -> SellerPayoutDetail (1:1)`
- `AuditLog` references actor + object metadata across all domains

---

## 3. Workflow (Step-by-step)

## A) Escrow Creation Tied to Listing
1. Seller creates an active listing.
2. Buyer submits `listing_id` to create escrow.
3. System validates:
   - listing exists
   - listing is active
   - buyer is not listing owner
4. Escrow is created with snapshot fields from listing:
   - `amount = listing.price`
   - `seller = listing.seller`
   - `title_snapshot`, `description_snapshot`
5. Escrow starts in `pending`.

## B) Funding Escrow (Paystack -> Escrow)
1. Buyer initializes payment for escrow.
2. System validates:
   - escrow exists
   - requester is escrow buyer
   - escrow state is `pending` or `payment_pending`
3. Backend calls Paystack initialize API and stores/updates `PaymentRecord` as `initialized`.
4. Escrow moves to `payment_pending` (if previously `pending`).
5. Payment is verified via:
   - buyer verification endpoint, or
   - Paystack webhook (`charge.success`)
6. Verification checks:
   - reference matches
   - amount matches escrow amount
   - currency matches expected currency
7. On verified success:
   - `PaymentRecord.status = success`
   - escrow moves to `funded`

## C) Holding State
Escrow is effectively in holding when:
- payment is in progress (`payment_pending`), or
- payment succeeded and funds are held (`funded`)

No payout/refund is allowed until escrow is safely in the correct state.

## D) Release Conditions
1. Buyer triggers release endpoint.
2. System validates:
   - requester is escrow buyer
   - escrow is `funded`
   - escrow is not disputed
   - no existing payout already pending/processing/success
3. System creates `PayoutRecord` (pending intent) and sets escrow to `released`.
4. Seller or admin executes payout via Paystack transfer.
5. On transfer success:
   - `PayoutRecord.status = success`
   - escrow moves `released -> completed`

## E) Dispute Handling
1. Buyer or seller opens dispute on eligible escrow.
2. Escrow is moved to `disputed`.
3. While disputed:
   - release is blocked
   - refund is blocked
4. Admin resolves dispute with:
   - `release`: creates payout intent + escrow to `released`
   - `refund`: creates refund intent, executes refund, escrow to `refunded` on success
5. Dispute is marked `resolved` with resolution outcome and notes.

## F) Refund Flow
1. Buyer/admin triggers refund for funded escrow.
2. System validates:
   - escrow is `funded`
   - escrow is not released/completed/disputed/refunded
   - successful Paystack payment exists for that escrow
   - no existing active refund in progress/success
3. Creates `RefundRecord` and executes Paystack refund.
4. On confirmed success:
   - `RefundRecord.status = success`
   - escrow moves to `refunded`

---

## 4. State Transitions

## Escrow States
- `pending`
- `payment_pending`
- `funded`
- `released`
- `refunded`
- `disputed`
- `cancelled`
- `completed`

## Active Transition Paths (implemented)
- `pending -> payment_pending`
- `payment_pending -> funded`
- `pending -> funded` (possible if verification comes before status switch finalizes)
- `funded -> released`
- `released -> completed` (after successful payout execution)
- `funded -> refunded`
- `funded -> disputed`
- `released -> disputed`
- `disputed -> released` (admin resolve with release)
- `disputed -> refunded` (admin resolve with refund)

## Reserved/Not Yet Exposed by Endpoint
- `cancelled` exists in enum but no direct cancellation endpoint yet.

---

## 5. API Endpoints

Base prefix: `/api/v1`

## Auth
- `POST /accounts/register/`
  - Request: `email`, `password`, optional `first_name`, `last_name`
  - Response data: `user`, `tokens {access, refresh}`

- `POST /accounts/login/` (also `/accounts/token/`)
  - Request: `email`, `password`
  - Response data: `user`, `tokens`

- `POST /accounts/token/refresh/`
  - Request: `refresh`
  - Response data: `access`

- `GET /accounts/profile/`
  - Auth required
  - Response data: current user profile

## Listings
- `GET /listings/`
  - Public active listings

- `POST /listings/create/`
  - Auth required (seller)
  - Request: `title`, `description`, `listing_type`, `price`

- `GET /listings/{listing_id}/`
  - Public detail for active listing
  - Inactive listing visible only to owner

- `PATCH /listings/{listing_id}/update/`
  - Owner only
  - Request: one or more of `title`, `description`, `listing_type`, `price`, `is_active`

- `POST /listings/{listing_id}/deactivate/`
  - Owner only

## Escrow
- `POST /escrow/create/`
  - Auth required (buyer)
  - Request: `listing_id`
  - Response data: escrow with snapshots

- `POST /escrow/{escrow_id}/release/`
  - Buyer only
  - Escrow must be funded and not disputed

- `POST /escrow/{escrow_id}/refund/`
  - Buyer or admin
  - Escrow must be funded and not disputed/released/completed
  - Optional request field: `reason`

## Payments
- `POST /payments/initialize/`
  - Buyer only
  - Request: `escrow_id`
  - Response data includes checkout info (`authorization_url`, `access_code`, `reference`)

- `POST /payments/verify/`
  - Buyer only
  - Request: `reference`
  - Funds escrow on verified success

- `POST /payments/webhooks/paystack/`
  - Paystack callback endpoint
  - Validates signature and deduplicates events

- `POST /payments/payout-details/`
  - Auth user sets own payout destination
  - Request: `bank_code`, `account_number`, optional `account_name`, optional `currency`

- `PATCH /payments/payout-details/update/`
  - Auth user updates own payout destination
  - Request: any of `bank_code`, `account_number`, `account_name`, `currency`, `is_active`

- `POST /payments/payouts/{payout_id}/execute/`
  - Seller or admin
  - Executes transfer via Paystack

## Disputes
- `POST /disputes/create/`
  - Buyer or seller on escrow
  - Request: `escrow_id`, `reason`

- `GET /disputes/`
  - Auth required
  - Admin sees all; others see own/involved disputes

- `GET /disputes/{dispute_id}/`
  - Involved user or admin only

- `POST /disputes/{dispute_id}/resolve/`
  - Admin only
  - Request: `outcome` (`release` or `refund`), optional `resolution_notes`

## Common Response Shape
```json
{
  "success": true,
  "message": "Operation message",
  "data": {},
  "errors": null
}
```

---

## 6. Validation & Safety Rules

## Monetary Safety
- Uses `DecimalField` for all money values (`price`, `amount`)
- No float-based money operations
- Verification compares amount in kobo with escrow amount converted from Decimal

## Atomicity & Concurrency
- Critical financial paths use `transaction.atomic()`
- Sensitive rows are locked with `select_for_update()` to prevent race conditions

## Double-Action Protection
- Double funding protection:
  - escrow can only be funded from allowed pre-funding states
  - duplicate success verification is handled idempotently
- Payment regression protection:
  - once payment is `success`, it is not downgraded by later inconsistent verify results
- Double release protection:
  - existing active payout records block repeated release attempts
- Double payout protection:
  - payout records in `processing/success` cannot be re-executed
- Double refund protection:
  - existing active refund records block repeated refund attempts

## Webhook Safety
- Paystack signature verification is mandatory
- Duplicate webhook handling checks:
  - payload hash
  - event identity (`event + event_id + reference`) when available

## Access Control
- Listing owner checks for updates/deactivation
- Buyer-only rules for payment init/verify and release
- Buyer/seller participation checks for disputes
- Admin-only dispute resolution

---

## 7. Edge Cases

## Failed Payments
- If Paystack verification returns non-success status:
  - payment is updated accordingly
  - escrow is not funded
- Provider/network errors return appropriate server errors (`502` for upstream reachability issues)

## User Cancellation
- Explicit escrow cancellation endpoint is not yet implemented.
- `cancelled` exists as a status enum value but is currently not driven by active API flow.

## Timeouts
- Outbound Paystack API calls use request timeout (`20s`) in provider service.
- On timeout/network failure:
  - initialization/verification/payout/refund surfaces provider error paths
- Escrow auto-timeout/auto-cancel job is not currently implemented.

---

## Additional Notes
- All critical lifecycle steps write to `AuditLog` for traceability.
- The architecture keeps escrow business state separate from Paystack provider calls.
- Wallet logic is intentionally not part of this system.
