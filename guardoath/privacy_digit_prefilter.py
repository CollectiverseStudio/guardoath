"""Sound lower bound for decimal digits consumed by copied privacy regexes.

This is a *skip-only* optimization: unsupported constructs return zero, so
they still run in the full regex engine. A nonzero result may suppress a
pattern only when the submitted field has fewer decimal digits than any
possible match must consume. It is never a detector or allow decision.
"""
import re


def minimum_decimal_digits(expression: str, *, verbose: bool = False) -> int:
    """Conservatively count digits that EVERY regex match must consume.

    The stdlib parser only recognizes its own regex subset. Patterns from
    the third-party regex engine with unsupported syntax, verbose-mode
    ambiguities, or fuzzy-match editing retain the ordinary scan path.
    Lookarounds and backreferences contribute zero rather than assuming
    their characters occur in the consumed match.
    """
    if verbose or any(token in expression for token in ("{e<=", "{i<=", "{d<=", "{s<=", "{1<=")):
        return 0
    try:
        from re import _parser

        digit_categories = {
            _parser.CATEGORY_DIGIT,
            getattr(_parser, "CATEGORY_UNI_DIGIT", _parser.CATEGORY_DIGIT),
        }

        def digit_class(members):
            if any(op == _parser.NEGATE for op, _ in members):
                return 0
            if not members:
                return 0
            for op, value in members:
                if op == _parser.LITERAL and chr(value).isdigit():
                    continue
                if op == _parser.RANGE and ord("0") <= value[0] <= value[1] <= ord("9"):
                    continue
                if op == _parser.CATEGORY and value in digit_categories:
                    continue
                return 0
            return 1

        def required(tokens):
            count = 0
            for op, value in tokens:
                if op == _parser.LITERAL:
                    count += int(chr(value).isdigit())
                elif op == _parser.IN:
                    count += digit_class(value)
                elif op == _parser.CATEGORY:
                    count += int(value in digit_categories)
                elif op == _parser.SUBPATTERN:
                    count += required(value[-1])
                elif op in (_parser.MAX_REPEAT, _parser.MIN_REPEAT):
                    count += value[0] * required(value[2])
                elif op == _parser.BRANCH:
                    count += min((required(branch) for branch in value[1]), default=0)
                # Positive/negative lookarounds, wildcards, group refs and
                # unfamiliar operators can NEVER increase this lower bound.
            return count

        return required(_parser.parse(expression))
    except (Exception, RecursionError):
        # A malformed or regex-only pattern remains subject to the original
        # full scan, which is responsible for its normal error handling.
        return 0
