"""The Resolver stages — the SPARRING challenge half, as a pipeline.

Importing this package registers all five Resolver stages:
author -> cross_review -> anchored_fix -> definitiveness_gate -> citation_gate.
"""
from . import (  # noqa: F401  (each import registers a stage)
    anchored_fix,
    author,
    citation_gate,
    compose,
    cross_review,
    definitiveness_gate,
)

__all__ = [
    "author",
    "cross_review",
    "anchored_fix",
    "definitiveness_gate",
    "citation_gate",
    "compose",
]
