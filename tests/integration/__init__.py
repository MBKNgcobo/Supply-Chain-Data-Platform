"""Applies the integration marker to every test in this package.

Kept minimal on purpose: the whole package is integration tests by
construction (ADR-006). Plain `pytest` (whose addopts carry
`-m "not integration"`) never collects these tests, and
`python -m pytest -m integration` opts in explicitly.
"""
