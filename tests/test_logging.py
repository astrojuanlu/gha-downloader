import logging

from gha_downloader.cli import configure_logging


def test_configure_logging_default():
    configure_logging(0)
    assert logging.root.level == logging.WARNING


def test_configure_logging_info():
    configure_logging(1)
    assert logging.root.level == logging.INFO


def test_configure_logging_debug():
    configure_logging(2)
    assert logging.root.level == logging.DEBUG


def test_configure_logging_capped():
    configure_logging(5)
    assert logging.root.level == logging.DEBUG
