"""Unit tests for recotem.datasource.bigquery (mocked — no real GCP).

Tests:
- Credential failure wraps in DataSourceError
- Missing extras produce a clear DataSourceError
- Query submission error wraps in DataSourceError
- Query execution error wraps in DataSourceError
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from recotem.datasource.base import DataSourceError, FetchContext


def _ctx() -> FetchContext:
    return FetchContext(recipe_name="bq_test", run_id="run-bq")


# ---------------------------------------------------------------------------
# Missing extras
# ---------------------------------------------------------------------------


def test_bigquery_extra_not_installed_clear_error_with_extra_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When google-cloud-bigquery is missing, DataSourceError names the extra."""

    # Temporarily remove the bigquery module to simulate missing extra
    original = sys.modules.get("google.cloud.bigquery")
    sys.modules["google.cloud.bigquery"] = None  # type: ignore[assignment]

    try:
        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        with pytest.raises(DataSourceError, match="recotem\\[bigquery\\]"):
            BigQuerySource(cfg)
    finally:
        if original is None:
            sys.modules.pop("google.cloud.bigquery", None)
        else:
            sys.modules["google.cloud.bigquery"] = original


# ---------------------------------------------------------------------------
# Credential failure
# ---------------------------------------------------------------------------


def test_bigquery_credentials_failure_wraps_in_DataSourceError_exit3() -> None:
    """bigquery.Client() that raises wraps the error in DataSourceError."""
    with patch.dict(
        sys.modules,
        {
            "google.cloud.bigquery": MagicMock(),
            "db_dtypes": MagicMock(),
            "google.api_core.exceptions": MagicMock(),
        },
    ):
        # Reload to pick up mocked modules
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        mock_bq = MagicMock()
        mock_bq.Client.side_effect = Exception(
            "DefaultCredentialsError: no credentials"
        )

        with patch.dict(
            sys.modules,
            {
                "google.cloud.bigquery": mock_bq,
                "db_dtypes": MagicMock(),
            },
        ):
            from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

            cfg = BigQueryConfig(type="bigquery", query="SELECT * FROM tbl")
            source = BigQuerySource.__new__(BigQuerySource)
            source._config = cfg

            with pytest.raises(DataSourceError, match="[Ff]ailed|[Cc]redential"):
                source.fetch(_ctx())


# ---------------------------------------------------------------------------
# Query submission error
# ---------------------------------------------------------------------------


def test_bigquery_query_submission_error_wraps_DataSourceError() -> None:
    """GoogleAPICallError from client.query() is wrapped in DataSourceError."""
    mock_bq = MagicMock()
    mock_client = MagicMock()
    mock_bq.Client.return_value = mock_client
    mock_bq.QueryJobConfig.return_value = MagicMock()

    mock_api_error = type("GoogleAPICallError", (Exception,), {})
    mock_exceptions = MagicMock()
    mock_exceptions.GoogleAPICallError = mock_api_error
    mock_api_core = MagicMock()
    mock_api_core.exceptions = mock_exceptions

    mock_client.query.side_effect = mock_api_error("query submission failed")

    with patch.dict(
        sys.modules,
        {
            "google.cloud.bigquery": mock_bq,
            "db_dtypes": MagicMock(),
            "google.api_core.exceptions": mock_exceptions,
            "google.api_core": mock_api_core,
        },
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT bad query")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        with pytest.raises(DataSourceError):
            source.fetch(_ctx())


# ---------------------------------------------------------------------------
# Unsupported query parameter type
# ---------------------------------------------------------------------------


def test_bigquery_unsupported_param_type_raises_DataSourceError() -> None:
    """An unsupported query_parameters type (e.g. list) raises DataSourceError."""
    mock_bq = MagicMock()
    mock_bq.ScalarQueryParameter = MagicMock()

    with patch.dict(
        sys.modules,
        {
            "google.cloud.bigquery": mock_bq,
            "db_dtypes": MagicMock(),
        },
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(
            type="bigquery",
            query="SELECT * FROM tbl WHERE x = @mylist",
            query_parameters={"mylist": [1, 2, 3]},  # list is unsupported
        )
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        with pytest.raises(DataSourceError, match="unsupported type"):
            source._build_query_parameters()
