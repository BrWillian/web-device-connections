"""Shared fixtures.

Everything here builds Settings explicitly instead of reading the environment:
a developer's own client/.env must never decide whether the suite passes.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wdc_client.config import Settings  # noqa: E402


@pytest.fixture
def sandbox(tmp_path):
    """A directory standing in for the device's home, and the only allowed root."""
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def roots(sandbox):
    return [os.path.realpath(str(sandbox))]


@pytest.fixture
def settings(sandbox):
    return Settings(
        device_id="test-device",
        device_token="token",
        server_url="ws://relay:8000/device",
        allowed_roots=str(sandbox),
        max_file_size=1024,
        chunk_size=64,
    )
