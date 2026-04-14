import json
import hmac
import hashlib
from urllib import error, request

from django.conf import settings


class PaystackInitializationError(Exception):
    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload or {}


class PaystackVerificationError(Exception):
    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload or {}


class PaystackPayoutError(Exception):
    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload or {}


def initialize_paystack_transaction(email, amount_kobo, reference, currency="NGN", metadata=None):
    secret_key = settings.PAYSTACK_SECRET_KEY
    if not secret_key:
        raise PaystackInitializationError("Paystack secret key is not configured.")

    payload = {
        "email": email,
        "amount": amount_kobo,
        "reference": reference,
        "currency": currency,
    }
    if metadata:
        payload["metadata"] = metadata

    url = f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/transaction/initialize"
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
    }
    req = request.Request(url=url, data=data, headers=headers, method="POST")

    try:
        with request.urlopen(req, timeout=20) as resp:
            raw_body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc else ""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        message = body.get("message") or "Paystack request failed."
        raise PaystackInitializationError(message, payload=body) from exc
    except error.URLError as exc:
        raise PaystackInitializationError("Unable to reach Paystack.") from exc

    try:
        response_data = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as exc:
        raise PaystackInitializationError("Invalid response from Paystack.") from exc

    if not response_data.get("status"):
        message = response_data.get("message") or "Paystack initialization failed."
        raise PaystackInitializationError(message, payload=response_data)

    return response_data


def verify_paystack_signature(raw_body, signature):
    secret_key = settings.PAYSTACK_SECRET_KEY
    if not secret_key or not signature:
        return False
    computed = hmac.new(secret_key.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(computed, signature)


def verify_paystack_transaction(reference):
    secret_key = settings.PAYSTACK_SECRET_KEY
    if not secret_key:
        raise PaystackVerificationError("Paystack secret key is not configured.")

    if not reference:
        raise PaystackVerificationError("Transaction reference is required.")

    url = f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/transaction/verify/{reference}"
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
    }
    req = request.Request(url=url, headers=headers, method="GET")

    try:
        with request.urlopen(req, timeout=20) as resp:
            raw_body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc else ""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        message = body.get("message") or "Paystack verification request failed."
        raise PaystackVerificationError(message, payload=body) from exc
    except error.URLError as exc:
        raise PaystackVerificationError("Unable to reach Paystack.") from exc

    try:
        response_data = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as exc:
        raise PaystackVerificationError("Invalid response from Paystack.") from exc

    if not response_data.get("status"):
        message = response_data.get("message") or "Paystack verification failed."
        raise PaystackVerificationError(message, payload=response_data)

    return response_data


def create_paystack_transfer_recipient(name, account_number, bank_code, currency="NGN"):
    secret_key = settings.PAYSTACK_SECRET_KEY
    if not secret_key:
        raise PaystackPayoutError("Paystack secret key is not configured.")

    payload = {
        "type": "nuban",
        "name": name,
        "account_number": account_number,
        "bank_code": bank_code,
        "currency": currency,
    }

    url = f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/transferrecipient"
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
    }
    req = request.Request(url=url, data=data, headers=headers, method="POST")

    try:
        with request.urlopen(req, timeout=20) as resp:
            raw_body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc else ""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        message = body.get("message") or "Paystack recipient creation failed."
        raise PaystackPayoutError(message, payload=body) from exc
    except error.URLError as exc:
        raise PaystackPayoutError("Unable to reach Paystack.") from exc

    try:
        response_data = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as exc:
        raise PaystackPayoutError("Invalid response from Paystack.") from exc

    if not response_data.get("status"):
        message = response_data.get("message") or "Paystack recipient creation failed."
        raise PaystackPayoutError(message, payload=response_data)

    return response_data


def initiate_paystack_transfer(amount_kobo, recipient_code, reference, reason=""):
    secret_key = settings.PAYSTACK_SECRET_KEY
    if not secret_key:
        raise PaystackPayoutError("Paystack secret key is not configured.")

    payload = {
        "source": "balance",
        "amount": amount_kobo,
        "recipient": recipient_code,
        "reference": reference,
    }
    if reason:
        payload["reason"] = reason

    url = f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/transfer"
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
    }
    req = request.Request(url=url, data=data, headers=headers, method="POST")

    try:
        with request.urlopen(req, timeout=20) as resp:
            raw_body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc else ""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        message = body.get("message") or "Paystack transfer initiation failed."
        raise PaystackPayoutError(message, payload=body) from exc
    except error.URLError as exc:
        raise PaystackPayoutError("Unable to reach Paystack.") from exc

    try:
        response_data = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as exc:
        raise PaystackPayoutError("Invalid response from Paystack.") from exc

    if not response_data.get("status"):
        message = response_data.get("message") or "Paystack transfer initiation failed."
        raise PaystackPayoutError(message, payload=response_data)

    return response_data


def initiate_paystack_refund(transaction_reference, amount_kobo=None):
    secret_key = settings.PAYSTACK_SECRET_KEY
    if not secret_key:
        raise PaystackPayoutError("Paystack secret key is not configured.")

    if not transaction_reference:
        raise PaystackPayoutError("Transaction reference is required for refund.")

    payload = {"transaction": transaction_reference}
    if amount_kobo is not None:
        payload["amount"] = amount_kobo

    url = f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/refund"
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
    }
    req = request.Request(url=url, data=data, headers=headers, method="POST")

    try:
        with request.urlopen(req, timeout=20) as resp:
            raw_body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc else ""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        message = body.get("message") or "Paystack refund initiation failed."
        raise PaystackPayoutError(message, payload=body) from exc
    except error.URLError as exc:
        raise PaystackPayoutError("Unable to reach Paystack.") from exc

    try:
        response_data = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as exc:
        raise PaystackPayoutError("Invalid response from Paystack.") from exc

    if not response_data.get("status"):
        message = response_data.get("message") or "Paystack refund initiation failed."
        raise PaystackPayoutError(message, payload=response_data)

    return response_data
