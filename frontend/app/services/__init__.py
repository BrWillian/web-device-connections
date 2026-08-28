"""Service layer: the rules, kept out of both the models and the controllers.

A model knows how to write a row. A controller knows how to turn a request into
a response. What sits between them — is this password long enough, would this
change lock everyone out, does the relay have to be told — lives here, where it
can be read without SQL or HTTP in the way.
"""

from app.services.errors import RuleError  # noqa: F401
