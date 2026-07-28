# app/schemas/transform_routing.py
"""Provider-qualified routing keys for transform requests.

A `transform_type` is a string of the form `<provider>.<provider-local-type>`,
e.g. `prefect.transcode` or `webhook.thumbnail.generate`. Only the *shape* is
validated here -- split on the first `.`; the provider is the text before it,
and everything after (which may itself contain dots) is that provider's own
vocabulary, passed through verbatim and never interpreted by this module. No
allow-list of providers or job names is enforced, so adding a new Prefect
deployment or a new provider needs no API release.
"""

from typing import Annotated

from pydantic import StringConstraints

TRANSFORM_ROUTING_KEY_PATTERN = r"^[^\s.]+\.[^\s]+$"

TRANSFORM_ROUTING_KEY_DESCRIPTION = (
    "Provider-qualified routing key, `<provider>.<provider-local-type>` "
    "(e.g. `prefect.transcode`). Only the shape is validated -- the "
    "provider is the text before the first `.`, and everything after is "
    "that provider's own vocabulary, forwarded verbatim."
)

TRANSFORM_ROUTING_KEY_EXAMPLES = ["prefect.transcode"]

TransformRoutingKey = Annotated[str, StringConstraints(pattern=TRANSFORM_ROUTING_KEY_PATTERN)]
