# tests/unit/test_elasticsearch_health.py
"""ElasticsearchClientManager.health_check must not return exception text.

Its result is served by the unauthenticated /api/health.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config.schema import ElasticsearchConfig
from app.elasticsearch_client import ElasticsearchClientManager


@pytest.mark.unit
def test_a_failing_ping_is_reported_without_its_message() -> None:
    manager = ElasticsearchClientManager(ElasticsearchConfig(url="http://es.internal:9200"))
    client = MagicMock()
    client.ping.side_effect = RuntimeError("auth failed for elastic@es.internal:9200")
    manager._client = client
    manager._is_available = True

    result = manager.health_check()

    assert result["status"] == "error"
    assert result["healthy"] is False
    assert "es.internal" not in str(result)
