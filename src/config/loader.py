"""Multi-account loader: discovers .env.* files and builds orchestrator config."""

import glob
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from src.config.account import AccountConfig, load_account

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorConfig:
    """Aggregated config for all accounts + merged filter parameters."""

    accounts: List[AccountConfig] = field(default_factory=list)

    # Merged filters (union/widest range across all accounts)
    merged_budget_min: int = 0
    merged_budget_max: int = 0
    merged_skill_ids: List[int] = field(default_factory=list)

    @property
    def account_names(self) -> List[str]:
        return [a.name for a in self.accounts]

    def get_account(self, name: str) -> AccountConfig:
        """Get account by name. Raises KeyError if not found."""
        for a in self.accounts:
            if a.name == name:
                return a
        raise KeyError(f"Account '{name}' not found")


def discover_accounts(pattern: str = ".env.*") -> List[str]:
    """Find all .env.* files in the working directory (excluding .env.example)."""
    paths = sorted(glob.glob(pattern))
    return [
        p for p in paths
        if not p.endswith(".example")
        and not p.endswith(".bak")
        and Path(p).is_file()
    ]


def build_config(env_paths: List[str] = None) -> OrchestratorConfig:
    """Load all accounts and compute merged filter parameters.

    Args:
        env_paths: Explicit list of .env file paths. If None, auto-discovers.
    """
    if env_paths is None:
        env_paths = discover_accounts()

    if not env_paths:
        raise RuntimeError("No .env.* account files found")

    accounts = []
    for path in env_paths:
        try:
            acc = load_account(path)
            accounts.append(acc)
            logger.info(f"Loaded account: {acc.name} ({path})")
        except Exception as e:
            logger.error(f"Failed to load account from {path}: {e}")

    if not accounts:
        raise RuntimeError("No accounts loaded successfully")

    # Merge filter parameters across all accounts
    # Budget: widest range (min of mins, max of maxes)
    # These are .env defaults — runtime DB settings override per-account later
    all_budget_mins = [a.min_daily_rate for a in accounts]  # fallback
    all_budget_maxes = [a.max_bid_count for a in accounts]  # not budget, just a field

    # Skills: union of all account skill IDs
    all_skills = set()
    for a in accounts:
        all_skills.update(a.skill_ids)

    config = OrchestratorConfig(
        accounts=accounts,
        merged_skill_ids=sorted(all_skills),
    )

    logger.info(
        f"Orchestrator: {len(accounts)} accounts ({', '.join(config.account_names)}), "
        f"{len(config.merged_skill_ids)} merged skills"
    )
    return config
