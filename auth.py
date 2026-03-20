import os
import secrets
from fastapi import Security, HTTPException, status
from fastapi.security.api_key import APIKeyHeader

# The API key is loaded from an environment variable for security.
# Default to a random key if not set (for development).
# For production, set the MASTER_MONITOR_API_KEY environment variable.
API_KEY = os.getenv("MASTER_MONITOR_API_KEY", "dev-key-change-me-in-production")
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(API_KEY_HEADER)):
    """
    FastAPI dependency to verify the API key in the request header.
    Local Monitor Server must include: X-API-Key: <key>
    """
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API Key. Include 'X-API-Key' header.",
        )
    if not secrets.compare_digest(api_key, API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API Key.",
        )
    return api_key
