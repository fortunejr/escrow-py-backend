# Frontend Build Guide: Endpoints + Required Pages

This file lists all current backend endpoints for the escrow payment system and maps them to frontend pages.

Base API prefix: `/api/v1`

---

## 1) Anonymous Endpoints

These endpoints do not require JWT access token.

| Method | Endpoint | Purpose | Frontend Usage |
|---|---|---|---|
| `POST` | `/api/v1/accounts/register/` | Create account and return tokens | Register page |
| `POST` | `/api/v1/accounts/login/` | Login and return tokens | Login page |
| `POST` | `/api/v1/accounts/token/` | Alias of login | Usually not needed if `/login/` is used |
| `POST` | `/api/v1/accounts/token/refresh/` | Refresh access token | Silent token refresh logic |
| `GET` | `/api/v1/listings/` | Public active listings | Home/Marketplace page |
| `GET` | `/api/v1/listings/{listing_id}/` | Public listing detail (inactive hidden for non-owner) | Listing detail page |
| `POST` | `/api/v1/payments/webhooks/paystack/` | Paystack webhook receiver | Not a frontend endpoint (provider callback only) |

---

## 2) Authenticated Endpoints

These require `Authorization: Bearer <access_token>`.

## Account
| Method | Endpoint | Role Notes | Frontend Page |
|---|---|---|---|
| `GET` | `/api/v1/accounts/profile/` | Any logged-in user | Profile page |

## Listings
| Method | Endpoint | Role Notes | Frontend Page |
|---|---|---|---|
| `POST` | `/api/v1/listings/create/` | Seller (owner of created listing) | Seller Create Listing page |
| `PATCH` | `/api/v1/listings/{listing_id}/update/` | Listing owner only | Seller Edit Listing page |
| `POST` | `/api/v1/listings/{listing_id}/deactivate/` | Listing owner only | Seller Listing Management page |

## Escrow
| Method | Endpoint | Role Notes | Frontend Page |
|---|---|---|---|
| `POST` | `/api/v1/escrow/create/` | Buyer only, cannot buy own listing | Buyer Checkout/Start Escrow flow |
| `POST` | `/api/v1/escrow/{escrow_id}/release/` | Buyer only, funded escrow required | Buyer Escrow Detail page (Release action) |
| `POST` | `/api/v1/escrow/{escrow_id}/refund/` | Buyer or admin (state-restricted) | Buyer Escrow Detail page (Refund action) |

## Payments
| Method | Endpoint | Role Notes | Frontend Page |
|---|---|---|---|
| `POST` | `/api/v1/payments/initialize/` | Escrow buyer only | Buyer Checkout page (Paystack init) |
| `POST` | `/api/v1/payments/verify/` | Escrow buyer only | Buyer Payment Callback/Confirmation page |
| `POST` | `/api/v1/payments/payout-details/` | Logged-in user (seller profile use-case) | Seller Payout Details page |
| `PATCH` | `/api/v1/payments/payout-details/update/` | Logged-in user (own payout details) | Seller Payout Details page |
| `POST` | `/api/v1/payments/payouts/{payout_id}/execute/` | Escrow seller or admin | Seller Payout Action page |

## Disputes
| Method | Endpoint | Role Notes | Frontend Page |
|---|---|---|---|
| `POST` | `/api/v1/disputes/create/` | Escrow buyer or seller only | Dispute Create modal/page |
| `GET` | `/api/v1/disputes/` | Own disputes (admin sees all) | Disputes List page |
| `GET` | `/api/v1/disputes/{dispute_id}/` | Participant or admin | Dispute Detail page |

---

## 3) Admin-Only Endpoints

| Method | Endpoint | Purpose | Frontend Page |
|---|---|---|---|
| `POST` | `/api/v1/disputes/{dispute_id}/resolve/` | Resolve dispute with `release` or `refund` outcome | Admin Dispute Resolution page |

Non-API admin UI:
- `/admin/` (Django admin panel, session-based)

---

## 4) Suggested Frontend Pages (by role)

## Public / Anonymous
1. Landing page
2. Marketplace listings page
3. Listing detail page
4. Login page
5. Register page

## Shared Authenticated
1. User profile page
2. Global dashboard shell (role-aware navigation)

## Buyer-Focused
1. Buyer checkout/start escrow page
2. Payment redirect/confirmation page
3. Buyer escrow detail page (release/refund actions)
4. Buyer disputes list page
5. Buyer dispute detail page

## Seller-Focused
1. Seller create listing page
2. Seller edit/manage listing page
3. Seller payout details page
4. Seller payout execution page
5. Seller disputes list/detail page

## Admin-Focused
1. Admin disputes list page
2. Admin dispute detail page
3. Admin dispute resolution action page (`release`/`refund`)

---

## 5) Current Backend Gaps You Should Know (for frontend planning)

The following are not currently exposed as dedicated endpoints:
- list my escrows
- get escrow detail by ID (read-only endpoint)
- list my payouts / refunds / payments
- list my listings (separate from public active list)

Impact:
- Some dashboard-style pages (buyer escrow history, seller escrow queue, payout history) will need new backend endpoints before full implementation.

---

## 6) Recommended Frontend Routing Skeleton

You can use this as a starting route map:

```txt
/login
/register
/marketplace
/listings/:listingId
/profile
/seller/listings/new
/seller/listings/:listingId/edit
/seller/payout-details
/seller/payouts/:payoutId
/buyer/checkout/:listingId
/buyer/payment/verify
/buyer/escrows/:escrowId
/disputes
/disputes/:disputeId
/admin/disputes
/admin/disputes/:disputeId
```

---

## 7) Auth Handling Notes for Frontend

- Store `access` and `refresh` tokens securely.
- Attach access token to all authenticated requests.
- On `401`, try `/api/v1/accounts/token/refresh/` then retry original request.
- Webhook endpoint is never called by frontend.

