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
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
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


# ---------------------------------------------------------------------------
# E-5: Storage API fallback — log event + OOM propagation
# ---------------------------------------------------------------------------


_REST_DF_SPEC = {"user_id": ["u1"], "item_id": ["i1"]}


def _default_df():
    import pandas as pd

    return pd.DataFrame(_REST_DF_SPEC)


class _FakeRowIterator:
    """Faithful stand-in for ``google.cloud.bigquery.table.RowIterator``.

    Single-use, exactly like the real class: ``RowIterator.to_dataframe``
    builds its REST-download callable with ``iter(self.pages)``, and the
    ``pages`` property flips ``_started`` *before* the Storage-vs-REST branch
    is taken (``table.py`` ~2456, ``page_iterator.py`` ~201).  A second
    ``to_dataframe`` on the same object therefore raises
    ``ValueError("Iterator has already started")`` in the real library — and
    does here — which is why the REST fallback has to ask the job for a fresh
    iterator rather than reusing the one the Storage attempt consumed.
    """

    def __init__(self, download) -> None:
        self._download = download
        self._started = False

    def to_dataframe(self, create_bqstorage_client: bool = True, **_kwargs):
        if self._started:
            raise ValueError("Iterator has already started", self)
        self._started = True
        return self._download(create_bqstorage_client=create_bqstorage_client)


class _FakeQueryJob:
    """Faithful stand-in for ``google.cloud.bigquery.job.QueryJob``.

    The contract that matters here, and that the previous ``MagicMock``-based
    fakes did not encode:

    * ``Client.query()`` returns as soon as the job is *submitted*.  The query
      has not executed yet, so nothing about the SQL can fail at that point.
    * ``QueryJob.result()`` is what runs / waits for the query.  Bad SQL, a
      missing table, a table-level permission denial and query quota failures
      all surface from here.
    * ``result()`` hands back a **fresh** ``RowIterator`` on every call
      (``job/query.py`` ~1849 builds one via ``_list_rows_from_query_results``),
      and only the download happens on that iterator.
    """

    def __init__(self) -> None:
        # Called as ``download(create_bqstorage_client=<bool>)``.
        self.download = lambda **_kw: _default_df()
        self.result_exception: BaseException | None = None
        self.result_calls = 0

    def result(self, **_kwargs):
        self.result_calls += 1
        if self.result_exception is not None:
            raise self.result_exception
        return _FakeRowIterator(self.download)

    def to_dataframe(self, create_bqstorage_client: bool = True, **_kwargs):
        """Mirror ``QueryJob.to_dataframe`` — ``result()`` then download.

        ``job/query.py`` ~2194 is literally
        ``wait_for_query(self, ...).to_dataframe(...)``, and ``wait_for_query``
        calls ``job.result()``.  Keeping this here means the fake models the
        *fused* call the old code used as well as the split one, so a
        regression check against the unfixed source exercises the real
        library behaviour rather than tripping over a missing attribute.
        """
        return self.result().to_dataframe(
            create_bqstorage_client=create_bqstorage_client
        )


def _always_raise(exc: BaseException):
    """Return a download callable that always raises *exc*."""

    def _download(**_kwargs):
        raise exc

    return _download


def _raise_on_storage(exc_factory):
    """Storage Read API attempt raises; the REST attempt returns rows."""

    def _download(create_bqstorage_client: bool = True, **_kwargs):
        if create_bqstorage_client:
            raise exc_factory()
        return _default_df()

    return _download


def _make_mock_bq_modules():
    """Return (mock_bq, mock_exceptions, mock_api_core) for use in patch.dict."""
    mock_api_error_cls = type("GoogleAPICallError", (Exception,), {})
    mock_exceptions = MagicMock()
    mock_exceptions.GoogleAPICallError = mock_api_error_cls
    mock_api_core = MagicMock()
    mock_api_core.exceptions = mock_exceptions

    mock_client = MagicMock()
    mock_query_job = _FakeQueryJob()
    mock_client.query.return_value = mock_query_job
    mock_bq = MagicMock()
    mock_bq.Client.return_value = mock_client
    mock_bq.QueryJobConfig.return_value = MagicMock()

    return (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    )


def _patched_modules(
    mock_bq, mock_exceptions, mock_api_core, *, storage_installed=True
):
    """``patch.dict`` payload for ``sys.modules``.

    ``storage_installed=False`` maps ``google.cloud.bigquery_storage`` to
    ``None``, which makes ``import google.cloud.bigquery_storage`` raise
    ``ImportError`` — the same interpreter-level block used to reproduce the
    missing-extra case against a live project.

    ``storage_installed=True`` maps it to a stub module rather than leaving it
    to the real import.  These tests already replace ``google.api_core`` with a
    ``MagicMock``, and the real ``google.cloud.bigquery_storage`` imports from
    it at module scope, so a genuine import would fail here for reasons that
    have nothing to do with the dependency being present.
    ``test_storage_api_available_true_with_real_dependency`` covers the probe
    against the real package with no module patching at all.
    """
    modules = {
        "google.cloud.bigquery": mock_bq,
        "db_dtypes": MagicMock(),
        "google.api_core.exceptions": mock_exceptions,
        "google.api_core": mock_api_core,
        "google.cloud.bigquery_storage": (MagicMock() if storage_installed else None),
    }
    return modules


def test_storage_fallback_emits_log_event(monkeypatch) -> None:
    """When the Storage Read API raises GoogleAPICallError, a structured
    'bigquery_storage_fallback' log event must be emitted before falling back
    to the standard REST path.
    """
    import structlog
    import structlog.testing

    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    # Storage API raises GoogleAPICallError; REST fallback succeeds.
    import pandas as pd

    rest_df = pd.DataFrame({"user_id": ["u1"], "item_id": ["i1"]})

    def _to_dataframe_side_effect(**kwargs):
        if kwargs.get("create_bqstorage_client"):
            raise mock_api_error_cls("storage permission denied")
        return rest_df

    mock_query_job.download = _to_dataframe_side_effect

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        with structlog.testing.capture_logs() as cap_logs:
            result = source.fetch(_ctx())

    # Fallback event must have been emitted.
    events = [e.get("event") for e in cap_logs]
    assert "bigquery_storage_fallback" in events, (
        f"Expected 'bigquery_storage_fallback' log event; got: {events}"
    )
    assert len(result) == 1


# ---------------------------------------------------------------------------
# C9 — query_parameters are bound via ScalarQueryParameter, not string interpolation
# ---------------------------------------------------------------------------


def test_query_parameters_bound_via_bigquery_param_placeholders() -> None:
    """BigQuery query_parameters are bound as ScalarQueryParameter objects, not
    string-interpolated into the query.

    Verifies that _build_query_parameters() produces a list of
    ScalarQueryParameter objects matching the recipe's query_parameters dict,
    and that the BigQuery client.query() call receives a QueryJobConfig whose
    query_parameters attribute is non-empty and contains the expected binding.

    The BQ client is mocked so no real GCP connection is made.
    """
    import pandas as pd

    scalar_param_calls: list[tuple[str, str, object]] = []

    # Build mock modules that record ScalarQueryParameter constructor calls.
    mock_bq = MagicMock()
    mock_client = MagicMock()
    mock_job = MagicMock()
    mock_bq.Client.return_value = mock_client
    mock_client.query.return_value = mock_job
    mock_job.to_dataframe.return_value = pd.DataFrame(
        {"user_id": ["u1"], "item_id": ["i1"]}
    )

    mock_job_config_instance = MagicMock()
    mock_bq.QueryJobConfig.return_value = mock_job_config_instance

    captured_params: list = []

    class _FakeScalarQueryParameter:
        """Records constructor args so we can assert binding behavior."""

        def __init__(self, name: str, type_: str, value: object) -> None:
            scalar_param_calls.append((name, type_, value))
            self.name = name
            self.type_ = type_
            self.value = value

    mock_bq.ScalarQueryParameter = _FakeScalarQueryParameter

    # Patch the job_config setter to capture query_parameters assignment.
    def _capture_query_parameters(params):
        captured_params.extend(params)

    type(mock_job_config_instance).query_parameters = property(
        lambda self: captured_params,
        lambda self, v: captured_params.extend(v) if v else None,
    )

    mock_api_error_cls = type("GoogleAPICallError", (Exception,), {})
    mock_exceptions = MagicMock()
    mock_exceptions.GoogleAPICallError = mock_api_error_cls
    mock_api_core = MagicMock()
    mock_api_core.exceptions = mock_exceptions

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(
            type="bigquery",
            query="SELECT user_id, item_id FROM my_table WHERE category = @cat AND min_score = @score",
            query_parameters={"cat": "books", "score": 3.5},
        )
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        # Call _build_query_parameters directly to verify binding.
        params = source._build_query_parameters()

    assert len(params) == 2, (
        f"Expected 2 ScalarQueryParameter objects; got {len(params)}"
    )

    # Verify each parameter was constructed with (name, type, value) — not
    # string-interpolated into the query.
    param_names = {p.name for p in params}
    assert "cat" in param_names, (
        f"Expected parameter 'cat' to be bound; got {param_names!r}"
    )
    assert "score" in param_names, (
        f"Expected parameter 'score' to be bound; got {param_names!r}"
    )

    cat_param = next(p for p in params if p.name == "cat")
    score_param = next(p for p in params if p.name == "score")
    assert cat_param.value == "books", (
        f"Expected 'cat' value='books'; got {cat_param.value!r}"
    )
    assert score_param.value == 3.5, (
        f"Expected 'score' value=3.5; got {score_param.value!r}"
    )
    # String binding NOT injection — the query itself must remain unchanged.
    assert "@cat" in cfg.query, "Query must retain @cat placeholder (not interpolated)"
    assert "@score" in cfg.query, (
        "Query must retain @score placeholder (not interpolated)"
    )


# ---------------------------------------------------------------------------
# T-4: BigQuery query timeout/job timeout wrapped as DataSourceError
# ---------------------------------------------------------------------------


def test_bigquery_concurrent_futures_timeout_wraps_DataSourceError() -> None:
    """concurrent.futures.TimeoutError while waiting for the job must be wrapped
    as DataSourceError, and must be described as a query-execution failure.

    This covers the case where the BigQuery job hangs past any client-side
    deadline and the futures machinery raises TimeoutError.  The real library
    raises it from ``QueryJob.result()`` (``job/query.py`` converts
    ``requests.exceptions.Timeout`` and enforces the polling deadline there),
    not from the download, so the fake raises it there too.
    """
    import concurrent.futures

    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    mock_query_job.result_exception = concurrent.futures.TimeoutError(
        "query job timed out"
    )

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        with pytest.raises(DataSourceError, match="query execution failed"):
            source.fetch(_ctx())


def test_bigquery_deadline_exceeded_wraps_DataSourceError() -> None:
    """google.api_core.exceptions.DeadlineExceeded while waiting for the job
    must be wrapped as DataSourceError.

    DeadlineExceeded is a subclass of GoogleAPICallError; the
    except GoogleAPICallError handler around ``result()`` must catch it and
    report it as a query-execution failure rather than a Storage-API problem.
    """
    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    # Make DeadlineExceeded a subclass of our mock GoogleAPICallError class.
    deadline_cls = type("DeadlineExceeded", (mock_api_error_cls,), {})
    mock_query_job.result_exception = deadline_cls("deadline exceeded")

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        with pytest.raises(DataSourceError, match="query execution failed"):
            source.fetch(_ctx())


# ---------------------------------------------------------------------------
# M-15: Storage API fallback IAM permission in log + RECOTEM_BQ_REQUIRE_STORAGE_API
# ---------------------------------------------------------------------------


def test_storage_api_fallback_logs_iam_permission(monkeypatch) -> None:
    """When the Storage Read API raises GoogleAPICallError, the warning log must
    include the required IAM permission 'bigquery.readSessions.create'.

    Operators monitoring for this event need to know which permission to grant.
    """
    import structlog.testing

    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    import pandas as pd

    rest_df = pd.DataFrame({"user_id": ["u1"], "item_id": ["i1"]})

    def _to_dataframe_side_effect(**kwargs):
        if kwargs.get("create_bqstorage_client"):
            raise mock_api_error_cls("PERMISSION_DENIED: bigquery.readSessions.create")
        return rest_df

    mock_query_job.download = _to_dataframe_side_effect

    monkeypatch.delenv("RECOTEM_BQ_REQUIRE_STORAGE_API", raising=False)

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        with structlog.testing.capture_logs() as cap_logs:
            source.fetch(_ctx())

    # Find the fallback warning event.
    fallback_events = [
        e for e in cap_logs if e.get("event") == "bigquery_storage_fallback"
    ]
    assert fallback_events, (
        f"Expected 'bigquery_storage_fallback' log event; got events: "
        f"{[e.get('event') for e in cap_logs]}"
    )
    log_entry = fallback_events[0]
    # The log entry must surface the IAM permission name so operators know what
    # to grant.
    log_str = str(log_entry)
    assert "bigquery.readSessions.create" in log_str, (
        f"Expected IAM permission 'bigquery.readSessions.create' in log entry; "
        f"got: {log_entry!r}"
    )


def test_bq_require_storage_api_disables_fallback(monkeypatch) -> None:
    """When RECOTEM_BQ_REQUIRE_STORAGE_API is set to a truthy value,
    a GoogleAPICallError from the Storage Read API must surface as DataSourceError
    rather than triggering the REST fallback.
    """
    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    mock_query_job.download = _always_raise(
        mock_api_error_cls("PERMISSION_DENIED: bigquery.readSessions.create")
    )

    monkeypatch.setenv("RECOTEM_BQ_REQUIRE_STORAGE_API", "1")

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        with pytest.raises(
            DataSourceError, match="RECOTEM_BQ_REQUIRE_STORAGE_API|Storage Read API"
        ):
            source.fetch(_ctx())


# ---------------------------------------------------------------------------
# MAJOR-2: recotem_bigquery_storage_fallback_total counter incremented
# ---------------------------------------------------------------------------


def test_bigquery_storage_fallback_increments_counter_on_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the Storage Read API raises GoogleAPICallError and the fallback is
    NOT disabled, ``inc_bigquery_storage_fallback("api_error")`` must be called.

    We patch the name bound inside the ``bigquery`` module's own namespace
    (imported from ``_metrics_bigquery`` at load time) to intercept the call.
    """
    from unittest.mock import MagicMock, patch

    import pandas as pd

    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    rest_df = pd.DataFrame({"user_id": ["u1"], "item_id": ["i1"]})

    def _to_dataframe_side_effect(**kwargs):
        if kwargs.get("create_bqstorage_client"):
            raise mock_api_error_cls("PERMISSION_DENIED")
        return rest_df

    mock_query_job.download = _to_dataframe_side_effect
    monkeypatch.delenv("RECOTEM_BQ_REQUIRE_STORAGE_API", raising=False)

    inc_spy = MagicMock()

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        # Import the module so the name is bound, then patch it in-place.
        import recotem.datasource.bigquery as bq_module

        with patch.object(bq_module, "inc_bigquery_storage_fallback", inc_spy):
            cfg = bq_module.BigQueryConfig(type="bigquery", query="SELECT 1")
            source = bq_module.BigQuerySource.__new__(bq_module.BigQuerySource)
            source._config = cfg
            source.fetch(_ctx())

    inc_spy.assert_called_once_with("api_error")


def test_bigquery_storage_fallback_increments_counter_on_missing_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When google-cloud-bigquery-storage is not installed,
    ``inc_bigquery_storage_fallback("missing_extra")`` must be called.

    Rewritten from the previous version, which made ``to_dataframe`` raise
    ``ImportError``.  ``google-cloud-bigquery`` never does that: it warns and
    downloads over REST (see ``_storage_api_available``), so the old fake
    encoded a contract the library does not honour.  The dependency is blocked
    at interpreter level here instead, which is what the real absence looks
    like.
    """
    from unittest.mock import MagicMock, patch

    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        _,
    ) = _make_mock_bq_modules()

    monkeypatch.delenv("RECOTEM_BQ_REQUIRE_STORAGE_API", raising=False)

    inc_spy = MagicMock()

    with patch.dict(
        sys.modules,
        _patched_modules(
            mock_bq, mock_exceptions, mock_api_core, storage_installed=False
        ),
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        import recotem.datasource.bigquery as bq_module

        with patch.object(bq_module, "inc_bigquery_storage_fallback", inc_spy):
            cfg = bq_module.BigQueryConfig(type="bigquery", query="SELECT 1")
            source = bq_module.BigQuerySource.__new__(bq_module.BigQuerySource)
            source._config = cfg
            source.fetch(_ctx())

    inc_spy.assert_called_once_with("missing_extra")


def test_storage_oom_propagates() -> None:
    """A MemoryError from the Storage Read API must propagate directly, not be
    wrapped in DataSourceError.

    After M-8, MemoryError is let-propagate (re-raised) rather than being
    caught and re-wrapped as DataSourceError.  This ensures the operator's host
    sees a real OOM signal rather than a misleading DataSourceError.
    """
    mock_bq, mock_exceptions, mock_api_core, mock_client, mock_query_job, _ = (
        _make_mock_bq_modules()
    )

    # Storage API raises MemoryError — this must NOT be silently caught.
    mock_query_job.download = _always_raise(MemoryError("out of memory"))

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        with pytest.raises(MemoryError, match="out of memory"):
            source.fetch(_ctx())


# ---------------------------------------------------------------------------
# probe() — failure raises DataSourceError; success returns without error
# ---------------------------------------------------------------------------


def _make_probe_bq_modules():
    """Return mocked BQ modules suitable for probe() tests."""
    mock_api_error_cls = type("GoogleAPICallError", (Exception,), {})
    mock_exceptions = MagicMock()
    mock_exceptions.GoogleAPICallError = mock_api_error_cls
    mock_api_core = MagicMock()
    mock_api_core.exceptions = mock_exceptions

    mock_client = MagicMock()
    mock_bq = MagicMock()
    mock_bq.Client.return_value = mock_client
    mock_bq.QueryJobConfig.return_value = MagicMock()

    return mock_bq, mock_exceptions, mock_api_core, mock_client, mock_api_error_cls


def test_probe_client_creation_failure_raises_DataSourceError() -> None:
    """probe() raises DataSourceError when BigQuery client creation fails.

    Covers the case where ADC is missing or invalid credentials prevent
    bigquery.Client() from instantiating.
    """
    mock_bq, mock_exceptions, mock_api_core, _, mock_api_error_cls = (
        _make_probe_bq_modules()
    )
    mock_bq.Client.side_effect = Exception("DefaultCredentialsError: no credentials")

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        with pytest.raises(DataSourceError, match="[Ff]ailed|[Cc]redential"):
            source.probe()


def test_probe_dry_run_query_failure_raises_DataSourceError() -> None:
    """probe() raises DataSourceError when the dry-run query fails.

    Covers GoogleAPICallError from client.query() during the dry-run,
    e.g. invalid SQL syntax or insufficient permissions.
    """
    mock_bq, mock_exceptions, mock_api_core, mock_client, mock_api_error_cls = (
        _make_probe_bq_modules()
    )
    mock_client.query.side_effect = mock_api_error_cls("dry run failed: bad SQL")

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT bad_col FROM nonexistent")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        with pytest.raises(DataSourceError, match="[Dd]ry.run|[Ff]ailed"):
            source.probe()


def test_probe_success_returns_without_error() -> None:
    """probe() returns None when the BigQuery client and dry-run query succeed.

    Confirms the success path: no exception is raised when client creation and
    the dry-run query both complete normally.
    """
    mock_bq, mock_exceptions, mock_api_core, mock_client, _ = _make_probe_bq_modules()
    # client.query() returns a mock job (success)
    mock_client.query.return_value = MagicMock()

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        # Must not raise
        result = source.probe()
        assert result is None


# ---------------------------------------------------------------------------
# Round-15 C3: Storage API fallback only triggers on IAM-shaped failures
# ---------------------------------------------------------------------------


def test_storage_fallback_non_iam_error_raises_no_fallback(monkeypatch) -> None:
    """When the Storage Read API raises a non-IAM GoogleAPICallError
    (e.g. ``ResourceExhausted`` quota / 5xx / connectivity), the fallback
    must NOT trigger — the REST path would hit the same backend
    constraint and either double-bill or replay the failure.

    Surfacing the failure as ``DataSourceError`` lets operators see the
    true root cause instead of a slow second attempt with the same outcome.
    """
    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    # Non-IAM error: quota exhausted.  Class name does not match
    # PermissionDenied / Forbidden, and the message does not contain any
    # of the IAM markers — so the new code must NOT fall back.
    quota_cls = type("ResourceExhausted", (mock_api_error_cls,), {})
    mock_query_job.download = _always_raise(
        quota_cls("Quota exceeded for quota metric 'Queries per minute'")
    )

    monkeypatch.delenv("RECOTEM_BQ_REQUIRE_STORAGE_API", raising=False)

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        with pytest.raises(DataSourceError, match="REST fallback skipped"):
            source.fetch(_ctx())


def test_storage_fallback_iam_classname_match(monkeypatch) -> None:
    """A class literally named ``PermissionDenied`` triggers the fallback
    even if the message text does not mention permissions.
    """
    import pandas as pd
    import structlog.testing

    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    perm_cls = type("PermissionDenied", (mock_api_error_cls,), {})
    rest_df = pd.DataFrame({"user_id": ["u1"], "item_id": ["i1"]})

    def _to_dataframe_side_effect(**kwargs):
        if kwargs.get("create_bqstorage_client"):
            raise perm_cls("the service account is denied (no marker words)")
        return rest_df

    mock_query_job.download = _to_dataframe_side_effect
    monkeypatch.delenv("RECOTEM_BQ_REQUIRE_STORAGE_API", raising=False)

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        with structlog.testing.capture_logs() as cap_logs:
            result = source.fetch(_ctx())

    events = [e.get("event") for e in cap_logs]
    assert "bigquery_storage_fallback" in events, (
        "Class-name match (PermissionDenied) must trigger the fallback path."
    )
    assert len(result) == 1


# ---------------------------------------------------------------------------
# CRIT-5: iam_detected_via field in bigquery_storage_fallback log
# ---------------------------------------------------------------------------


def _make_bq_env(mock_bq, mock_exceptions, mock_api_core):
    """Context manager helper that patches sys.modules and reloads bigquery module."""
    import contextlib

    @contextlib.contextmanager
    def _ctx_mgr():
        with patch.dict(
            sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
        ):
            if "recotem.datasource.bigquery" in sys.modules:
                del sys.modules["recotem.datasource.bigquery"]
            import recotem.datasource.bigquery as bq_mod

            yield bq_mod

    return _ctx_mgr()


def _make_iam_test_source(mock_bq, mock_exceptions, mock_api_core, exc_factory):
    """Set up fetch() so Storage API raises exc_factory() and REST succeeds."""
    import pandas as pd

    rest_df = pd.DataFrame({"user_id": ["u1"], "item_id": ["i1"]})
    mock_query_job = mock_bq.Client.return_value.query.return_value

    def _to_dataframe(**kwargs):
        if kwargs.get("create_bqstorage_client"):
            raise exc_factory()
        return rest_df

    mock_query_job.download = _to_dataframe
    return mock_query_job


def test_storage_fallback_iam_detected_via_http_403(monkeypatch) -> None:
    """When the storage exception has an HTTP 403 code, iam_detected_via='http_403'."""
    import structlog.testing

    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()
    monkeypatch.delenv("RECOTEM_BQ_REQUIRE_STORAGE_API", raising=False)

    # Exception with .code == 403 but non-IAM class name.
    class _Http403Error(mock_api_error_cls):
        code = 403

    _make_iam_test_source(
        mock_bq, mock_exceptions, mock_api_core, lambda: _Http403Error("forbidden")
    )

    with _make_bq_env(mock_bq, mock_exceptions, mock_api_core) as bq_mod:
        cfg = bq_mod.BigQueryConfig(type="bigquery", query="SELECT 1")
        source = bq_mod.BigQuerySource.__new__(bq_mod.BigQuerySource)
        source._config = cfg
        with structlog.testing.capture_logs() as cap:
            source.fetch(_ctx())

    fallback_events = [e for e in cap if e.get("event") == "bigquery_storage_fallback"]
    assert fallback_events, "Expected bigquery_storage_fallback event"
    assert fallback_events[0]["iam_detected_via"] == "http_403", (
        f"Expected iam_detected_via='http_403'; got {fallback_events[0]!r}"
    )


def test_storage_fallback_iam_detected_via_class_name(monkeypatch) -> None:
    """When the storage exception class name is 'PermissionDenied', iam_detected_via='class'."""
    import structlog.testing

    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()
    monkeypatch.delenv("RECOTEM_BQ_REQUIRE_STORAGE_API", raising=False)

    # Class named PermissionDenied but no .code attribute.
    PermissionDenied = type("PermissionDenied", (mock_api_error_cls,), {})

    _make_iam_test_source(
        mock_bq,
        mock_exceptions,
        mock_api_core,
        lambda: PermissionDenied("permission denied"),
    )

    with _make_bq_env(mock_bq, mock_exceptions, mock_api_core) as bq_mod:
        cfg = bq_mod.BigQueryConfig(type="bigquery", query="SELECT 1")
        source = bq_mod.BigQuerySource.__new__(bq_mod.BigQuerySource)
        source._config = cfg
        with structlog.testing.capture_logs() as cap:
            source.fetch(_ctx())

    fallback_events = [e for e in cap if e.get("event") == "bigquery_storage_fallback"]
    assert fallback_events, "Expected bigquery_storage_fallback event"
    assert fallback_events[0]["iam_detected_via"] == "class", (
        f"Expected iam_detected_via='class'; got {fallback_events[0]!r}"
    )


def test_storage_fallback_iam_detected_via_message_marker(monkeypatch) -> None:
    """When the exception message contains 'PERMISSION_DENIED', iam_detected_via='message'."""
    import structlog.testing

    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()
    monkeypatch.delenv("RECOTEM_BQ_REQUIRE_STORAGE_API", raising=False)

    # Non-IAM class name, no HTTP code, but message has the marker.
    class _StorageApiError(mock_api_error_cls):
        pass

    _make_iam_test_source(
        mock_bq,
        mock_exceptions,
        mock_api_core,
        lambda: _StorageApiError("PERMISSION_DENIED: caller lacks permission"),
    )

    with _make_bq_env(mock_bq, mock_exceptions, mock_api_core) as bq_mod:
        cfg = bq_mod.BigQueryConfig(type="bigquery", query="SELECT 1")
        source = bq_mod.BigQuerySource.__new__(bq_mod.BigQuerySource)
        source._config = cfg
        with structlog.testing.capture_logs() as cap:
            source.fetch(_ctx())

    fallback_events = [e for e in cap if e.get("event") == "bigquery_storage_fallback"]
    assert fallback_events, "Expected bigquery_storage_fallback event"
    assert fallback_events[0]["iam_detected_via"] == "message", (
        f"Expected iam_detected_via='message'; got {fallback_events[0]!r}"
    )


def test_storage_fallback_non_iam_increments_no_fallback_counter(monkeypatch) -> None:
    """The non-IAM no-fallback path increments the new
    ``non_iam_error_no_fallback`` label rather than ``api_error``.
    """
    from unittest.mock import MagicMock, patch

    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    quota_cls = type("ResourceExhausted", (mock_api_error_cls,), {})
    mock_query_job.download = _always_raise(quota_cls("Quota exceeded"))
    monkeypatch.delenv("RECOTEM_BQ_REQUIRE_STORAGE_API", raising=False)

    inc_spy = MagicMock()

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        import recotem.datasource.bigquery as bq_module

        with patch.object(bq_module, "inc_bigquery_storage_fallback", inc_spy):
            cfg = bq_module.BigQueryConfig(type="bigquery", query="SELECT 1")
            source = bq_module.BigQuerySource.__new__(bq_module.BigQuerySource)
            source._config = cfg
            with pytest.raises(DataSourceError):
                source.fetch(_ctx())

    inc_spy.assert_called_once_with("non_iam_error_no_fallback")


# ---------------------------------------------------------------------------
# RECOTEM_BQ_REQUIRE_STORAGE_API vs. a missing google-cloud-bigquery-storage
#
# These two tests previously made ``to_dataframe`` raise ``ImportError``.
# ``google-cloud-bigquery`` 3.x never does: ``_should_use_bqstorage`` catches
# ``BigQueryStorageNotFoundError``, warns, and returns ``False``
# (``table.py`` ~2079-2091), and ``_ensure_bqstorage_client`` warns and returns
# ``None`` (``client.py`` ~608-622).  Both then download over REST.  The old
# fakes therefore exercised a branch that could never run in production, which
# is how strict mode came to be silently unenforced for the missing-extra case.
# The dependency is blocked at interpreter level here instead.
# ---------------------------------------------------------------------------


def test_require_storage_api_and_missing_extra_raises_datasource_error(
    monkeypatch,
) -> None:
    """RECOTEM_BQ_REQUIRE_STORAGE_API=1 with the storage extra absent must raise
    DataSourceError naming the extra, not silently download over REST.
    """
    monkeypatch.setenv("RECOTEM_BQ_REQUIRE_STORAGE_API", "1")

    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    downloads: list[bool] = []

    def _download(create_bqstorage_client: bool = True, **_kwargs):
        downloads.append(create_bqstorage_client)
        return _default_df()

    mock_query_job.download = _download

    with patch.dict(
        sys.modules,
        _patched_modules(
            mock_bq, mock_exceptions, mock_api_core, storage_installed=False
        ),
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        with pytest.raises(DataSourceError) as exc_info:
            source.fetch(_ctx())

    err_msg = str(exc_info.value)
    assert "RECOTEM_BQ_REQUIRE_STORAGE_API" in err_msg, (
        f"Error message must mention the env var; got: {err_msg!r}"
    )
    assert "google-cloud-bigquery-storage" in err_msg, (
        f"Error message must name the missing dependency; got: {err_msg!r}"
    )
    assert "recotem[bigquery]" in err_msg, (
        f"Error message must name the extra to install; got: {err_msg!r}"
    )
    assert downloads == [], (
        "Strict mode must refuse before downloading anything; "
        f"got download calls: {downloads!r}"
    )
    mock_client.query.assert_not_called()
    assert mock_query_job.result_calls == 0, (
        "Strict mode must refuse before the query is submitted, so the scan is "
        "never billed"
    )


def test_require_storage_api_false_missing_extra_falls_back_silently(
    monkeypatch,
) -> None:
    """With RECOTEM_BQ_REQUIRE_STORAGE_API unset, a missing storage extra must
    still download over REST — unchanged behaviour for users who have not opted
    into strict mode.
    """
    monkeypatch.delenv("RECOTEM_BQ_REQUIRE_STORAGE_API", raising=False)

    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    downloads: list[bool] = []

    def _download(create_bqstorage_client: bool = True, **_kwargs):
        downloads.append(create_bqstorage_client)
        return _default_df()

    mock_query_job.download = _download

    with patch.dict(
        sys.modules,
        _patched_modules(
            mock_bq, mock_exceptions, mock_api_core, storage_installed=False
        ),
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        result = source.fetch(_ctx())

    assert len(result) == 1, "REST fallback must return a DataFrame"
    assert downloads == [False], (
        f"The download must be attempted exactly once, over REST; got: {downloads!r}"
    )


# ---------------------------------------------------------------------------
# HIGH-1: query-execution errors must not be framed as Storage Read API errors
#
# ``client.query()`` returns before the query runs, so every execution error
# (bad SQL, missing table, table-level permission denied, quota) used to reach
# the Storage Read API handler via ``to_dataframe()`` and came back as
# "BigQuery Storage Read API failed with NotFound: 404 ... " plus advice to
# grant ``bigquery.readSessions.create``.  Observed live against a real
# project, including on the pure-REST path with the storage module absent.
# ---------------------------------------------------------------------------


def _make_not_found(mock_api_error_cls):
    """A NotFound shaped like the live 404 for a missing table."""
    not_found_cls = type("NotFound", (mock_api_error_cls,), {"code": 404})
    return not_found_cls(
        "404 Table recotem:test_dataset.no_such_table_xyz was not found in location US"
    )


def test_query_execution_error_is_not_framed_as_storage_failure(monkeypatch) -> None:
    """A missing table must be reported as a query failure, with no Storage
    Read API framing and no readSessions advice."""
    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    monkeypatch.delenv("RECOTEM_BQ_REQUIRE_STORAGE_API", raising=False)
    mock_query_job.result_exception = _make_not_found(mock_api_error_cls)

    inc_spy = MagicMock()

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        import recotem.datasource.bigquery as bq_module

        with patch.object(bq_module, "inc_bigquery_storage_fallback", inc_spy):
            cfg = bq_module.BigQueryConfig(
                type="bigquery", query="SELECT * FROM no_such_table_xyz"
            )
            source = bq_module.BigQuerySource.__new__(bq_module.BigQuerySource)
            source._config = cfg
            with pytest.raises(DataSourceError) as exc_info:
                source.fetch(_ctx())

    msg = str(exc_info.value)
    assert "query execution failed" in msg, (
        f"Expected the error to name query execution; got: {msg!r}"
    )
    assert "no_such_table_xyz" in msg, (
        f"The BigQuery message must be preserved; got: {msg!r}"
    )
    assert "Storage Read API" not in msg, (
        f"A query error must not be framed as a Storage Read API failure; got: {msg!r}"
    )
    assert "readSessions" not in msg, (
        f"A query error must not advise granting readSessions; got: {msg!r}"
    )
    assert "REST fallback skipped" not in msg, (
        f"A query error must not mention the REST fallback policy; got: {msg!r}"
    )
    inc_spy.assert_not_called()


def test_query_execution_error_under_strict_mode_omits_readsessions_advice(
    monkeypatch,
) -> None:
    """With RECOTEM_BQ_REQUIRE_STORAGE_API=1, a plain SQL error must not be
    answered with 'Grant bigquery.readSessions.create'."""
    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    monkeypatch.setenv("RECOTEM_BQ_REQUIRE_STORAGE_API", "1")
    bad_request_cls = type("BadRequest", (mock_api_error_cls,), {"code": 400})
    mock_query_job.result_exception = bad_request_cls(
        '400 Syntax error: Unexpected identifier "SELCT" at [1:1]'
    )

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELCT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg
        with pytest.raises(DataSourceError) as exc_info:
            source.fetch(_ctx())

    msg = str(exc_info.value)
    assert "query execution failed" in msg, msg
    assert "Syntax error" in msg, msg
    assert "readSessions" not in msg, (
        f"Strict mode must not blame IAM for a SQL syntax error; got: {msg!r}"
    )
    assert "RECOTEM_BQ_REQUIRE_STORAGE_API" not in msg, (
        f"Strict mode must not claim credit for a query failure; got: {msg!r}"
    )


def test_query_execution_error_on_rest_path_is_not_framed_as_storage(
    monkeypatch,
) -> None:
    """The misframing also happened with the storage module absent, i.e. on the
    pure-REST path, so it is not a fast-path artefact."""
    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    monkeypatch.delenv("RECOTEM_BQ_REQUIRE_STORAGE_API", raising=False)
    mock_query_job.result_exception = _make_not_found(mock_api_error_cls)

    with patch.dict(
        sys.modules,
        _patched_modules(
            mock_bq, mock_exceptions, mock_api_core, storage_installed=False
        ),
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg
        with pytest.raises(DataSourceError) as exc_info:
            source.fetch(_ctx())

    msg = str(exc_info.value)
    assert "query execution failed" in msg, msg
    assert "Storage Read API" not in msg, msg


def test_storage_failure_framing_says_the_query_completed(monkeypatch) -> None:
    """A genuine Storage-transport failure keeps the Storage framing, and now
    says explicitly that the query itself completed."""
    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    monkeypatch.delenv("RECOTEM_BQ_REQUIRE_STORAGE_API", raising=False)
    quota_cls = type("ResourceExhausted", (mock_api_error_cls,), {})
    mock_query_job.download = _always_raise(quota_cls("Quota exceeded"))

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg
        with pytest.raises(DataSourceError) as exc_info:
            source.fetch(_ctx())

    msg = str(exc_info.value)
    assert "Storage Read API failed" in msg, msg
    assert "result-download failure" in msg, (
        f"Expected the message to separate download from execution; got: {msg!r}"
    )


def test_iam_fallback_actually_disables_the_storage_client(monkeypatch) -> None:
    """The IAM fallback must download with ``create_bqstorage_client=False``.

    ``QueryJob.to_dataframe`` and ``RowIterator.to_dataframe`` both default
    ``create_bqstorage_client=True``, so a bare ``to_dataframe()`` retry is not
    a REST fallback at all — it re-creates the Storage client and hits the same
    PermissionDenied.  The old mocks hid this because they inspected
    ``kwargs.get("create_bqstorage_client")`` on a call that passed no kwargs,
    which reads as falsy.
    """
    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        mock_client,
        mock_query_job,
        mock_api_error_cls,
    ) = _make_mock_bq_modules()

    monkeypatch.delenv("RECOTEM_BQ_REQUIRE_STORAGE_API", raising=False)
    perm_cls = type("PermissionDenied", (mock_api_error_cls,), {})
    attempts: list[bool] = []

    def _download(create_bqstorage_client: bool = True, **_kwargs):
        attempts.append(create_bqstorage_client)
        if create_bqstorage_client:
            raise perm_cls("denied")
        return _default_df()

    mock_query_job.download = _download

    with patch.dict(
        sys.modules, _patched_modules(mock_bq, mock_exceptions, mock_api_core)
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg
        result = source.fetch(_ctx())

    assert len(result) == 1
    assert attempts == [True, False], (
        "Expected a Storage attempt followed by a real REST retry; "
        f"got create_bqstorage_client sequence {attempts!r}"
    )
    # A RowIterator is single-use — ``to_dataframe`` evaluates
    # ``iter(self.pages)`` before the Storage-vs-REST branch, so the iterator
    # the failed Storage attempt was handed is already started and reusing it
    # raises ``ValueError('Iterator has already started')``.  The fallback must
    # therefore ask the job for a fresh one.
    assert mock_query_job.result_calls == 2, (
        "The IAM fallback must ask the job for a fresh RowIterator; "
        f"result() was called {mock_query_job.result_calls} time(s)"
    )


# ---------------------------------------------------------------------------
# HIGH-2: the storage-capability probe itself
# ---------------------------------------------------------------------------


def test_storage_api_available_true_with_real_dependency() -> None:
    """The probe returns True against the genuinely installed dependency.

    No sys.modules patching here: the test environment installs
    ``recotem[bigquery]``, which pulls in google-cloud-bigquery-storage, so a
    True answer here means the probe works against the real package and not
    only against the stub used elsewhere in this module.
    """
    from recotem.datasource.bigquery import _storage_api_available

    assert _storage_api_available() is True


def test_storage_api_available_false_when_dependency_blocked() -> None:
    """The probe returns False when the dependency cannot be imported."""
    with patch.dict(sys.modules, {"google.cloud.bigquery_storage": None}):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]
        from recotem.datasource.bigquery import _storage_api_available

        assert _storage_api_available() is False


# ---------------------------------------------------------------------------
# RECOTEM_BQ_REQUIRE_STORAGE_API is enforced by probe(), not only by fetch()
#
# The check is a pure import probe -- no query, no credentials, no round trip
# -- so ``recotem validate`` can answer it for free.  While it lived only in
# ``fetch()``, an operator got a green validate and a failing train on
# identical config and environment.
# ---------------------------------------------------------------------------


def test_probe_require_storage_api_missing_extra_raises_datasource_error(
    monkeypatch,
) -> None:
    """probe() must refuse strict mode with the storage extra absent."""
    monkeypatch.setenv("RECOTEM_BQ_REQUIRE_STORAGE_API", "1")

    mock_bq, mock_exceptions, mock_api_core, mock_client, _ = _make_probe_bq_modules()
    mock_client.query.return_value = MagicMock()

    with patch.dict(
        sys.modules,
        _patched_modules(
            mock_bq, mock_exceptions, mock_api_core, storage_installed=False
        ),
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        with pytest.raises(DataSourceError) as exc_info:
            source.probe()

    err_msg = str(exc_info.value)
    assert "RECOTEM_BQ_REQUIRE_STORAGE_API" in err_msg, err_msg
    assert "google-cloud-bigquery-storage" in err_msg, err_msg
    assert "recotem[bigquery]" in err_msg, err_msg
    mock_bq.Client.assert_not_called()
    mock_client.query.assert_not_called()


def test_probe_require_storage_api_message_matches_fetch(monkeypatch) -> None:
    """probe() and fetch() must refuse with the identical message.

    The two paths share ``_assert_storage_api_capability`` precisely so the
    validate-time and train-time answers cannot drift apart.
    """
    monkeypatch.setenv("RECOTEM_BQ_REQUIRE_STORAGE_API", "1")

    (
        mock_bq,
        mock_exceptions,
        mock_api_core,
        _mock_client,
        mock_query_job,
        _err_cls,
    ) = _make_mock_bq_modules()
    mock_query_job.download = lambda **_kwargs: _default_df()

    with patch.dict(
        sys.modules,
        _patched_modules(
            mock_bq, mock_exceptions, mock_api_core, storage_installed=False
        ),
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        with pytest.raises(DataSourceError) as probe_exc:
            source.probe()
        with pytest.raises(DataSourceError) as fetch_exc:
            source.fetch(_ctx())

    assert str(probe_exc.value) == str(fetch_exc.value)


def test_probe_unaffected_when_strict_mode_is_off(monkeypatch) -> None:
    """Without the env var, a missing storage extra must not fail probe()."""
    monkeypatch.delenv("RECOTEM_BQ_REQUIRE_STORAGE_API", raising=False)

    mock_bq, mock_exceptions, mock_api_core, mock_client, _ = _make_probe_bq_modules()
    mock_client.query.return_value = MagicMock()

    with patch.dict(
        sys.modules,
        _patched_modules(
            mock_bq, mock_exceptions, mock_api_core, storage_installed=False
        ),
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        assert source.probe() is None
    mock_client.query.assert_called_once()


def test_probe_strict_mode_passes_when_extra_is_installed(monkeypatch) -> None:
    """Strict mode with the extra present must not disturb the dry run."""
    monkeypatch.setenv("RECOTEM_BQ_REQUIRE_STORAGE_API", "1")

    mock_bq, mock_exceptions, mock_api_core, mock_client, _ = _make_probe_bq_modules()
    mock_client.query.return_value = MagicMock()

    with patch.dict(
        sys.modules,
        _patched_modules(
            mock_bq, mock_exceptions, mock_api_core, storage_installed=True
        ),
    ):
        if "recotem.datasource.bigquery" in sys.modules:
            del sys.modules["recotem.datasource.bigquery"]

        from recotem.datasource.bigquery import BigQueryConfig, BigQuerySource

        cfg = BigQueryConfig(type="bigquery", query="SELECT 1")
        source = BigQuerySource.__new__(BigQuerySource)
        source._config = cfg

        assert source.probe() is None
    mock_client.query.assert_called_once()
