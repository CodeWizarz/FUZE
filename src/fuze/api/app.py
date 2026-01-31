import logging
import structlog
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException, Depends
from temporalio.client import Client, WorkflowHandle
from temporalio.exceptions import FailureError

from fuze.common.temporal import get_temporal_client
from fuze.common.logging import configure_logging
from fuze.common.db.session import get_db_session
from fuze.common.db.repositories import OrderRepository
from fuze.api.models import (
    OrderStartRequest, AddressUpdateRequest, OrderStatusResponse
)
# Use string reference for OrderWorkflow to avoid direct import if potential circular deps,
# but here it's fine.
from fuze.workflows.orders import OrderWorkflow

logger = logging.getLogger("uvicorn")

# Global Temporal Client
temporal_client: Client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global temporal_client
    # Startup
    configure_logging()
    logger.info("Initializing Temporal Client...")
    try:
        temporal_client = await get_temporal_client()
    except Exception as e:
        logger.error(f"Failed to connect to Temporal: {e}")
        # We might want to fail startup in prod, but for now we continue
    yield
    # Shutdown
    logger.info("Shutting down API...")

app = FastAPI(title="Fuze API", version="0.1.0", lifespan=lifespan)

# --- Dependencies ---

def get_client() -> Client:
    if not temporal_client:
        raise HTTPException(status_code=503, detail="Temporal client not available")
    return temporal_client

# --- Endpoints ---

@app.post("/orders/{order_id}/start")
async def start_order(
    request: OrderStartRequest,
    client: Client = Depends(get_client)
):
    """Start a new OrderWorkflow."""
    # Note: We use the order_id as the Workflow ID to ensure uniqueness/idempotency.
    workflow_id = str(request.order_id)
    
    try:
        handle = await client.start_workflow(
            OrderWorkflow.run,
            str(request.order_id),  # arg 1
            request.address,        # arg 2
            id=workflow_id,
            task_queue="orders-tq"
        )
        return {"workflow_id": handle.id, "run_id": handle.result_run_id, "status": "started"}
    except Exception as e:
        # If workflow already running, Temporal raises WorkflowExecutionAlreadyStartedError
        logger.error(f"Failed to start workflow: {e}")
        raise HTTPException(status_code=409, detail=f"Workflow start failed: {str(e)}")


@app.post("/orders/{order_id}/signals/approve")
async def approve_order_signal(
    order_id: UUID, 
    client: Client = Depends(get_client)
):
    """Signal the workflow to approve the order."""
    handle = client.get_workflow_handle(str(order_id))
    try:
        # Matches @workflow.signal(name="ApproveOrder")
        await handle.signal("ApproveOrder")
        return {"status": "signal_sent"}
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Signal failed: {str(e)}")


@app.post("/orders/{order_id}/signals/cancel")
async def cancel_order_signal(
    order_id: UUID, 
    client: Client = Depends(get_client)
):
    """Signal the workflow to cancel the order."""
    handle = client.get_workflow_handle(str(order_id))
    try:
        await handle.signal("CancelOrder")
        return {"status": "signal_sent"}
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Signal failed: {str(e)}")


@app.post("/orders/{order_id}/signals/update-address")
async def update_address_signal(
    order_id: UUID,
    request: AddressUpdateRequest,
    client: Client = Depends(get_client)
):
    """Signal the workflow to update values."""
    handle = client.get_workflow_handle(str(order_id))
    try:
        await handle.signal("UpdateAddress", request.new_address)
        return {"status": "signal_sent"}
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Signal failed: {str(e)}")


@app.get("/orders/{order_id}/status", response_model=OrderStatusResponse)
async def get_order_status(
    order_id: UUID, 
    client: Client = Depends(get_client)
):
    """
    Get composite status from DB and Workflow Query.
    """
    workflow_id = str(order_id)
    
    # 1. Fetch from DB
    order_db = None
    async with get_db_session() as session:
        repo = OrderRepository(session)
        order_db = await repo.get_order_by_id(order_id)
        
    if not order_db:
        raise HTTPException(status_code=404, detail="Order not found in DB")

    # 2. Fetch from Workflow Query (if running)
    wf_step = None
    wf_error = None
    is_running = False
    
    try:
        handle = client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        is_running = desc.status == 1 # RUNNING
        
        if is_running:
            # Query workflow for internal state
            wf_step = await handle.query("get_current_step")
            wf_error = await handle.query("get_last_error")
            # We could also query get_address if we wanted to verify sync
            
        else:
            # If not running, we might still want to know final status from Temporal history 
            # but usually DB is the source of truth for completed orders.
            pass
            
    except Exception as e:
        logger.warning(f"Could not query workflow {workflow_id}: {e}")
        # Workflow might not exist if we only created DB record manually (edge case) 
        # or if retention period expired.
    
    return OrderStatusResponse(
        order_id=order_db.id,
        db_state=order_db.state,
        db_step=order_db.current_step,
        workflow_step=wf_step,
        workflow_last_error=order_db.last_error or wf_error,
        is_running=is_running,
        retries=0 # Placeholder: Needs logic to count retries via Events or Workflow Query
    )
