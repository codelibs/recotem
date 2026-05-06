"""Re-export IDMappedRecommender from recotem.training._compat.

The artifact allow-list must include this FQCN (recotem.serving._compat.IDMappedRecommender)
so that models unpickled at serve time can reconstruct correctly regardless of
which path the class was pickled under.
"""

from recotem.training._compat import IDMappedRecommender

__all__ = ["IDMappedRecommender"]
