"""One fixed Stripe period-end operation, never a general financial proxy.

The caller supplies only an independently loaded server-owned billing binding.
Account identity and supported subscription state are checked before dispatch.
There is no automatic retry, redirect, entitlement write or invoice adjustment.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time
import urllib.request

from guardoath.stripe_provider import _strict_provider_json


class CancellationProviderUnavailable(Exception):
    """The provider did not establish the narrow supported state."""


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("ascii")).hexdigest()


def provider_id(value, prefix: str) -> bool:
    return isinstance(value, str) and re.fullmatch(prefix + r"_[A-Za-z0-9_]{1,96}", value) is not None


class StripePeriodEndCancellation:
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
                or not math.isfinite(timeout) or not 0 < timeout <= 1):
            raise ValueError("Explicit account-scoped cancellation configuration required")
        self.account_id = expected_account_id
        self.livemode = expected_livemode
        self.price_id = expected_price_id
        self._key, self._timeout = api_key, timeout

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        self._send = opener or urllib.request.build_opener(NoRedirect()).open

    def _request(self, path: str, *, idempotency_key: str | None = None,
                 deadline: float | None = None) -> dict:
        headers = {"Authorization": "Basic " + base64.b64encode(
            (self._key + ":").encode("ascii")).decode("ascii"),
            "Accept": "application/json", "Stripe-Version": self.API_VERSION}
        body = None
        if idempotency_key is not None:
            from guardoath.state_contracts import new_cancellation_state_enabled
            if not new_cancellation_state_enabled():
                raise CancellationProviderUnavailable() from None
            if not re.fullmatch(r"guardoath-sc1-[a-f0-9]{64}", idempotency_key):
                raise CancellationProviderUnavailable() from None
            headers.update({"Idempotency-Key": idempotency_key,
                            "Content-Type": "application/x-www-form-urlencoded"})
            body = b"cancel_at_period_end=true"
        request = urllib.request.Request("https://api.stripe.com/v1/" + path,
                                         data=body, headers=headers,
                                         method="POST" if body is not None else "GET")
        try:
            timeout = self._timeout if deadline is None else min(self._timeout, deadline - time.monotonic())
            if not math.isfinite(timeout) or timeout <= 0:
                raise CancellationProviderUnavailable()
            with self._send(request, timeout=timeout) as response:
                if getattr(response, "status", None) != 200:
                    raise CancellationProviderUnavailable()
                raw = response.read(262145)
            if not isinstance(raw, bytes) or len(raw) > 262144:
                raise CancellationProviderUnavailable()
            if deadline is not None and time.monotonic() >= deadline:
                raise CancellationProviderUnavailable()
            return _strict_provider_json(raw)
        except Exception:
            # Provider bodies/URLs/credentials must not reach a public error.
            raise CancellationProviderUnavailable() from None

    def target_id(self, subscription_id: str) -> str:
        if not provider_id(subscription_id, "sub"):
            raise CancellationProviderUnavailable() from None
        # Stable across owners, tenant aliases, action keys and secret rotations.
        return digest(["guardoath-stripe-subscription-target-v1", self.account_id,
                       self.livemode, subscription_id])

    def inspect(self, binding, *, scheduled: bool, now: int, deadline: float | None = None) -> dict:
        account = self._request("account", deadline=deadline)
        if account.get("object") != "account" or account.get("id") != self.account_id:
            raise CancellationProviderUnavailable() from None
        return self.validate(self._request(self._path(binding), deadline=deadline), binding,
                             scheduled=scheduled, now=now)

    @staticmethod
    def _path(binding) -> str:
        if (not provider_id(getattr(binding, "subscription_id", None), "sub")
                or not provider_id(getattr(binding, "customer_id", None), "cus")):
            raise CancellationProviderUnavailable() from None
        return "subscriptions/" + binding.subscription_id

    def dispatch(self, binding, *, idempotency_key: str, now: int, deadline: float | None = None) -> dict:
        # Account and pre-write admission are performed by inspect immediately
        # before the caller's durable dispatch claim. This method sends once.
        response = self._request(self._path(binding), idempotency_key=idempotency_key, deadline=deadline)
        return self.validate(response, binding, scheduled=True, now=now)

    def validate(self, value: dict, binding, *, scheduled: bool, now: int) -> dict:
        self._path(binding)
        if type(scheduled) is not bool or type(now) is not int or now <= 0:
            raise CancellationProviderUnavailable() from None
        try:
            customer = value["customer"]
            if isinstance(customer, dict):
                customer = customer.get("id") if customer.get("object") == "customer" else None
            items = value["items"]
            entries = items["data"]
            start, end = value["current_period_start"], value["current_period_end"]
            if (value["object"] != "subscription" or value["id"] != binding.subscription_id
                    or customer != binding.customer_id or value["livemode"] is not self.livemode
                    or value["status"] != "active" or value["collection_method"] != "charge_automatically"
                    or value["pause_collection"] is not None or value["schedule"] is not None
                    or value["pending_update"] is not None
                    or value["cancel_at_period_end"] is not scheduled
                    or (value["cancel_at"] is not None and
                        (not scheduled or type(value["cancel_at"]) is not int or value["cancel_at"] != end))
                    or items["object"] != "list" or items["has_more"] is not False
                    or not isinstance(entries, list) or len(entries) != 1
                    or type(start) is not int or type(end) is not int
                    or not 0 < start <= now < end <= 253402300799):
                raise CancellationProviderUnavailable()
            item = entries[0]
            price = item["price"]
            price_id = price.get("id") if isinstance(price, dict) else price
            if (item["object"] != "subscription_item" or not provider_id(item["id"], "si")
                    or item["subscription"] != binding.subscription_id
                    or type(item["quantity"]) is not int or item["quantity"] != 1
                    or price_id != self.price_id
                    or ("current_period_start" in item and
                        (type(item["current_period_start"]) is not int or item["current_period_start"] != start))
                    or ("current_period_end" in item and
                        (type(item["current_period_end"]) is not int or item["current_period_end"] != end))):
                raise CancellationProviderUnavailable()
            if isinstance(price, dict) and (price.get("object") != "price"
                    or price.get("livemode") is not self.livemode):
                raise CancellationProviderUnavailable()
            # This is the reviewed mutation-relevant shape. Metadata and the
            # cancellation request timestamp do not define the effective end.
            return {"account_id": self.account_id, "livemode": self.livemode,
                    "subscription_id": binding.subscription_id, "customer_id": binding.customer_id,
                    "price_id": self.price_id, "item_id": item["id"], "quantity": 1,
                    "period_start": start, "period_end": end}
        except Exception:
            raise CancellationProviderUnavailable() from None
