"""Neutral home for side-feature encoding.

Why this module exists
-----------------------
``recotem.training`` and ``recotem.serving`` must never import each other
(CLAUDE.md architecture constraint), but both need to turn a table of raw
attribute values into the numeric matrix irspack's feature-aware iALS expects.
Training builds the encoder state and encodes the training matrices; serving
re-encodes a single request's features with the same state to reach the
cold-start API. Defining the functions here (under ``recotem.*`` -- no
sub-package), both training and serving can import them without violating
the boundary, the same reasoning as ``recotem._idmap``.

Why the state is plain data
----------------------------
The state is persisted inside the artifact payload alongside the trained
recommender. It contains only plain Python containers -- dict, list, str,
int, and float, and nothing else: deliberately no numpy, no classes, no
sklearn estimators, and no pandas objects. The numpy arrays live in
``encode`` / ``encode_one``, which build them per call from this state; they
are never persisted.
Keeping the state plain means the artifact FQCN allow-list does not need to
grow to support this feature.

The artifact allow-list is only a PARTIAL backstop for that invariant, and
it is worth being precise about where it stops. A stray ``pd.Index`` really
would be refused at load time (``pandas.core.indexes.base._new_Index`` is
not allow-listed -- verified). But a ``numpy.str_`` would NOT: it pickles
via ``numpy._core.multiarray.scalar`` + ``numpy.dtype``, both explicitly
allow-listed, so it round-trips through ``SafeUnpickler`` keeping its type
(verified). Nothing downstream catches it either -- ``numpy.str_`` hashes
and compares equal to ``str``, so every vocabulary lookup keeps working and
the leak stays invisible at runtime.

So the plainness coercions in ``build_encoder_state`` are load-bearing on
their own for the numpy scalar types, not a belt-and-braces gesture on top
of an allow-list that would fail closed anyway. There are two, one per scalar
kind, and neither is cosmetic:

- the ``str()`` coercions on vocabulary keys (categorical / multi_label):
  total -- every key is constructed through ``str()`` -- and enforced by
  ``tests/unit/test_features.py::test_vocabulary_keys_are_exactly_str_not_
  numpy_str``, which asserts the EXACT key type (an ``isinstance`` check
  cannot, because ``numpy.str_`` subclasses ``str``).
- the ``float()`` coercions on the numerical branch's ``mean`` / ``std``:
  ``numeric.mean()`` / ``numeric.std()`` return ``np.float64`` scalars, which
  leak into the state and unpickle via the same allow-listed
  ``numpy._core.multiarray.scalar`` if the wrapping ``float(...)`` is dropped.
  Do NOT refactor ``float(numeric.mean())`` back to ``numeric.mean()``: it
  silently reintroduces a numpy scalar the allow-list will happily load.

Why ``encode`` demands ``index_order``
----------------------------------------
irspack raises on a feature-matrix row-count mismatch but accepts a
*misordered* matrix silently, and recotem's search phase and final refit do
not share one canonical row ordering (see below). Requiring the caller to
name the row order, and always reindexing onto it, makes *omission* of the
order unrepresentable: there is no convenience overload that infers it and
no "build once, reuse" caching, so a caller cannot forget to think about it.

That is the honest claim, and it is deliberately narrower than
"misalignment is unrepresentable" -- which would be false. Passing a
wrong-but-same-length permutation of the right ids still returns a
same-shaped, differently-populated matrix, silently. What the signature buys
is that reaching that state requires an explicit wrong argument at a
greppable call site, rather than an omission that reads as correct code.

Why the row order differs per phase
-------------------------------------
The final refit orders items by ``pd.Categorical`` (sorted). The search
phase does not match it, but the reason is per-scheme: ``random`` and
``time_user`` order items by ``list(set(...))``, which is neither sorted nor
stable across processes for string ids, while ``time_global`` routes to
irspack's ``holdout_specific_interactions``, whose item vocabulary is
``np.unique`` -- sorted and stable.

The invariant therefore rests on the USER axis, where it holds universally:
``split.py`` builds ``row_user_ids`` as ``train.user_ids + val.user_ids``
(a concatenation performed after the per-scheme branch, so it applies to
every scheme), and that train-then-val order is pinned to irspack's
Evaluator and never globally sorted. Per-phase re-encoding is mandatory
regardless of scheme.

Why the bias column is collinear with the categorical one-hots
-----------------------------------------------------------------
Each categorical column's one-hot block sums to 1 for every row (when the
value is known), which is linearly dependent on the always-1 bias column.
This is accepted, not an oversight: drop-first encoding would make an
unknown/missing value (all-zero block) indistinguishable from the dropped
reference level. The rank deficiency this creates is exact, not approximate:
``F^T F`` is singular by construction, and irspack's ``lambda_*_feature`` --
added straight to the Gram diagonal and factorised in float32 -- is the sole
eigenvalue along that null direction. That is why the tuned search range
floors at ``lambda >= 1.0`` rather than going lower; see the range comment in
``recotem.training.search``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import scipy.sparse as sps
import structlog

from recotem.config import get_max_feature_dim
from recotem.recipe.models import FeatureColumn

_logger = structlog.get_logger(__name__)

# Bump when the state dict's shape changes in a way older readers would
# mis-encode. Serving refuses an artifact whose version it does not know
# (see Task 10's check_artifact_feature_version).
FEATURE_STATE_VERSION: int = 1

# Stable message prefix -- serving/watcher.py's `_classify_artifact_error` keys
# the Prometheus `reason` label off it, the same pattern `_irspack_compat.py`
# uses for `SKEW_MSG_PREFIX`. Keep the two in sync: if this string changes,
# update the classifier too, or the failure silently relabels to "parse"
# (the message contains the word "version").
FEATURE_VERSION_MSG_PREFIX = "feature version check failed:"

# Same contract as FEATURE_VERSION_MSG_PREFIX above, for the header/payload
# reconciliation gate (`check_artifact_feature_state`). It needs its own prefix
# -- and its own `reason` label -- because it reports a different condition: not
# "this build cannot read that shape" (retrain / upgrade serving) but "this
# artifact contradicts itself" (a tampered or mis-built artifact).
FEATURE_STATE_MSG_PREFIX = "feature state check failed:"

# The complete key set of the header's `features` descriptor. Validated as an
# exact allow-list so an unrecognised key is refused rather than silently
# ignored: a reader that ignores what it does not understand cannot tell a
# future descriptor field from a fabricated one.
_FEATURE_HEADER_KEYS: frozenset[str] = frozenset({"version", "active", "item", "user"})

# The complete key set of one side's descriptor, as `state_descriptor` writes it.
_FEATURE_SIDE_KEYS: frozenset[str] = frozenset({"n_features", "columns"})

# Bounds on the column names quoted back in a reconciliation refusal. The
# message becomes `ModelEntry.last_load_error`, which `/v1/health` echoes, and
# on the refusing side it comes from a header this build has decided not to
# trust -- so neither the count nor each name's length may be taken on faith.
# Same reasoning as `training/features.py`'s `_ID_SAMPLE_SIZE`; the values are
# column NAMES, which `feature_table_loaded` already logs, so this is a size
# bound rather than a disclosure bound.
_COLUMN_SAMPLE_SIZE = 8
_COLUMN_SAMPLE_MAX_CHARS = 64

# A numerical column need not be EXACTLY constant (std == 0.0) to behave like
# one. Floating-point rounding noise -- e.g. values that are "the same
# number" up to a few ULPs, giving std ~= 1e-15 -- survives an exact
# std == 0.0 check but still divides serve-time standardization
# ((raw - mean) / std, see `_row_values`) by a near-zero denominator, turning
# an ordinary raw request value (e.g. 1e4) into an astronomically large
# standardized one. That is a false-positive amplifier, not a real signal:
# it makes an unremarkable client value trip the serve-time cold-start
# solver's own numerical-stability guard (see docs/api-reference.md#feature-
# aware-cold-start) for a reason the client cannot see or control.
#
# The floor is RELATIVE to the column's own scale (`max(abs(mean), 1.0)`),
# not an absolute constant, so it means the same thing whether the column's
# values sit near 0 or near 1e9. float64 has ~15-17 significant decimal
# digits (relative machine epsilon ~2.22e-16); 1e-8 is ~8 orders of
# magnitude looser than that -- generous enough to absorb realistic
# floating-point rounding noise accumulated across parsing/aggregation,
# while still many orders of magnitude tighter than any spread an operator
# would call real, intentional variance.
_NUMERICAL_STD_RELATIVE_FLOOR = 1e-8

# Share of a column's rows that must be left with an all-zero encoded block by
# `min_frequency` pruning before `_warn_if_vocabulary_pruned` fires. Pruning a
# long tail that covers a couple of percent of the catalog is the intended use
# of `min_frequency`; a warning there would be noise an operator learns to
# ignore. One row in five losing its signal is not a tail -- it is the column.
_PRUNED_ROWS_WARN_RATIO = 0.2

# Share of a `numerical` column's rows that must be present-but-unparseable
# before `_warn_if_numerical_unparseable` fires. A handful of bad cells in a
# large catalog is ordinary data noise the mean absorbs; one row in five is a
# malformed export, and the fitted mean/std then describe a different column
# from the one the operator thinks they supplied.
_UNPARSEABLE_ROWS_WARN_RATIO = 0.2

# The encoded matrices are float32 (`encode` / `encode_one` both build with
# `dtype=np.float32`). A standardized value finite as float64 whose magnitude
# exceeds float32's max becomes +-inf on the cast, so it must be caught on the
# float64 side and routed to `unknown` -- see `_row_values`'s numerical branch.
_FLOAT32_MAX = float(np.finfo(np.float32).max)


class FeatureEncodeError(Exception):
    """Raised for any structural problem building or applying an encoder state."""


def _is_missing(raw: Any) -> bool:
    """True if *raw* is a "no value supplied" sentinel.

    A DataFrame row and a hand-built request dict represent "no value"
    differently: plain dicts use ``None``, pandas stores missing values as
    float ``NaN`` even in object-dtype columns (confirmed: constructing a
    DataFrame from a Python list containing ``None`` normalizes it to
    ``nan``), and nullable extension dtypes use the ``pandas.NA`` singleton.
    Treating all three identically is what keeps ``encode`` and
    ``encode_one`` in agreement for a missing value.
    """
    if raw is None:
        return True
    if isinstance(raw, float) and np.isnan(raw):
        return True
    return raw is pd.NA


def _vocabulary(values: Sequence[str], min_frequency: int) -> dict[str, int]:
    """Build the kept-token -> index vocabulary, pruned by ``min_frequency``.

    ``min_frequency`` counts occurrences in *values*, not rows of the source
    table -- what a "row" means depends on the caller. ``categorical``
    passes one value per row, so the count is a row count. ``multi_label``
    flattens every row's tokens into *values* first (see the ``multi_label``
    branch of ``build_encoder_state``), so the count is a token
    **occurrence** count: a single row with ``tags="a|a"`` contributes 2
    toward ``a``'s threshold, not 1.
    """
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    kept = sorted(t for t, n in counts.items() if n >= min_frequency)
    return {t: i for i, t in enumerate(kept)}


# The scalar (non-text) types `pd.to_numeric(..., errors="coerce")` fits a
# numeric value FROM. `_parse_number` allows `float()` ONLY for these, so it
# can never turn a value the fitting parser dropped into a finite number.
#
# It is a hand-enumerated allow-list, not `numbers.Number`, because that ABC is
# wrong in BOTH directions (verified): it MISSES `np.bool_` (not registered)
# and ADMITS `Fraction` (which `to_numeric` NaNs). `bool` needs no entry -- it
# is an `int` subclass -- but `np.bool_` is neither an `int` nor an `np.number`
# subclass, so it is listed explicitly. `complex` is omitted deliberately:
# `to_numeric` keeps a complex column, but `build_encoder_state` rejects it
# explicitly with `FeatureEncodeError` (via `pd.api.types.is_complex_dtype`)
# BEFORE any `float()` coercion runs -- because under numpy 2.x
# `float(np.complex128)` does NOT raise `TypeError`, it silently discards the
# imaginary part with a `ComplexWarning`. So no state is ever built for a
# complex column and the encode path is unreachable -- there is nothing to
# mirror.
_FIT_NUMERIC_TYPES: tuple[type, ...] = (int, float, Decimal, np.number, np.bool_)


# `build_encoder_state` fits mean/std with `pd.to_numeric(..., errors="coerce")`
# while `_row_values` parses a request/row value per-scalar. The two must accept
# exactly the same values, or a value pandas silently dropped from a column's
# own statistics could still be encoded against those statistics: a table of
# ["1_000", "5", "7"] fits mean=6.0/std=1.0 from {5, 7} ALONE, then encodes the
# "1_000" row to 994.0 -- a 994-sigma value the fitted statistics never saw.
#
# The bug reproduces across the WHOLE non-`str` domain, not just `str`. `float()`
# also parses `bytes`/`bytearray`/`memoryview` and any object with
# `__float__`/`__index__`, while `to_numeric` NaNs all of those EXCEPT `bytes`
# (which it treats as text, applying the same grammar it applies to `str`). So a
# BYTES column (a SQL BLOB / parquet binary column declared `numerical`) of
# [b"1_000", b"5", b"7"] reproduces the 994-sigma bug verbatim -- and JSON
# cannot carry any of these types, so this hole is reachable at TRAINING time,
# through the same shared `_row_values`, invisibly (the statistics look healthy).
#
# The gate therefore has two shapes, one per part of `to_numeric`'s domain:
#   * TEXT (`str`, `bytes`): `to_numeric` parses both as text with an ASCII-only,
#     underscore-free grammar. `float()` is LOOSER on text -- it honours PEP 515
#     underscores ("1_000") and non-ASCII digits (full-width "１２３",
#     Arabic-Indic "١٢٣", both of which occur in Japanese CSV exports) -- so
#     text outside that grammar is refused.
#   * NON-TEXT: allowed only if it is one of `_FIT_NUMERIC_TYPES`. This is what
#     rejects bytearray / memoryview / Fraction / `__float__`-objects that
#     `float()` would otherwise turn into a finite number the fit never saw.
#
# The invariant restored, across both shapes: a value the FITTING parser could
# not use must not be encodable by the ENCODING parser. It is restored by
# tightening the encoding side only -- loosening the fit would newly admit
# "1_000" into the statistics and silently change every model trained on such a
# table. And the mirror runs BOTH ways: refusing a value the fit DID use (e.g.
# a `bool` / `np.bool_` / `Decimal`) is the same class of bug in the opposite
# direction -- because `_row_values` is shared, it zeroes every training row of
# that column against healthy statistics, with no warning able to fire. That is
# exactly the reverted bool regression; `_FIT_NUMERIC_TYPES` includes `int`
# (covers `bool`) and `np.bool_` precisely so it cannot come back.
#
# Why not just call `pd.to_numeric` on the scalar, which would give parity by
# construction:
#   1. It does not survive its own edge case. `pd.to_numeric(pd.Series([10**309],
#      dtype=object), errors="coerce")` raises OverflowError *despite*
#      errors="coerce" (verified) -- so routing a request value through it
#      would re-introduce the very HTTP 500 this function exists to close.
#   2. It costs ~3.3us per scalar vs ~0.045us for `float()` (measured, ~70x),
#      on a path that runs once per served request AND once per catalog row
#      per training phase.
#
# Parity is verified by the committed differential fuzz in
# tests/unit/test_features.py (`test_parse_number_mirrors_the_fitting_parser_
# for_non_text` and `..._never_encodes_text_the_fitting_parser_dropped`), which
# -- unlike the earlier str-only fuzz that structurally could not see the bytes
# hole -- exercises non-`str` types too. The residual divergences are benign or
# in the safe direction: pandas accepts an internal space in an exponent
# ("6E 66") that `float()` rejects (encode is stricter, so the value joins the
# documented unparseable gap), and the two disagree by ~1 ULP on some
# exponent-heavy literals ("22e224"), which standardization renders immaterial.
def _parse_number(raw: Any) -> float | None:
    """Parse *raw* the way ``build_encoder_state``'s fitting parser would.

    Returns the parsed float, or ``None`` if this encoder may not use the
    value. ``None`` and ``±inf`` are different answers and the caller treats
    them differently: ``None`` is the deliberately uncounted "unparseable"
    gap, while a non-finite result is routed to ``unknown`` (a counted
    signal). A magnitude too large for float64 therefore comes back as
    ``±inf`` rather than ``None``.
    """
    # A JSON `true` encoding identically to the number 1.0 is the correct
    # reading, not a collision to close: for a column the recipe declares
    # `numerical`, `true` IS 1.0, which is also what pandas makes of it (a
    # bool-dtype column fits its mean/std FROM the bools -- verified:
    # [True, False, True, False] fits mean=0.5, std=0.5). `bool` reaches
    # `float()` below via the `int` entry in `_FIT_NUMERIC_TYPES`. Note the
    # contrast with `check_artifact_feature_version`, which DOES exclude bool
    # from its `isinstance(version, int)` check: a state version is an identity
    # token, where `True == 1` is a type confusion with no numeric reading at
    # all. That exclusion is right there and wrong here.
    if isinstance(raw, str | bytes):
        # Text: mirror `to_numeric`'s ASCII-only, underscore-free grammar. A
        # text literal too large for float64 returns ±inf, never OverflowError
        # (verified) -- unlike the int branch below -- so no OverflowError arm
        # is needed for this path.
        underscore = b"_" if isinstance(raw, bytes) else "_"
        if not raw.isascii() or underscore in raw:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    # Non-text: allow `float()` ONLY for the scalar types `to_numeric` fits
    # from. `float()` would otherwise parse a bytearray/memoryview, a Fraction,
    # or any object with `__float__`/`__index__` -- all of which `to_numeric`
    # NaNs -- reproducing the 994-sigma bug one type over from "1_000".
    if not isinstance(raw, _FIT_NUMERIC_TYPES):
        return None

    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    except OverflowError:
        # `float()` raises OverflowError -- an ArithmeticError, NOT a
        # ValueError -- for a Python int above float64's max, so this escaped
        # the caller's original `except (TypeError, ValueError)` and reached
        # the generic HTTP 500 handler: `encode_one` does not catch it,
        # `_idmap`'s cold-start methods catch only RuntimeError, and
        # `routes.py` catches only ColdStartNumericalError/ValueError. Any JSON
        # integer literal of >=309 digits triggers it with nothing but a valid
        # API key.
        #
        # Such a magnitude IS ±inf in float64, so report it as such rather
        # than returning None: None would silently degrade it to the column
        # mean, exactly the "an unknown value must not also be invisible"
        # failure the caller's non-finite branch exists to prevent.
        return math.inf if raw > 0 else -math.inf


def _tokens(raw: Any, delimiter: str) -> list[str]:
    if _is_missing(raw):
        return []
    text = str(raw)
    if not text:
        return []
    return [t for t in (p.strip() for p in text.split(delimiter)) if t]


def _kept_tokens(
    raw: object, vocab: dict[str, int], *, encoding: str, delimiter: str
) -> frozenset[str]:
    """Reduce one raw cell to the token set ``_row_values`` would encode.

    Missing, empty, and unknown (out-of-vocab) values all collapse to the empty
    set -- an empty set means the row emits nothing but the bias for this
    column. Shared by ``_column_block_varies`` and ``_rows_without_signal`` so
    the "does this row carry signal" question is answered identically wherever
    it is asked.
    """
    if encoding == "multi_label":
        return frozenset(t for t in _tokens(raw, delimiter) if t in vocab)
    key = "" if _is_missing(raw) else str(raw)
    return frozenset((key,)) if (key != "" and key in vocab) else frozenset()


def _column_block_varies(
    series: pd.Series, vocab: dict[str, int], *, encoding: str, delimiter: str
) -> bool:
    """True if the column's encoded one-hot/multi-hot block differs across rows.

    Keys on the same property the numerical branch expresses with ``std``: does
    the column actually carry signal, or is every row's block identical (and
    therefore collinear with the always-1 bias)? Each row is reduced to the SAME
    "kept token set" that ``_row_values`` would encode, so a null-bearing column
    like ``[rock, None, rock]`` correctly VARIES (``{rock}`` vs ``{}``) while a
    constant one like ``[rock, rock, rock]`` does not. Returns as soon as a
    second distinct block is seen, so it is O(rows) with an early-out on the
    common (varying) case.
    """
    seen: set[frozenset[str]] = set()
    for raw in series.tolist():
        seen.add(_kept_tokens(raw, vocab, encoding=encoding, delimiter=delimiter))
        if len(seen) > 1:
            return True
    return False


def _rows_without_signal(
    series: pd.Series, vocab: dict[str, int], *, encoding: str, delimiter: str
) -> int:
    """Count rows whose encoded block for this column is entirely zero.

    Such a row contributes nothing for this column -- it is indistinguishable,
    on this axis, from every other row in the same state. Counted so that
    ``min_frequency`` pruning is reported as a row-level cost rather than only
    as a vocabulary-size change.
    """
    return sum(
        1
        for raw in series.tolist()
        if not _kept_tokens(raw, vocab, encoding=encoding, delimiter=delimiter)
    )


def spec_is_live(series: pd.Series, spec: dict) -> bool:
    """True if an encoded column *spec* can contribute signal beyond the bias.

    Unifies the two liveness signals ``build_encoder_state`` already computes:
    a ``numerical`` spec is live iff its recorded ``std`` is non-zero; a
    ``categorical`` / ``multi_label`` spec is live iff its encoded block
    varies across rows (``_column_block_varies``) -- i.e. it is not constant,
    and therefore not collinear with the always-1 bias. A width-only test
    cannot see a constant-but-present categorical column (non-empty vocab,
    ``width > 0``, yet every row emits the same one-hot); this can, because it
    re-reads *series*. The training-side whole-block-dead refusal keys on this
    so it agrees with the per-column dead-column warning and with the
    numerical branch's zero-variance refusal.
    """
    if spec["encoding"] == "numerical":
        return spec["std"] != 0.0
    return _column_block_varies(
        series,
        spec["vocab"],
        encoding=spec["encoding"],
        delimiter=spec.get("delimiter", ""),
    )


def _warn_if_column_dead(
    col: FeatureColumn,
    tokens: Sequence[str],
    vocab: dict[str, int],
    *,
    varies: bool,
) -> None:
    """Warn when a ``categorical`` / ``multi_label`` column contributes nothing.

    "Contributes nothing" means the encoded block is byte-identical for every
    row, so it is collinear with the always-1 bias column and adds no signal.
    Three routes reach that state, and this check keys on the shared observable
    (the block does not vary across rows) rather than on any one route:

    - an unsatisfiable ``min_frequency`` prunes the vocabulary empty
      (``min_frequency`` is ``Field(default=1, ge=1)`` with no upper bound, so
      ``min_frequency: 50`` against a 3-row catalog validates happily),
    - a column has no usable (non-null, non-empty) values at all, or
    - a NON-empty vocabulary is nonetheless CONSTANT across rows (e.g. every
      item shares one genre).

    The third route is why the check is keyed on ``varies`` and not on "vocab
    empty": the earlier ``if vocab: return`` early-out let a constant column
    (non-empty vocab, all-1 one-hot) through as though it were active. A
    null-bearing column such as ``[rock, None, rock]`` DOES vary and is genuine
    signal, so it stays silent -- that distinction is the whole point. This is
    the categorical/multi_label parallel of the numerical branch's
    ``feature_zero_variance_column``.

    Logs column names and counts only, never token values: they are catalog
    data, and the pipeline's redaction processor is keyed to credentials, not
    to arbitrary feature values.
    """
    if varies:
        return
    # Only computed on the warning path, so the set() costs nothing normally.
    distinct = len(set(tokens))
    if not vocab:
        detail = (
            "every value was pruned by min_frequency; column contributes "
            "nothing and every row falls back to the bias column"
            if distinct
            else "column has no non-empty values; column contributes nothing"
        )
    else:
        detail = (
            "vocabulary is non-empty but the encoded block is identical for "
            "every row, collinear with the bias column; column contributes "
            "nothing"
        )
    _logger.warning(
        "feature_empty_vocabulary_column",
        column=col.name,
        encoding=col.encoding,
        min_frequency=col.min_frequency,
        distinct_values=distinct,
        occurrences=len(tokens),
        detail=detail,
    )


def _warn_if_vocabulary_pruned(
    col: FeatureColumn,
    *,
    distinct_values: int,
    kept_values: int,
    n_rows: int,
    rows_without_signal: int,
) -> None:
    """Warn when ``min_frequency`` pruned a material share of ROWS to nothing.

    ``_warn_if_column_dead`` catches only total collapse -- the encoded block
    is identical for EVERY row. It is silent on the far more common partial
    collapse, because a single surviving token is enough to make the block
    vary: with one value kept out of 41, rows carrying the survivor differ from
    rows carrying nothing, ``varies`` is True, and nothing is reported. The
    pruned rows are nonetheless byte-identical to each other on this axis, so
    the column silently stops distinguishing them.

    That matters because ``min_frequency`` is the exact lever the
    ``RECOTEM_MAX_FEATURE_DIM`` error recommends. An operator who hits the cap,
    raises ``min_frequency`` as instructed, and retrains successfully has no
    signal at all that most of the column just stopped existing.

    Keyed on the share of ROWS left with an all-zero block rather than on the
    share of vocabulary pruned: dropping 40 of 41 values is the INTENDED effect
    when those values are long-tail junk covering 2% of the catalog, and a
    warning there would be noise. It is only a problem when it costs a material
    share of the rows their signal, so that is what the threshold measures.

    Logs column names and counts only, never token values -- catalog data, and
    the redaction processor is keyed to credentials, not to feature values.
    """
    if kept_values >= distinct_values or n_rows <= 0:
        return
    if rows_without_signal < _PRUNED_ROWS_WARN_RATIO * n_rows:
        return
    _logger.warning(
        "feature_vocabulary_pruned",
        column=col.name,
        encoding=col.encoding,
        min_frequency=col.min_frequency,
        distinct_values=distinct_values,
        kept_values=kept_values,
        rows_without_signal=rows_without_signal,
        n_rows=n_rows,
        detail=(
            f"min_frequency={col.min_frequency} kept {kept_values} of "
            f"{distinct_values} distinct values; {rows_without_signal} of "
            f"{n_rows} rows now encode to an all-zero block for this column "
            f"and are indistinguishable from each other on this axis. Lower "
            f"min_frequency to keep more values, or drop the column if its "
            f"long tail carries no signal."
        ),
    )


def _warn_if_numerical_unparseable(
    col: FeatureColumn, *, n_rows: int, n_unparseable: int
) -> None:
    """Warn when a material share of a numerical column did not parse.

    ``feature_zero_variance_column`` fires only when a column has NO finite
    value at all. A column that is, say, one fifth unparseable text is fit
    silently from the rest, and the offending rows then standardize to 0.0 --
    which is exactly what a row sitting at the column mean encodes to, and what
    a legitimately *missing* cell encodes to. After the fit there is nothing
    left to distinguish "no price" from "price we could not read", so the count
    has to be taken here.

    This is the numerical counterpart of the vocabulary encodings' pruning
    warning: the same failure -- a subset of rows quietly losing their signal
    while the column as a whole still looks healthy -- reached through a
    different mechanism.

    Missing cells are deliberately NOT counted. Encoding them to the mean is
    documented behaviour, not a defect; the signal worth raising is a value the
    operator believes they supplied and recotem could not read.
    """
    if n_rows <= 0 or n_unparseable <= 0:
        return
    if n_unparseable < _UNPARSEABLE_ROWS_WARN_RATIO * n_rows:
        return
    _logger.warning(
        "feature_numerical_unparseable_column",
        column=col.name,
        n_unparseable=n_unparseable,
        n_rows=n_rows,
        detail=(
            f"{n_unparseable} of {n_rows} supplied values could not be parsed "
            f"as numbers; the column was standardized from the rest and those "
            f"rows encode to the mean, indistinguishable from a missing value. "
            f"Check the column's export format (thousands separators, currency "
            f"symbols, and placeholder text such as 'N/A' are the usual causes)."
        ),
    )


def build_encoder_state(
    df: pd.DataFrame,
    columns: Sequence[FeatureColumn],
) -> dict:
    """Build the phase-independent encoder state from a whole feature table.

    *df* must be indexed by entity id. The vocabulary is built from every
    row, not only rows that appear in the interaction data, so that
    cold-start entities are representable. That means the resulting
    dimension scales with catalog size; ``min_frequency`` is the operator's
    lever against it.

    Raises
    ------
    FeatureEncodeError
        If a declared column is absent from *df*, or the encoded dimension
        exceeds ``RECOTEM_MAX_FEATURE_DIM``.
    """
    specs: list[dict] = []
    offset = 0

    for col in columns:
        if col.name not in df.columns:
            raise FeatureEncodeError(
                f"feature column {col.name!r} is not present in the feature "
                f"table; available columns: {sorted(df.columns)}"
            )
        series = df[col.name]

        if col.encoding == "categorical":
            present = [str(v) for v in series.dropna().tolist() if str(v) != ""]
            vocab = _vocabulary(present, col.min_frequency)
            _warn_if_column_dead(
                col,
                present,
                vocab,
                varies=_column_block_varies(
                    series, vocab, encoding="categorical", delimiter=""
                ),
            )
            dead_rows = _rows_without_signal(
                series, vocab, encoding="categorical", delimiter=""
            )
            _warn_if_vocabulary_pruned(
                col,
                distinct_values=len(set(present)),
                kept_values=len(vocab),
                n_rows=len(series),
                rows_without_signal=dead_rows,
            )
            spec = {
                "name": col.name,
                "encoding": "categorical",
                "vocab": vocab,
                "offset": offset,
                "width": len(vocab),
            }

        elif col.encoding == "multi_label":
            delimiter = col.delimiter or "|"
            flat: list[str] = []
            for raw in series.tolist():
                flat.extend(_tokens(raw, delimiter))
            vocab = _vocabulary(flat, col.min_frequency)
            _warn_if_column_dead(
                col,
                flat,
                vocab,
                varies=_column_block_varies(
                    series, vocab, encoding="multi_label", delimiter=delimiter
                ),
            )
            dead_rows = _rows_without_signal(
                series, vocab, encoding="multi_label", delimiter=delimiter
            )
            _warn_if_vocabulary_pruned(
                col,
                distinct_values=len(set(flat)),
                kept_values=len(vocab),
                n_rows=len(series),
                rows_without_signal=dead_rows,
            )
            spec = {
                "name": col.name,
                "encoding": "multi_label",
                "delimiter": delimiter,
                "vocab": vocab,
                "offset": offset,
                "width": len(vocab),
            }

        else:  # numerical
            try:
                numeric = pd.to_numeric(series, errors="coerce")
                # A complex column cannot be standardized. `to_numeric` keeps
                # complex dtype, and under numpy 2.x `float(np.complex128)`
                # silently discards the imaginary part (ComplexWarning, NOT the
                # TypeError older numpy raised), so without this guard a complex
                # feature column would train on its real part alone. Reject it
                # explicitly, before any float() coercion runs. (Raising here
                # bypasses the OverflowError handler below -- FeatureEncodeError
                # is not an OverflowError.)
                if pd.api.types.is_complex_dtype(numeric):
                    raise FeatureEncodeError(
                        f"feature column {col.name!r} has complex values, which "
                        f"cannot be standardized; declare it categorical or "
                        f"drop it"
                    )
                # Compute mean/std over the FINITE values only. A single
                # non-finite cell must not poison the whole column's
                # statistics: `pd.to_numeric(..., errors="coerce")` maps an
                # overflow token like "1e400" to +inf (NOT NaN --
                # `float("1e400") == inf`), and pandas `mean()`/`std()` do NOT
                # skip +-inf, so without this restriction one such cell would
                # make mean=+inf (reset below) and std=nan (floored below),
                # silently marking a column with usable finite values like
                # [1,2,3] dead. `encode()` already routes a per-row non-finite
                # standardized value to `unknown` (see the non-finite guard in
                # `_row_values`), so restricting the fit to the finite values
                # keeps the two paths consistent -- the "1e400" cell degrades
                # to `unknown` at encode time, not the whole column at fit time.
                # Excluding non-finite values also subsumes the NaN exclusion
                # `notna()` gave before.
                finite = numeric[np.isfinite(numeric)]
                has_finite = bool(finite.size)
                # Cells that WERE supplied but did not survive coercion. A
                # genuinely absent cell is a documented case (it encodes to the
                # mean); a present-but-unparseable one is a data bug that looks
                # exactly the same afterwards, so it has to be counted here or
                # it is never visible at all.
                _warn_if_numerical_unparseable(
                    col,
                    n_rows=len(series),
                    n_unparseable=int((series.notna() & ~np.isfinite(numeric)).sum()),
                )
                # The `float()` coercions are load-bearing plainness guards,
                # not cosmetic -- see the module docstring. `mean()` / `std()`
                # return `np.float64` scalars that would otherwise leak into the
                # persisted state and unpickle via the allow-listed
                # `numpy._core.multiarray.scalar`; they are the numerical-branch
                # counterpart of the vocabulary `str()` coercions.
                mean = float(finite.mean()) if has_finite else 0.0
                std = float(finite.std(ddof=0)) if has_finite else 0.0
            except OverflowError as exc:
                # `pd.to_numeric(..., errors="coerce")` does NOT suppress
                # OverflowError for an object-dtype Python int above float64's
                # max (a >=309-digit int escapes errors="coerce"). Unhandled it
                # escapes `_fetch_side`'s FeatureEncodeError-only catch and
                # surfaces as exit 1 (unmapped) instead of the training-domain
                # exit 4. Map it to FeatureEncodeError, naming the column.
                raise FeatureEncodeError(
                    f"feature column {col.name!r} contains a value too large to "
                    f"standardize as float64"
                ) from exc
            if not np.isfinite(mean):
                mean = 0.0
            scale = max(abs(mean), 1.0)
            if not has_finite:
                # Distinct cause from zero variance: the column parsed to NO
                # finite value at all -- every cell was NaN/unparseable or a
                # non-finite overflow like "1e400". Name that cause rather than
                # blaming "divide by zero" (finding M2): the two are different,
                # and a stray non-finite cell must not misattribute the death of
                # a column that in fact has no usable values.
                _logger.warning(
                    "feature_zero_variance_column",
                    column=col.name,
                    detail="no finite parseable values to standardize; emitting zeros",
                )
                std = 0.0
            # See _NUMERICAL_STD_RELATIVE_FLOOR's module-level comment: a std
            # that is merely tiny relative to the column's own scale is treated
            # the same as an exact 0.0, not just a literal 0.0. This is genuine
            # (near-)zero variance AMONG the finite values -- a distinct cause
            # from the no-finite-values case above, so it keeps the original
            # divide-by-zero wording.
            elif not np.isfinite(std) or std <= _NUMERICAL_STD_RELATIVE_FLOOR * scale:
                _logger.warning(
                    "feature_zero_variance_column",
                    column=col.name,
                    detail="standardization would divide by zero; emitting zeros",
                )
                std = 0.0
            spec = {
                "name": col.name,
                "encoding": "numerical",
                "mean": mean,
                "std": std,
                "offset": offset,
                "width": 1,
            }

        offset += spec["width"]
        specs.append(spec)

    # Bias column -- see the module docstring for why it is deliberately
    # collinear with the categorical one-hots.
    bias_offset = offset
    n_features = offset + 1

    cap = get_max_feature_dim()
    if n_features > cap:
        raise FeatureEncodeError(
            f"encoded feature dimension {n_features} exceeds "
            f"RECOTEM_MAX_FEATURE_DIM ({cap}). The vocabulary is built from "
            f"the whole feature table, so dimension scales with catalog "
            f"size, not interaction count. Raise min_frequency on "
            f"high-cardinality columns, drop a column, or raise the cap -- "
            f"but note the per-trial cost grows super-linearly in this number, "
            f"and the exponent rises with it: a doubling measured 5.1x from "
            f"5,000 to 10,000 and 7.5x from 10,000 to 20,000. Memory grows "
            f"quadratically."
        )

    _logger.info(
        "feature_encoder_state_built",
        columns=[s["name"] for s in specs],
        n_features=n_features,
    )

    return {
        "version": FEATURE_STATE_VERSION,
        "columns": specs,
        "bias_offset": bias_offset,
        "n_features": n_features,
    }


def _row_values(state: dict, values: dict) -> tuple[list[int], list[float], list[str]]:
    """Encode one entity's raw feature mapping into COO-style (col, value) pairs.

    Shared by ``encode`` (one call per requested row) and ``encode_one`` (the
    serve-time single-row path) so the two can never diverge on how a given
    value is turned into numbers -- see the module's parity tests.
    """
    cols: list[int] = []
    data: list[float] = []
    unknown: list[str] = []

    for spec in state["columns"]:
        name = spec["name"]
        raw = values.get(name)

        if spec["encoding"] == "categorical":
            if _is_missing(raw):
                continue
            key = str(raw)
            if key == "":
                continue
            idx = spec["vocab"].get(key)
            if idx is None:
                unknown.append(name)
                continue
            cols.append(spec["offset"] + idx)
            data.append(1.0)

        elif spec["encoding"] == "multi_label":
            toks = _tokens(raw, spec["delimiter"])
            # Dedupe per row so a repeated token (e.g. "rock|pop|rock")
            # contributes exactly one 1.0 to its dimension, not one per
            # occurrence. docs/recipe-reference.md documents this encoding as
            # "multi-hot" (binary), but scipy's COO->CSR conversion SUMS
            # duplicate (row, col) entries, so appending one 1.0 per raw
            # token would silently double (or more) the weight of a
            # repeated tag -- a count vector, not the documented multi-hot
            # one. This is purely a row-encoding concern: it must NOT change
            # `_vocabulary`'s occurrence counting, which deliberately counts
            # every raw token toward `min_frequency` (a duplicate-heavy row
            # legitimately pushes a rare token past the threshold faster).
            seen: set[str] = set()
            any_unknown = False
            for tok in toks:
                if tok in seen:
                    continue
                seen.add(tok)
                idx = spec["vocab"].get(tok)
                if idx is None:
                    any_unknown = True
                    continue
                cols.append(spec["offset"] + idx)
                data.append(1.0)
            if any_unknown:
                unknown.append(name)

        else:  # numerical
            std = spec["std"]
            if std == 0.0:
                continue
            if _is_missing(raw):
                continue
            num = _parse_number(raw)
            if num is None:
                continue
            if not np.isfinite(num):
                # A value that WAS supplied and DID parse as a number, but
                # is +-inf (directly, via a string like "1e400", or via an
                # integer too large for float64 -- see _parse_number's
                # OverflowError branch), or NaN reached via a string like
                # "nan"/"-nan" (a real float NaN is caught by _is_missing
                # above and is a separate, deliberately uncounted gap), is
                # not the same kind of gap as "missing" or "unparseable":
                # the client sent something that parses as a number but
                # cannot be standardized. Recording it as unknown is what
                # makes
                # `recotem_v1_feature_unknown_value_total` fire instead of
                # silently degrading exactly like an omitted column --
                # matching encode_one's own docstring: an unknown value
                # must not also be invisible.
                unknown.append(name)
                continue
            scaled = (num - spec["mean"]) / std
            # `num` is finite (checked above) but the matrix stores `scaled`
            # cast to float32: a standardized magnitude above float32's max
            # (or an intermediate float64 overflow from a tiny std) would
            # become +-inf on that cast. Count it as unknown here rather than
            # let the invisible inf through -- same contract as the non-finite
            # branch above ("an unknown value must not also be invisible").
            if not np.isfinite(scaled) or abs(scaled) > _FLOAT32_MAX:
                unknown.append(name)
                continue
            if scaled != 0.0:
                cols.append(spec["offset"])
                data.append(scaled)

    cols.append(state["bias_offset"])
    data.append(1.0)
    return cols, data, unknown


def encode(
    state: dict,
    df: pd.DataFrame,
    index_order: Sequence[str],
) -> sps.csr_matrix:
    """Encode *df* into a ``(len(index_order), n_features)`` csr_matrix.

    *df* must be indexed by entity id. Rows are emitted in exactly
    *index_order*; ids absent from *df* produce an all-zero row (plus bias),
    which irspack treats as "prior at the origin" -- that entity degrades to
    plain iALS rather than failing.

    ``index_order`` is required, not optional. See the module docstring for
    why: irspack accepts a misordered feature matrix silently, and recotem's
    search phase and final refit do not share one canonical item ordering.
    """
    order = [str(i) for i in index_order]
    frame = df.copy()
    frame.index = [str(i) for i in frame.index]
    frame = frame[~frame.index.duplicated(keep="first")]
    # Bound the row->dict conversion by len(order), not len(df): only rows
    # that ``order`` could ever look up below are worth converting, and
    # ``to_dict`` is the dominant cost for a large feature table (measured:
    # ~4s / ~154MB transient for 400k rows vs. 1k requested ids -- and this
    # function runs twice per training run, search + final refit).
    #
    # ``frame.loc[frame.index.intersection(order)]`` rather than
    # ``frame.reindex(order)`` is deliberate, not a style choice: reindex
    # introduces a NaN row for every id in *order* absent from *df*, and
    # pandas fills that NaN by upcasting the WHOLE column to float64 when
    # the column's dtype cannot natively hold NaN (e.g. an int64
    # ``categorical`` column) -- silently turning every PRESENT row's value
    # too (``1`` -> ``1.0``), which then fails to match the ``str``-keyed
    # vocabulary built from the original dtype at train time, degrading a
    # known category to "unknown". reindex also raises on a duplicate
    # *target* label (``to_dict(orient="index")`` demands a unique index),
    # which a duplicate id in *order* would trigger. ``Index.intersection``
    # only ever selects rows that already exist in *df* -- it cannot
    # introduce a NaN row or touch a column's dtype -- and is unaffected by
    # duplicates in either operand, so it has neither failure mode.
    wanted = frame.index.intersection(order)
    lookup = frame.loc[wanted].to_dict(orient="index")

    indptr = [0]
    indices: list[int] = []
    data: list[float] = []
    for entity_id in order:
        values = lookup.get(entity_id) or {}
        cols, vals, _unknown = _row_values(state, values)
        indices.extend(cols)
        data.extend(vals)
        indptr.append(len(indices))

    return sps.csr_matrix(
        (
            np.asarray(data, dtype=np.float32),
            np.asarray(indices, dtype=np.int32),
            np.asarray(indptr, dtype=np.int32),
        ),
        shape=(len(order), state["n_features"]),
        dtype=np.float32,
    )


def encode_one(state: dict, values: dict) -> tuple[sps.csr_matrix, list[str]]:
    """Encode a single request's raw feature mapping.

    Returns the ``(1, n_features)`` matrix and the list of column names whose
    supplied value was not in the training vocabulary. Callers should count
    the unknowns: an unknown category degrades the recommendation silently,
    so it must not also be invisible.
    """
    cols, data, unknown = _row_values(state, values)
    return (
        sps.csr_matrix(
            (
                np.asarray(data, dtype=np.float32),
                np.asarray(cols, dtype=np.int32),
                np.asarray([0, len(cols)], dtype=np.int32),
            ),
            shape=(1, state["n_features"]),
            dtype=np.float32,
        ),
        unknown,
    )


def state_descriptor(state: dict | None) -> dict | None:
    """Return the small header summary for *state*, or None.

    Deliberately still just ``n_features`` + column names, even though
    ``feature_vocabulary_pruned`` shows there is per-column detail worth
    auditing after the fact. ``_FEATURE_SIDE_KEYS`` pins this key set exactly
    and ``check_artifact_feature_version`` fails CLOSED on any other, so a new
    key here is an artifact-format break in both directions: artifacts written
    by this build would be refused by every 2.0.x serve, and relaxing the check
    to accept both shapes would give up the property that makes it worth
    having. Carrying the counts is a ``FEATURE_STATE_VERSION`` bump -- and a
    forced retrain for every feature-aware operator -- not a diagnostics tweak.
    """
    if state is None:
        return None
    return {
        "n_features": state["n_features"],
        "columns": [s["name"] for s in state["columns"]],
    }


def check_artifact_feature_version(header_dict: dict, *, name: str) -> None:
    """Refuse an artifact whose feature-encoder state this build cannot read.

    Policy
    ------
    - ``features`` absent -> pass. Either the artifact predates the feature or
      the model has no features; there is nothing to mis-encode.
    - ``version`` equals ``FEATURE_STATE_VERSION`` -> pass.
    - anything else (newer, older-and-unknown, missing, malformed) -> refuse.

    Failing CLOSED is the point. If the state's shape changed, serving would
    encode a request's features into the wrong vector space and return
    silently incorrect recommendations -- the one failure mode no counter can
    catch. This mirrors ``_irspack_compat``'s posture on unverified
    transitions.

    Note the asymmetry with a pre-feature serve, which is unprotected and does
    not need to be: it has no feature code at all, never reads the state, and
    serves known-user recommendations that remain correct. It is safe by
    ignorance. This gate protects exactly the builds that could mis-encode.
    """
    # Deferred import: _features.py is a neutral top-level module imported by
    # both recotem.training and recotem.serving (CLAUDE.md architecture
    # constraint). A module-level import of recotem.artifact would pull the
    # artifact package into every training-only invocation.
    from recotem.artifact.format import ArtifactError  # noqa: PLC0415

    raw = header_dict.get("features")
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ArtifactError(
            f"{FEATURE_VERSION_MSG_PREFIX} artifact for recipe {name!r} has a "
            f"malformed 'features' header (expected an object, got "
            f"{type(raw).__name__}); refusing to load"
        )
    version = raw.get("version")
    # bool is an int subclass in Python -- exclude it explicitly so a stray
    # `"version": true` is not silently treated as `1`.
    if not isinstance(version, int) or isinstance(version, bool):
        raise ArtifactError(
            f"{FEATURE_VERSION_MSG_PREFIX} artifact for recipe {name!r} has a "
            f"'features' header with a missing or non-integer version "
            f"({version!r}); refusing to load"
        )
    if version != FEATURE_STATE_VERSION:
        raise ArtifactError(
            f"{FEATURE_VERSION_MSG_PREFIX} artifact for recipe {name!r} "
            f"declares feature encoder version {version}, but this build "
            f"implements version {FEATURE_STATE_VERSION}. Loading it could "
            f"encode request features into the wrong vector space and "
            f"return silently incorrect recommendations. Retrain with this "
            f"recotem version, or upgrade serving."
        )


def _column_sample(columns: Any) -> str:
    """Render a column list for an error message, bounded in count and length.

    See ``_COLUMN_SAMPLE_SIZE``: one side of every comparison below comes from a
    header this build has just decided not to trust, so its column list cannot
    be quoted back verbatim into a string that ends up in ``/v1/health``.
    """
    if not isinstance(columns, list):
        return repr(columns)
    shown = []
    for value in columns[:_COLUMN_SAMPLE_SIZE]:
        text = value if isinstance(value, str) else repr(value)
        if len(text) > _COLUMN_SAMPLE_MAX_CHARS:
            text = f"{text[:_COLUMN_SAMPLE_MAX_CHARS]}..."
        shown.append(text)
    rendered = ", ".join(repr(s) for s in shown)
    if len(columns) > _COLUMN_SAMPLE_SIZE:
        rendered += f", ...(+{len(columns) - _COLUMN_SAMPLE_SIZE} more)"
    return f"[{rendered}]"


def _check_state_side(
    desc: Any,
    state: Any,
    *,
    side: str,
    fail: Callable[[str], Exception],
) -> None:
    """Reconcile one side's header descriptor with its deserialized state."""
    if desc is None and state is None:
        return
    if desc is None:
        raise fail(
            f"declares no {side} features, but its payload carries {side} "
            f"feature encoder state. The header is what `recotem inspect` and "
            f"the version gate read, so a state the header does not declare is "
            f"a state nothing checks"
        )
    if state is None:
        raise fail(
            f"declares {side} features, but its payload carries no {side} "
            f"feature encoder state, so every {side} cold-start request would "
            f"be refused by a model the header says can serve it"
        )
    if not isinstance(desc, dict):
        raise fail(
            f"has a malformed {side} feature descriptor (expected an object, "
            f"got {type(desc).__name__})"
        )
    if not isinstance(state, dict):
        raise fail(
            f"carries {side} feature encoder state of an unexpected type "
            f"({type(state).__name__})"
        )

    side_keys = set(desc)
    if side_keys != _FEATURE_SIDE_KEYS:
        raise fail(
            f"has a {side} feature descriptor with keys {sorted(side_keys)}; "
            f"this build writes exactly {sorted(_FEATURE_SIDE_KEYS)}"
        )

    # The payload state's own `version`, written by `build_encoder_state` and
    # until now never read back. It is the payload-side anchor of the version
    # gate: without it, `features.version` in the header describes only itself.
    state_version = state.get("version")
    if state_version != FEATURE_STATE_VERSION:
        raise fail(
            f"carries {side} feature encoder state at version "
            f"{state_version!r}, but this build implements version "
            f"{FEATURE_STATE_VERSION}. Encoding a request against it could "
            f"produce the wrong vector space"
        )

    # Summarise through `state_descriptor` itself -- the very function that
    # produced the header at train time -- so the two sides can never disagree
    # on what "the descriptor of this state" means.
    try:
        expected = state_descriptor(state)
    except (KeyError, TypeError, AttributeError) as exc:
        raise fail(
            f"carries {side} feature encoder state this build cannot summarise "
            f"({type(exc).__name__}: {exc})"
        ) from exc

    if desc["n_features"] != expected["n_features"]:
        raise fail(
            f"declares {desc['n_features']!r} {side} feature dimensions, but "
            f"its payload's encoder state has {expected['n_features']}"
        )
    if desc["columns"] != expected["columns"]:
        raise fail(
            f"declares {side} feature columns "
            f"{_column_sample(desc['columns'])}, but its payload's encoder "
            f"state has {_column_sample(expected['columns'])}"
        )


def check_artifact_feature_state(
    header_dict: dict, recommender: Any, *, name: str
) -> None:
    """Refuse an artifact whose ``features`` header contradicts its payload.

    Runs AFTER deserialization (``check_artifact_feature_version`` runs before,
    on the header alone) because it is the first point at which both halves are
    known. It reconciles three things the artifact format otherwise never
    checks against each other:

    - presence: a payload state no header declares, or a header side no payload
      backs. The first is what makes the version gate deletable -- drop the
      ``features`` key and the state loads unchecked;
    - shape: ``n_features`` and ``columns``, per side, against the deserialized
      state (via ``state_descriptor``, the same function that wrote them);
    - capability: ``features.active`` against the winning recommender's actual
      ability to consume the state.

    Absent ``features`` with no payload state passes untouched: that is every
    artifact recotem produced before feature-aware iALS existed, and every
    features-less recipe since.

    Reaching any refusal here requires a validly-signed artifact, i.e. the HMAC
    signing key -- which already permits substituting the model wholesale. This
    is defence in depth against a mis-built or partially-tampered artifact, not
    a security boundary; the point is that an internally inconsistent artifact
    fails loudly at load instead of serving silently wrong recommendations.
    """
    # Deferred import for the same reason as check_artifact_feature_version's.
    from recotem.artifact.format import ArtifactError  # noqa: PLC0415

    def fail(detail: str) -> ArtifactError:
        return ArtifactError(
            f"{FEATURE_STATE_MSG_PREFIX} artifact for recipe {name!r} "
            f"{detail}; refusing to load"
        )

    # getattr, not attribute access: the payload is whatever the (allow-listed)
    # unpickler produced, and older artifacts predate these attributes -- the
    # class-level defaults in recotem._idmap cover the normal case, this covers
    # a payload that is not an IDMappedRecommender at all.
    states = {
        "item": getattr(recommender, "item_feature_state", None),
        "user": getattr(recommender, "user_feature_state", None),
    }

    raw = header_dict.get("features")
    if raw is None:
        carried = sorted(s for s, st in states.items() if st is not None)
        if carried:
            raise fail(
                f"has no 'features' header, but its payload carries "
                f"{', '.join(carried)} feature encoder state. Deleting the "
                f"descriptor would otherwise delete the version gate along "
                f"with it"
            )
        return

    if not isinstance(raw, dict):
        # Unreachable through the serve loaders (check_artifact_feature_version
        # refuses this first); handled so the function is correct standalone.
        raise fail(
            f"has a malformed 'features' header (expected an object, got "
            f"{type(raw).__name__})"
        )

    unknown = sorted(set(raw) - _FEATURE_HEADER_KEYS)
    if unknown:
        raise fail(
            f"has a 'features' header with unrecognised key(s) "
            f"{', '.join(repr(k) for k in unknown)}; this build understands "
            f"only {sorted(_FEATURE_HEADER_KEYS)}"
        )

    active = raw.get("active")
    if active is not None and not isinstance(active, bool):
        raise fail(f"has a non-boolean 'features.active' ({active!r})")

    for side in ("item", "user"):
        _check_state_side(raw.get(side), states[side], side=side, fail=fail)

    # `active` is optional: artifacts written before the flag existed carry a
    # descriptor without it, and must keep loading. When it IS present it is a
    # claim about the payload, so it gets reconciled like every other one.
    # `_is_feature_capable` is looked up rather than called directly because the
    # payload need not be an IDMappedRecommender at all (see `states` above).
    capable = getattr(recommender, "_is_feature_capable", None)
    if active is not None and callable(capable):
        actual = bool(capable())
        if actual != active:
            inner = type(getattr(recommender, "recommender", None)).__name__
            raise fail(
                f"declares features.active={active}, but its payload's "
                f"recommender ({inner}) "
                f"{'can' if actual else 'cannot'} consume feature state"
            )
