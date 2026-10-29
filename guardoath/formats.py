"""Explicit supported schema formats; unknown formats fail configuration."""

from email_validator import EmailNotValidError, validate_email
from jsonschema import FormatChecker
from rfc3339_validator import validate_rfc3339

SUPPORTED_FORMATS = {"date", "date-time", "time", "ipv4", "ipv6", "uuid", "email"}
FORMAT_CHECKER = FormatChecker(formats=sorted(SUPPORTED_FORMATS))


@FORMAT_CHECKER.checks("email", raises=EmailNotValidError)
def email_format(value):
    if not isinstance(value, str):
        return True
    validate_email(value, check_deliverability=False)
    return True


@FORMAT_CHECKER.checks("time")
def time_format(value):
    if not isinstance(value, str):
        return True
    # Generic FormatChecker otherwise installs the older draft-3 HH:MM:SS rule.
    # Draft 2020-12 uses RFC3339 full-time, including a timezone offset.
    return value == value.strip() and validate_rfc3339("2000-01-01T" + value.upper())


@FORMAT_CHECKER.checks("date-time")
def date_time_format(value):
    if not isinstance(value, str):
        return True
    # The dependency's end anchor accepts a final LF unless we exclude padding.
    return value == value.strip() and validate_rfc3339(value.upper())


def validate_schema_formats(schema):
    if not isinstance(schema, dict):
        return
    if "format" in schema and isinstance(schema["format"], str) and schema["format"] not in SUPPORTED_FORMATS:
        raise ValueError("Unsupported schema format")
    for name in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
        for child in schema.get(name, {}).values():
            validate_schema_formats(child)
    for name in ("allOf", "anyOf", "oneOf", "prefixItems"):
        for child in schema.get(name, []):
            validate_schema_formats(child)
    for name in ("items", "contains", "additionalProperties", "unevaluatedProperties",
                 "unevaluatedItems", "propertyNames", "not", "if", "then", "else"):
        validate_schema_formats(schema.get(name))
