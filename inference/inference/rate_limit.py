"""Rate limiting configuration."""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def get_api_key_or_ip(request: Request) -> str:
    """Use API key prefix as rate limit key, falling back to IP."""
    api_key_header = request.headers.get("x-api-key", "")
    if api_key_header.startswith("rctm_") and len(api_key_header) > 13:
        return api_key_header[5:13]  # Use key prefix as rate limit key
    return get_remote_address(request)


limiter = Limiter(key_func=get_api_key_or_ip)
