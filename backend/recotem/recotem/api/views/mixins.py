"""Shared ViewSet mixins for owned resource filtering."""

from django.db import models as db_models


class OwnedResourceMixin:
    """Mixin for ViewSets that filter resources by the current user's ownership.

    Subclasses must define ``owner_lookup`` as the field path from the model
    to the ``owner`` ForeignKey (e.g. ``"project__owner"`` or ``"owner"``).
    The mixin adds a filter of ``Q(owner_lookup=user) | Q(owner_lookup__isnull=True)``
    so that shared (unowned) resources are also visible.

    The ``isnull`` clause exists for backward compatibility: legacy data created
    before the multi-user migration has ``owner=NULL``.  These rows are treated
    as "shared" and visible to all authenticated users.
    """

    owner_lookup: str = "owner"

    def get_owner_filter(self):
        user = self.request.user
        return db_models.Q(**{self.owner_lookup: user}) | db_models.Q(
            **{f"{self.owner_lookup}__isnull": True}
        )


class CreatedByResourceMixin:
    """Mixin for ViewSets whose models use ``created_by`` instead of owner.

    Like :class:`OwnedResourceMixin`, rows with ``created_by=NULL`` (legacy
    data from before multi-user support) are visible to all authenticated users.
    """

    created_by_lookup: str = "created_by"

    def get_owner_filter(self):
        user = self.request.user
        return db_models.Q(**{self.created_by_lookup: user}) | db_models.Q(
            **{f"{self.created_by_lookup}__isnull": True}
        )
