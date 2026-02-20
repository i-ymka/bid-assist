"""Freelancer API client wrapper."""

import logging
from typing import Optional, Dict, Any
import requests
from src.config import settings
from src.config.constants import FREELANCER_API_BASE_URL, USERS_SELF_ENDPOINT
from src.core.exceptions import FreelancerAPIError

logger = logging.getLogger(__name__)


class FreelancerClient:
    """HTTP client for Freelancer API requests."""

    def __init__(self, oauth_token: str = None):
        """Initialize the Freelancer client.

        Args:
            oauth_token: OAuth token for authentication.
                        If None, uses settings.freelancer_oauth_token.
        """
        self._token = oauth_token or settings.freelancer_oauth_token
        self._base_url = FREELANCER_API_BASE_URL
        self._session = requests.Session()
        self._session.headers.update({
            "Freelancer-OAuth-V1": self._token,
            "Content-Type": "application/json",
        })
        self._user_id: Optional[int] = None

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Dict = None,
        json_data: Dict = None,
    ) -> Dict[str, Any]:
        """Make an API request.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., /projects/0.1/projects/active/)
            params: Query parameters
            json_data: JSON body for POST requests

        Returns:
            Parsed JSON response

        Raises:
            FreelancerAPIError: If the request fails
        """
        url = f"{self._base_url}{endpoint}"

        try:
            response = self._session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                verify=True,
                timeout=30,  # 30 second timeout to prevent hanging
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            error_data = {}
            try:
                error_data = e.response.json()
            except Exception:
                pass

            error_msg = error_data.get("message", str(e))
            error_code = error_data.get("error_code", "UNKNOWN")

            logger.error(
                f"API request failed: {method} {endpoint} - "
                f"{e.response.status_code}: {error_msg}"
            )
            raise FreelancerAPIError(
                message=error_msg,
                status_code=e.response.status_code,
                error_code=error_code,
            )

        except requests.exceptions.Timeout as e:
            logger.error(f"Request timeout: {method} {endpoint} - {e}")
            raise FreelancerAPIError(message=f"Request timed out: {e}")

        except requests.exceptions.RequestException as e:
            logger.error(f"Request error: {method} {endpoint} - {e}")
            raise FreelancerAPIError(message=str(e))

    def get(self, endpoint: str, params: Dict = None) -> Dict[str, Any]:
        """Make a GET request."""
        return self._request("GET", endpoint, params=params)

    def post(self, endpoint: str, data: Dict = None) -> Dict[str, Any]:
        """Make a POST request."""
        return self._request("POST", endpoint, json_data=data)

    def get_user_id(self) -> int:
        """Get the authenticated user's ID.

        Returns:
            The user ID

        Raises:
            FreelancerAPIError: If unable to get user ID
        """
        if self._user_id is not None:
            return self._user_id

        logger.debug("Fetching authenticated user ID...")
        response = self.get(USERS_SELF_ENDPOINT)
        self._user_id = response.get("result", {}).get("id")

        if not self._user_id:
            raise FreelancerAPIError("Could not retrieve user ID from API response")

        logger.info(f"Authenticated user ID: {self._user_id}")
        return self._user_id

    def get_remaining_bids(self) -> Optional[int]:
        """Get remaining bid count for the authenticated user.

        Returns:
            Number of remaining bids, or None if unavailable.
        """
        try:
            response = self.get(USERS_SELF_ENDPOINT, params={
                "membership_details": "true",
                "status": "true",
            })
            result = response.get("result", {})

            # Search all nested dicts for any key containing "bid" and "remain"
            def _find_bid_remaining(obj, path=""):
                if isinstance(obj, dict):
                    for key, val in obj.items():
                        if "bid" in key.lower() and "remain" in key.lower():
                            logger.info(f"Found {path}.{key} = {val}")
                            if val is not None:
                                return int(val)
                        found = _find_bid_remaining(val, f"{path}.{key}")
                        if found is not None:
                            return found
                return None

            remaining = _find_bid_remaining(result, "result")
            if remaining is not None:
                return remaining

            # Log available keys for debugging
            logger.warning(f"bid_remaining not found. Top keys: {list(result.keys())}")
            membership = result.get("membership_package", {})
            if membership:
                logger.debug(f"membership_package keys: {list(membership.keys())}")
            status = result.get("status", {})
            if status:
                logger.debug(f"status keys: {list(status.keys())}")
            return None

        except Exception as e:
            logger.error(f"Failed to get remaining bids: {e}")
            return None
