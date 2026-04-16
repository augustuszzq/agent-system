from autoresearch.retries.policy import RetryPolicy
from autoresearch.settings import RetryPolicySettings


def test_retry_policy_accepts_whitelisted_category_and_action() -> None:
    policy = RetryPolicy(
        RetryPolicySettings(
            safe_retry_categories=("FILESYSTEM_UNAVAILABLE",),
            allowed_actions=("RETRY_SAME_CONFIG",),
        )
    )

    assert policy.allows(category="FILESYSTEM_UNAVAILABLE", action="RETRY_SAME_CONFIG") is True


def test_retry_policy_rejects_non_whitelisted_category() -> None:
    policy = RetryPolicy(
        RetryPolicySettings(
            safe_retry_categories=("FILESYSTEM_UNAVAILABLE",),
            allowed_actions=("RETRY_SAME_CONFIG",),
        )
    )

    assert policy.allows(category="RESOURCE_OOM", action="RETRY_SAME_CONFIG") is False

