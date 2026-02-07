from rest_framework import status
from rest_framework.exceptions import APIException


class RecotemBaseException(APIException):
    """Base exception for all Recotem-specific errors."""

    pass


class ModelLoadError(RecotemBaseException):
    """Raised when a trained model cannot be loaded."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_detail = "Failed to load the trained model."
    default_code = "model_load_error"


class DataValidationError(RecotemBaseException):
    """Raised when uploaded data fails validation."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Data validation failed."
    default_code = "data_validation_error"


class TuningJobError(RecotemBaseException):
    """Raised when a parameter tuning job encounters an error."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_detail = "Parameter tuning job failed."
    default_code = "tuning_job_error"


class ResourceNotFoundError(RecotemBaseException):
    """Raised when a requested resource does not exist."""

    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "The requested resource was not found."
    default_code = "resource_not_found"
