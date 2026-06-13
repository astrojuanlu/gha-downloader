from contextlib import contextmanager
from unittest import mock

import pytest


@contextmanager
def _fake_alive_bar(total, *, title="", file=None, ctrl_c=False):
    bar = mock.MagicMock()
    bar._total = total
    bar._title = title
    yield bar


@pytest.fixture(autouse=True)
def _patch_alive_bar(monkeypatch):
    monkeypatch.setattr(
        "gha_downloader.downloader.alive_bar",
        _fake_alive_bar,
    )
