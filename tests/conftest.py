"""pytest fixtures.

When a real-API cassette test is needed, re-add `pytest-recording` to dev deps
and define a `vcr_config` fixture here that redacts the Kalshi auth headers.
"""
