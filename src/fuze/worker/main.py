import asyncio
import signal
import sys
from structlog import get_logger, configure as structlog_configure
from temporalio.worker import Worker

from fuze.common.temporal import get_temporal_client
from fuze.common.logging import configure_logging
from fuze.workflows.orders import OrderWorkflow
from fuze.workflows.shipping import ShippingWorkflow
from fuze.activities import orders as order_activities
from fuze.activities import shipping as shipping_activities

logger = get_logger()

async def main():
    configure_logging()
    logger.info("worker_startup", version="0.1.0")

    client = await get_temporal_client()

    # Define tasks for graceful shutdown interrupt
    interrupt_event = asyncio.Event()

    def signal_handler(sig, frame):
        logger.info("signal_received", signal=sig)
        interrupt_event.set()

    # Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Core Queues
    # Each Worker instance listens on a single Task Queue properly.
    
    # 1. Order Worker
    # Registers Order Workflows and Order Activities
    order_worker = Worker(
        client,
        task_queue="orders-tq",
        workflows=[OrderWorkflow],
        activities=[order_activities.validate_order],
    )
    
    # 2. Shipping Worker
    # Registers Shipping Workflows and Shipping Activities
    shipping_worker = Worker(
        client,
        task_queue="shipping-tq",
        workflows=[ShippingWorkflow],
        activities=[shipping_activities.ship_order],
    )

    logger.info("workers_initialized", queues=["orders-tq", "shipping-tq"])

    # Run workers until interrupt
    # asyncio.gather will run them concurrently.
    # We use run() directly on the worker which is a convenience wrapper.
    # To handle shutdown gracefully, we can just await the workers and rely on standard Python async cancellation 
    # OR we can pass a shared shutdown event if using a runner. 
    # But `Worker.run()` is infinite. A clean way is using `asyncio.wait` with the interrupt event.
    
    async with order_worker, shipping_worker:
        logger.info("workers_started")
        await interrupt_event.wait()
        logger.info("shutdown_signal_received_draining")
        # Exiting the context manager will implicitly shut down the workers gracefully.

    logger.info("shutdown_complete")

if __name__ == "__main__":
    asyncio.run(main())
