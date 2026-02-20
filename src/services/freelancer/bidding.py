"""Bidding service for placing bids on Freelancer."""

import logging
import re
from typing import Optional, Set
from src.services.freelancer.client import FreelancerClient
from src.models import Bid, BidResult
from src.config import settings
from src.config.constants import BIDS_ENDPOINT, BID_DETAILS_ENDPOINT
from src.core.exceptions import BidPlacementError

logger = logging.getLogger(__name__)


def strip_markdown(text: str) -> str:
    """Remove all markdown formatting from text.

    Removes:
    - **bold** and __bold__
    - *italic* and _italic_
    - `code` and ```code blocks```
    - [links](url)
    - ### headers

    Args:
        text: Text with potential markdown formatting

    Returns:
        Clean text without markdown
    """
    if not text:
        return text

    # Remove code blocks (``` or `)
    text = re.sub(r'```[\s\S]*?```', '', text)  # multiline code blocks
    text = re.sub(r'`([^`]+)`', r'\1', text)  # inline code

    # Remove bold (**text** or __text__)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)

    # Remove italic (*text* or _text_)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)

    # Remove links [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)

    # Remove headers (### text -> text)
    text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)

    return text.strip()


class BiddingService:
    """Service for placing bids on Freelancer projects."""

    def __init__(self, client: FreelancerClient = None):
        """Initialize the bidding service.

        Args:
            client: FreelancerClient instance. If None, creates a new one.
        """
        self._client = client or FreelancerClient()
        self._bidder_id: Optional[int] = None

    def _get_bidder_id(self) -> int:
        """Get the authenticated user's ID for bidding."""
        if self._bidder_id is None:
            self._bidder_id = self._client.get_user_id()
        return self._bidder_id

    def place_bid(self, bid: Bid) -> BidResult:
        """Place a bid on a project.

        Args:
            bid: The Bid object containing bid details.

        Returns:
            BidResult indicating success or failure.
        """
        logger.info(
            f"Placing bid on project {bid.project_id}: "
            f"${bid.amount} for {bid.period} days"
        )

        try:
            bidder_id = self._get_bidder_id()

            # Clean markdown from description
            clean_description = strip_markdown(bid.description)

            payload = {
                "project_id": bid.project_id,
                "bidder_id": bidder_id,
                "amount": bid.amount,
                "period": bid.period,
                "milestone_percentage": bid.milestone_percentage,
                "description": clean_description,
            }

            response = self._client.post(BIDS_ENDPOINT, data=payload)

            if response.get("status") == "success":
                bid_id = response.get("result", {}).get("id")
                logger.info(
                    f"Bid placed successfully on project {bid.project_id}, "
                    f"bid ID: {bid_id}"
                )
                return BidResult(
                    success=True,
                    message="Bid placed successfully",
                    bid_id=bid_id,
                )
            else:
                error_msg = response.get("message", "Unknown error from API")
                logger.error(f"Bid failed for project {bid.project_id}: {error_msg}")
                return BidResult(
                    success=False,
                    message=error_msg,
                    error_code=response.get("error_code"),
                )

        except BidPlacementError as e:
            logger.error(f"Bid placement error for project {bid.project_id}: {e}")
            return BidResult(
                success=False,
                message=str(e),
                error_code=e.error_code,
            )
        except Exception as e:
            logger.error(f"Unexpected error placing bid on project {bid.project_id}: {e}")
            return BidResult(
                success=False,
                message=f"Unexpected error: {e}",
            )

    def place_bid_simple(
        self,
        project_id: int,
        amount: float,
        period: int = None,
        description: str = "",
        milestone_percentage: int = None,
    ) -> BidResult:
        """Convenience method to place a bid without creating a Bid object.

        Args:
            project_id: The project ID to bid on.
            amount: Bid amount in project currency.
            period: Delivery period in days. If None, uses settings.default_bid_period.
            description: Bid description text.
            milestone_percentage: Milestone percentage. If None, uses settings.default_milestone_pct.

        Returns:
            BidResult indicating success or failure.
        """
        bid = Bid(
            project_id=project_id,
            amount=amount,
            period=period or settings.default_bid_period,
            milestone_percentage=milestone_percentage or settings.default_milestone_pct,
            description=description,
        )
        return self.place_bid(bid)

    def get_bid_rank(self, bid_id: int, project_id: int = None, retry_delay: float = 1.0) -> Optional[dict]:
        """Get bid details including rank from Freelancer API.

        Args:
            bid_id: The bid ID returned when bid was placed.
            project_id: The project ID (needed for fetching all bids to find rank).
            retry_delay: Seconds to wait before fetching (API needs time to calculate rank).

        Returns:
            Dict with 'rank' and 'total_bids' keys, or None if failed.
        """
        import time

        # Brief delay to let Freelancer API calculate the rank
        if retry_delay > 0:
            time.sleep(retry_delay)

        try:
            endpoint = BID_DETAILS_ENDPOINT.format(bid_id=bid_id)
            logger.debug(f"Fetching bid details from: {endpoint}")

            # Fetch bid details
            response = self._client.get(
                endpoint,
                params={
                    "bid_stats": "true",  # Try to get bid statistics
                }
            )

            if response.get("status") == "success":
                result = response.get("result", {})

                # Check if bid_rank field exists (API returns None but might work someday)
                bid_rank = result.get("bid_rank")
                logger.info(f"bid_rank from API: {bid_rank}")

                # If we have project_id, fetch all bids and find our position
                if project_id:
                    rank_data = self._get_rank_from_project_bids(bid_id, project_id)
                    if rank_data:
                        return rank_data

                # If no rank available, return None
                logger.info(f"Rank not available for bid {bid_id}")
                return None

            logger.warning(f"Could not get rank for bid {bid_id}")
            return None

        except Exception as e:
            logger.error(f"Error fetching bid rank for {bid_id}: {e}")
            return None

    def _get_rank_from_project_bids(self, bid_id: int, project_id: int) -> Optional[dict]:
        """Try to get rank by fetching all bids for the project.

        Note: Freelancer API returns bids sorted by DATE for freelancers,
        not by rank. The actual competitive rank is only visible to employers.
        We return position in the date-sorted list as an approximation.

        Returns:
            Dict with 'rank', 'total_bids', and 'avg_bid', or None if failed.
        """
        try:
            from src.config.constants import PROJECT_BIDS_ENDPOINT

            endpoint = PROJECT_BIDS_ENDPOINT.format(project_id=project_id)
            response = self._client.get(
                endpoint,
                params={
                    "limit": 200,
                }
            )

            if response.get("status") == "success":
                bids = response.get("result", {}).get("bids", [])
                total_bids = len(bids)

                # Calculate average bid amount
                amounts = [b.get("amount") for b in bids if b.get("amount")]
                avg_bid = sum(amounts) / len(amounts) if amounts else 0

                logger.info(f"Fetched {total_bids} bids for project {project_id} (avg: {avg_bid:.0f})")

                # Find our bid's position (note: sorted by date, not rank)
                for idx, bid in enumerate(bids):
                    if bid.get("id") == bid_id:
                        position = idx + 1  # 1-based position
                        logger.info(f"Our bid at position {position} of {total_bids}")
                        return {"rank": position, "total_bids": total_bids, "avg_bid": avg_bid}

                # Bid not found in list, just return total
                logger.warning(f"Our bid {bid_id} not found in bids list")
                return {"rank": None, "total_bids": total_bids, "avg_bid": avg_bid}

            return None

        except Exception as e:
            logger.error(f"Error fetching project bids: {e}")
            return None

    def get_my_bidded_project_ids(self, limit: int = 100) -> Set[int]:
        """Get project IDs that the user has already bid on.

        Args:
            limit: Maximum number of recent bids to fetch.

        Returns:
            Set of project IDs that have been bid on.
        """
        try:
            bidder_id = self._get_bidder_id()
            response = self._client.get(
                BIDS_ENDPOINT,
                params={
                    "bidders[]": bidder_id,
                    "limit": limit,
                }
            )

            if response.get("status") == "success":
                bids = response.get("result", {}).get("bids", [])
                project_ids = {bid.get("project_id") for bid in bids if bid.get("project_id")}
                logger.info(f"Found {len(project_ids)} projects already bid on")
                return project_ids

            logger.warning("Could not fetch user's bids")
            return set()

        except Exception as e:
            logger.error(f"Error fetching user's bids: {e}")
            return set()

    def has_bid_on_project(self, project_id: int) -> bool:
        """Check if user has already bid on a specific project.

        Args:
            project_id: The project ID to check.

        Returns:
            True if already bid on, False otherwise.
        """
        bidded_ids = self.get_my_bidded_project_ids()
        return project_id in bidded_ids

    def get_remaining_bids(self) -> Optional[int]:
        """Get remaining bid count."""
        return self._client.get_remaining_bids()
