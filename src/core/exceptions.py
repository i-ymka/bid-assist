"""Custom exceptions for the application."""


class BidAssistError(Exception):
    """Base exception for Bid-Assist application."""

    pass


class APIError(BidAssistError):
    """Error communicating with external APIs."""

    def __init__(self, message: str, status_code: int = None, error_code: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class FreelancerAPIError(APIError):
    """Error from Freelancer API."""

    pass


class BidPlacementError(FreelancerAPIError):
    """Error placing a bid on Freelancer."""

    pass


class AIAnalysisError(BidAssistError):
    """Error during AI analysis."""

    pass


class ConfigurationError(BidAssistError):
    """Configuration or environment variable error."""

    pass
