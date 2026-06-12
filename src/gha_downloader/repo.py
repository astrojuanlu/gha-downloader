import re


def validate_repo(repo: str) -> str:
    """Validate and return repo in ORG/REPO format."""
    if not re.fullmatch(r"[^/]+/[^/]+", repo):
        msg = f"Invalid repository format: {repo!r}. Expected ORG/REPO."
        raise ValueError(msg)
    return repo
