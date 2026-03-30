"""Per-account service initialization."""

import logging
from typing import Dict

from src.config.account import AccountConfig
from src.services.freelancer.client import FreelancerClient
from src.services.freelancer.projects import ProjectService
from src.services.freelancer.bidding import BiddingService
from src.services.telegram.notifier import Notifier

logger = logging.getLogger(__name__)


def init_account_services(account: AccountConfig) -> Dict:
    """Create per-account service instances.

    Returns dict with keys: freelancer_client, project_service, bidding_service, notifier.
    """
    client = FreelancerClient(oauth_token=account.freelancer_token)
    # NOTE: freelancer_auth_v2 is currently read from global `settings` in client methods
    # (getBidLimit, getCountry). Will need per-account injection when refactoring client.

    project_service = ProjectService(client)
    bidding_service = BiddingService(client)
    notifier = Notifier(
        bot_token=account.telegram_token,
        chat_ids=account.telegram_chat_ids,
        thread_id=account.telegram_thread_id,
    )

    logger.info(f"Services initialized for account: {account.name}")

    return {
        "freelancer_client": client,
        "project_service": project_service,
        "bidding_service": bidding_service,
        "notifier": notifier,
    }


def init_all_services(accounts: list[AccountConfig]) -> Dict[str, Dict]:
    """Initialize services for all accounts.

    Returns: {account_name: {freelancer_client, project_service, bidding_service, notifier}}
    """
    all_services = {}
    for acc in accounts:
        all_services[acc.name] = init_account_services(acc)
    return all_services
