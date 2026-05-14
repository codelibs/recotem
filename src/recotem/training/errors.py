"""Training error hierarchy.

All training-time failures are subclasses of ``TrainingError``.  The CLI maps
``TrainingError`` to exit 4 unless the exception carries a ``code`` attribute
that the CLI handles differently.
"""

from __future__ import annotations


class TrainingError(Exception):
    """Base class for all training-time failures.

    Attributes
    ----------
    code:
        Short machine-readable error code string.  The CLI uses this to
        distinguish error categories and emit structured JSON error lines.
        Defaults to ``"training_error"``.
    """

    code: str = "training_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class MinDataViolation(TrainingError):
    """Raised when the cleansed dataset is below configured minimum thresholds.

    Carries observed counts and configured thresholds for diagnostic output.
    """

    code = "min_data_violation"

    def __init__(
        self,
        message: str,
        *,
        n_rows: int | None = None,
        n_users: int | None = None,
        n_items: int | None = None,
        min_rows: int | None = None,
        min_users: int | None = None,
        min_items: int | None = None,
    ) -> None:
        super().__init__(message, code="min_data_violation")
        self.n_rows = n_rows
        self.n_users = n_users
        self.n_items = n_items
        self.min_rows = min_rows
        self.min_users = min_users
        self.min_items = min_items


class SplitError(TrainingError):
    """Raised when irspack split produces an unusable result (e.g. empty test)."""

    code = "split_error"


class SearchError(TrainingError):
    """Raised when Optuna search fails to produce a usable result."""

    code = "search_error"


class ZeroScoreError(SearchError):
    """All completed trials produced a score of 0.0."""

    code = "zero_score"


class UnknownAlgorithmError(TrainingError):
    """Raised when an algorithm alias cannot be resolved to an irspack class."""

    code = "unknown_algorithm"
