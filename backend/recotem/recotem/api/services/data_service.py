# Re-export read_dataframe from the canonical location (api/utils.py)
# to avoid duplication while maintaining import compatibility.
from recotem.api.utils import read_dataframe

__all__ = ["read_dataframe"]
