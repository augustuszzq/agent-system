from autoresearch.schemas import IncidentCategory, RetryAction
from autoresearch.settings import RetryPolicySettings


class RetryPolicy:
    def __init__(self, settings: RetryPolicySettings) -> None:
        self._settings = settings

    def allows(self, *, category: IncidentCategory, action: RetryAction) -> bool:
        return (
            category in self._settings.safe_retry_categories
            and action in self._settings.allowed_actions
        )

