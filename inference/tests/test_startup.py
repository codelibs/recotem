"""Tests for inference service startup behaviour."""

import logging
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

DEFAULT_KEY = "VeryBadSecret@ChangeThis"
CUSTOM_KEY = "a-real-secret-key-that-is-long-enough-123456"


@pytest.fixture(autouse=True)
def _mock_irspack():
    """Stub irspack so inference.main can be imported without C extensions."""
    stub = ModuleType("irspack")
    stub.utils = ModuleType("irspack.utils")  # type: ignore[attr-defined]
    stub.utils.id_mapping = ModuleType("irspack.utils.id_mapping")  # type: ignore[attr-defined]
    stub.utils.id_mapping.IDMapper = MagicMock()  # type: ignore[attr-defined]

    stubs = {
        "irspack": stub,
        "irspack.utils": stub.utils,
        "irspack.utils.id_mapping": stub.utils.id_mapping,
        "irspack.evaluation": ModuleType("irspack.evaluation"),
        "irspack.evaluation._core_evaluator": ModuleType(
            "irspack.evaluation._core_evaluator"
        ),
    }
    saved = {}
    for name, mod in stubs.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mod

    # Force reimport of inference.main so it picks up the stubs
    for mod_name in list(sys.modules):
        if mod_name.startswith("inference."):
            del sys.modules[mod_name]

    yield

    # Restore
    for mod_name in list(sys.modules):
        if mod_name.startswith("inference."):
            del sys.modules[mod_name]
    for name, original in saved.items():
        if original is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original


@pytest.mark.asyncio
async def test_default_secret_key_logs_warning(caplog):
    """Starting with the default SECRET_KEY must emit a warning."""
    with patch("inference.main.settings") as mock_settings:
        mock_settings.secret_key = DEFAULT_KEY
        mock_settings.inference_preload_model_ids = ""
        mock_settings.inference_port = 8081
        with patch(
            "inference.main.start_listener",
            new_callable=MagicMock,
        ):
            from fastapi import FastAPI

            from inference.main import lifespan

            app = FastAPI()
            with caplog.at_level(logging.WARNING, logger="inference"):
                async with lifespan(app):
                    pass
    assert any(
        "SECRET_KEY" in r.message or "insecure" in r.message.lower()
        for r in caplog.records
        if r.levelno >= logging.WARNING
    )


@pytest.mark.asyncio
async def test_custom_secret_key_no_warning(caplog):
    """Custom SECRET_KEY must NOT emit the insecure-key warning."""
    with patch("inference.main.settings") as mock_settings:
        mock_settings.secret_key = CUSTOM_KEY
        mock_settings.inference_preload_model_ids = ""
        mock_settings.inference_port = 8081
        with patch(
            "inference.main.start_listener",
            new_callable=MagicMock,
        ):
            from fastapi import FastAPI

            from inference.main import lifespan

            app = FastAPI()
            with caplog.at_level(logging.WARNING, logger="inference"):
                async with lifespan(app):
                    pass
    warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert not any("SECRET_KEY" in m or "insecure" in m.lower() for m in warnings)


@pytest.mark.asyncio
async def test_warning_is_at_warning_level(caplog):
    """Default key warning must be at WARNING level (not ERROR)."""
    with patch("inference.main.settings") as mock_settings:
        mock_settings.secret_key = DEFAULT_KEY
        mock_settings.inference_preload_model_ids = ""
        mock_settings.inference_port = 8081
        with patch(
            "inference.main.start_listener",
            new_callable=MagicMock,
        ):
            from fastapi import FastAPI

            from inference.main import lifespan

            app = FastAPI()
            with caplog.at_level(logging.DEBUG):
                async with lifespan(app):
                    pass
    secret_records = [
        r
        for r in caplog.records
        if "SECRET_KEY" in r.message or "insecure" in r.message.lower()
    ]
    assert len(secret_records) >= 1
    assert secret_records[0].levelno == logging.WARNING


@pytest.mark.asyncio
async def test_empty_secret_key_does_not_trigger_warning(caplog):
    """Empty SECRET_KEY is not the default — no insecure warning."""
    with patch("inference.main.settings") as mock_settings:
        mock_settings.secret_key = ""
        mock_settings.inference_preload_model_ids = ""
        mock_settings.inference_port = 8081
        with patch(
            "inference.main.start_listener",
            new_callable=MagicMock,
        ):
            from fastapi import FastAPI

            from inference.main import lifespan

            app = FastAPI()
            with caplog.at_level(logging.WARNING, logger="inference"):
                async with lifespan(app):
                    pass
    warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert not any("insecure" in m.lower() for m in warnings)


@pytest.mark.asyncio
async def test_start_listener_is_called():
    """start_listener must be called during lifespan startup."""
    with patch("inference.main.settings") as mock_settings:
        mock_settings.secret_key = CUSTOM_KEY
        mock_settings.inference_preload_model_ids = ""
        mock_settings.inference_port = 8081
        with patch("inference.main.start_listener") as mock_listener:
            from fastapi import FastAPI

            from inference.main import lifespan

            app = FastAPI()
            async with lifespan(app):
                pass
    mock_listener.assert_called_once()
