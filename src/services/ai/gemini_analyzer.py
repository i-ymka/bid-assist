"""Gemini CLI-based project analyzer.

Uses the locally installed Gemini CLI to analyze projects
according to pal_rules.md and return structured verdicts.
"""

import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.config import settings

logger = logging.getLogger(__name__)

# Path to pal_rules.md
RULES_PATH = Path(__file__).parent.parent.parent.parent / "pal_rules.md"

# Model fallback state
FALLBACK_MODEL = "gemini-2.5-pro"
_primary_cooldown_until: float = 0  # timestamp when primary model can be retried


@dataclass
class AnalysisResult:
    """Result of project analysis."""
    verdict: str  # "BID" or "SKIP"
    summary: str
    bid_text: str
    amount: float
    period: int
    raw_response: str


def load_rules() -> str:
    """Load the PAL rules from file."""
    if RULES_PATH.exists():
        return RULES_PATH.read_text()
    else:
        logger.warning(f"Rules file not found at {RULES_PATH}")
        return ""


def parse_response(response: str) -> Optional[AnalysisResult]:
    """Parse Gemini response into structured result.

    Expected format:
    VERDICT: BID or SKIP
    ---
    SUMMARY: ...
    ---
    BID: ...
    ---
    AMOUNT: 150
    ---
    PERIOD: 3
    """
    try:
        if "===RESULT===" in response:
            response = response.split("===RESULT===")[1]

        # Extract each field using regex
        verdict_match = re.search(r"VERDICT:\s*(BID|SKIP)", response, re.IGNORECASE)
        summary_match = re.search(r"SUMMARY:\s*(.+?)(?=\n---|\nBID:|\Z)", response, re.DOTALL | re.IGNORECASE)
        bid_match = re.search(r"BID:\s*(.+?)(?=\n---|\nAMOUNT:|\Z)", response, re.DOTALL | re.IGNORECASE)
        amount_match = re.search(r"AMOUNT:\s*(\d+(?:\.\d+)?)", response, re.IGNORECASE)
        period_match = re.search(r"PERIOD:\s*(\d+)", response, re.IGNORECASE)

        if not verdict_match:
            logger.error("Could not find VERDICT in response")
            return None

        verdict = verdict_match.group(1).upper()
        summary = summary_match.group(1).strip() if summary_match else ""
        bid_text = bid_match.group(1).strip() if bid_match else ""
        amount = float(amount_match.group(1)) if amount_match else 0
        period = int(period_match.group(1)) if period_match else 0

        return AnalysisResult(
            verdict=verdict,
            summary=summary,
            bid_text=bid_text,
            amount=amount,
            period=period,
            raw_response=response,
        )

    except Exception as e:
        logger.error(f"Failed to parse response: {e}")
        return None


def _classify_cli_error(stderr: str) -> str:
    """Classify CLI error into a short category.

    Returns:
        'capacity' for 429 errors, 'cancelled' for user interrupt, 'unknown' otherwise.
    """
    lower = stderr.lower()
    if "429" in lower or "capacity" in lower or "rate limit" in lower or "resource_exhausted" in lower:
        return "capacity"
    if "operation cancelled" in lower or "sigint" in lower or "sigterm" in lower:
        return "cancelled"
    return "unknown"


def _extract_clean_error(stderr: str) -> str:
    """Extract a short human-readable error from Gemini CLI stderr."""
    # Look for the "message" field in JSON error
    msg_match = re.search(r'"message":\s*"([^"]+)"', stderr)
    if msg_match:
        return msg_match.group(1)
    # Look for "Operation cancelled"
    if "Operation cancelled" in stderr:
        return "Operation cancelled (interrupted)"
    # Fallback: first non-empty line that isn't boilerplate
    for line in stderr.strip().split("\n"):
        line = line.strip()
        if line and not line.startswith(("Loaded cached", "YOLO mode", "Attempt", "    at ")):
            return line[:200]
    return stderr[:200]


def _run_gemini_cli(prompt: str, model: str, timeout: int = 600) -> Optional[str]:
    """Run Gemini CLI with automatic model fallback on 429 errors.

    Tries the primary model first. If it fails with 429, falls back
    to FALLBACK_MODEL and puts primary on a 5-minute cooldown.

    Returns:
        CLI stdout text, or None if all attempts failed.
    """
    global _primary_cooldown_until

    primary_model = model
    use_fallback = primary_model != FALLBACK_MODEL

    # If primary is on cooldown, start with fallback
    if use_fallback and time.time() < _primary_cooldown_until:
        remaining = int(_primary_cooldown_until - time.time())
        logger.info(f"Model {primary_model} on cooldown ({remaining}s left), using {FALLBACK_MODEL}")
        models_to_try = [FALLBACK_MODEL]
    elif use_fallback:
        models_to_try = [primary_model, FALLBACK_MODEL]
    else:
        models_to_try = [primary_model]

    for current_model in models_to_try:
        try:
            logger.info(f"Running Gemini CLI with model: {current_model}")
            result = subprocess.run(
                ["gemini", "-m", current_model, "--yolo", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            # Negative return code = killed by signal (e.g. Ctrl+C sends SIGINT = -2)
            if result.returncode < 0:
                logger.info(f"Gemini CLI killed by signal {-result.returncode}")
                return None

            if result.returncode != 0:
                error_type = _classify_cli_error(result.stderr)
                clean_msg = _extract_clean_error(result.stderr)

                if error_type == "capacity":
                    logger.warning(f"Model {current_model}: 429 — {clean_msg}")
                    if current_model == primary_model and use_fallback:
                        _primary_cooldown_until = time.time() + 300  # 5 min cooldown
                        logger.info(f"Falling back to {FALLBACK_MODEL}, will retry {primary_model} in 5 min")
                        continue
                    return None
                elif error_type == "cancelled":
                    logger.info(f"Gemini CLI interrupted (Ctrl+C)")
                    return None
                else:
                    logger.error(f"Gemini CLI failed ({current_model}): {clean_msg}")
                    return None

            # Success — clear cooldown if primary worked
            if current_model == primary_model:
                _primary_cooldown_until = 0

            response = result.stdout.strip()
            # Clean Gemini CLI boilerplate from output
            for boilerplate in ["Loaded cached credentials.", "YOLO mode is enabled. All tool calls will be automatically approved."]:
                response = response.replace(boilerplate, "")
            response = response.strip()

            return response

        except subprocess.TimeoutExpired:
            logger.error(f"Gemini CLI timed out ({current_model}, {timeout}s)")
            if current_model == primary_model and use_fallback:
                continue
            return None

    return None


def analyze_project(
    project_id: int,
    title: str,
    description: str,
    budget: str,
    avg_bid: float = None,
    bid_count: int = None,
    model: str = None,
) -> Optional[AnalysisResult]:
    """Analyze a project using Gemini CLI.

    Args:
        project_id: Freelancer project ID
        title: Project title
        description: Full project description
        budget: Budget string like "$100 - $250 USD"
        avg_bid: Average bid amount on this project
        bid_count: Number of bids on this project
        model: Gemini model to use

    Returns:
        AnalysisResult or None if analysis failed
    """
    model = model or settings.gemini_model
    rules = load_rules()

    # Format avg_bid string for prompt
    avg_bid_str = f"{avg_bid:.0f}" if avg_bid else "No bids yet"
    bid_count_str = str(bid_count) if bid_count else "0"

    prompt = f"""{rules}

---

CRITICAL REMINDERS:
1. Write your bid like a human texting - plain conversational text, no special formatting. Follow the ONE GOLDEN EXAMPLE.
2. Your training data is from 2025. Use google_web_search to verify current versions of AI models, APIs, frameworks, services.

Now analyze this project. Follow ALL rules at the top of the prompt.

PROJECT ID: {project_id}
TITLE: {title}
BUDGET: {budget}
AVERAGE BID: {avg_bid_str}
BID COUNT: {bid_count_str}

DESCRIPTION:
{description}

---

First, write a "THOUGHTS:" section with your step-by-step reasoning (Risk, Tech, Budget).
Then, output the marker "===RESULT===" followed by the exact format specified (VERDICT, SUMMARY, BID, AMOUNT, PERIOD).
If the verdict is BID, end the bid with a question. If something is unclear in the project - ask about it. If the project is clear and well-described - ask when they are ready to start (e.g. "I'm free now, when do you want to begin?").
"""

    logger.info(f"Analyzing project {project_id}: {title[:50]}...")

    response = _run_gemini_cli(prompt, model)
    if not response:
        return None

    logger.debug(f"Raw Gemini response:\n{response}")

    parsed = parse_response(response)
    if parsed:
        logger.info(f"Project {project_id} verdict: {parsed.verdict}")

    return parsed


def force_bid_analysis(
    project_id: int,
    title: str,
    description: str,
    budget: str,
    avg_bid: float = None,
    bid_count: int = None,
    model: str = None,
) -> Optional[AnalysisResult]:
    """Force generate a bid for a project (skip verdict, always BID).

    Use this when user clicks "Ask for Bid" on a skipped project.
    """
    model = model or settings.gemini_model
    rules = load_rules()

    # Format avg_bid string for prompt
    avg_bid_str = f"{avg_bid:.0f}" if avg_bid else "No bids yet"
    bid_count_str = str(bid_count) if bid_count else "0"

    prompt = f"""{rules}

---

CRITICAL REMINDERS:
1. Write your bid like a human texting - plain conversational text, no special formatting. Follow the ONE GOLDEN EXAMPLE.
2. Your training data is from 2025. Use google_web_search to verify current versions of AI models, APIs, frameworks, services.

The user has decided to bid on this project despite previous SKIP recommendation.
Your task: Generate a BID for this project. Do NOT skip. VERDICT must be BID.

PROJECT ID: {project_id}
TITLE: {title}
BUDGET: {budget}
AVERAGE BID: {avg_bid_str}
BID COUNT: {bid_count_str}

DESCRIPTION:
{description}

---

Generate a bid. VERDICT must be BID. Follow bid writing rules from instructions above.
First, write a "THOUGHTS:" section with your step-by-step reasoning.
Then, output the marker "===RESULT===" followed by the exact format specified (VERDICT, SUMMARY, BID, AMOUNT, PERIOD).
End the bid with a question. If something is unclear - ask about it. If everything is clear - ask when they are ready to start.
"""

    logger.info(f"Force-generating bid for project {project_id}: {title[:50]}...")

    response = _run_gemini_cli(prompt, model)
    if not response:
        return None

    logger.debug(f"Raw Gemini response:\n{response}")

    parsed = parse_response(response)
    if parsed:
        # Force verdict to BID in case AI still returned SKIP
        parsed.verdict = "BID"
        logger.info(f"Force bid for project {project_id}: amount={parsed.amount}")

    return parsed
