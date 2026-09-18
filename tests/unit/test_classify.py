import pytest

from eventmesh_common.classify import Outcome, classify_response


@pytest.mark.parametrize("status", [200, 201, 204, 299])
def test_2xx_is_success(status):
    assert classify_response(status).outcome == Outcome.SUCCESS


@pytest.mark.parametrize("status", [300, 301, 302, 307, 308])
def test_3xx_is_permanent_failure_not_followed(status):
    assert classify_response(status).outcome == Outcome.PERMANENT_FAILURE


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_declared_permanent_4xx(status):
    assert classify_response(status).outcome == Outcome.PERMANENT_FAILURE


@pytest.mark.parametrize("status", [408, 409, 425, 429])
def test_declared_retryable_4xx(status):
    assert classify_response(status).outcome == Outcome.RETRY


@pytest.mark.parametrize("status", [404, 406, 410, 451])
def test_undeclared_4xx_defaults_to_permanent(status):
    assert classify_response(status).outcome == Outcome.PERMANENT_FAILURE


@pytest.mark.parametrize("status", [500, 502, 503, 504, 599])
def test_5xx_is_retryable(status):
    assert classify_response(status).outcome == Outcome.RETRY


def test_timeout_is_retryable():
    assert classify_response(None, timed_out=True).outcome == Outcome.RETRY


def test_connection_error_is_retryable():
    assert classify_response(None, connection_error=True).outcome == Outcome.RETRY


def test_no_response_defaults_retryable():
    assert classify_response(None).outcome == Outcome.RETRY
