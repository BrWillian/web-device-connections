"""The one exception the service layer raises."""


class RuleError(Exception):
    """A refusal the operator is meant to read.

    Its message goes straight onto the page, so it is written in Portuguese and
    says what to do about it — not what went wrong internally.
    """
