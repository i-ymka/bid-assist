"""GitHub Issues integration for posting AI analysis results."""

import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)


def post_issue(
    token: str,
    repo: str,
    title: str,
    body: str,
    labels: list[str] = None,
) -> Optional[str]:
    """Create a GitHub Issue and return its URL.

    Args:
        token: GitHub Personal Access Token with repo scope.
        repo: Repository in "owner/name" format (e.g. "i-ymka/bid-assist").
        title: Issue title.
        body: Issue body (Markdown).
        labels: Optional list of label names to apply.

    Returns:
        HTML URL of the created issue, or None on failure.
    """
    if not token or not repo:
        logger.warning("post_issue: token or repo not configured, skipping")
        return None

    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload: dict = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code == 201:
            issue_url = resp.json().get("html_url")
            logger.info(f"GitHub Issue created: {issue_url}")
            return issue_url
        else:
            logger.error(f"GitHub Issue creation failed: {resp.status_code} {resp.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"GitHub Issue request error: {e}")
        return None
