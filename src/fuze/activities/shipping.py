from uuid import UUID
import asyncio
from temporalio import activity
from temporalio.exceptions import ApplicationError

from fuze.common.db.session import get_db_session
from fuze.common.db.models import Event
from fuze.common.db.repositories import OrderRepository, EventRepository

# -----------------------------------------------------------------------------
# Recommended Retry Policies:
# -----------------------------------------------------------------------------
# package_prepared:   Retry Standard (e.g., Warehouse API down).
# carrier_dispatched: Retry Standard with Timeouts.
#                     This typically involves external API calls.
# -----------------------------------------------------------------------------

@activity.defn
async def package_prepared(order_id: str) -> str:
    """
    Simulate "Picking and Packing" at the warehouse.
    """
    activity.logger.info("activity_started", activity="package_prepared", order_id=order_id)
    
    # Simulate processing time
    await asyncio.sleep(0.5)
    
    async with get_db_session() as session:
        repo = OrderRepository(session)
        event_repo = EventRepository(session)
        
        await repo.update_order_state(UUID(order_id), state="PACKAGING", step="warehouse_processing")
        
        # ... Warehouse Logic ...
        
        await repo.update_order_state(UUID(order_id), state="PACKAGED", step="ready_for_dispatch")
        
        await event_repo.log_event(Event(
            order_id=UUID(order_id),
            type="PACKAGE_PREPARED",
            payload_json={"warehouse_id": "WH-NY-01"}
        ))
        
    return "box_12345"


@activity.defn
async def carrier_dispatched(order_id: str, box_id: str) -> str:
    """
    Request pickup from Carrier (FedEx/UPS).
    """
    activity.logger.info("activity_started", activity="carrier_dispatched", order_id=order_id, box_id=box_id)
    
    async with get_db_session() as session:
        repo = OrderRepository(session)
        event_repo = EventRepository(session)
        
        await repo.update_order_state(UUID(order_id), state="DISPATCHING", step="contacting_carrier")
        
        # ... Carrier API Call ...
        tracking_number = f"TRK-{order_id[:8].upper()}"
        
        await repo.update_order_state(UUID(order_id), state="SHIPPED", step="completed")
        
        await event_repo.log_event(Event(
            order_id=UUID(order_id),
            type="ORDER_SHIPPED",
            payload_json={"tracking_number": tracking_number, "carrier": "FEDEX"}
        ))
        
    return tracking_number
