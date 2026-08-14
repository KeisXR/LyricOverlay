import sys

sys.path.insert(0, "src")

from runtime_status import RuntimeStatus, classify_fetch_error


def test_fetch_error_classification():
    assert classify_fetch_error("ReadTimeout").status == RuntimeStatus.NETWORK_TIMEOUT
    assert classify_fetch_error("HTTP 429 Too Many Requests").status == RuntimeStatus.RATE_LIMITED
    assert classify_fetch_error("DNS name resolution failed").status == RuntimeStatus.NETWORK_ERROR
    assert classify_fetch_error("provider returned malformed data").status == RuntimeStatus.PROVIDER_ERROR
