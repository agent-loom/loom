from agent_platform.integrations.errors import (
    IntegrationError,
    PlaneError,
    RetryableError,
    ScmError,
)


class TestErrorHierarchy:
    def test_scm_error_is_integration_error(self):
        assert issubclass(ScmError, IntegrationError)

    def test_plane_error_is_integration_error(self):
        assert issubclass(PlaneError, IntegrationError)

    def test_retryable_error_is_integration_error(self):
        assert issubclass(RetryableError, IntegrationError)

    def test_scm_not_plane(self):
        assert not issubclass(ScmError, PlaneError)

    def test_plane_not_scm(self):
        assert not issubclass(PlaneError, ScmError)


class TestStatusCode:
    def test_default_status_code_is_none(self):
        err = IntegrationError("fail")
        assert err.status_code is None

    def test_status_code_preserved(self):
        err = ScmError("not found", status_code=404)
        assert err.status_code == 404
        assert "not found" in str(err)

    def test_plane_error_with_status(self):
        err = PlaneError("server error", status_code=500)
        assert err.status_code == 500

    def test_retryable_error_with_status(self):
        err = RetryableError("gateway timeout", status_code=504)
        assert err.status_code == 504


class TestExceptionCatching:
    def test_catch_scm_as_integration(self):
        try:
            raise ScmError("test", status_code=400)
        except IntegrationError as e:
            assert e.status_code == 400

    def test_catch_plane_as_integration(self):
        try:
            raise PlaneError("test")
        except IntegrationError:
            pass

    def test_catch_retryable_as_integration(self):
        try:
            raise RetryableError("test", status_code=503)
        except IntegrationError as e:
            assert e.status_code == 503
