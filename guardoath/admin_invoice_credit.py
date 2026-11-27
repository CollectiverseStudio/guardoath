"""Apply an owner-reviewed pre-payment credit to one verified open Stripe invoice."""

import base64
import hashlib
import hmac
import os
import re
import time
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from guardoath.admin_audit import EVENT
from guardoath.admin_cancellation_provider import canonical, digest, provider_id
from guardoath.admin_invoices import CURRENCY
from guardoath.stripe_provider import _strict_provider_json


class CreditConflict(ValueError):
    """The selected invoice or the reviewed amount changed."""


class CreditUnavailable(Exception):
    """The provider could not establish the requested observation."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class StripeInvoiceCredits:
    """Fixed account, API version and operation. All amounts use minor units."""

    def __init__(self, read_key, write_key, account_id, *, opener=None):
        if (
            not provider_id(account_id, "acct")
            or not re.fullmatch(r"sk_test_[A-Za-z0-9_]{11,504}", read_key)
            or (write_key and not re.fullmatch(r"rk_test_[A-Za-z0-9_]{11,504}", write_key))
        ):
            raise ValueError(
                "Expected a test-mode invoice reader and a dedicated restricted credit-note key"
            )
        self.account_id = account_id
        self._reader, self._writer = read_key, write_key
        self.can_issue = bool(write_key)
        self._send = opener or urllib.request.build_opener(_NoRedirect()).open

    def _request(self, path, *, query=None, body=None, idem=None, writer=False):
        key = self._writer if writer else self._reader
        if not key or (body is not None and (not writer or path != "credit_notes" or not idem)):
            raise CreditUnavailable()
        headers = {
            "Authorization": "Basic " + base64.b64encode((key + ":").encode()).decode(),
            "Accept": "application/json",
            "Stripe-Version": "2025-02-24.acacia",
        }
        url = "https://api.stripe.com/v1/" + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        if idem:
            headers["Idempotency-Key"] = idem
        data = urllib.parse.urlencode(body).encode() if body is not None else None
        try:
            with self._send(
                urllib.request.Request(url, data=data, headers=headers), timeout=2
            ) as response:
                raw = response.read(262145)
                if response.status != 200 or len(raw) > 262144:
                    raise CreditUnavailable()
            return _strict_provider_json(raw)
        except Exception:
            raise CreditUnavailable() from None

    def check_account(self, *, writer=False):
        value = self._request("account", writer=writer)
        if value.get("object") != "account" or value.get("id") != self.account_id:
            raise CreditUnavailable()

    def invoice(self, record, invoice_id):
        if not provider_id(invoice_id, "in"):
            raise CreditConflict()
        self.check_account()
        value = self._request("invoices/" + invoice_id)
        balance = value.get("amount_remaining")
        credits = value.get("pre_payment_credit_notes_amount")
        if (
            value.get("object") != "invoice"
            or value.get("id") != invoice_id
            or value.get("customer") != record.payer_id
            or value.get("subscription") != record.stripe_subscription_id
            or value.get("livemode") is not False
            or value.get("status") != "open"
            or not isinstance(value.get("currency"), str)
            or not CURRENCY.fullmatch(value["currency"])
            or type(balance) is not int
            or not 1 <= balance <= 10**12
            or type(credits) is not int
            or not 0 <= credits <= 10**12
            or any(value.get(k, "missing") is not None for k in ("on_behalf_of", "transfer_data"))
        ):
            raise CreditConflict()
        return {
            "invoice": invoice_id,
            "customer": record.payer_id,
            "subscription": record.stripe_subscription_id,
            "currency": value["currency"],
            "remaining": balance,
            "prior_credits": credits,
        }

    def issue(self, review):
        # Zero post-payment allocations must sum to any post-payment amount.
        # Stripe therefore rejects a payment race that would require a refund
        # or a customer-balance credit. No transport retry is performed.
        body = {
            "invoice": review["invoice"],
            "amount": review["amount"],
            "reason": review["reason"],
            "email_type": "none",
            "refund_amount": 0,
            "credit_amount": 0,
            "out_of_band_amount": 0,
            "metadata[guardoath_credit_review]": review["audit"],
        }
        return self._request(
            "credit_notes", body=body, writer=True, idem="guardoath-credit-v1-" + review["balance_key"]
        )

    def find_receipt(self, review):
        self.check_account()
        after = None
        for _ in range(5):
            query = {"invoice": review["invoice"], "limit": 25}
            if after:
                query["starting_after"] = after
            page = self._request("credit_notes", query=query)
            notes = page.get("data")
            if (
                page.get("object") != "list"
                or not isinstance(notes, list)
                or len(notes) > 25
                or type(page.get("has_more")) is not bool
            ):
                raise CreditUnavailable()
            for note in notes:
                if not isinstance(note, dict) or note.get("invoice") != review["invoice"]:
                    raise CreditUnavailable()
                if not isinstance(note.get("metadata"), dict):
                    raise CreditUnavailable()
                if note["metadata"].get("guardoath_credit_review") == review["audit"]:
                    return self.receipt(note, review)
            if not page["has_more"]:
                return {"status": "unconfirmed"}
            last = notes[-1].get("id") if notes else None
            if not provider_id(last, "cn") or last == after:
                raise CreditUnavailable()
            after = last
        raise CreditUnavailable()

    @staticmethod
    def receipt(note, review):
        if (
            note.get("object") != "credit_note"
            or not provider_id(note.get("id"), "cn")
            or note.get("invoice") != review["invoice"]
            or note.get("customer") != review["customer"]
            or note.get("livemode") is not False
            or note.get("type") != "pre_payment"
            or note.get("status") not in ("issued", "void")
            or note.get("currency") != review["currency"]
            or note.get("reason") != review["reason"]
            or type(note.get("amount")) is not int
            or note["amount"] != review["amount"]
            or type(note.get("total")) is not int
            or note["total"] != review["amount"]
            or not isinstance(note.get("metadata"), dict)
            or note["metadata"].get("guardoath_credit_review") != review["audit"]
            or any(
                note.get(k, "missing") is not None for k in ("refund", "customer_balance_transaction")
            )
            or note.get("out_of_band_amount", "missing") not in (None, 0)
            or not isinstance(note.get("number"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", note["number"])
        ):
            raise CreditUnavailable()
        return {
            "status": note["status"],
            "number": note["number"],
            "currency": review["currency"],
            "amount_minor": review["amount"],
            "review_audit_id": review["audit"],
        }


class AdminInvoiceCredit:
    def __init__(self, provider, history, key):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("A persistent audit signing key is required")
        self.audit_key = key
        self.provider, self.history = provider, history
        self._cipher = AESGCM(hmac.digest(key, b"guardoath-invoice-credit-review-v1", hashlib.sha256))

    def _context(self, tenant, actor):
        return canonical(["credit-review-v1", self.provider.account_id, tenant, actor]).encode()

    def _open(self, token, tenant, actor):
        try:
            encoded = token.removeprefix("icv1.")
            if not token.startswith("icv1.") or not re.fullmatch(r"[A-Za-z0-9_-]{40,4050}", encoded):
                raise ValueError()
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            if base64.urlsafe_b64encode(raw).decode().rstrip("=") != encoded:
                raise ValueError()
            data = self._cipher.decrypt(raw[:12], raw[12:], self._context(tenant, actor))
            return _strict_provider_json(data)
        except Exception:
            raise CreditConflict() from None

    def _inspect(self, record, invoice):
        if (
            record.demo is not False
            or record.billing_mode != "stripe"
            or self.history.reporter.expected_livemode is not False
            or self.history.reporter.read(record).get("verified") is not True
        ):
            raise CreditConflict()
        return self.provider.invoice(record, invoice)

    def preview(self, record, actor, selection, amount, reason, audit):
        if not EVENT.fullmatch(audit) or type(amount) is not int or not 1 <= amount <= 99_999_999:
            raise CreditConflict()
        invoice = self.history.credit_references.open(
            selection, record, actor=actor, now=int(time.time())
        )
        observed = self._inspect(record, invoice)
        if amount > observed["remaining"] or reason not in (
            "duplicate",
            "fraudulent",
            "order_change",
            "product_unsatisfactory",
        ):
            raise CreditConflict()
        balance_key = digest([self.provider.account_id, observed])
        proof = {
            **observed,
            "revision": record.revision,
            "amount": amount,
            "reason": reason,
            "audit": audit,
            "balance_key": balance_key,
            "expires": int(time.time()) + 180,
        }
        nonce = os.urandom(12)
        encrypted = self._cipher.encrypt(
            nonce, canonical(proof).encode(), self._context(record.tenant_id, actor)
        )
        return {
            "reference": "icv1." + base64.urlsafe_b64encode(nonce + encrypted).decode().rstrip("="),
            "currency": observed["currency"],
            "before_minor": observed["remaining"],
            "credit_minor": amount,
            "after_minor": observed["remaining"] - amount,
            "confirmation": self.confirmation(proof),
        }

    @staticmethod
    def confirmation(review):
        return f"CREDIT {review['amount']} {review['currency'].upper()} MINOR UNITS"

    def apply(self, record, actor, reference, confirmation, load):
        review = self._open(reference, record.tenant_id, actor)
        if (
            not self.provider.can_issue
            or confirmation != self.confirmation(review)
            or review["revision"] != record.revision
            or time.time() >= review["expires"]
        ):
            raise CreditConflict()
        current = self._inspect(record, review["invoice"])
        if any(review[k] != v for k, v in current.items()):
            raise CreditConflict()
        self.provider.check_account(writer=True)
        latest = load(record.tenant_id)
        if latest is None or latest != record or time.time() >= review["expires"]:
            raise CreditConflict()
        return self.provider.receipt(self.provider.issue(review), review)

    def receipt(self, record, actor, reference):
        review = self._open(reference, record.tenant_id, actor)
        if (
            record.demo is not False
            or record.billing_mode != "stripe"
            or record.payer_id != review["customer"]
            or record.stripe_subscription_id != review["subscription"]
        ):
            raise CreditConflict()
        return self.provider.find_receipt(review)
