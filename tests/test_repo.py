import pytest

from gha_downloader.repo import validate_repo


def test_validate_repo_valid():
    assert validate_repo("myorg/myrepo") == "myorg/myrepo"


def test_validate_repo_no_slash():
    with pytest.raises(ValueError, match="Invalid repository format"):
        validate_repo("invalid")


def test_validate_repo_too_many_slashes():
    with pytest.raises(ValueError, match="Invalid repository format"):
        validate_repo("a/b/c")


def test_validate_repo_empty():
    with pytest.raises(ValueError, match="Invalid repository format"):
        validate_repo("")
