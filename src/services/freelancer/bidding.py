"""Bidding service for placing bids on Freelancer."""

import logging
from typing import Optional
from src.services.freelancer.client import FreelancerClient
from src.models import Bid, BidResult
from src.config import settings
from src.config.constants import BIDS_ENDPOINT
from src.core.exceptions import BidPlacementError

logger = logging.getLogger(__name__)


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

            payload = {
                "project_id": bid.project_id,
                "bidder_id": bidder_id,
                "amount": bid.amount,
                "period": bid.period,
                "milestone_percentage": bid.milestone_percentage,
                "description": bid.description,
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
