"""One full, provider-bound invoice refund; no general financial proxy.

The service supplies a server-owned billing binding and a signed reviewed
snapshot. This adapter never chooses a browser-supplied amount, retries a POST,
follows redirects, adjusts an invoice, or changes subscription entitlement.
The Acacia invoice, PaymentIntent and charge graph establishes one successful
payment; explicit refund reads establish prior claims and later outcomes.
"""
from __future__ import annotations

import base64
import math
import re
import time
import urllib.parse
import urllib.request

from guardoath.admin_cancellation_provider import digest, provider_id
from guardoath.stripe_provider import _strict_provider_json
from guardoath.subscription_reconciliation import StripeBinding


class RefundProviderUnavailable(Exception):
    """The provider did not establish the supported refund or its outcome."""


SNAPSHOT_FIELDS = frozenset({
    "account_id", "livemode", "customer_id", "subscription_id", "invoice_id",
    "charge_id", "payment_intent_id", "amount_minor", "currency", "expected_price_id",
})
REFUND_STATUSES = frozenset({"pending", "requires_action", "succeeded", "failed", "canceled"})
MAX_AMOUNT = 99_999_999
MAX_TIME = 253402300799


def _integer(value, *, minimum: int = 0, maximum: int = MAX_AMOUNT) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _no_connect(value: dict, fields: tuple[str, ...]) -> bool:
    return all(name in value and value[name] is None for name in fields)


class StripeInvoiceRefund:
    API_VERSION = "2025-02-24.acacia"

    def __init__(self, *, api_key: str, expected_account_id: str,
                 expected_livemode: bool, expected_price_id: str,
                 opener=None, timeout: float = 1.0):
        if (not isinstance(api_key, str)
                or not re.fullmatch(r"(?:rk|sk)_(?:test|live)_[A-Za-z0-9_]{11,504}", api_key)
                or type(expected_livemode) is not bool
                or ("_live_" in api_key[:8]) != expected_livemode
                or not provider_id(expected_account_id, "acct")
                or not provider_id(expected_price_id, "price")
                or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 1
                or (opener is not None and not callable(opener))):
            raise ValueError("Explicit account-scoped refund configuration required")
        self.account_id, self.livemode = expected_account_id, expected_livemode
        self.price_id = expected_price_id
        self._key, self._timeout = api_key, timeout

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        self._send = opener or urllib.request.build_opener(NoRedirect()).open

    @property
    def expected_account_id(self) -> str:
        return self.account_id

    @property
    def expected_livemode(self) -> bool:
        return self.livemode

    @property
    def expected_price_id(self) -> str:
        return self.price_id

    @staticmethod
    def _now(now: int) -> None:
        if not _integer(now, minimum=1, maximum=MAX_TIME):
            raise RefundProviderUnavailable() from None

    @staticmethod
    def _observed_time(now: int, started: float) -> int:
        # now is sampled by the service before I/O and has whole-second
        # precision. A newly created refund can cross the next second during
        # the request; allow only actual elapsed monotonic time plus rounding.
        elapsed = time.monotonic() - started
        if not math.isfinite(elapsed) or elapsed < 0:
            raise RefundProviderUnavailable() from None
        return min(MAX_TIME, now + math.ceil(elapsed))

    def _request(self, path: str, *, body: dict | None = None,
                 idempotency_key: str | None = None, deadline: float | None = None) -> dict:
        try:
            headers = {"Authorization": "Basic " + base64.b64encode(
                (self._key + ":").encode("ascii")).decode("ascii"),
                "Accept": "application/json", "Stripe-Version": self.API_VERSION}
            encoded = None
            if body is not None:
                from guardoath.state_contracts import new_refund_state_enabled
                if (not new_refund_state_enabled() or path != "refunds"
                        or not isinstance(idempotency_key, str)
                        or not re.fullmatch(r"guardoath-ir1-[a-f0-9]{64}", idempotency_key)):
                    raise RefundProviderUnavailable()
                headers.update({"Idempotency-Key": idempotency_key,
                                "Content-Type": "application/x-www-form-urlencoded"})
                encoded = urllib.parse.urlencode(body).encode("ascii")
            elif idempotency_key is not None:
                raise RefundProviderUnavailable()
            timeout = self._timeout
            if deadline is not None:
                if (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                        or not math.isfinite(deadline)):
                    raise RefundProviderUnavailable()
                timeout = min(timeout, deadline - time.monotonic())
            if timeout <= 0:
                raise RefundProviderUnavailable()
            request = urllib.request.Request("https://api.stripe.com/v1/" + path,
                data=encoded, headers=headers, method="POST" if encoded is not None else "GET")
            with self._send(request, timeout=timeout) as response:
                if getattr(response, "status", None) != 200:
                    raise RefundProviderUnavailable()
                raw = response.read(262145)
            if not isinstance(raw, bytes) or len(raw) > 262144:
                raise RefundProviderUnavailable()
            if deadline is not None and time.monotonic() >= deadline:
                raise RefundProviderUnavailable()
            return _strict_provider_json(raw)
        except Exception:
            # Credential, provider payload, URL and transport detail stay private.
            raise RefundProviderUnavailable() from None

    def _account(self, *, deadline: float | None = None) -> None:
        value = self._request("account", deadline=deadline)
        if value.get("object") != "account" or value.get("id") != self.account_id:
            raise RefundProviderUnavailable() from None

    def target_id(self, charge_id: str) -> str:
        if not provider_id(charge_id, "ch"):
            raise RefundProviderUnavailable() from None
        return digest(["guardoath-stripe-invoice-refund-target-v1", self.account_id,
                       self.livemode, charge_id])

    def _snapshot(self, value: dict, *, historical: bool = False) -> dict:
        if (not isinstance(value, dict) or set(value) != SNAPSHOT_FIELDS
                or value["account_id"] != self.account_id
                or value["livemode"] is not self.livemode
                or (not historical and value["expected_price_id"] != self.price_id)
                or any(not provider_id(value[field], prefix) for field, prefix in (
                    ("customer_id", "cus"), ("subscription_id", "sub"), ("invoice_id", "in"),
                    ("charge_id", "ch"), ("payment_intent_id", "pi"), ("expected_price_id", "price")))
                or not _integer(value["amount_minor"], minimum=1)
                or not isinstance(value["currency"], str)
                or not re.fullmatch(r"[a-z]{3}", value["currency"])):
            raise RefundProviderUnavailable() from None
        return dict(value)

    def _invoice(self, value: dict, binding: StripeBinding, invoice_id: str, *, now: int) -> dict:
        try:
            amount = value["amount_paid"]
            created = value["created"]
            transitions = value["status_transitions"]
            lines = value["lines"]
            if (value["object"] != "invoice" or value["id"] != invoice_id
                    or value["customer"] != binding.customer_id
                    or value["subscription"] != binding.subscription_id
                    or value["livemode"] is not self.livemode
                    or value["status"] != "paid" or value["paid"] is not True
                    or value["paid_out_of_band"] is not False
                    or value["collection_method"] != "charge_automatically"
                    or value["billing_reason"] not in ("subscription_create", "subscription_cycle")
                    or not _integer(amount, minimum=1)
                    or any(not _integer(value[k], minimum=1) or value[k] != amount
                           for k in ("amount_due", "total"))
                    or any(not _integer(value[k]) or value[k] != 0 for k in (
                        "amount_remaining", "starting_balance", "pre_payment_credit_notes_amount",
                        "post_payment_credit_notes_amount"))
                    or (value["ending_balance"] is not None and
                        (not _integer(value["ending_balance"]) or value["ending_balance"] != 0))
                    or not _integer(created, minimum=1, maximum=now)
                    or not _integer(transitions["finalized_at"], minimum=created, maximum=now)
                    or not _integer(transitions["paid_at"],
                                    minimum=transitions["finalized_at"], maximum=now)
                    or transitions["voided_at"] is not None
                    or transitions["marked_uncollectible_at"] is not None
                    or not _no_connect(value, ("application", "application_fee_amount",
                                              "on_behalf_of", "transfer_data"))
                    or value.get("payments") is not None
                    or ("amount_overpaid" in value and
                        (not _integer(value["amount_overpaid"]) or value["amount_overpaid"] != 0))
                    or not isinstance(value["currency"], str)
                    or not re.fullmatch(r"[a-z]{3}", value["currency"])
                    or not provider_id(value["charge"], "ch")
                    or not provider_id(value["payment_intent"], "pi")
                    or lines["object"] != "list" or lines["has_more"] is not False
                    or not isinstance(lines["data"], list) or len(lines["data"]) != 1):
                raise RefundProviderUnavailable()
            line = lines["data"][0]
            price = line["price"]
            if (line["object"] != "line_item" or not provider_id(line["id"], "il")
                    or line["invoice"] != invoice_id or line["subscription"] != binding.subscription_id
                    or line["livemode"] is not self.livemode
                    or line["type"] != "subscription" or line["proration"] is not False
                    or not _integer(line["quantity"], minimum=1, maximum=1)
                    or not _integer(line["amount"], minimum=1)
                    or line["currency"] != value["currency"]
                    or not isinstance(price, dict) or price.get("object") != "price"
                    or price.get("id") != self.price_id or price.get("livemode") is not self.livemode
                    or price.get("type") != "recurring" or price.get("currency") != value["currency"]):
                raise RefundProviderUnavailable()
            credits = line.get("pretax_credit_amounts")
            if credits is not None and (not isinstance(credits, list) or any(
                    not isinstance(credit, dict) or credit.get("type") != "discount"
                    or not _integer(credit.get("amount")) for credit in credits)):
                raise RefundProviderUnavailable()
            return self._snapshot({"account_id": self.account_id, "livemode": self.livemode,
                "customer_id": binding.customer_id, "subscription_id": binding.subscription_id,
                "invoice_id": invoice_id, "charge_id": value["charge"],
                "payment_intent_id": value["payment_intent"], "amount_minor": amount,
                "currency": value["currency"], "expected_price_id": self.price_id})
        except Exception:
            raise RefundProviderUnavailable() from None

    def _payment_intent(self, value: dict, snapshot: dict, *, now: int) -> None:
        try:
            if (value["object"] != "payment_intent" or value["id"] != snapshot["payment_intent_id"]
                    or value["customer"] != snapshot["customer_id"]
                    or value["invoice"] != snapshot["invoice_id"]
                    or value["latest_charge"] != snapshot["charge_id"]
                    or value["livemode"] is not self.livemode or value["status"] != "succeeded"
                    or value["currency"] != snapshot["currency"]
                    or any(not _integer(value[k], minimum=1) or value[k] != snapshot["amount_minor"]
                           for k in ("amount", "amount_received"))
                    or not _integer(value["amount_capturable"]) or value["amount_capturable"] != 0
                    or not _integer(value["created"], minimum=1, maximum=now)
                    or value["capture_method"] not in ("automatic", "automatic_async")
                    or value["canceled_at"] is not None or value["next_action"] is not None
                    or value["processing"] is not None or value["review"] is not None
                    or not _no_connect(value, ("application", "application_fee_amount",
                                              "on_behalf_of", "transfer_data", "transfer_group"))):
                raise RefundProviderUnavailable()
        except Exception:
            raise RefundProviderUnavailable() from None

    def _charge(self, value: dict, snapshot: dict, *, now: int, before: bool = False) -> None:
        try:
            amount, refunded = snapshot["amount_minor"], value["amount_refunded"]
            if (value["object"] != "charge" or value["id"] != snapshot["charge_id"]
                    or value["customer"] != snapshot["customer_id"]
                    or value["invoice"] != snapshot["invoice_id"]
                    or value["payment_intent"] != snapshot["payment_intent_id"]
                    or value["livemode"] is not self.livemode
                    or value["status"] != "succeeded" or value["paid"] is not True
                    or value["captured"] is not True or value["disputed"] is not False
                    or value["currency"] != snapshot["currency"]
                    or any(not _integer(value[k], minimum=1) or value[k] != amount
                           for k in ("amount", "amount_captured"))
                    or not _integer(refunded, maximum=amount)
                    or type(value["refunded"]) is not bool
                    or value["refunded"] is not (refunded == amount)
                    or (before and refunded != 0)
                    or not _integer(value["created"], minimum=1, maximum=now)
                    or not provider_id(value["balance_transaction"], "txn")
                    or value["failure_code"] is not None or value["failure_message"] is not None
                    or value["failure_balance_transaction"] is not None or value["review"] is not None
                    or not _no_connect(value, ("application", "application_fee", "application_fee_amount",
                                              "on_behalf_of", "transfer_data", "source_transfer",
                                              "transfer_group"))):
                raise RefundProviderUnavailable()
        except Exception:
            raise RefundProviderUnavailable() from None

    def _refunds(self, charge_id: str, *, deadline: float | None = None) -> list:
        path = "refunds?" + urllib.parse.urlencode({"charge": charge_id, "limit": 2})
        value = self._request(path, deadline=deadline)
        if (value.get("object") != "list" or value.get("has_more") is not False
                or not isinstance(value.get("data"), list) or len(value["data"]) > 1):
            raise RefundProviderUnavailable() from None
        return value["data"]

    def inspect(self, binding: StripeBinding, invoice_id: str, *, now: int,
                deadline: float | None = None) -> dict:
        self._now(now)
        if (not isinstance(binding, StripeBinding)
                or not provider_id(binding.customer_id, "cus")
                or not provider_id(binding.subscription_id, "sub")
                or not provider_id(invoice_id, "in")):
            raise RefundProviderUnavailable() from None
        self._account(deadline=deadline)
        snapshot = self._invoice(self._request("invoices/" + invoice_id, deadline=deadline),
                                 binding, invoice_id, now=now)
        self._payment_intent(self._request("payment_intents/" + snapshot["payment_intent_id"],
                                          deadline=deadline), snapshot, now=now)
        self._charge(self._request("charges/" + snapshot["charge_id"], deadline=deadline),
                     snapshot, now=now, before=True)
        if self._refunds(snapshot["charge_id"], deadline=deadline):
            raise RefundProviderUnavailable() from None
        return snapshot

    def validate_refund(self, value: dict, snapshot: dict, *, action_id: str, now: int,
                        refund_id: str | None = None) -> dict:
        snapshot = self._snapshot(snapshot)
        return self._refund_proof(value, snapshot, action_id=action_id, now=now, refund_id=refund_id)

    def _refund_proof(self, value: dict, snapshot: dict, *, action_id: str, now: int,
                      refund_id: str | None = None) -> dict:
        # The caller has validated the snapshot for either a current dispatch
        # or a historical read. Preserve that exact authority in the proof.
        self._now(now)
        try:
            if (not isinstance(action_id, str) or not re.fullmatch(r"[a-f0-9]{64}", action_id)
                    or not isinstance(value, dict) or value["object"] != "refund"
                    or not provider_id(value["id"], "re")
                    or (refund_id is not None and value["id"] != refund_id)
                    or value["charge"] != snapshot["charge_id"]
                    or value["payment_intent"] != snapshot["payment_intent_id"]
                    or not _integer(value["amount"], minimum=1)
                    or value["amount"] != snapshot["amount_minor"]
                    or value["currency"] != snapshot["currency"]
                    or not isinstance(value["status"], str) or value["status"] not in REFUND_STATUSES
                    or not _integer(value["created"], minimum=1, maximum=now)
                    or not isinstance(value["metadata"], dict)
                    or value["metadata"].get("guardoath_action") != action_id
                    or value["reason"] is not None
                    or not _no_connect(value, ("source_transfer_reversal", "transfer_reversal"))
                    or ("livemode" in value and value["livemode"] is not self.livemode)
                    or (value["status"] == "succeeded" and
                        (value.get("failure_reason") is not None
                         or value.get("failure_balance_transaction") is not None
                         or value.get("next_action") is not None))):
                raise RefundProviderUnavailable()
            return {**snapshot, "action_id": action_id, "refund_id": value["id"],
                    "status": value["status"], "refund_created": value["created"]}
        except Exception:
            raise RefundProviderUnavailable() from None

    def dispatch(self, snapshot: dict, *, action_id: str, idempotency_key: str,
                 now: int, deadline: float | None = None) -> dict:
        self._now(now)
        snapshot = self._snapshot(snapshot)
        if (not isinstance(action_id, str) or not re.fullmatch(r"[a-f0-9]{64}", action_id)
                or idempotency_key != "guardoath-ir1-" + action_id):
            raise RefundProviderUnavailable() from None
        # The caller has just re-inspected this exact snapshot and atomically
        # claimed its charge. One POST follows that claim; never retry here.
        started = time.monotonic()
        value = self._request("refunds", body={"charge": snapshot["charge_id"],
            "amount": snapshot["amount_minor"], "metadata[guardoath_action]": action_id},
            idempotency_key=idempotency_key, deadline=deadline)
        return self.validate_refund(value, snapshot, action_id=action_id,
                                    now=self._observed_time(now, started))

    def verify(self, snapshot: dict, *, action_id: str, refund_id: str | None = None,
               now: int, deadline: float | None = None) -> dict:
        self._now(now)
        # Signed historical intents remain readable after a plan-price change.
        # Only this GET-only path can retain a canonical historical price;
        # inspection and dispatch still require the configured current price.
        snapshot = self._snapshot(snapshot, historical=True)
        if (not isinstance(action_id, str) or not re.fullmatch(r"[a-f0-9]{64}", action_id)
                or (refund_id is not None and not provider_id(refund_id, "re"))):
            raise RefundProviderUnavailable() from None
        started = time.monotonic()
        self._account(deadline=deadline)
        if refund_id is None:
            values = self._refunds(snapshot["charge_id"], deadline=deadline)
            if len(values) != 1:
                raise RefundProviderUnavailable() from None
            found = self._refund_proof(values[0], snapshot, action_id=action_id,
                                       now=self._observed_time(now, started))
            refund_id = found["refund_id"]
        # Even a list match or a POST response is independently retrieved.
        value = self._request("refunds/" + refund_id, deadline=deadline)
        proof = self._refund_proof(value, snapshot, action_id=action_id,
                                   refund_id=refund_id, now=self._observed_time(now, started))
        charge = self._request("charges/" + snapshot["charge_id"], deadline=deadline)
        self._charge(charge, snapshot, now=now)
        if proof["status"] == "succeeded" and (
                charge["amount_refunded"] != snapshot["amount_minor"] or charge["refunded"] is not True):
            raise RefundProviderUnavailable() from None
        return proof
