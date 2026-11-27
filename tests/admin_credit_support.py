"""Deterministic test boundary for Stripe's open-invoice credit note endpoints."""

import io
import json
from urllib.parse import urlsplit, parse_qs


class Response(io.BytesIO):
    status = 200


class CreditBoundary:
    def __init__(
        self,
        account="acct_credit",
        invoice="in_owner123",
        customer="cus_owner123",
        subscription="sub_owner123",
        balance=2900,
    ):
        self.account = account
        self.invoice = {
            "object": "invoice",
            "id": invoice,
            "livemode": False,
            "status": "open",
            "currency": "usd",
            "customer": customer,
            "subscription": subscription,
            "amount_remaining": balance,
            "pre_payment_credit_notes_amount": 0,
            "on_behalf_of": None,
            "transfer_data": None,
        }
        self.posts = []
        self.notes = []
        self.lose_response = False

    def __call__(self, req, *, timeout):
        assert urlsplit(req.full_url).hostname == "api.stripe.com"
        path = urlsplit(req.full_url).path
        if path == "/v1/account":
            result = {"object": "account", "id": self.account}
        elif path == "/v1/invoices/" + self.invoice["id"]:
            result = self.invoice
        elif path == "/v1/credit_notes" and req.get_method() == "GET":
            assert parse_qs(urlsplit(req.full_url).query)["invoice"] == [self.invoice["id"]]
            result = {"object": "list", "has_more": False, "data": self.notes}
        else:
            assert path == "/v1/credit_notes" and req.get_method() == "POST"
            body = {k: v[0] for k, v in parse_qs(req.data.decode()).items()}
            assert body["invoice"] == self.invoice["id"] and body["email_type"] == "none"
            assert all(body[k] == "0" for k in ("refund_amount", "credit_amount", "out_of_band_amount"))
            self.posts.append(req.get_header("Idempotency-key"))
            amount = int(body["amount"])
            assert self.invoice["status"] == "open" and 0 < amount <= self.invoice["amount_remaining"]
            result = {
                "object": "credit_note",
                "id": "cn_credit",
                "type": "pre_payment",
                "status": "issued",
                "invoice": body["invoice"],
                "customer": self.invoice["customer"],
                "livemode": False,
                "reason": body["reason"],
                "currency": "usd",
                "amount": amount,
                "total": amount,
                "number": "TEST-CN-1",
                "metadata": {"guardoath_credit_review": body["metadata[guardoath_credit_review]"]},
                "refund": None,
                "customer_balance_transaction": None,
                "out_of_band_amount": None,
            }
            self.notes.append(result)
            self.invoice["amount_remaining"] -= amount
            self.invoice["pre_payment_credit_notes_amount"] += amount
            if not self.invoice["amount_remaining"]:
                self.invoice["status"] = "paid"
            if self.lose_response:
                raise TimeoutError()
        return Response(json.dumps(result).encode())
