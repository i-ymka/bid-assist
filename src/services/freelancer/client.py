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

            log_fn = logger.debug if e.response.status_code == 404 else logger.error
            log_fn(
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

    def put(self, endpoint: str, data: Dict = None, params: Dict = None) -> Dict[str, Any]:
        """Make a PUT request."""
        return self._request("PUT", endpoint, params=params, json_data=data)

    def delete(self, endpoint: str) -> Dict[str, Any]:
        """Make a DELETE request."""
        return self._request("DELETE", endpoint)

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

        logger.debug(f"Authenticated user ID: {self._user_id}")
        return self._user_id

    def get_remaining_bids(self) -> Optional[int]:
        """Get remaining bid count via Freelancer internal API.

        Uses the getBidLimit.php endpoint which requires the session-based
        freelancer-auth-v2 token (set FREELANCER_AUTH_V2 in .env).

        Returns:
            Number of remaining bids, or None if unavailable.
        """
        try:
            auth_v2 = settings.freelancer_auth_v2
            if not auth_v2:
                return None

            user_id = self.get_user_id()
            response = requests.get(
                "https://www.freelancer.com/ajax-api/projects/getBidLimit.php",
                params={"userId": user_id, "compact": "true"},
                headers={
                    "freelancer-auth-v2": auth_v2,
                    "Accept": "application/json",
                },
                timeout=10,
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    result = data.get("result", {})
                    remaining = result.get("bidsRemaining")
                    limit = result.get("bidLimit")
                    logger.info(f"Bids remaining: {remaining} / {limit}")
                    return remaining

            logger.debug(f"getBidLimit failed: {response.status_code}")
            return None

        except Exception as e:
            logger.error(f"Failed to get remaining bids: {e}")
            return None

    def get_project_owner_info(self, project_id: int) -> Optional[Dict[str, Any]]:
        """Fetch project owner info via auth-v2 API.

        Uses the same projects endpoint but with freelancer-auth-v2 header
        and owner_info=true to get client's country/city.

        Returns:
            owner_info dict with country, city etc., or None if unavailable.
        """
        auth_v2 = settings.freelancer_auth_v2
        if not auth_v2:
            return None

        try:
            response = requests.get(
                f"https://www.freelancer.com/api/projects/0.1/projects/{project_id}/",
                params={"owner_info": "true", "compact": "true"},
                headers={
                    "freelancer-auth-v2": auth_v2,
                    "Accept": "application/json",
                },
                timeout=10,
            )

            if response.status_code == 200:
                data = response.json()
                result = data.get("result", {})
                owner_info = result.get("owner_info")
                if owner_info:
                    logger.debug(f"Project {project_id} owner_info: {owner_info}")
                    return owner_info

            return None

        except Exception as e:
            logger.warning(f"Failed to get owner info for {project_id}: {e}")
            return None
