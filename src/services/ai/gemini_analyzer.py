"""Gemini CLI-based project analyzer.

Two-call architecture:
  Call 1 (analysis_model): analyze.md  → VERDICT / DAYS / SUMMARY
  Call 2 (bid_model):      bid_writer.md → BID text

Pricing is computed deterministically in _calculate_amount(), not by AI.
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

# Prompt paths (configurable per-account via PROMPTS_DIR in .env)
_ROOT = Path(__file__).parent.parent.parent.parent
ANALYSIS_RULES_PATH = _ROOT / settings.prompts_dir / "analyze.md"
BID_WRITER_RULES_PATH = _ROOT / settings.prompts_dir / "bid_writer.md"

# Fallback chains (primary model comes from settings)
ANALYSIS_FALLBACK_MODELS = ["gemini-2.5-pro"]
BID_FALLBACK_MODELS = ["gemini-2.5-pro"]

_cooldowns: dict[str, float] = {}  # model -> timestamp when it can be retried


@dataclass
class AnalysisResult:
    """Result of project analysis."""
    verdict: str   # "BID" or "SKIP"
    summary: str
    bid_text: str
    amount: float
    period: int    # working days
    raw_response: str
    fair_price: Optional[float] = None  # AI's market price estimate from Call 2


def _load_prompt(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    logger.warning(f"Prompt file not found: {path}")
    return ""


def _classify_cli_error(stderr: str) -> str:
    lower = stderr.lower()
    if "429" in lower or "capacity" in lower or "rate limit" in lower or "resource_exhausted" in lower:
        return "capacity"
    if "operation cancelled" in lower or "sigint" in lower or "sigterm" in lower:
        return "cancelled"
    return "unknown"


def _extract_clean_error(stderr: str) -> str:
    msg_match = re.search(r'"message":\s*"([^"]+)"', stderr)
    if msg_match:
        return msg_match.group(1)
    if "Operation cancelled" in stderr:
        return "Operation cancelled (interrupted)"
    for line in stderr.strip().split("\n"):
        line = line.strip()
        if line and not line.startswith(("Loaded cached", "YOLO mode", "Attempt", "    at ")):
            return line[:200]
    return stderr[:200]


def _run_gemini_cli(
    prompt: str,
    model: str,
    fallback_models: list[str],
    timeout: int = 600,
) -> Optional[str]:
    """Run Gemini CLI with automatic model fallback chain on 429 errors.

    Args:
        prompt: Full prompt text
        model: Primary model to use
        fallback_models: Ordered list of fallback models if primary fails
        timeout: Subprocess timeout in seconds

    Returns:
        CLI stdout text, or None if all attempts failed.
    """
    now = time.time()
    all_models = [model] + [m for m in fallback_models if m != model]
    models_to_try = []
    for m in all_models:
        cooldown_until = _cooldowns.get(m, 0)
        if now < cooldown_until:
            remaining = int(cooldown_until - now)
            logger.info(f"Model {m} on cooldown ({remaining}s left), skipping")
        else:
            models_to_try.append(m)

    if not models_to_try:
        logger.error("All models are on cooldown, cannot run")
        return None

    for current_model in models_to_try:
        try:
            logger.info(f"Running Gemini CLI: {current_model}")
            result = subprocess.run(
                ["gemini", "-m", current_model, "--yolo", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode < 0:
                logger.info(f"Gemini CLI killed by signal {-result.returncode}")
                return None

            if result.returncode != 0:
                error_type = _classify_cli_error(result.stderr)
                clean_msg = _extract_clean_error(result.stderr)

                if error_type == "capacity":
                    logger.warning(f"Model {current_model}: 429 — {clean_msg}")
                    _cooldowns[current_model] = time.time() + 300
                    continue
                elif error_type == "cancelled":
                    logger.info("Gemini CLI interrupted (Ctrl+C)")
                    return None
                else:
                    logger.error(f"Gemini CLI failed ({current_model}): {clean_msg}")
                    return None

            _cooldowns.pop(current_model, None)

            response = result.stdout.strip()
            for boilerplate in [
                "Loaded cached credentials.",
                "YOLO mode is enabled. All tool calls will be automatically approved.",
            ]:
                response = response.replace(boilerplate, "")
            return response.strip()

        except subprocess.TimeoutExpired:
            logger.error(f"Gemini CLI timed out ({current_model}, {timeout}s)")
            continue

    return None


def analyze_feasibility(
    project_id: int,
    title: str,
    description: str,
    budget_str: str,
    avg_bid_usd: float,
    bid_count: int,
) -> Optional[dict]:
    """Call 1: Analyze project feasibility.

    Returns dict with keys: verdict ('PASS'/'SKIP'), days (int), summary (str).
    Returns None if the call failed entirely.
    """
    rules = _load_prompt(ANALYSIS_RULES_PATH)

    prompt = f"""{rules}

---

Now analyze this project. Follow ALL rules above.

TITLE: {title}
BUDGET: {budget_str}

DESCRIPTION:
{description}

---

Write your THOUGHTS first (Risk check, Tech check, Red Zone check, Day estimate).
Then output ===RESULT=== and the structured result.
"""

    logger.info(f"[Call 1] Analyzing feasibility: {title[:50]}...")
    response = _run_gemini_cli(prompt, settings.gemini_model, ANALYSIS_FALLBACK_MODELS, timeout=1200)
    if not response:
        return None

    logger.debug(f"[Call 1] Raw response:\n{response}")

    # Parse ===RESULT=== block
    if "===RESULT===" in response:
        result_block = response.split("===RESULT===")[1]
    else:
        result_block = response

    verdict_match = re.search(r"VERDICT:\s*(PASS|SKIP)", result_block, re.IGNORECASE)
    days_match = re.search(r"DAYS:\s*(\d+)", result_block, re.IGNORECASE)
    summary_match = re.search(r"SUMMARY:\s*(.+?)(?=\nVERDICT:|\nDAYS:|\Z)", result_block, re.DOTALL | re.IGNORECASE)

    if not verdict_match:
        # Try to detect BID/SKIP from old format (graceful degradation)
        old_verdict = re.search(r"VERDICT:\s*(BID|SKIP)", result_block, re.IGNORECASE)
        if old_verdict:
            verdict_raw = old_verdict.group(1).upper()
            verdict = "PASS" if verdict_raw == "BID" else "SKIP"
        else:
            logger.error(f"[Call 1] Could not find VERDICT in response for project {project_id}")
            return None
    else:
        verdict = verdict_match.group(1).upper()

    days = int(days_match.group(1)) if days_match else 1
    summary = summary_match.group(1).strip() if summary_match else ""

    logger.info(f"[Call 1] Project {project_id}: VERDICT={verdict}, DAYS={days}")
    return {"verdict": verdict, "days": days, "summary": summary}


def _calculate_amount(
    days: int,
    avg_bid_usd: float,
    budget_min_usd: float,
    budget_max_usd: float,
    min_daily_rate: int = 100,
    bid_adjustment: int = -10,
) -> Optional[float]:
    """Deterministic pricing formula.

    Args:
        days: Estimated working days
        avg_bid_usd: Average bid on the project in USD (0 if none)
        budget_min_usd: Client's minimum budget in USD
        budget_max_usd: Client's maximum budget in USD
        min_daily_rate: Minimum USD per day (default 100)
        bid_adjustment: % above/below market (-10 = 10% below, 0 = at market, +10 = 10% above)

    Returns:
        Bid amount in USD, rounded to nearest $10.
        Returns None if market price is below our minimum daily rate (→ SKIP).
    """
    floor = days * min_daily_rate
    multiplier = 1 + bid_adjustment / 100

    if avg_bid_usd and avg_bid_usd > 0:
        target = avg_bid_usd * multiplier
    else:
        midpoint = ((budget_min_usd or 0) + (budget_max_usd or 0)) / 2
        target = midpoint * multiplier

    if target < floor:
        logger.info(
            f"Pricing: target ${target:.0f} < floor ${floor:.0f} "
            f"({days}d × ${min_daily_rate}/d) — project underpriced for us"
        )
        return None  # signal to caller: skip this project

    return round(target / 10) * 10


def write_bid(
    project_id: int,
    title: str,
    description: str,
    summary: str,
    amount: float,
    period: int,
    owner_username: str = "",
) -> tuple[Optional[str], Optional[float]]:
    """Call 2: Write bid text for a project that passed feasibility.

    Args:
        summary: SUMMARY from Call 1 (context for the bid writer)
        amount: Pre-calculated bid amount (DO NOT mention in bid text)
        period: Working days (DO NOT mention in bid text)
        owner_username: Freelancer username of the client (optional, for personalization)

    Returns:
        Tuple of (bid_text, fair_price). bid_text is None if the call failed.
    """
    rules = _load_prompt(BID_WRITER_RULES_PATH)

    client_line = f"\nCLIENT USERNAME: {owner_username}" if owner_username else ""

    prompt = f"""{rules}

---

Write a bid for this project. Follow ALL rules above.

PROJECT TITLE: {title}{client_line}

PROJECT DESCRIPTION:
{description}

ANALYSIS SUMMARY (context for you, do NOT copy verbatim):
{summary}

YOUR BID AMOUNT: ${amount:.0f} — DO NOT mention this number in the bid text
DELIVERY: {period} day(s) — DO NOT mention this in the bid text

---

Output ONLY the BID: and FAIR_PRICE: lines. No other text.
"""

    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        logger.info(f"[Call 2] Writing bid for project {project_id} (attempt {attempt}/{max_attempts})...")
        response = _run_gemini_cli(prompt, settings.bid_model, BID_FALLBACK_MODELS)
        if not response:
            return None, None

        logger.debug(f"[Call 2] Raw response:\n{response}")

        bid_match = re.search(r"BID:\s*(.+?)(?=\nFAIR_PRICE:|\Z)", response, re.DOTALL | re.IGNORECASE)
        if bid_match:
            bid_text = bid_match.group(1).strip()
        else:
            bid_text = response.strip()

        fair_price_match = re.search(r"FAIR_PRICE:\s*\$?(\d+)", response, re.IGNORECASE)
        fair_price = float(fair_price_match.group(1)) if fair_price_match else None

        if not bid_text:
            logger.error(f"[Call 2] Empty bid text for project {project_id}")
            continue

        # Validate: reject AI thinking chains / search logs / garbage
        rejection = _validate_bid_text(bid_text)
        if rejection:
            logger.error(f"[Call 2] Bid REJECTED (attempt {attempt}): {rejection}")
            logger.error(f"[Call 2] Rejected text: {bid_text[:300]}...")
            continue

        return bid_text, fair_price

    logger.error(f"[Call 2] All {max_attempts} attempts failed for project {project_id}")
    return None, None


# Phrases that indicate AI thinking/search process leaked into bid text
_GARBAGE_PATTERNS = [
    r"(?i)I need to perform",
    r"(?i)I will search for",
    r"(?i)my search for .+ (came up|yielded|returned|failed)",
    r"(?i)I'll (rephrase|re-run|continue|retry)",
    r"(?i)I already submitted the bid",
    r"(?i)task completed",
    r"(?i)I have completed the task",
    r"(?i)(okay|ok),?\s+I",
    r"(?i)search .+ (empty|nothing|failed)",
    r"(?i)google_web_search",
    r"(?i)I'll focus on",
    r"(?i)I'm ready to write the bid",
    r"(?i)got it\.",
    r"(?i)next[:,]?\s+(Laravel|React|WordPress|search)",
]
_GARBAGE_RE = [re.compile(p) for p in _GARBAGE_PATTERNS]


def _validate_bid_text(text: str) -> str | None:
    """Validate bid text. Returns rejection reason or None if OK."""
    # Too short (less than 50 chars = not a real proposal)
    if len(text) < 50:
        return f"Too short ({len(text)} chars)"

    # Too long (> 2000 chars = probably thinking chain)
    if len(text) > 2000:
        return f"Too long ({len(text)} chars)"

    # Check for AI thinking/search patterns
    for pattern in _GARBAGE_RE:
        match = pattern.search(text)
        if match:
            return f"AI thinking detected: '{match.group()[:60]}'"

    # Too many sentences for a bid (3-5 expected, >15 = garbage)
    sentence_count = len(re.findall(r'[.!?]+', text))
    if sentence_count > 15:
        return f"Too many sentences ({sentence_count})"

    return None


def analyze_project(
    project_id: int,
    title: str,
    description: str,
    budget_str: str,
    avg_bid_usd: float,
    bid_count: int,
    budget_min_usd: float = 0,
    budget_max_usd: float = 0,
    min_daily_rate: int = 100,
    owner_username: str = "",
    bid_adjustment: int = -10,
    feasibility: Optional[dict] = None,
) -> Optional[AnalysisResult]:
    """Orchestrate the two-call analysis pipeline.

    Call 1: analyze_feasibility → VERDICT / DAYS / SUMMARY  (skipped if feasibility provided)
    Code:   _calculate_amount → AMOUNT
    Call 2: write_bid → BID text

    Args:
        feasibility: Pre-computed Call 1 result {verdict, days, summary} from shared cache.
                     If provided, Call 1 is skipped entirely.

    Returns AnalysisResult or None if a call failed.
    """
    # --- Call 1: Feasibility (skip if cached result provided) ---
    if feasibility is None:
        feasibility = analyze_feasibility(
            project_id, title, description, budget_str, avg_bid_usd, bid_count
        )
        if not feasibility:
            return None

    if feasibility["verdict"] == "SKIP":
        return AnalysisResult(
            verdict="SKIP",
            summary=feasibility["summary"],
            bid_text="",
            amount=0,
            period=0,
            raw_response="",
        )

    # --- Pricing (deterministic) ---
    days = max(feasibility["days"], 1)
    amount = _calculate_amount(days, avg_bid_usd, budget_min_usd, budget_max_usd, min_daily_rate, bid_adjustment)
    if amount is None:
        return AnalysisResult(
            verdict="SKIP",
            summary=f"{feasibility['summary']} [Market price below ${min_daily_rate}/day minimum]",
            bid_text="",
            amount=0,
            period=days,
            raw_response="",
        )

    # --- Call 2: Bid writing ---
    bid_text, fair_price = write_bid(
        project_id, title, description,
        feasibility["summary"], amount, days,
        owner_username=owner_username,
    )
    if not bid_text:
        logger.error(f"Bid writing failed for project {project_id}")
        return None

    return AnalysisResult(
        verdict="BID",
        summary=feasibility["summary"],
        bid_text=bid_text,
        amount=amount,
        period=days,
        raw_response="",
        fair_price=fair_price,
    )


def force_bid_analysis(
    project_id: int,
    title: str,
    description: str,
    budget_str: str,
    avg_bid_usd: float,
    bid_count: int,
    budget_min_usd: float = 0,
    budget_max_usd: float = 0,
    min_daily_rate: int = 100,
    owner_username: str = "",
    bid_adjustment: int = -10,
) -> Optional[AnalysisResult]:
    """Force generate a bid regardless of SKIP verdict (user clicked 'Ask for Bid').

    Runs Call 1 to get DAYS (ignores SKIP verdict), then calculates amount and writes bid.
    """
    # Call 1 to get day estimate (ignore SKIP verdict)
    feasibility = analyze_feasibility(
        project_id, title, description, budget_str, avg_bid_usd, bid_count
    )
    if feasibility:
        days = max(feasibility["days"], 1)
        summary = feasibility["summary"]
    else:
        # Fallback if Call 1 failed
        days = settings.default_bid_period
        summary = ""
        logger.warning(f"Force bid: Call 1 failed for {project_id}, using default period={days}")

    # Pricing — for forced bids, use floor if market is below minimum
    amount = _calculate_amount(days, avg_bid_usd, budget_min_usd, budget_max_usd, min_daily_rate, bid_adjustment)
    if amount is None:
        amount = round((days * min_daily_rate) / 10) * 10
        logger.info(f"Force bid: market below floor, using floor ${amount:.0f}")

    # Call 2: write bid
    bid_text, fair_price = write_bid(
        project_id, title, description, summary, amount, days,
        owner_username=owner_username,
    )
    if not bid_text:
        logger.error(f"Force bid: Call 2 failed for project {project_id}")
        return None

    logger.info(f"Force bid for project {project_id}: amount={amount}, period={days}")
    return AnalysisResult(
        verdict="BID",
        summary=summary,
        bid_text=bid_text,
        amount=amount,
        period=days,
        raw_response="",
        fair_price=fair_price,
    )
