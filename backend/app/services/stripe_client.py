"""The few Stripe calls billing needs, made directly against Stripe's REST API (no SDK).

An invoice of ours becomes a Stripe invoice the company pays on Stripe's own hosted page:

    customer (once per company) -> invoice (draft) -> one item per line -> finalize -> send

Stripe then emails the company the invoice with a "Pay" link, and tells us on the webhook
(/webhooks/stripe) when it has been paid.
"""
import hashlib
import hmac
import logging
import time
from typing import Any

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

# Currencies Stripe counts in whole units (no cents / paise).
ZERO_DECIMAL = {"BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF"}


class StripeError(RuntimeError):
    pass


def configured() -> bool:
    return bool(settings.STRIPE_SECRET_KEY)


def minor_units(amount: Any, currency: str) -> int:
    """1499.50 INR -> 149950 (paise): Stripe wants amounts in the smallest unit."""
    return int(round(float(amount) * (1 if currency.upper() in ZERO_DECIMAL else 100)))


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """{"metadata": {"a": 1}} -> {"metadata[a]": "1"}: Stripe takes nested values as bracketed form fields."""
    out: dict[str, str] = {}
    for key, value in data.items():
        name = f"{prefix}[{key}]" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten(value, name))
        elif isinstance(value, bool):
            out[name] = "true" if value else "false"
        elif value is not None:
            out[name] = str(value)
    return out


async def _post(path: str, data: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
    if not configured():
        raise StripeError("Stripe is not connected: set STRIPE_SECRET_KEY in the server settings.")
    headers = {"Authorization": f"Bearer {settings.STRIPE_SECRET_KEY}"}
    if idempotency_key:  # a retried request is answered with the first result instead of acting twice
        headers["Idempotency-Key"] = idempotency_key
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{settings.STRIPE_API_BASE.rstrip('/')}/v1/{path}", data=_flatten(data), headers=headers)
    if resp.is_error:
        try:
            detail = resp.json().get("error", {}).get("message") or resp.text
        except ValueError:
            detail = resp.text
        raise StripeError(f"Stripe refused the request ({resp.status_code}): {detail[:300]}")
    return resp.json()


async def create_customer(name: str, email: str | None, company_id: str, tax_id: str | None = None) -> str:
    data: dict[str, Any] = {"name": name, "email": email, "metadata": {"company_id": company_id}}
    if tax_id:
        data["metadata"]["tax_id"] = tax_id
    return (await _post("customers", data, f"customer-{company_id}"))["id"]


async def send_invoice(
    customer_id: str, key: str, currency: str, lines: list[tuple[str, Any]], days_until_due: int, description: str,
    metadata: dict[str, str],
) -> dict[str, Any]:
    """Create, finalize and email one invoice. lines: (description, amount). Returns Stripe's invoice.

    key: our invoice's own id, so trying again after a failure never makes a second Stripe invoice."""
    invoice = await _post("invoices", {
        "customer": customer_id, "collection_method": "send_invoice", "days_until_due": max(1, days_until_due),
        "auto_advance": False, "currency": currency.lower(), "description": description, "metadata": metadata,
        "pending_invoice_items_behavior": "exclude",
    }, f"invoice-{key}")
    for i, (text, amount) in enumerate(lines):
        await _post("invoiceitems", {
            "customer": customer_id, "invoice": invoice["id"], "currency": currency.lower(),
            "amount": minor_units(amount, currency), "description": text[:250],
        }, f"invoice-{key}-line-{i}")
    await _post(f"invoices/{invoice['id']}/finalize", {"auto_advance": False}, f"invoice-{key}-finalize")
    return await _post(f"invoices/{invoice['id']}/send", {}, f"invoice-{key}-send")


async def void_invoice(stripe_invoice_id: str) -> None:
    await _post(f"invoices/{stripe_invoice_id}/void", {})


async def mark_paid_outside_stripe(stripe_invoice_id: str) -> None:
    """The company paid by bank transfer: close Stripe's copy too, so its "Pay" link stops asking for money."""
    await _post(f"invoices/{stripe_invoice_id}/pay", {"paid_out_of_band": True})


def verify_webhook(payload: bytes, signature_header: str | None, tolerance_seconds: int = 300) -> bool:
    """Check Stripe's signature on a webhook ("t=<time>,v1=<hmac of 'time.payload'>")."""
    secret = settings.STRIPE_WEBHOOK_SECRET
    if not secret or not signature_header:
        return False
    parts = dict(item.split("=", 1) for item in signature_header.split(",") if "=" in item)
    timestamp, given = parts.get("t", ""), [v for k, v in (i.split("=", 1) for i in signature_header.split(",") if "=" in i) if k == "v1"]
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > tolerance_seconds:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, g) for g in given)
