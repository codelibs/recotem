from rest_framework.views import exception_handler as drf_exception_handler


def custom_exception_handler(exc, context):
    """Custom exception handler that wraps DRF responses in a consistent envelope.

    Response format:
        {
            "success": false,
            "error": {
                "code": "error_code",
                "detail": "Human-readable message"
            },
            "data": null
        }
    """
    response = drf_exception_handler(exc, context)

    if response is not None:
        error_code = getattr(exc, "default_code", "error")
        if hasattr(exc, "get_codes"):
            error_code = exc.get_codes()

        detail = response.data
        if isinstance(detail, dict) and "detail" in detail:
            detail = detail["detail"]

        response.data = {
            "success": False,
            "error": {
                "code": error_code,
                "detail": detail,
            },
            "data": None,
        }

    return response
