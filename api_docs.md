# API Docs (Frontend Guide)

This document explains all backend endpoints in a frontend-friendly way, including:
- URL
- method
- auth requirements
- request payload
- response shape and examples

Base API path: `/api/v1`

Example full base URL (local): `http://127.0.0.1:8000/api/v1`

---

## 1) Common Rules

## Auth
- The API uses JWT Bearer tokens for protected endpoints.
- Send token in header:

```http
Authorization: Bearer <access_token>
```

## Response format (all custom endpoints)
```json
{
  "success": true,
  "message": "Operation message",
  "data": {},
  "errors": null
}
```

Validation/auth failures:
```json
{
  "success": false,
  "message": "Failure message",
  "data": null,
  "errors": {
    "field_name": ["error detail"]
  }
}
```

## Date/time format
- Datetimes are ISO strings (example: `"2026-04-14T15:10:20.123456Z"`).

## Money format
- Money values are handled as Decimal on backend.
- In responses, amounts/prices are typically returned as strings (example: `"1200.00"`).

---

## 2) Auth Endpoints

## POST `/accounts/register/`
Create a new user and immediately return JWT tokens.

Auth: Not required

Request:
```json
{
  "email": "buyer@example.com",
  "password": "StrongPass123!",
  "first_name": "Ada",
  "last_name": "Buyer"
}
```

Success (`201`):
```json
{
  "success": true,
  "message": "Registration successful.",
  "data": {
    "user": {
      "id": 3,
      "email": "buyer@example.com",
      "first_name": "Ada",
      "last_name": "Buyer",
      "is_active": true,
      "date_joined": "2026-04-14T15:10:20.123456Z"
    },
    "tokens": {
      "refresh": "<jwt_refresh>",
      "access": "<jwt_access>"
    }
  },
  "errors": null
}
```

## POST `/accounts/login/`
Login with email/password and get tokens.

Auth: Not required

Request:
```json
{
  "email": "buyer@example.com",
  "password": "StrongPass123!"
}
```

Success (`200`): same data shape as register.

## POST `/accounts/token/`
Alias of login endpoint (same payload/response as `/accounts/login/`).

## POST `/accounts/token/refresh/`
Get a new access token from refresh token.

Auth: Not required

Request:
```json
{
  "refresh": "<jwt_refresh>"
}
```

Success (`200`):
```json
{
  "success": true,
  "message": "Token refreshed successfully.",
  "data": {
    "access": "<new_jwt_access>"
  },
  "errors": null
}
```

## GET `/accounts/profile/`
Get authenticated user profile.

Auth: Required

Success (`200`):
```json
{
  "success": true,
  "message": "Profile fetched successfully.",
  "data": {
    "id": 3,
    "email": "buyer@example.com",
    "first_name": "Ada",
    "last_name": "Buyer",
    "is_active": true,
    "date_joined": "2026-04-14T15:10:20.123456Z"
  },
  "errors": null
}
```

---

## 3) Listings Endpoints

## GET `/listings/`
Public list of active listings.

Auth: Not required

Success (`200`):
```json
{
  "success": true,
  "message": "Active listings fetched successfully.",
  "data": [
    {
      "id": 10,
      "seller": {
        "id": 2,
        "email": "seller@example.com",
        "name": "Seller One"
      },
      "title": "MacBook Pro",
      "description": "16 inch model",
      "listing_type": "product",
      "listing_type_display": "Product",
      "price": "2500.00",
      "is_active": true,
      "created_at": "2026-04-14T12:00:00.000000Z",
      "updated_at": "2026-04-14T12:00:00.000000Z"
    }
  ],
  "errors": null
}
```

## POST `/listings/create/`
Create a listing as authenticated seller.

Auth: Required

Request:
```json
{
  "title": "Logo Design",
  "description": "3 concepts + revisions",
  "listing_type": "service",
  "price": "150.00"
}
```

Success (`201`): returns created listing object.

## GET `/listings/{listing_id}/`
Get listing details.

Auth: Not required

Notes:
- Active listing is public.
- Inactive listing returns `404` for non-owner.

## PATCH `/listings/{listing_id}/update/`
Update own listing.

Auth: Required (owner only)

Request (partial):
```json
{
  "title": "Logo Design Pro",
  "price": "180.00",
  "is_active": true
}
```

Success (`200`): updated listing object.

## POST `/listings/{listing_id}/deactivate/`
Deactivate own listing.

Auth: Required (owner only)

Request body: `{}` (or empty)

Success (`200`): listing object with `is_active: false`.

---

## 4) Escrow Endpoints

## POST `/escrow/create/`
Create escrow from an active listing.

Auth: Required (buyer)

Request:
```json
{
  "listing_id": 10
}
```

Success (`201`):
```json
{
  "success": true,
  "message": "Escrow created successfully.",
  "data": {
    "id": 21,
    "listing": {
      "id": 10,
      "title": "MacBook Pro",
      "listing_type": "product",
      "is_active": true
    },
    "buyer": {
      "id": 3,
      "email": "buyer@example.com",
      "name": "Ada Buyer"
    },
    "seller": {
      "id": 2,
      "email": "seller@example.com",
      "name": "Seller One"
    },
    "amount": "2500.00",
    "title_snapshot": "MacBook Pro",
    "description_snapshot": "16 inch model",
    "status": "pending",
    "status_display": "Pending",
    "created_at": "2026-04-14T12:10:00.000000Z",
    "updated_at": "2026-04-14T12:10:00.000000Z"
  },
  "errors": null
}
```

Common errors:
- listing not found (`404`)
- listing inactive (`400`)
- self-purchase blocked (`400`)

## POST `/escrow/{escrow_id}/release/`
Buyer releases funded escrow (creates payout intent).

Auth: Required (buyer only)

Request:
```json
{}
```

Success (`200`):
```json
{
  "success": true,
  "message": "Escrow released successfully. Payout queued.",
  "data": {
    "escrow_id": 21,
    "escrow_status": "released",
    "payout": {
      "id": 8,
      "escrow_id": 21,
      "reference": "payout_21_xxxxxxxx",
      "amount": "2500.00",
      "currency": "NGN",
      "status": "pending",
      "initiated_by": 3,
      "created_at": "2026-04-14T12:30:00.000000Z"
    }
  },
  "errors": null
}
```

## POST `/escrow/{escrow_id}/refund/`
Trigger refund for funded escrow.

Auth: Required (buyer or admin)

Request:
```json
{
  "reason": "Seller could not deliver"
}
```

Success (`200`, when refund succeeds immediately):
```json
{
  "success": true,
  "message": "Refund completed successfully.",
  "data": {
    "escrow_id": 21,
    "escrow_status": "refunded",
    "refund": {
      "id": 5,
      "escrow_id": 21,
      "reference": "refund_21_xxxxxxxx",
      "provider_reference": "202020",
      "amount": "2500.00",
      "currency": "NGN",
      "status": "success",
      "reason": "Seller could not deliver",
      "initiated_by": 3,
      "processed_at": "2026-04-14T12:40:00.000000Z",
      "created_at": "2026-04-14T12:40:00.000000Z"
    }
  },
  "errors": null
}
```

Can also return `400` if refund is not yet in success state (processing path).

---

## 5) Payments Endpoints

## POST `/payments/initialize/`
Initialize Paystack payment for an escrow.

Auth: Required (buyer only)

Request:
```json
{
  "escrow_id": 21
}
```

Success (`200`):
```json
{
  "success": true,
  "message": "Payment initialized successfully.",
  "data": {
    "escrow_id": 21,
    "escrow_status": "payment_pending",
    "payment": {
      "id": 14,
      "escrow_id": 21,
      "provider": "paystack",
      "reference": "escrow_21_abc123def456",
      "amount": "2500.00",
      "currency": "NGN",
      "status": "initialized",
      "authorization_url": "https://checkout.paystack.com/abc123",
      "gateway_metadata": {},
      "created_at": "2026-04-14T12:20:00.000000Z",
      "updated_at": "2026-04-14T12:20:00.000000Z"
    },
    "checkout": {
      "authorization_url": "https://checkout.paystack.com/abc123",
      "access_code": "abc123",
      "reference": "escrow_21_abc123def456"
    }
  },
  "errors": null
}
```

## POST `/payments/verify/`
Verify payment reference and fund escrow on success.

Auth: Required (buyer only)

Request:
```json
{
  "reference": "escrow_21_abc123def456"
}
```

Success (`200`) message can be:
- `"Payment verified successfully and escrow funded."`
- `"Payment already verified and escrow already funded."`

Data:
```json
{
  "escrow_id": 21,
  "escrow_status": "funded",
  "payment": {
    "id": 14,
    "escrow_id": 21,
    "provider": "paystack",
    "reference": "escrow_21_abc123def456",
    "amount": "2500.00",
    "currency": "NGN",
    "status": "success",
    "authorization_url": "https://checkout.paystack.com/abc123",
    "gateway_metadata": {},
    "created_at": "2026-04-14T12:20:00.000000Z",
    "updated_at": "2026-04-14T12:22:00.000000Z"
  }
}
```

## POST `/payments/webhooks/paystack/`
Paystack webhook endpoint.

Auth: No JWT (signature-based)

Important:
- This is usually called by Paystack, not frontend.
- Backend verifies `x-paystack-signature`.
- Duplicate webhooks are safely ignored.

Example duplicate response (`200`):
```json
{
  "success": true,
  "message": "Duplicate webhook event ignored.",
  "data": {
    "duplicate": true
  },
  "errors": null
}
```

## POST `/payments/payout-details/`
Create or upsert seller payout destination.

Auth: Required

Request:
```json
{
  "bank_code": "058",
  "account_number": "0123456789",
  "account_name": "Seller One",
  "currency": "NGN"
}
```

Success (`201` created or `200` updated):
```json
{
  "success": true,
  "message": "Payout details created successfully.",
  "data": {
    "id": 4,
    "user_id": 2,
    "provider": "paystack",
    "bank_code": "058",
    "account_number": "0123456789",
    "account_name": "Seller One",
    "currency": "NGN",
    "recipient_code": null,
    "recipient_reference": null,
    "is_active": true,
    "created_at": "2026-04-14T12:35:00.000000Z",
    "updated_at": "2026-04-14T12:35:00.000000Z"
  },
  "errors": null
}
```

## PATCH `/payments/payout-details/update/`
Update seller payout details.

Auth: Required

Request (partial):
```json
{
  "bank_code": "033",
  "account_number": "9998887776"
}
```

Success (`200`): same shape as payout-details object above.

## POST `/payments/payouts/{payout_id}/execute/`
Execute payout transfer for released escrow.

Auth: Required (seller or admin)

Request:
```json
{}
```

Success (`200`) message can be:
- `"Payout executed successfully."`
- `"Payout is processing."`

Data:
```json
{
  "escrow_id": 21,
  "escrow_status": "completed",
  "payout": {
    "id": 8,
    "escrow_id": 21,
    "reference": "payout_21_xxxxxxxx",
    "provider_reference": "TRF_xxxxxxxx",
    "amount": "2500.00",
    "currency": "NGN",
    "status": "success",
    "initiated_by": 3,
    "processed_at": "2026-04-14T12:45:00.000000Z",
    "metadata": {},
    "created_at": "2026-04-14T12:30:00.000000Z",
    "updated_at": "2026-04-14T12:45:00.000000Z"
  }
}
```

---

## 6) Disputes Endpoints

## POST `/disputes/create/`
Open dispute (buyer or seller on that escrow).

Auth: Required

Request:
```json
{
  "escrow_id": 21,
  "reason": "Item not as described"
}
```

Success (`201`):
```json
{
  "success": true,
  "message": "Dispute created successfully.",
  "data": {
    "id": 6,
    "escrow": {
      "id": 21,
      "status": "disputed",
      "buyer_id": 3,
      "seller_id": 2,
      "amount": "2500.00"
    },
    "raised_by": {
      "id": 3,
      "email": "buyer@example.com",
      "name": "Ada Buyer"
    },
    "reason": "Item not as described",
    "status": "open",
    "status_display": "Open",
    "resolution_outcome": null,
    "resolution_outcome_display": null,
    "resolution_notes": null,
    "created_at": "2026-04-14T12:50:00.000000Z",
    "updated_at": "2026-04-14T12:50:00.000000Z"
  },
  "errors": null
}
```

## GET `/disputes/`
List disputes visible to current user.

Auth: Required

Notes:
- Admin sees all disputes.
- Non-admin sees only involved disputes.

## GET `/disputes/{dispute_id}/`
Get dispute details if requester is participant or admin.

Auth: Required

## POST `/disputes/{dispute_id}/resolve/`
Resolve dispute (admin only).

Auth: Required (admin)

Request:
```json
{
  "outcome": "release",
  "resolution_notes": "Delivery confirmed"
}
```

or
```json
{
  "outcome": "refund",
  "resolution_notes": "Refund approved"
}
```

Success (`200` with `release`):
```json
{
  "success": true,
  "message": "Dispute resolved with release.",
  "data": {
    "dispute": { "...": "dispute object" },
    "escrow_status": "released",
    "payout": {
      "id": 9,
      "escrow_id": 21,
      "reference": "payout_21_yyyyyyyy",
      "provider_reference": null,
      "amount": "2500.00",
      "currency": "NGN",
      "status": "pending",
      "initiated_by": 1,
      "processed_at": null,
      "created_at": "2026-04-14T13:00:00.000000Z"
    }
  },
  "errors": null
}
```

Success (`200` with `refund`) returns `dispute`, `escrow_status: refunded`, and `refund` payload.

---

## 7) Frontend Integration Cheat Sheet

Typical buyer checkout flow:
1. Login/register and store `access` + `refresh`.
2. Fetch listings (`GET /listings/`).
3. Create escrow (`POST /escrow/create/`).
4. Initialize payment (`POST /payments/initialize/`).
5. Redirect user to `checkout.authorization_url`.
6. After return/callback, verify (`POST /payments/verify/`).
7. Show escrow funded status.
8. Later, buyer can release (`/escrow/{id}/release/`) or raise dispute (`/disputes/create/`).

Typical seller payout flow:
1. Save payout details (`POST /payments/payout-details/`).
2. After buyer release, execute payout (`POST /payments/payouts/{payout_id}/execute/`).

---

## 8) Useful Status Values for UI

Escrow `status`:
- `pending`
- `payment_pending`
- `funded`
- `released`
- `refunded`
- `disputed`
- `cancelled`
- `completed`

Payment `status`:
- `initialized`
- `pending`
- `success`
- `failed`
- `reversed`

Payout/Refund `status`:
- `pending`
- `processing`
- `success`
- `failed`
- `reversed`

Dispute `status`:
- `open`
- `resolved`
- `rejected`

Dispute `resolution_outcome`:
- `release`
- `refund`

---

## 9) Common Error Cases to Handle in UI

- `401`: missing/expired token (for protected endpoints)
- `403`: permission denied (wrong actor)
- `404`: resource not found / hidden inactive listing
- `400`: validation failure or invalid state transition
- `502`: upstream Paystack network/reachability issue
- `500`: unexpected backend/provider processing issue

Tip:
- Always show `message`.
- Show field-specific messages from `errors` when present.
