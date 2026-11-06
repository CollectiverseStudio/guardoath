"""FC-031/032/033/034/036/037/041/046: bounded, exact local arithmetic.

These are tenant-owned cross_field rules, not a second policy engine. Values are
strict JSON integers or Decimal float tokens. No eval, external service, floating
point math, trusted exchange-rate assumptions or submitted-data echo.
"""
from decimal import Decimal, InvalidOperation, localcontext

from guardoath.models import Finding
from guardoath.business import resolve

MAX_LINES = 128
MAX_DIGITS = 32
MAX_MAGNITUDE = 18


class InvalidArithmetic(ValueError):
    pass


def _number(value, *, nonnegative=False, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise InvalidArithmetic("invalid_numeric_type")
    number = Decimal(value)
    if not number.is_finite() or len(number.as_tuple().digits) > MAX_DIGITS or abs(number.adjusted()) > MAX_MAGNITUDE:
        raise InvalidArithmetic("arithmetic_numeric_budget")
    if positive and number <= 0:
        raise InvalidArithmetic("nonpositive_denominator")
    if nonnegative and number < 0:
        raise InvalidArithmetic("negative_value")
    return number


def _sum_lines(lines, *, quantity_only=False):
    if not isinstance(lines, list) or not 1 <= len(lines) <= MAX_LINES:
        raise InvalidArithmetic("invalid_lines")
    total = Decimal(0)
    for item in lines:
        if not isinstance(item, dict):
            raise InvalidArithmetic("invalid_line")
        qty = _number(item.get("quantity"), nonnegative=True)
        if quantity_only:
            total += qty
        else:
            total += qty * _number(item.get("unit_price"), nonnegative=True)
    return total


def _between_0_1(value):
    number = _number(value, nonnegative=True)
    if number > 1:
        raise InvalidArithmetic("fraction_out_of_range")
    return number


def _valid(rule, payload):
    values = {name: resolve(payload, pointer) for name, pointer in rule.fields.items()}
    kind = rule.kind
    with localcontext() as ctx:
        # 32 significant input digits and <=128 lines cannot round at 128
        # digits. Decimal exponent and input-size bounds stop pathological work.
        ctx.prec, ctx.Emax, ctx.Emin = 128, 128, -128
        if kind == "invoice_line_subtotal":
            return _sum_lines(values["lines"]) == _number(values["subtotal"], nonnegative=True)
        if kind == "invoice_total":
            subtotal = _number(values["subtotal"], nonnegative=True)
            discount = _number(values["discount"], nonnegative=True)
            tax = _number(values["tax"], nonnegative=True)
            shipping = _number(values["shipping"], nonnegative=True)
            return subtotal - discount + tax + shipping == _number(values["total"], nonnegative=True)
        if kind == "tax_tolerance":
            tax = _number(values["tax"], nonnegative=True)
            base = _number(values["base"], nonnegative=True)
            rate = _between_0_1(values["rate"])
            tolerance = _number(values["tolerance"], nonnegative=True)
            return abs(tax - base * rate) <= tolerance
        if kind == "discount_fraction_cap":
            discount = _number(values["discount"], nonnegative=True)
            eligible = _number(values["eligible_subtotal"], positive=True)
            fraction = _between_0_1(values["max_fraction"])
            return discount <= eligible * fraction
        if kind == "inventory_conservation":
            opening = _number(values["opening"], nonnegative=True)
            received = _number(values["received"], nonnegative=True)
            shipped = _number(values["shipped"], nonnegative=True)
            lost = _number(values["lost"], nonnegative=True)
            return opening + received - shipped - lost == _number(values["closing"], nonnegative=True)
        if kind == "shipment_quantity_sum":
            return _sum_lines(values["lines"], quantity_only=True) == _number(values["total"], nonnegative=True)
        if kind == "manufacturing_tolerance":
            measured = _number(values["measured"])
            nominal = _number(values["nominal"])
            tolerance = _number(values["tolerance"], nonnegative=True)
            return abs(measured - nominal) <= tolerance
        if kind == "carbon_intensity":
            emissions = _number(values["emissions"], nonnegative=True)
            output = _number(values["output"], positive=True)
            ceiling = _number(values["max_intensity"], nonnegative=True)
            return emissions <= output * ceiling
        raise InvalidArithmetic("unknown_arithmetic_kind")


def arithmetic_findings(payload, rules, stage):
    """Return every failed configured rule in order; never include source values."""
    findings = []
    for rule in rules:
        if stage not in rule.stages:
            continue
        try:
            valid = _valid(rule, payload)
        except (InvalidArithmetic, ValueError, KeyError, IndexError, TypeError,
                ArithmeticError, InvalidOperation):
            valid = False
        if not valid:
            findings.append(Finding(rule_id="business." + rule.rule_id, category="business",
                                    severity="medium",
                                    explanation="Configured arithmetic fields are missing, invalid, outside bounds, or inconsistent."))
    return tuple(findings)
