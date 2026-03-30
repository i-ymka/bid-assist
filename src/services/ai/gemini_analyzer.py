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

_MODEL_SHORT: dict[str, str] = {
    "gemini-3.1-pro-preview":        "pro-3.1",
    "gemini-3.1-flash-preview":      "flash-3.1",
    "gemini-3-flash-preview":        "flash-3",
    "gemini-3-pro-preview":          "pro-3",
    "gemini-3.1-flash-lite-preview": "flash-3.1-lite",
}

def _short_model(model: str) -> str:
    return _MODEL_SHORT.get(model, model)


_TITLE_COLORS = ["bright_red", "orange1", "gold1", "chartreuse1", "spring_green1", "cyan1", "deep_sky_blue1", "hot_pink"]
_color_cache: dict[int, str] = {}  # local cache to avoid DB hit on every log line
_color_repo = None  # set by init_color_db() at startup

_ACCT_COLORS: dict[str, str] = {"ymka": "plum1", "yehia": "cornflower_blue"}


def _acct_color(name: str) -> str:
    """Return Rich color name for an account, defaulting to white."""
    return _ACCT_COLORS.get(name.lower(), "white")


def init_color_db(repo) -> None:
    global _color_repo
    _color_repo = repo


def _title_color(project_id: int) -> str:
    if project_id in _color_cache:
        return _color_cache[project_id]
    if _color_repo is not None:
        idx = _color_repo.get_or_assign_color(project_id, len(_TITLE_COLORS))
    else:
        idx = project_id % len(_TITLE_COLORS)  # fallback (shouldn't happen)
    color = _TITLE_COLORS[idx]
    _color_cache[project_id] = color
    return color


# Prompt paths (configurable per-account via PROMPTS_DIR in .env)
_ROOT = Path(__file__).parent.parent.parent.parent
ANALYSIS_RULES_PATH = _ROOT / settings.prompts_dir / "analyze.md"
BID_WRITER_RULES_PATH = _ROOT / settings.prompts_dir / "bid_writer.md"

# Per-(home_dir, model) cooldown tracking. Key: (home_dir, model), Value: unix timestamp until ready.
# home_dir="" means default ~/.gemini (pro account).
_cooldowns: dict[tuple[str, str], float] = {}

# Flash overload retry counters. Key: (home_dir, model), Value: retry count so far.
_overload_retries: dict[tuple[str, str], int] = {}

# Slow-model tracking: if a model hangs for too long, skip all accounts using it temporarily.
_model_slow_until: dict[str, float] = {}
RETRY_SKIP_THRESHOLD = 60    # seconds: skip retries if attempt took longer than this
MODEL_SLOW_THRESHOLD  = 300  # seconds: mark model as degraded if hung this long
MODEL_SLOW_COOLDOWN   = 300  # seconds: how long to skip the degraded model

# Set to True when all accounts/models are exhausted. Consumed once by analysis_loop to send notification.
_all_exhausted_flag: bool = False

# Account pool — populated lazily on first call
_pool_initialized: bool = False
_primary_home: str = ""        # expanded path or "" for default
_pool_homes: list[str] = []    # expanded paths for free accounts

# Load-based rotation: active CLI subprocess count per home_dir
_active_counts: dict[str, int] = {}
_MAX_ACTIVE_PRIMARY = 2   # pro account: 2 concurrent (RPM ~5, each call ~2min → safe)
_MAX_ACTIVE_POOL    = 1   # free accounts: 1 concurrent (lower RPM + verify-account risk)

# Auth-disabled accounts: log once, skip silently afterwards
_auth_disabled: set = set()

# Per-account lock: only one thread tries a given account at a time.
# Prevents 5 parallel threads from all hitting the same broken account.
import threading
_account_locks: dict[str, threading.Semaphore] = {}
_account_locks_guard = threading.Lock()  # protects _account_locks dict itself

# Shutdown: flag + track active subprocesses to kill them
_shutdown_flag: bool = False
_active_procs: set = set()  # active subprocess.Popen objects
_active_procs_lock = threading.Lock()


def shutdown_gemini():
    """Signal all running/pending Gemini CLI calls to abort and kill active subprocesses."""
    global _shutdown_flag
    _shutdown_flag = True
    with _active_procs_lock:
        for proc in _active_procs:
            try:
                proc.terminate()
            except OSError:
                pass


def _init_pool() -> None:
    """Lazily initialize account pool from settings."""
    global _pool_initialized, _primary_home, _pool_homes
    if _pool_initialized:
        return
    from pathlib import Path
    _primary_home = str(Path(settings.gemini_home_primary).expanduser()) if settings.gemini_home_primary else ""
    _pool_homes = [str(Path(p).expanduser()) for p in settings.gemini_home_pool]
    _pool_initialized = True
    logger.info(f"Gemini pool: 1 pro + {len(_pool_homes)} free accounts")


def consume_exhaustion_flag() -> bool:
    """Return True (and clear flag) if all accounts just became exhausted. Call from analysis_loop."""
    global _all_exhausted_flag
    if _all_exhausted_flag:
        _all_exhausted_flag = False
        return True
    return False


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
    is_price_nope: bool = False          # True when PASS but market price < floor (min_daily_rate)


def _load_prompt(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    logger.warning(f"Prompt file not found: {path}")
    return ""



def _parse_quota_cooldown(stderr: str) -> int:
    """Parse exact cooldown from Gemini CLI quota error.

    Looks for retryDelayMs (precise) or "reset after Xh Ym Zs" (human-readable).
    Returns seconds to wait. Falls back to 24h if unparseable.
    """
    # Try retryDelayMs first (most precise): retryDelayMs: 82640512.98
    ms_match = re.search(r'retryDelayMs:\s*([\d.]+)', stderr)
    if ms_match:
        return int(float(ms_match.group(1)) / 1000)

    # Try human-readable: "reset after 22h57m20s" or "reset after 5h3m"
    human_match = re.search(r'reset after\s+(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?', stderr)
    if human_match:
        h = int(human_match.group(1) or 0)
        m = int(human_match.group(2) or 0)
        s = int(human_match.group(3) or 0)
        total = h * 3600 + m * 60 + s
        if total > 0:
            return total

    return 3600  # fallback: 1h (real quota resets are always ≤24h, usually much less)


def _classify_cli_error(stderr: str) -> str:
    lower = stderr.lower()
    # Cancelled/interrupted — highest priority
    if "operation cancelled" in lower or "sigint" in lower or "sigterm" in lower:
        return "cancelled"
    # Real quota exhaustion — check BEFORE keychain (stderr often contains both keychain warning + quota error)
    if "quota_exhausted" in lower or "resource_exhausted" in lower:
        return "quota"
    if "429" in lower or "rate limit" in lower:
        return "overload"
    # Server capacity issue — no cooldown, just try next account
    if "no capacity available" in lower or "capacity available for model" in lower:
        return "overload"
    # Auth/verification — account needs manual intervention, disable until restart
    if ("authentication failed" in lower or "invalid credentials" in lower or
            "verify your account" in lower or "validationrequirederror" in lower or
            ("credentials" in lower and "not found" in lower and "quota" not in lower)):
        return "auth"
    return "unknown"


def _extract_clean_error(stderr: str) -> str:
    msg_match = re.search(r'"message":\s*"([^"]+)"', stderr)
    if msg_match:
        return msg_match.group(1)
    if "Operation cancelled" in stderr:
        return "Operation cancelled (interrupted)"
    for line in stderr.strip().split("\n"):
        line = line.strip()
        if line and not line.startswith((
            "Loaded cached", "YOLO mode", "Attempt", "    at ",
            "Keychain initialization", "MacOS default keychain",
            "Using FileKeychain",
        )):
            return line[:200]
    return stderr[:200]


def _run_gemini_cli(
    prompt: str,
    primary_model: str,
    pool_model: str,
    timeout: int = 600,
    call_label: str = "",
    log_title: str = "",
    project_id: int = 0,
    required_pattern: Optional[str] = None,
    account_name: str = "",
) -> Optional[str]:
    """Run Gemini CLI with automatic account pool rotation on quota exhaustion.

    Args:
        prompt: Full prompt text
        primary_model: Model to use with the primary (pro) account
        pool_model: Model to use with free pool accounts
        timeout: Subprocess timeout in seconds
        call_label: e.g. "call1" or "call2" — prefixed to each attempt log
        log_title: Short title for log lines (e.g. project title)
        project_id: For colored title in log lines

    Returns:
        CLI stdout text, or None if all attempts failed.
    """
    global _all_exhausted_flag
    if _shutdown_flag:
        return None
    _init_pool()

    # Build ordered (home_dir, model) pairs:
    # 1. pro account + primary model (e.g. pro-3.1)
    # 2. pro account + pool model (e.g. flash-3) — separate quota per model
    # 3. free accounts + pool model
    pairs = [(_primary_home, primary_model)]
    if pool_model != primary_model:
        pairs.append((_primary_home, pool_model))
    for home in _pool_homes:
        pairs.append((home, pool_model))

    now = time.time()
    available = []
    for home, model in pairs:
        key = (home, model)
        until = _cooldowns.get(key, 0)
        if now < until:
            remaining = int(until - now)
            label = Path(home).name if home else "default"
            logger.debug(f"{label}/{_short_model(model)}: cooldown {remaining}s left, skipping")
        elif _active_counts.get(home, 0) >= (_MAX_ACTIVE_PRIMARY if home == _primary_home else _MAX_ACTIVE_POOL):
            label = Path(home).name if home else "default"
            max_for_account = _MAX_ACTIVE_PRIMARY if home == _primary_home else _MAX_ACTIVE_POOL
            logger.debug(f"{label}/{_short_model(model)}: {_active_counts[home]} active (max {max_for_account}), skipping")
        elif now < _model_slow_until.get(model, 0):
            label = Path(home).name if home else "default"
            remaining = int(_model_slow_until[model] - now)
            logger.debug(f"{label}/{_short_model(model)}: model degraded {remaining}s left, skipping")
        else:
            available.append((home, model))

    if not available:
        logger.error("All Gemini accounts/models are on cooldown")
        _all_exhausted_flag = True
        return None

    tag = f"[light_cyan1]{call_label}[/light_cyan1]  " if call_label else ""
    if account_name:
        ac = _acct_color(account_name)
        who = f"[{ac}]{account_name}[/{ac}]  "
    else:
        who = ""
    if log_title and project_id:
        tc = _title_color(project_id)
        colored_title = f"  [{tc}]{log_title}[/{tc}]"
    elif log_title:
        colored_title = f"  {log_title}"
    else:
        colored_title = ""

    for home, model in available:
        if _shutdown_flag:
            return None

        # Re-check cooldown (another thread may have disabled this account)
        key = (home, model)
        if time.time() < _cooldowns.get(key, 0):
            continue

        label = Path(home).name if home else "pro"
        short = _short_model(model)

        # Semaphore per account: pro allows 2 concurrent, free pool allows 1.
        # If account is broken (auth error), thread sets cooldown — others re-check after acquiring.
        with _account_locks_guard:
            if home not in _account_locks:
                limit = _MAX_ACTIVE_PRIMARY if home == _primary_home else _MAX_ACTIVE_POOL
                _account_locks[home] = threading.Semaphore(limit)
            lock = _account_locks[home]

        if not lock.acquire(timeout=0.1):
            # All slots occupied — skip, try next account
            continue
        try:
            # Re-check after acquiring lock (account may have been disabled while waiting)
            if time.time() < _cooldowns.get((home, model), 0):
                continue
            if time.time() < _model_slow_until.get(model, 0):
                continue

            env = None
            if home:
                import os as _os
                env = {**_os.environ, "HOME": home}

            logger.info(f"{tag}{who}[dim]{label}/{short}[/dim]{colored_title}")
            t0 = time.time()
            _active_counts[home] = _active_counts.get(home, 0) + 1
            try:
                proc = subprocess.Popen(
                    ["gemini", "-m", model, "--yolo", "-p", prompt],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                    start_new_session=True,
                )
                with _active_procs_lock:
                    _active_procs.add(proc)
                try:
                    stdout, stderr = proc.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    raise
                finally:
                    with _active_procs_lock:
                        _active_procs.discard(proc)

                # Build a result-like object for compatibility with existing code
                class _R:
                    pass
                result = _R()
                result.returncode = proc.returncode
                result.stdout = stdout
                result.stderr = stderr
            finally:
                _active_counts[home] = max(0, _active_counts.get(home, 1) - 1)

            if _shutdown_flag:
                return None

            if result.returncode < 0:
                logger.debug(f"Gemini CLI killed by signal {-result.returncode}")
                return None

            if result.returncode != 0:
                error_type = _classify_cli_error(result.stderr)
                clean_msg = _extract_clean_error(result.stderr)

                if error_type == "cancelled":
                    return None
                elif error_type == "auth":
                    _cooldowns[(home, model)] = time.time() + 86400  # 24h = effectively disabled
                    if home not in _auth_disabled:
                        _auth_disabled.add(home)
                        logger.info(f"{tag}{label}/{short}: [bold red]auth error[/bold red] — disabled — {clean_msg}")
                    continue
                elif error_type == "quota":
                    cooldown_sec = _parse_quota_cooldown(result.stderr)
                    cd_h, cd_m = divmod(cooldown_sec // 60, 60)
                    fallback_note = " [fallback]" if cooldown_sec == 86400 else ""
                    logger.info(f"{tag}{label}/{short}: [bold red]quota exhausted[/bold red] — {cd_h}h {cd_m}m{fallback_note}  {clean_msg}")
                    logger.debug(f"quota stderr: {result.stderr[:500]}")
                    _cooldowns[(home, model)] = time.time() + cooldown_sec
                    continue
                elif error_type == "overload":
                    elapsed = time.time() - t0
                    if elapsed > RETRY_SKIP_THRESHOLD:
                        logger.info(f"{tag}{label}/{short}: [bright_yellow]overload[/bright_yellow] — slow ({elapsed:.0f}s), skip retry → next")
                        if elapsed > MODEL_SLOW_THRESHOLD:
                            _model_slow_until[model] = time.time() + MODEL_SLOW_COOLDOWN
                            logger.info(f"{tag}model [bold]{short}[/bold] degraded → skip for {MODEL_SLOW_COOLDOWN // 60}min")
                        continue
                    okey = (home, model)
                    n = _overload_retries.get(okey, 0) + 1
                    if n <= 3:
                        _overload_retries[okey] = n
                        logger.info(f"{tag}{label}/{short}: [bright_yellow]overload[/bright_yellow] — retry {n}/3")
                        available.append((home, model))
                    else:
                        _overload_retries.pop(okey, None)
                        fallback = settings.gemini_overload_fallback_model
                        if fallback and fallback != model:
                            logger.info(f"{tag}{label}/{short}: [bright_yellow]overload[/bright_yellow] — fallback → {_short_model(fallback)}")
                            available.append((home, fallback))
                        else:
                            logger.info(f"{tag}{label}/{short}: [bright_yellow]overload[/bright_yellow] — giving up")
                    continue
                else:
                    elapsed = time.time() - t0
                    if elapsed > RETRY_SKIP_THRESHOLD:
                        logger.info(f"{tag}{label}/{short}: [bold yellow]slow fail ({elapsed:.0f}s)[/bold yellow] — skip retry → next")
                        if elapsed > MODEL_SLOW_THRESHOLD:
                            _model_slow_until[model] = time.time() + MODEL_SLOW_COOLDOWN
                            logger.info(f"{tag}model [bold]{short}[/bold] degraded → skip for {MODEL_SLOW_COOLDOWN // 60}min")
                    else:
                        logger.info(f"{tag}{label}/{short}: [bold yellow]{clean_msg}[/bold yellow]")
                    continue

            _cooldowns.pop((home, model), None)
            _overload_retries.pop((home, model), None)

            response = result.stdout.strip()
            for boilerplate in [
                "Loaded cached credentials.",
                "YOLO mode is enabled. All tool calls will be automatically approved.",
            ]:
                response = response.replace(boilerplate, "")
            response = response.strip()
            if not response:
                elapsed = time.time() - t0
                logger.info(f"{tag}{label}/{short}: [bold yellow]empty response[/bold yellow] ({elapsed:.0f}s)")
                if elapsed > MODEL_SLOW_THRESHOLD:
                    _model_slow_until[model] = time.time() + MODEL_SLOW_COOLDOWN
                    logger.info(f"{tag}model [bold]{short}[/bold] degraded → skip for {MODEL_SLOW_COOLDOWN // 60}min")
                continue
            if required_pattern and not re.search(required_pattern, response, re.IGNORECASE):
                elapsed = time.time() - t0
                logger.info(f"{tag}{label}/{short}: [bold yellow]malformed response[/bold yellow] ({elapsed:.0f}s) — no pattern, try next")
                logger.debug(f"{tag}{label}/{short}: malformed snippet: {response[:200]!r}")
                continue
            return response

        except subprocess.TimeoutExpired:
            logger.error(f"{tag}{label}/{short}: timed out ({timeout}s)")
            continue
        finally:
            lock.release()

    logger.error("All Gemini accounts failed")
    _all_exhausted_flag = True
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

    response = _run_gemini_cli(prompt, settings.gemini_model, settings.gemini_pool_model, timeout=1200, call_label="call1", log_title=title[:55], project_id=project_id, required_pattern=r"VERDICT:\s*(PASS|SKIP|BID)")
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
            logger.error(f"[Call 1] No VERDICT in response: {title[:55]}")
            return None
    else:
        verdict = verdict_match.group(1).upper()

    days = int(days_match.group(1)) if days_match else 1
    summary = summary_match.group(1).strip() if summary_match else ""

    tc = _title_color(project_id)
    if verdict == "PASS":
        logger.info(f"[sea_green2]PASS[/sea_green2]  [{tc}]{title[:60]}[/{tc}]  ({days}d)")
    else:
        logger.info(f"[red3]SKIP[/red3]  [{tc}]{title[:60]}[/{tc}]")
    return {"verdict": verdict, "days": days, "summary": summary}


def _calculate_amount(
    days: int,
    avg_bid_usd: float,
    budget_min_usd: float,
    budget_max_usd: float,
    min_daily_rate: int = 100,
    bid_adjustment: int = -10,
    tier2_pct: int = 65,
    tier3_pct: int = 50,
    account_name: str = "",
    silent: bool = False,
    title: str = "",
    project_id: int = 0,
) -> Optional[float]:
    """Deterministic pricing formula.

    Args:
        days: Estimated working days
        avg_bid_usd: Average bid on the project in USD (0 if none)
        budget_min_usd: Client's minimum budget in USD
        budget_max_usd: Client's maximum budget in USD
        min_daily_rate: Minimum USD per day (default 100)
        bid_adjustment: % above/below market (-10 = 10% below, 0 = at market, +10 = 10% above)
        tier2_pct: % of min_daily_rate applied for 4-7 day projects (default 65)
        tier3_pct: % of min_daily_rate applied for 8+ day projects (default 50)

    Returns:
        Bid amount in USD, rounded to nearest $10.
        Returns None if market price is below our minimum daily rate (→ SKIP).
    """
    if days <= 3:
        effective_rate = min_daily_rate
    elif days <= 7:
        effective_rate = min_daily_rate * tier2_pct / 100
    else:
        effective_rate = min_daily_rate * tier3_pct / 100
    floor = days * effective_rate
    multiplier = 1 + bid_adjustment / 100

    if avg_bid_usd and avg_bid_usd > 0:
        target = avg_bid_usd * multiplier
    else:
        midpoint = ((budget_min_usd or 0) + (budget_max_usd or 0)) / 2
        target = midpoint * multiplier

    if target < floor:
        if not silent:
            if account_name:
                ac = _acct_color(account_name)
                who = f"[{ac}]{account_name}[/{ac}]: "
            else:
                who = ""
            tc = _title_color(project_id) if project_id else "white"
            title_part = f"[{tc}]{title[:55]}[/{tc}]  " if title else ""
            logger.info(
                f"[slate_blue1]NOPE[/slate_blue1]  {who}{title_part}${target:.0f} < floor ${floor:.0f}  ({days}d × ${effective_rate:.0f}/d)"
            )
        return None  # signal to caller: skip this project

    # Never bid below client's minimum budget (Freelancer rejects such bids)
    if budget_min_usd and budget_min_usd > 0:
        effective_min = round(budget_min_usd / 10) * 10
        if target < effective_min:
            if not silent:
                logger.info(
                    f"Pricing: target ${target:.0f} raised to budget_min ${effective_min:.0f}"
                )
            target = effective_min

    final = round(target / 10) * 10
    if not silent:
        if account_name:
            ac = _acct_color(account_name)
            who = f"[{ac}]{account_name}[/{ac}]: "
        else:
            who = ""
        tc = _title_color(title) if title else "white"
        title_part = f"[{tc}]{title[:55]}[/{tc}]  " if title else ""
        logger.info(
            f"[bold green]YEP[/bold green]   {who}{title_part}${final:.0f} > floor ${floor:.0f}  ({days}d × ${effective_rate:.0f}/d)"
        )
    return final


def write_bid(
    project_id: int,
    title: str,
    description: str,
    summary: str,
    amount: float,
    period: int,
    owner_name: str = "",
    account_name: str = "",
) -> tuple[Optional[str], Optional[float]]:
    """Call 2: Write bid text for a project that passed feasibility.

    Args:
        summary: SUMMARY from Call 1 (context for the bid writer)
        amount: Pre-calculated bid amount (DO NOT mention in bid text)
        period: Working days (DO NOT mention in bid text)
        owner_name: Display name of the client (optional, for personalization)

    Returns:
        Tuple of (bid_text, fair_price). bid_text is None if the call failed.
    """
    rules = _load_prompt(BID_WRITER_RULES_PATH)

    client_line = f"\nCLIENT NAME: {owner_name}" if owner_name else ""

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
        suffix = f" [dim](attempt {attempt}/{max_attempts})[/dim]" if attempt > 1 else ""
        response = _run_gemini_cli(prompt, settings.bid_model, settings.bid_pool_model, timeout=600, call_label="call2", log_title=title[:55], project_id=project_id, account_name=account_name)
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

    logger.error(f"[Call 2] All {max_attempts} attempts failed: {title[:55]}")
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
    owner_name: str = "",
    bid_adjustment: int = -10,
    feasibility: Optional[dict] = None,
    tier2_pct: int = 65,
    tier3_pct: int = 50,
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
    amount = _calculate_amount(days, avg_bid_usd, budget_min_usd, budget_max_usd, min_daily_rate, bid_adjustment, tier2_pct, tier3_pct)
    if amount is None:
        return AnalysisResult(
            verdict="SKIP",
            summary=f"{feasibility['summary']} [Market price below ${min_daily_rate}/day minimum]",
            bid_text="",
            amount=0,
            period=days,
            raw_response="",
            is_price_nope=True,
        )

    # --- Call 2: Bid writing ---
    bid_text, fair_price = write_bid(
        project_id, title, description,
        feasibility["summary"], amount, days,
        owner_name=owner_name,
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
    owner_name: str = "",
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
    amount = _calculate_amount(days, avg_bid_usd, budget_min_usd, budget_max_usd, min_daily_rate, bid_adjustment, tier2_pct, tier3_pct)
    if amount is None:
        amount = round((days * min_daily_rate) / 10) * 10
        logger.info(f"Force bid: market below floor, using floor ${amount:.0f}")

    # Call 2: write bid
    bid_text, fair_price = write_bid(
        project_id, title, description, summary, amount, days,
        owner_name=owner_name,
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


def analyse_weekly_bids(
    wins: list[dict],
    losses: list[dict],
    my_profile: dict,
) -> Optional[str]:
    """Run Gemini analysis on a week's worth of bids and return actionable suggestions.

    Args:
        wins: List of win dicts (project_id, title, amount, bid_text, my_time_to_bid_sec).
        losses: List of loss dicts (same + winner_* profile fields).
        my_profile: Dict with my current profile stats and settings.

    Returns:
        Analysis text (str) or None if Gemini CLI failed.
    """
    def _fmt_time(secs):
        if secs is None:
            return "?"
        if secs < 3600:
            return f"{secs // 60}min"
        return f"{secs // 3600}h {(secs % 3600) // 60}m"

    wins_block = ""
    for w in wins:
        wins_block += (
            f"- [{w.get('title', 'N/A')}] Amount: ${w.get('amount', '?')} | "
            f"Time to bid: {_fmt_time(w.get('my_time_to_bid_sec'))}\n"
            f"  Bid excerpt: {str(w.get('bid_text', ''))[:300]}\n"
        )

    losses_block = ""
    for lo in losses:
        reg_date = lo.get("winner_reg_date")
        years_on = ""
        if reg_date:
            import time as _time
            years = (_time.time() - reg_date) / (365.25 * 86400)
            years_on = f"{years:.1f}yr"
        losses_block += (
            f"- [{lo.get('title', 'N/A')}] My bid: ${lo.get('my_amount', '?')} | "
            f"Winner bid: ${lo.get('winner_amount', '?')}\n"
            f"  Time to bid — me: {_fmt_time(lo.get('my_time_to_bid_sec'))} | "
            f"winner: {_fmt_time(lo.get('winner_time_to_bid_sec'))}\n"
            f"  Winner profile: {lo.get('winner_reviews', '?')} reviews | "
            f"${lo.get('winner_hourly_rate', '?')}/hr | {years_on} on platform | "
            f"earnings score {lo.get('winner_earnings_score', '?')}/10 | "
            f"portfolio: {lo.get('winner_portfolio_count', '?')} items\n"
            f"  My bid excerpt: {str(lo.get('bid_text', ''))[:300]}\n"
        )

    profile_block = (
        f"Username: {my_profile.get('username', '?')} | "
        f"Country: {my_profile.get('country', '?')} | "
        f"Rating: {my_profile.get('rating', '?')} | "
        f"Reviews: {my_profile.get('reviews', '?')} | "
        f"Hourly rate: ${my_profile.get('hourly_rate', '?')}/hr | "
        f"Years on platform: {my_profile.get('years_on_platform', '?')} | "
        f"Earnings score: {my_profile.get('earnings_score', '?')}/10 | "
        f"Portfolio: {my_profile.get('portfolio_count', '?')} items\n"
        f"Settings: bid_adjustment={my_profile.get('bid_adjustment', '?')}% | "
        f"min_daily_rate=${my_profile.get('min_daily_rate', '?')}/day | "
        f"prompts_dir={my_profile.get('prompts_dir', '?')}"
    )

    prompt = f"""You are an expert freelance bid coach. Analyse the following weekly bidding data and provide concrete, actionable improvement suggestions.

=== MY PROFILE ===
{profile_block}

=== WINS THIS WEEK ({len(wins)}) ===
{wins_block or '(none)'}

=== LOSSES THIS WEEK ({len(losses)}) ===
{losses_block or '(none)'}

=== YOUR TASK ===
1. Identify patterns that distinguish wins from losses (price, speed, bid style, project type, winner profile).
2. Provide at least 3 numbered, specific improvement suggestions I can act on immediately.
3. Prioritise suggestions by expected impact (highest first).
4. Be direct — no fluff. Concrete numbers and examples where possible.

Format: plain text, numbered suggestions, no markdown headers.
"""

    return _run_gemini_cli(prompt, settings.gemini_model, settings.gemini_pool_model, timeout=300, call_label="analysis", log_title="weekly analysis")
