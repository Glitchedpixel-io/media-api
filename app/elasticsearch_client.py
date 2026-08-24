# app/elasticsearch_client.py
"""
Elasticsearch client lifecycle management with proper initialization,
connection pooling, and graceful shutdown.
"""

from __future__ import annotations

import logfire

from elasticsearch import Elasticsearch

from app.config import ElasticsearchConfig


class ElasticsearchClientManager:
    """
    Manages the Elasticsearch client lifecycle with proper initialization,
    health checking, and cleanup.

    This replaces the global singleton pattern with a properly managed client
    that can be created during app startup and cleaned up during shutdown.
    """

    def __init__(self, config: ElasticsearchConfig) -> None:
        self.config = config
        self._client: Elasticsearch | None = None
        self._is_available = False

    def initialize(self) -> None:
        """
        Initialize the Elasticsearch client with configuration from settings.

        This should be called during application startup (in lifespan context).
        If Elasticsearch is not configured, this will log a warning but not fail.
        """
        with logfire.span("elasticsearch_client_init") as span:
            if not self.config.url:
                logfire.warning(
                    "Elasticsearch URL not configured; search features will be unavailable"
                )
                self._is_available = False
                return

            logfire.info(f"Initializing Elasticsearch client: {self.config.url}")

            try:
                es_kwargs: dict = {
                    "hosts": [self.config.url],
                    "max_retries": 3,
                    "retry_on_timeout": True,
                    "retry_on_status": [429, 502, 503, 504],
                    "request_timeout": 30,
                    "sniff_on_start": False,
                    "sniff_on_node_failure": False,
                }

                # Authentication
                if self.config.api_key:
                    es_kwargs["api_key"] = self.config.api_key
                    logfire.info("Using Elasticsearch API key authentication")
                elif self.config.username and self.config.password:
                    es_kwargs["basic_auth"] = (self.config.username, self.config.password)
                    logfire.info("Using Elasticsearch basic authentication")

                # TLS configuration
                if self.config.insecure:
                    es_kwargs["verify_certs"] = False
                    logfire.warning("Elasticsearch TLS verification disabled (insecure mode)")
                elif self.config.ca_cert:
                    es_kwargs["ca_certs"] = self.config.ca_cert
                    logfire.info(f"Using custom CA certificate: {self.config.ca_cert}")

                # Create client
                self._client = Elasticsearch(**es_kwargs)

                # Test connection
                if self._client.ping():
                    logfire.info("Successfully connected to Elasticsearch")
                    self._is_available = True
                else:
                    logfire.error("Failed to ping Elasticsearch; service may be unavailable")
                    self._is_available = False

            except Exception as e:
                span.record_exception(e)
                self._is_available = False
                # Don't raise - allow app to start without ES

    def close(self) -> None:
        """
        Close the Elasticsearch client and clean up resources.

        This should be called during application shutdown (in lifespan context).
        """
        if self._client is not None:
            logfire.info("Closing Elasticsearch client")
            try:
                self._client.close()
                logfire.info("Elasticsearch client closed successfully")
            except Exception as e:
                logfire.warning(f"Error closing Elasticsearch client: {e}")
            finally:
                self._client = None
                self._is_available = False

    def get_client(self) -> Elasticsearch:
        """
        Get the Elasticsearch client instance.

        :return: Elasticsearch client
        :raises RuntimeError: If client is not initialized or unavailable
        """
        if not self._is_available or self._client is None:
            raise RuntimeError(
                "Elasticsearch is not available. "
                "Ensure elasticsearch_url is configured and the service is running."
            )
        return self._client

    @property
    def is_available(self) -> bool:
        """Check if Elasticsearch is available."""
        return self._is_available

    def health_check(self) -> dict[str, str | bool]:
        """
        Perform a health check on the Elasticsearch connection.

        :return: Dictionary with health status information
        """
        if not self._is_available or self._client is None:
            return {
                "status": "unavailable",
                "healthy": False,
                "message": "Elasticsearch client not initialized",
            }

        try:
            # Ping to verify connection
            if self._client.ping():
                # Get cluster health
                health = self._client.cluster.health()
                cluster_status = health.get("status", "unknown")

                return {
                    "status": cluster_status,
                    "healthy": cluster_status in ["green", "yellow"],
                    "cluster_name": health.get("cluster_name", "unknown"),
                    "number_of_nodes": health.get("number_of_nodes", 0),
                }
            else:
                return {
                    "status": "unreachable",
                    "healthy": False,
                    "message": "Elasticsearch ping failed",
                }
        except Exception as e:
            logfire.warning(f"Elasticsearch health check failed: {e}")
            return {
                "status": "error",
                "healthy": False,
                "message": str(e),
            }


# Global instance to be initialized during app startup
_es_manager: ElasticsearchClientManager | None = None


def get_es_manager() -> ElasticsearchClientManager:
    """
    Get the global Elasticsearch manager instance.

    :return: Elasticsearch client manager
    :raises RuntimeError: If manager is not initialized
    """
    if _es_manager is None:
        raise RuntimeError("Elasticsearch manager not initialized. Call initialize_es() first.")
    return _es_manager


def initialize_es(config: ElasticsearchConfig) -> ElasticsearchClientManager:
    """
    Initialize the global Elasticsearch manager.

    This should be called during application startup.
    """
    global _es_manager  # noqa: PLW0603
    _es_manager = ElasticsearchClientManager(config)
    _es_manager.initialize()
    return _es_manager


def close_es() -> None:
    """
    Close the global Elasticsearch manager.

    This should be called during application shutdown.
    """
    global _es_manager  # noqa: PLW0603
    if _es_manager is not None:
        _es_manager.close()
        _es_manager = None
