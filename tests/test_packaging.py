"""Release-identity invariants (hardening pass Rev 3, Slice E)."""

from importlib.metadata import version

import contextual_intelligence


def test_version_metadata_matches_module():
    """The version lives in pyproject.toml AND __init__.py — they must agree
    (the third copy, uv.lock, regenerates from pyproject on sync)."""
    assert version("contextual-intelligence") == contextual_intelligence.__version__


def test_version_is_pep440_developmental_release():
    """Pre-release artifacts must not claim final-release status: the tag is
    v0.1.0-dev.N and the package version is the PEP 440 form 0.1.0.devN."""
    assert ".dev" in contextual_intelligence.__version__
