import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from maxwell.api import cors_middleware

def test_cors_middleware_options():
    request = MagicMock()
    request.method = "OPTIONS"

    handler = AsyncMock()

    response = asyncio.run(cors_middleware(request, handler))

    assert response.headers.get("Access-Control-Allow-Origin") == "*"
    assert "OPTIONS" in response.headers.get("Access-Control-Allow-Methods")
    assert "Content-Type" in response.headers.get("Access-Control-Allow-Headers")
    handler.assert_not_called()

def test_cors_middleware_get():
    request = MagicMock()
    request.method = "GET"

    handler = AsyncMock()
    mock_response = MagicMock()
    mock_response.headers = {}
    handler.return_value = mock_response

    response = asyncio.run(cors_middleware(request, handler))

    assert response.headers.get("Access-Control-Allow-Origin") == "*"
    assert "OPTIONS" in response.headers.get("Access-Control-Allow-Methods")
    assert "Content-Type" in response.headers.get("Access-Control-Allow-Headers")
    handler.assert_called_once_with(request)
