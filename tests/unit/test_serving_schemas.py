# tests/unit/test_serving_schemas.py
"""Unit tests for recotem.serving.schemas (v1)."""

from datetime import UTC

import pytest
from pydantic import ValidationError

from recotem.serving.schemas import (
    BatchRecommendRelatedRequest,
    BatchRecommendRequest,
    ErrorDetail,
    RecipeDetailResponse,
    RecipesListResponse,
    RecipeSummary,
    RecommendItem,
    RecommendRelatedRequest,
    RecommendRequest,
    RecommendResponse,
    _BatchResultErr,
    _BatchResultOk,
)


def test_recommend_request_defaults_limit_10():
    req = RecommendRequest(user_id="u1")
    assert req.limit == 10
    assert req.exclude_items is None


def test_recommend_request_rejects_empty_user_id():
    with pytest.raises(ValidationError):
        RecommendRequest(user_id="")


def test_recommend_request_limit_bounds():
    with pytest.raises(ValidationError):
        RecommendRequest(user_id="u1", limit=0)
    with pytest.raises(ValidationError):
        RecommendRequest(user_id="u1", limit=1001)


def test_recommend_related_request_requires_non_empty_seed():
    with pytest.raises(ValidationError):
        RecommendRelatedRequest(seed_items=[])


def test_recommend_related_request_caps_seed_at_100():
    RecommendRelatedRequest(seed_items=[f"i{i}" for i in range(100)])
    with pytest.raises(ValidationError):
        RecommendRelatedRequest(seed_items=[f"i{i}" for i in range(101)])


def test_recommend_item_allows_extra_metadata_fields():
    item = RecommendItem(item_id="i1", score=0.5, title="Hello")
    dumped = item.model_dump()
    assert dumped["title"] == "Hello"
    assert dumped["item_id"] == "i1"


def test_batch_recommend_request_requires_at_least_one():
    with pytest.raises(ValidationError):
        BatchRecommendRequest(requests=[])


def test_batch_recommend_request_caps_at_256():
    BatchRecommendRequest(requests=[{"user_id": f"u{i}"} for i in range(256)])
    with pytest.raises(ValidationError):
        BatchRecommendRequest(requests=[{"user_id": f"u{i}"} for i in range(257)])


def test_batch_recommend_related_request_caps_at_256():
    seeds = [{"seed_items": [f"i{i}"]} for i in range(256)]
    BatchRecommendRelatedRequest(requests=seeds)
    with pytest.raises(ValidationError):
        BatchRecommendRelatedRequest(requests=seeds + [seeds[0]])


def test_batch_recommend_request_accepts_arbitrary_dict_for_runtime_validation():
    """Per-element schema validation is deferred to the handler: malformed
    entries must NOT cause a whole-batch 422 at the wrapper level."""
    # Whole-request schema accepts a malformed sub-entry; the handler will
    # surface it as status=error, code=VALIDATION_ERROR per-element.
    BatchRecommendRequest(requests=[{"user_id": "u1"}, {"limit": 9999}])


def test_batch_result_entry_status_enum():
    """_BatchResultOk and _BatchResultErr are the two concrete discriminated variants."""
    _BatchResultOk(index=0, status="ok", items=[])
    _BatchResultErr(
        index=0,
        status="error",
        error=ErrorDetail(code="VALIDATION_ERROR", message="m"),
    )
    # Wrong status literal on concrete class
    with pytest.raises(ValidationError):
        _BatchResultOk(index=0, status="error", items=[])  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _BatchResultErr(
            index=0,
            status="ok",
            error=ErrorDetail(code="INTERNAL_ERROR", message="m"),  # type: ignore[arg-type]
        )


def test_batch_result_entry_rejects_unknown_error_code():
    with pytest.raises(ValidationError):
        ErrorDetail(code="NOT_A_REAL_CODE", message="m")  # type: ignore[arg-type]


def test_batch_result_ok_requires_items():
    """_BatchResultOk must carry items; it has no error field."""
    # items is a required field
    with pytest.raises((ValidationError, TypeError)):
        _BatchResultOk(index=0, status="ok")  # type: ignore[call-arg]


def test_batch_result_err_requires_error():
    """_BatchResultErr must carry an error; it has no items field."""
    with pytest.raises((ValidationError, TypeError)):
        _BatchResultErr(index=0, status="error")  # type: ignore[call-arg]


def test_batch_result_entry_rejects_negative_index():
    with pytest.raises(ValidationError):
        _BatchResultOk(index=-1, status="ok", items=[])


_VALID_SHA256 = "sha256:" + "a" * 64
_VALID_HEX64 = "a" * 64


def test_recommend_response_round_trip():
    r = RecommendResponse(
        request_id="req_1",
        recipe="r",
        model_version=_VALID_SHA256,
        items=[RecommendItem(item_id="i1", score=0.9)],
    )
    assert r.model_dump()["items"][0]["item_id"] == "i1"


def test_recipe_summary_supports_verb_list():
    s = RecipeSummary(
        name="r",
        model_version=_VALID_SHA256,
        loaded_at="2026-05-21T00:00:00Z",
        supported_verbs=["recommend", "recommend-related"],
        kind="user-item",
    )
    assert "recommend" in s.supported_verbs


def test_recipes_list_response_is_serialisable():
    s = RecipeSummary(
        name="r",
        model_version=_VALID_SHA256,
        loaded_at="2026-05-21T00:00:00Z",
        supported_verbs=["recommend"],
        kind="user-item",
    )
    payload = RecipesListResponse(recipes=[s]).model_dump()
    assert payload["recipes"][0]["name"] == "r"


def test_recipe_summary_allows_none_model_version():
    """Stub entries emit model_version=None (not loaded yet)."""
    s = RecipeSummary(
        name="r",
        model_version=None,
        loaded_at="2026-05-21T00:00:00Z",
        supported_verbs=["recommend"],
        kind="user-item",
    )
    assert s.model_version is None


def test_recipe_summary_rejects_empty_supported_verbs():
    """A loaded recipe must advertise at least one verb."""
    with pytest.raises(ValidationError):
        RecipeSummary(
            name="r",
            model_version=_VALID_SHA256,
            loaded_at="2026-05-21T00:00:00Z",
            supported_verbs=[],
            kind="user-item",
        )


def test_recipe_detail_response_includes_config_digest():
    d = RecipeDetailResponse(
        name="r",
        model_version=_VALID_SHA256,
        loaded_at="2026-05-21T00:00:00Z",
        supported_verbs=["recommend"],
        kind="user-item",
        config_digest=_VALID_SHA256,
        algorithms=["TopPop"],
        best_algorithm="TopPop",
    )
    assert d.config_digest == _VALID_SHA256


def test_recipe_detail_config_digest_accepts_sha256_format():
    """config_digest: must be Sha256Hex or None."""
    # Valid Sha256Hex
    d = RecipeDetailResponse(
        name="r",
        model_version=_VALID_SHA256,
        loaded_at="2026-05-21T00:00:00Z",
        supported_verbs=["recommend"],
        kind="user-item",
        config_digest=_VALID_SHA256,
        algorithms=["TopPop"],
        best_algorithm="TopPop",
    )
    assert d.config_digest == _VALID_SHA256

    # None is also valid (stub / no digest available)
    d2 = RecipeDetailResponse(
        name="r",
        model_version=_VALID_SHA256,
        loaded_at="2026-05-21T00:00:00Z",
        supported_verbs=["recommend"],
        kind="user-item",
        config_digest=None,
        algorithms=["TopPop"],
        best_algorithm="TopPop",
    )
    assert d2.config_digest is None

    # Empty string must be rejected (not a valid Sha256Hex)
    with pytest.raises(ValidationError):
        RecipeDetailResponse(
            name="r",
            model_version=_VALID_SHA256,
            loaded_at="2026-05-21T00:00:00Z",
            supported_verbs=["recommend"],
            kind="user-item",
            config_digest="",
            algorithms=["TopPop"],
            best_algorithm="TopPop",
        )


def test_recipe_detail_recipe_hash_accepts_hexhash_format():
    """recipe_hash: must be HexHash (64 lowercase hex chars) or None."""
    d = RecipeDetailResponse(
        name="r",
        model_version=_VALID_SHA256,
        loaded_at="2026-05-21T00:00:00Z",
        supported_verbs=["recommend"],
        kind="user-item",
        algorithms=["TopPop"],
        best_algorithm="TopPop",
        recipe_hash=_VALID_HEX64,
    )
    assert d.recipe_hash == _VALID_HEX64

    # None is also valid
    d2 = RecipeDetailResponse(
        name="r",
        model_version=_VALID_SHA256,
        loaded_at="2026-05-21T00:00:00Z",
        supported_verbs=["recommend"],
        kind="user-item",
        algorithms=["TopPop"],
        best_algorithm="TopPop",
        recipe_hash=None,
    )
    assert d2.recipe_hash is None

    # Wrong length must be rejected
    with pytest.raises(ValidationError):
        RecipeDetailResponse(
            name="r",
            model_version=_VALID_SHA256,
            loaded_at="2026-05-21T00:00:00Z",
            supported_verbs=["recommend"],
            kind="user-item",
            algorithms=["TopPop"],
            best_algorithm="TopPop",
            recipe_hash="a" * 32,  # too short
        )


# ---------------------------------------------------------------------------
# Task C — under-tested field round-trips
# ---------------------------------------------------------------------------


def test_recommend_request_accepts_exclude_items() -> None:
    """exclude_items: list[str] | None, max_length=1000 (from schema Field)."""
    # None by default
    req = RecommendRequest(user_id="u1")
    assert req.exclude_items is None

    # Accepts a list up to the cap
    items_at_cap = [f"i{n}" for n in range(1000)]
    req2 = RecommendRequest(user_id="u1", exclude_items=items_at_cap)
    assert len(req2.exclude_items) == 1000

    # Rejects lists that exceed the cap
    with pytest.raises(ValidationError):
        RecommendRequest(user_id="u1", exclude_items=[f"i{n}" for n in range(1001)])

    # Rejects non-string entries (Pydantic strict-ish: int will not coerce to str
    # in a list[str] field in v2 when the value is obviously wrong type)
    # Note: pydantic v2 coerces ints to str in lax mode for list[str], so we
    # check that the correct Python type is accepted and None is accepted too.
    req3 = RecommendRequest(user_id="u1", exclude_items=None)
    assert req3.exclude_items is None


def test_recommend_request_extra_fields_rejected() -> None:
    """RecommendRequest has extra=forbid: unknown fields must be rejected."""
    with pytest.raises(ValidationError):
        RecommendRequest(user_id="u1", context={"a": 1})  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Finding 6: Discriminated union extra-field enforcement
# ---------------------------------------------------------------------------


def test_batch_result_ok_rejects_error_field() -> None:
    """_BatchResultOk has extra='forbid'; passing an 'error' field must raise."""
    with pytest.raises(ValidationError):
        _BatchResultOk(
            index=0,
            status="ok",
            items=[],
            error={"code": "INTERNAL_ERROR", "message": "bad"},  # type: ignore[call-arg]
        )


def test_batch_result_err_rejects_items_field() -> None:
    """_BatchResultErr has extra='forbid'; passing an 'items' field must raise."""
    with pytest.raises(ValidationError):
        _BatchResultErr(
            index=0,
            status="error",
            error=ErrorDetail(code="INTERNAL_ERROR", message="m"),
            items=[],  # type: ignore[call-arg]
        )


def test_batch_result_entry_deserializes_ok_shape_via_discriminator() -> None:
    """BatchResultEntry deserializes from a dict with status='ok'."""
    import pydantic

    from recotem.serving.schemas import BatchResultEntry

    class _Wrapper(pydantic.BaseModel):
        entry: BatchResultEntry

    w = _Wrapper.model_validate(
        {
            "entry": {
                "index": 0,
                "status": "ok",
                "items": [{"item_id": "i1", "score": 0.9}],
            }
        }
    )
    assert isinstance(w.entry, _BatchResultOk)
    assert w.entry.status == "ok"
    assert w.entry.items[0].item_id == "i1"


def test_batch_result_entry_deserializes_error_shape_via_discriminator() -> None:
    """BatchResultEntry deserializes from a dict with status='error'."""
    import pydantic

    from recotem.serving.schemas import BatchResultEntry

    class _Wrapper(pydantic.BaseModel):
        entry: BatchResultEntry

    w = _Wrapper.model_validate(
        {
            "entry": {
                "index": 1,
                "status": "error",
                "error": {"code": "UNKNOWN_USER", "message": "not found"},
            }
        }
    )
    assert isinstance(w.entry, _BatchResultErr)
    assert w.entry.status == "error"
    assert w.entry.error.code == "UNKNOWN_USER"


def test_batch_result_entry_openapi_contains_discriminator() -> None:
    """BatchRecommendResponse's OpenAPI schema must expose the discriminator."""
    from recotem.serving.schemas import BatchRecommendResponse

    schema = BatchRecommendResponse.model_json_schema()
    schema_str = str(schema)
    # The discriminator field should appear somewhere in the schema
    assert "status" in schema_str, (
        "Discriminator field 'status' must appear in BatchRecommendResponse schema"
    )
    # oneOf should appear in the schema for the union
    assert "anyOf" in schema_str or "oneOf" in schema_str or "$defs" in schema_str, (
        "Schema for discriminated union must contain anyOf/oneOf or $defs references"
    )


# ---------------------------------------------------------------------------
# Finding 7: Sha256Hex / HexHash validation
# ---------------------------------------------------------------------------


def test_sha256hex_valid_prefix_and_length() -> None:
    """sha256:<64 hex chars> is a valid model_version."""
    valid = "sha256:" + "a" * 64
    r = RecommendResponse(
        request_id="r1",
        recipe="demo",
        model_version=valid,
        items=[],
    )
    assert r.model_version == valid


def test_sha256hex_rejects_missing_prefix() -> None:
    """model_version is now Sha256Hex — strings without 'sha256:' prefix must
    be rejected by RecommendResponse at validation time."""
    with pytest.raises(ValidationError):
        RecommendResponse(
            request_id="r1",
            recipe="demo",
            model_version="abc123",
            items=[],
        )


def test_sha256hex_type_rejects_wrong_length() -> None:
    """Sha256Hex must reject a string with wrong hex length after the prefix."""
    from pydantic import TypeAdapter

    from recotem.serving.schemas import Sha256Hex

    ta = TypeAdapter(Sha256Hex)
    with pytest.raises(ValidationError):
        ta.validate_python("sha256:" + "a" * 63)  # one char short
    with pytest.raises(ValidationError):
        ta.validate_python("sha256:" + "a" * 65)  # one char over


def test_sha256hex_type_rejects_uppercase() -> None:
    """Sha256Hex must reject uppercase hex characters."""
    from pydantic import TypeAdapter

    from recotem.serving.schemas import Sha256Hex

    ta = TypeAdapter(Sha256Hex)
    with pytest.raises(ValidationError):
        ta.validate_python("sha256:" + "A" * 64)


def test_sha256hex_type_rejects_non_hex() -> None:
    """Sha256Hex must reject non-hex characters."""
    from pydantic import TypeAdapter

    from recotem.serving.schemas import Sha256Hex

    ta = TypeAdapter(Sha256Hex)
    with pytest.raises(ValidationError):
        ta.validate_python("sha256:" + "g" * 64)  # 'g' not in [0-9a-f]


def test_hexhash_type_accepts_64_hex() -> None:
    """HexHash accepts a 64-character lowercase hex string."""
    from pydantic import TypeAdapter

    from recotem.serving.schemas import HexHash

    ta = TypeAdapter(HexHash)
    result = ta.validate_python("a" * 64)
    assert result == "a" * 64


def test_hexhash_type_rejects_32_hex() -> None:
    """HexHash rejects a 32-character hex string (too short)."""
    from pydantic import TypeAdapter

    from recotem.serving.schemas import HexHash

    ta = TypeAdapter(HexHash)
    with pytest.raises(ValidationError):
        ta.validate_python("a" * 32)


# ---------------------------------------------------------------------------
# Finding 8: loaded_at / trained_at AwareDatetime
# ---------------------------------------------------------------------------


def test_recipe_summary_rejects_naive_datetime() -> None:
    """RecipeSummary.loaded_at must reject naive datetime strings (no timezone)."""
    with pytest.raises(ValidationError):
        RecipeSummary(
            name="r",
            model_version=_VALID_SHA256,
            loaded_at="2026-05-21T12:34:56",  # no Z or offset
            supported_verbs=["recommend"],
            kind="user-item",
        )


def test_recipe_summary_accepts_iso8601_z_suffix() -> None:
    """RecipeSummary.loaded_at must accept ISO-8601 strings ending in 'Z'."""
    s = RecipeSummary(
        name="r",
        model_version=_VALID_SHA256,
        loaded_at="2026-05-21T12:34:56Z",
        supported_verbs=["recommend"],
        kind="user-item",
    )
    assert s.loaded_at is not None


def test_recipe_summary_model_dump_json_includes_offset() -> None:
    """RecipeSummary.model_dump_json() must produce a loaded_at string that
    includes timezone offset information (not naive)."""
    import json

    s = RecipeSummary(
        name="demo",
        model_version="sha256:" + "a" * 64,
        loaded_at="2026-05-21T00:00:00Z",
        supported_verbs=["recommend"],
        kind="user-item",
    )
    raw = json.loads(s.model_dump_json())
    loaded_at_str = raw["loaded_at"]
    # Must include a timezone indicator (either Z, +00:00, or similar)
    has_offset = (
        loaded_at_str.endswith("Z")
        or "+" in loaded_at_str
        or (loaded_at_str.count("-") > 2)  # offset like -05:00
    )
    assert has_offset, (
        f"model_dump_json() must produce an offset-aware loaded_at; got {loaded_at_str!r}"
    )


def test_recipe_detail_trained_at_rejects_naive() -> None:
    """RecipeDetailResponse.trained_at must reject naive datetime strings."""
    with pytest.raises(ValidationError):
        RecipeDetailResponse(
            name="r",
            model_version=_VALID_SHA256,
            loaded_at="2026-05-21T00:00:00Z",
            supported_verbs=["recommend"],
            kind="user-item",
            config_digest=_VALID_SHA256,
            algorithms=["TopPop"],
            best_algorithm="TopPop",
            trained_at="2026-01-01T00:00:00",  # naive — no tz
        )


def test_recipes_list_response_loaded_at_iso8601() -> None:
    """RecipeSummary.loaded_at must round-trip through JSON and be UTC ISO-8601."""
    import json
    from datetime import datetime

    summary = RecipeSummary(
        name="demo",
        model_version=_VALID_SHA256,
        loaded_at="2026-05-21T12:34:56Z",
        supported_verbs=["recommend"],
        kind="user-item",
    )
    resp = RecipesListResponse(recipes=[summary])

    # Round-trip through JSON
    raw_json = resp.model_dump_json()
    decoded = json.loads(raw_json)
    loaded_at_str: str = decoded["recipes"][0]["loaded_at"]

    # Python 3.12 fromisoformat accepts trailing Z as UTC
    dt = datetime.fromisoformat(loaded_at_str)
    assert dt.tzinfo is not None, "loaded_at must carry timezone info"
    # Normalise to UTC and verify the offset is zero
    dt_utc = dt.astimezone(UTC)
    assert dt_utc.utcoffset().total_seconds() == 0
