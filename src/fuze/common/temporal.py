import os
from temporalio.client import Client
from structlog import get_logger

logger = get_logger()

async def get_temporal_client() -> Client:
    """
    Connect to the Temporal server.
    Read configuration from environment variables.
    """
    target_host = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    logger.info("connecting_to_temporal", address=target_host)
    
    # In production, you would configure TLS here
    client = await Client.connect(target_host)
    
    logger.info("connected_to_temporal", address=target_host)
    return client
