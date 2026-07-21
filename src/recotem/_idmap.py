"""Neutral package-level home for IDMappedRecommender.

This module is the canonical location for ``IDMappedRecommender`` so that
pickled artifacts record a FQCN (``recotem._idmap.IDMappedRecommender``) that
is independent of whether the class was instantiated by the training or serving
sub-package.

Why this module exists
-----------------------
``recotem.training`` and ``recotem.serving`` must never import each other
(CLAUDE.md architecture constraint).  Previously ``IDMappedRecommender`` was
defined in ``recotem.training._compat`` and re-exported from
``recotem.serving._compat``, causing a cross-package import violation when the
serving package imported the training package.

By defining the class here (under ``recotem.*`` -- no sub-package), both
training and serving can import from this neutral location without violating
the boundary.  The IPython stub that must run *before* the first irspack
import is still installed in ``recotem.training._compat``, which is imported
first by the training sub-package.

FQCN allow-list note
--------------------
``recotem.artifact.signing._ALLOWED_CLASSES`` contains
``("recotem._idmap", "IDMappedRecommender")``.  Artifacts pickled before this
commit (which recorded ``recotem.training._compat``) cannot be loaded after
this change -- this is intentional for the 2.0.0a0 pre-release.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
import structlog

# IPython stub: install before any irspack import.  Irspack pulls in fastprogress
# at import time, which in turn imports IPython.display.  The stub provides only
# the display symbols that fastprogress references and is idempotent.
# `recotem.training._compat` installs the same stub for callers that go through
# the training sub-package, but importing `_idmap` directly (e.g. from serving)
# must also work, so we self-bootstrap here.
# Both "IPython" and "IPython.display" are checked independently so a partial
# real-IPython install (IPython present but IPython.display absent) is handled.
from recotem._ipython_stub import install as _install_ipython_stub

_install_ipython_stub()

from irspack.utils.id_mapping import IDMapper  # noqa: E402

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Feature-capable recommender allow-list
# ---------------------------------------------------------------------------
#
# Mirrors ``recotem.training.algorithms.FEATURE_CAPABLE_CLASS_NAMES`` BY
# VALUE. That module cannot be imported here: ``_idmap.py`` is a neutral
# module shared by both ``training`` and ``serving``, and importing
# ``recotem.training`` from it would violate the training/serving boundary
# (CLAUDE.md). The two sets must therefore be kept in sync BY HAND -- if
# irspack ever grows a second feature-aware recommender class, add it to
# BOTH ``FEATURE_CAPABLE_CLASS_NAMES`` and this set in the same change.
#
# This is an explicit allow-list, not duck-typing (``hasattr`` /
# ``inspect.signature``), for the same reason ``training/algorithms.py``,
# ``artifact/signing.py``'s ``_ALLOWED_CLASSES``, and ``_irspack_compat.py``
# all use hand-enumerated name/FQCN tables rather than deriving the answer at
# runtime: a future irspack class that happens to expose the same method
# names/signatures with different semantics must not be silently accepted
# just because it "looks like" IALSRecommender.
_FEATURE_CAPABLE_CLASS_NAMES: frozenset[str] = frozenset({"IALSRecommender"})


# ---------------------------------------------------------------------------
# Numerical cold-start failure signatures
# ---------------------------------------------------------------------------
#
# Allow-list of substrings (matched case-insensitively) that identify a
# ``RuntimeError`` from irspack's native core as a NUMERICAL condition on the
# client-supplied value, as opposed to a server-side fault. A bare
# ``except RuntimeError`` at the three call sites below would also swallow
# non-numerical ``RuntimeError``s the exact same native calls can raise for
# reasons that have nothing to do with the request (e.g.
# ``irspack/recommenders/ials.py``'s ``trainer_as_ials`` raising
# ``RuntimeError("tried to fetch trainer before the training.")`` when
# ``trainer`` is unexpectedly ``None``) and mislabel them as a 400 client
# error instead of letting them surface as the 500 they actually are.
#
# Hand-enumerated, not duck-typed, for the same reason
# ``_FEATURE_CAPABLE_CLASS_NAMES`` above, ``training.algorithms
# .FEATURE_CAPABLE_CLASS_NAMES``, ``artifact.signing._ALLOWED_CLASSES``, and
# ``_irspack_compat.py``'s verified-transition table all hand-enumerate
# rather than infer: "this RuntimeError happened inside a cold-start solve"
# is not sufficient evidence that it is safe to blame the client.
#
# Verified present in the installed irspack (0.5.0) via ``strings`` on the
# compiled ``recommenders/_ials_core.abi3.so`` (2026-07-17):
#   - "Conjugate-gradient solver encountered a singular system." -- the only
#     solver recotem's recipes exercise today (recotem never sets
#     ``solver_type``, so CG is always the default).
#   - "Cholesky decomposition failed." / "Feature ridge Cholesky
#     decomposition failed." -- the Cholesky solver's ridge-regression
#     failure. Not reachable through any recipe field today, but the literal
#     ships in the same binary as the CG one; scoping this allow-list on
#     "singular system" alone would silently start 500ing again the moment a
#     future recipe field lets an operator pick ``solver_type: cholesky``.
#   - "Cholesky solve failed." -- the Cholesky solver's main solve step.
_NUMERICAL_FAILURE_SIGNATURES: tuple[str, ...] = (
    "singular system",
    "cholesky decomposition failed",
    "cholesky solve failed",
)


def _is_numerical_cold_start_failure(exc: RuntimeError) -> bool:
    """Return True iff *exc* matches a verified numerical-failure signature.

    See the ``_NUMERICAL_FAILURE_SIGNATURES`` comment above for what this
    allow-list covers and why it must not be widened to a bare
    ``except RuntimeError``.
    """
    message = str(exc).lower()
    return any(signature in message for signature in _NUMERICAL_FAILURE_SIGNATURES)


class ColdStartNumericalError(Exception):
    """A supplied feature value made irspack's cold-start solver numerically
    unstable.

    An extreme-but-finite ``numerical`` feature value (e.g. ``1e22``)
    standardizes (``recotem._features._row_values``) to a magnitude that
    makes the per-request conjugate-gradient system irspack solves for a
    cold-start embedding ill-conditioned. irspack's native core raises a
    bare ``RuntimeError`` ("Conjugate-gradient solver encountered a singular
    system.") with no input-validation semantics of its own -- it has no way
    to know the value came from an untrusted client rather than a bug.

    The three cold-start call sites below that feed a features-derived
    matrix into irspack's solver (``get_score_cold_user_from_features``,
    ``get_score_cold_user``, ``compute_item_embedding_from_features``) catch
    ``RuntimeError`` and, ONLY when its message matches
    ``_NUMERICAL_FAILURE_SIGNATURES`` above, re-raise this instead -- so
    ``serving/routes.py`` can map it to a 400 (bad client input) rather than
    let it surface as an unhandled 500. A ``RuntimeError`` that does NOT
    match (e.g. a genuinely broken model with ``trainer is None``) is
    re-raised unchanged and reaches the router's generic handler as a 500 --
    matching a real server fault is not this client's problem to be blamed
    for. Deliberately NOT a ``ValueError`` subclass: ``ValueError`` here
    already means "this model / feature side cannot do cold start at all"
    (see ``_require_capability`` and the ``*_feature_state is None`` guards
    below), a model-capability condition. This is a per-VALUE numerical
    condition on an otherwise capable model, so routes.py gives it its own
    error code (``FEATURE_VALUE_UNUSABLE``) rather than folding it into
    ``FEATURES_NOT_SUPPORTED``, which would incorrectly imply the model can
    never serve this recipe's cold start.

    Training is NOT affected by this change, for two independent reasons.
    Code-path: ``build_encoder_state`` / ``encode`` / ``_row_values``
    (``recotem._features``) are untouched, so the exact same extreme value
    flowing through training-time ``encode()`` still standardizes the same
    way -- this class only wraps the three SERVE-time cold-start solves
    listed above. Structural (the stronger reason): at train time, a
    numerical column's mean/std are computed FROM the same column that
    contains the outlier, so the outlier inflates the very std it is later
    divided by. That self-referential bound caps the worst-case standardized
    magnitude at ``(n - 1) / sqrt(n)`` regardless of how extreme the raw
    value is -- verified empirically: an outlier among ``n=20`` training
    rows reaches ``max|z| ~= 4.36``, ``n=1000`` reaches ``~= 31.6``,
    ``n=400000`` reaches ``~= 632.5`` (a ``1e22`` outlier among 1000 normal
    values gives ``max|z| = 31.61``, matching the ``(n-1)/sqrt(n)`` bound of
    ``31.59`` almost exactly). Reaching the ``~1e19`` standardized magnitude
    needed to break the solver this way would take on the order of ``1e38``
    training rows. At the other extreme, an overflowing outlier (e.g.
    ``1e308``) makes the sum-of-squares behind the std computation overflow
    to a non-finite value; ``build_encoder_state`` already guards this --
    it warns and pins ``std`` to ``0.0`` whenever the fitted std comes out
    non-finite or merely negligible against the column's own scale -- and
    ``_row_values``'s numerical branch then skips any column whose std is
    ``0.0`` rather than dividing by it. Serve-time cold start has
    neither guard: the request's raw value is divided by a std that was fit
    WITHOUT it, so nothing bounds how extreme the standardized magnitude can
    get. A final-refit Cholesky failure on an ill-conditioned *training*
    matrix (a different, already-bounded failure mode) surfaces as
    ``TrainingError`` (exit 4) through an entirely different code path.
    """


class IDMappedRecommender:
    """String-keyed recommender wrapper around irspack IDMapper.

    Wraps any trained irspack recommender and exposes user/item IDs as strings.
    Reconstructs the transient ``_mapper`` on unpickle via __setstate__.

    The class is defined here (``recotem._idmap``) rather than in
    ``recotem.training._compat`` so that the recorded FQCN is not tied
    to either the training or the serving sub-package.
    """

    # Class-level defaults, deliberately WITH an assignment.
    #
    # __setstate__ is what pickle uses to restore state, and pickle constructs
    # via cls.__new__(cls) under protocol 2+, so __init__ never runs on
    # unpickle. Defaults assigned in __init__ would therefore not protect
    # artifacts pickled before these attributes existed -- attribute lookup on
    # the class is what does. A bare annotation (no `= None`) would create no
    # class attribute at all and would not work.
    item_feature_state: dict | None = None
    user_feature_state: dict | None = None

    def __init__(
        self,
        recommender: object,
        user_ids: Iterable[str],
        item_ids: Iterable[str],
        *,
        item_feature_state: dict | None = None,
        user_feature_state: dict | None = None,
    ) -> None:
        self.recommender = recommender
        self.user_ids: list[str] = [str(u) for u in user_ids]
        self.item_ids: list[str] = [str(i) for i in item_ids]
        # Assigned as INSTANCE attributes so __getstate__ (which returns
        # dict(self.__dict__)) actually persists them. The class defaults above
        # cover the read path for older artifacts; they do not persist anything.
        self.item_feature_state = item_feature_state
        self.user_feature_state = user_feature_state
        self._mapper: IDMapper = IDMapper(self.user_ids, self.item_ids)

    # ------------------------------------------------------------------
    # State protocol (used by the artifact serializer)
    # ------------------------------------------------------------------

    def __getstate__(self) -> dict:
        state = dict(self.__dict__)
        state.pop("_mapper", None)
        return state

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self.user_ids = [str(u) for u in self.user_ids]
        self.item_ids = [str(i) for i in self.item_ids]
        # Explicit, greppable normalization for artifacts pickled before these
        # attributes existed. Redundant with the class-level defaults on
        # purpose: the redundancy is cheap and makes the intent searchable.
        self.__dict__.setdefault("item_feature_state", None)
        self.__dict__.setdefault("user_feature_state", None)
        self._mapper = IDMapper(self.user_ids, self.item_ids)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_recommendation_for_known_user_id(
        self,
        user_id: str,
        cutoff: int = 20,
    ) -> list[tuple[str, float]]:
        """Return top-*cutoff* (item_id, score) pairs for a known user.

        Raises
        ------
        KeyError
            If *user_id* was not in the training set.
        RuntimeError
            If the underlying recommender raises an internal error (propagated
            so it surfaces as a 500 rather than being masked as a 404).
        """
        uid = str(user_id)
        if uid not in self._mapper.user_id_to_index:
            raise KeyError(uid)
        return self._mapper.recommend_for_known_user_id(
            self.recommender,
            uid,
            cutoff=cutoff,
        )

    def _require_capability(self, ok: bool, *, missing: str) -> None:
        """Raise ``ValueError`` if *ok* is False.

        Guards every feature-based cold-start entry point against a model
        whose feature state is present but whose winning recommender cannot
        act on it. Task 9 persists ``item_feature_state`` /
        ``user_feature_state`` unconditionally so the artifact header always
        agrees with the payload -- even when the Optuna search winner is not
        feature-capable. ``algorithms: ["TopPop", "IALS"]`` with a
        ``features:`` block is valid (``Recipe._validate_features_algorithms``
        requires only that at least one listed algorithm be feature-capable),
        and TopPop can win the search. Calling an irspack method that does
        not exist on the winner would raise a bare ``AttributeError``; this
        turns that into the same ``ValueError`` family as the "no state at
        all" checks in the callers below, but with a distinct message so
        operators can tell "retrain with features" (no state) apart from
        "retrain restricting algorithms to a feature-capable one" (state
        present, winner incapable) at a glance.
        """
        if not ok:
            raise ValueError(
                "this model's recommender "
                f"({type(self.recommender).__name__}) does not support "
                f"feature-based cold-start; it has no {missing}. This "
                "happens when a recipe lists a feature-capable algorithm "
                "alongside a non-feature-capable one (e.g. TopPop, "
                "CosineKNN) and the search winner was the latter -- retrain "
                "restricting `algorithms` to a feature-aware one (e.g. IALS) "
                "to use this endpoint."
            )

    def _is_feature_capable(self) -> bool:
        """Return True iff the wrapped recommender's class is allow-listed.

        Matches on the BARE class name (``type(self.recommender).__name__``),
        not the full FQCN (``__module__`` + ``__qualname__``), which is what
        the other "which class may do X" decisions of this shape do:
        ``training.algorithms.FEATURE_CAPABLE_CLASS_NAMES``, and
        ``_irspack_compat.py``'s verified-transition table, whose
        ``best_class`` rows are bare names too.
        ``artifact.signing._ALLOWED_CLASSES`` is the deliberate exception --
        it keys on the full ``(module, qualname)`` pair because it answers a
        different question: which classes the unpickler may construct from
        untrusted bytes, where ``SafeUnpickler.find_class`` is handed exactly
        that pair. This set instead asks whether an already-deserialized
        object can do feature cold start, and exists specifically to mirror
        ``FEATURE_CAPABLE_CLASS_NAMES`` value-for-value (see the comment
        above ``_FEATURE_CAPABLE_CLASS_NAMES``); matching on FQCN here would
        make the two sets structurally different and harder to eyeball as
        "in sync".

        A subclass of ``IALSRecommender`` does NOT pass: ``type(x).__name__``
        returns the subclass's own name, not its base's. This is deliberate,
        not an oversight -- admitting a subclass automatically via
        ``isinstance`` would reintroduce exactly the failure mode this gate
        exists to close: a class trusted because it *looks* like
        IALSRecommender (same MRO, same inherited method names) rather than
        because it was individually verified. ``_irspack_compat.py`` states
        the identical rule for version-transition trust: "unproven is not
        the same as safe." Admitting a future subclass requires adding its
        name here explicitly, in the same change that adds it to
        ``FEATURE_CAPABLE_CLASS_NAMES``.
        """
        return type(self.recommender).__name__ in _FEATURE_CAPABLE_CLASS_NAMES

    def get_recommendation_for_new_user(
        self,
        item_ids: Iterable[str],
        cutoff: int = 20,
        user_features: dict | None = None,
    ) -> list[tuple[str, float]] | tuple[list[tuple[str, float]], list[str]]:
        """Recommend for an ad-hoc history, optionally with a user profile.

        Without *user_features* this is unchanged and returns a plain list
        -- existing callers depend on this exact type. With *user_features*
        (case B) it runs irspack's joint solve over the seed history AND the
        feature prior, and returns ``(recommendations, unknown_columns)``
        instead. The return type differs by argument on purpose: the old
        signature has existing callers.

        Raises
        ------
        ValueError
            Only when *user_features* is given: if this model carries no
            user feature state, or if the wrapped recommender's
            ``get_score_cold_user`` does not accept a ``user_features``
            keyword (the search winner was not feature-capable even though
            the artifact carries feature state -- see ``_require_capability``).
        ColdStartNumericalError
            Only when *user_features* is given: if an extreme-but-finite
            supplied value makes irspack's cold-start solver numerically
            unstable (see the class docstring).
        """
        seeds = [str(iid) for iid in item_ids]
        if user_features is None:
            return self._mapper.recommend_for_new_user(
                self.recommender, seeds, cutoff=cutoff
            )

        from recotem._features import encode_one

        if self.user_feature_state is None:
            raise ValueError(
                "this model has no user feature state; it was not trained with "
                "features.user"
            )
        self._require_capability(
            self._is_feature_capable(),
            missing="a `user_features`-aware `get_score_cold_user`",
        )
        matrix, unknown = encode_one(self.user_feature_state, user_features)
        X_seed = self._mapper.list_of_user_profile_to_matrix([seeds])
        try:
            score = self.recommender.get_score_cold_user(X_seed, user_features=matrix)[
                0
            ]
        except RuntimeError as exc:
            if not _is_numerical_cold_start_failure(exc):
                raise
            logger.warning(
                "cold_start_numerical_failure",
                method="get_score_cold_user",
                irspack_message=str(exc),
            )
            raise ColdStartNumericalError(str(exc)) from exc
        recs = self._mapper.score_to_recommended_items(
            score, cutoff=cutoff, forbidden_item_ids=seeds or None
        )
        return recs, unknown

    def get_recommendation_for_cold_user(
        self,
        user_features: dict,
        cutoff: int = 20,
    ) -> tuple[list[tuple[str, float]], list[str]]:
        """Case A: recommend for an unknown user from their features alone.

        Returns ``(recommendations, unknown_columns)``. *unknown_columns*
        names the feature columns whose supplied value was not in the
        training vocabulary; the caller must count them, because an unknown
        category degrades the result silently.

        Client-requested exclusion is deliberately NOT a parameter here.
        ``serving/routes.py``'s ``_build_items`` post-filters
        ``exclude_items`` off this list, uniformly for every verb and every
        case. Accepting it here and passing it down as ``forbidden_item_ids``
        instead would make the ranker back-fill to a full *cutoff*, so the
        same ``exclude_items`` request would return MORE items on this path
        than on the pre-existing ones that only post-filter -- one parameter
        silently meaning two different things depending on whether features
        were supplied.

        Raises
        ------
        ValueError
            If this model carries no user feature state, or if the wrapped
            recommender has no ``get_score_cold_user_from_features`` method.
            Passing either through to irspack is unsafe: no state means a
            shape mismatch or, for a (1, 0) matrix, silent all-zero scores
            with no error; no method means a bare ``AttributeError`` -- a
            non-feature-capable search winner (e.g. TopPop, CosineKNN) can
            carry non-None feature state (Task 9 persists it
            unconditionally) without being able to act on it.
        ColdStartNumericalError
            If an extreme-but-finite supplied value makes irspack's
            cold-start solver numerically unstable (see the class
            docstring).
        """
        from recotem._features import encode_one

        if self.user_feature_state is None:
            raise ValueError(
                "this model has no user feature state; it was not trained with "
                "features.user"
            )
        self._require_capability(
            self._is_feature_capable(),
            missing="`get_score_cold_user_from_features`",
        )
        matrix, unknown = encode_one(self.user_feature_state, user_features)
        try:
            score = self.recommender.get_score_cold_user_from_features(matrix)[0]
        except RuntimeError as exc:
            if not _is_numerical_cold_start_failure(exc):
                raise
            logger.warning(
                "cold_start_numerical_failure",
                method="get_score_cold_user_from_features",
                irspack_message=str(exc),
            )
            raise ColdStartNumericalError(str(exc)) from exc
        recs = self._mapper.score_to_recommended_items(score, cutoff=cutoff)
        return recs, unknown

    def get_recommendation_for_cold_seeds(
        self,
        seed_items: Sequence[str],
        item_features: dict[str, dict],
        cutoff: int = 20,
    ) -> tuple[list[tuple[str, float]], list[str]]:
        """Case C: seeds that include items absent from training.

        Known seeds contribute their learned item embedding; unknown seeds
        contribute an embedding computed from their features. The mean is
        scored as if it were a user embedding, which is exactly item-item
        similarity in this model.

        Removing the seeds from their own related-items result DOES use
        ``forbidden_item_ids``, so the ranker back-fills around them: a
        client that asked "what goes with i0" should not spend a slot on i0
        itself. Client-requested exclusion is the opposite and is NOT a
        parameter here -- ``serving/routes.py``'s ``_build_items``
        post-filters ``exclude_items`` off this list for every verb, and
        back-filling it here instead would make ``exclude_items`` mean
        something different on this path than on the pre-existing ones.

        This deliberately has DIFFERENT semantics from
        ``get_recommendation_for_new_user``, which runs an iALS cold-user
        solve treating seeds as an interaction history. Routes must only take
        this path when a genuinely new input (item_features for an unknown
        seed) is present, so existing clients see no behavior change.

        Raises
        ------
        ValueError
            If this model carries no item feature state, or if the wrapped
            recommender has no item-embedding API. See
            ``get_recommendation_for_cold_user`` for why a non-None state
            does not guarantee the search winner can act on it.
        KeyError
            If no seed is either a known item id or accompanied by an entry
            in *item_features*.
        ColdStartNumericalError
            If an extreme-but-finite value in a cold seed's *item_features*
            entry makes irspack's cold-item-embedding solver numerically
            unstable (see the class docstring).
        """
        from recotem._features import encode_one

        if self.item_feature_state is None:
            raise ValueError(
                "this model has no item feature state; it was not trained with "
                "features.item"
            )
        self._require_capability(
            self._is_feature_capable(),
            missing=(
                "the item-embedding API (`get_item_embedding` / "
                "`compute_item_embedding_from_features` / "
                "`get_score_from_user_embedding`)"
            ),
        )
        # Deliberately OUTSIDE the per-seed try/except below: this reads the
        # already-trained item-embedding matrix and does not depend on any
        # per-request value at all -- it returns the identical array for
        # every call against this model. A RuntimeError here (e.g. the
        # ``trainer is None`` fault from irspack's ``trainer_as_ials``) can
        # therefore never be the numerical-instability condition this class
        # exists to catch; it is unconditionally a server fault and must
        # reach the router's generic 500 handler, not be routed through
        # ``_is_numerical_cold_start_failure``'s message check only to be
        # re-raised anyway. Wrapping it would add a no-op except clause with
        # no observable behavior change -- see docs/api-reference.md and the
        # task report for the fuller rationale.
        item_emb = self.recommender.get_item_embedding()
        vectors = []
        unknown: list[str] = []
        for seed in seed_items:
            sid = str(seed)
            idx = self._mapper.item_id_to_index.get(sid)
            if idx is not None:
                vectors.append(item_emb[idx])
                continue
            raw = item_features.get(sid)
            if raw is None:
                continue
            matrix, unk = encode_one(self.item_feature_state, raw)
            unknown.extend(unk)
            try:
                vectors.append(
                    self.recommender.compute_item_embedding_from_features(matrix)[0]
                )
            except RuntimeError as exc:
                if not _is_numerical_cold_start_failure(exc):
                    raise
                logger.warning(
                    "cold_start_numerical_failure",
                    method="compute_item_embedding_from_features",
                    irspack_message=str(exc),
                )
                raise ColdStartNumericalError(str(exc)) from exc
        if not vectors:
            raise KeyError("no usable seed: none known and none carried features")

        mean_emb = np.mean(np.stack(vectors), axis=0, keepdims=True)
        score = self.recommender.get_score_from_user_embedding(mean_emb)[0]
        forbidden = [
            str(s) for s in seed_items if str(s) in self._mapper.item_id_to_index
        ]
        recs = self._mapper.score_to_recommended_items(
            score, cutoff=cutoff, forbidden_item_ids=forbidden or None
        )
        return recs, sorted(set(unknown))
