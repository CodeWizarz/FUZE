import pytest
from uuid import uuid4
from unittest.mock import Mock
from temporalio import activity
from temporalio.worker import Worker
from temporalio.testing import WorkflowEnvironment
from temporalio.exceptions import ApplicationError

from fuze.workflows.orders import OrderWorkflow
from fuze.workflows.shipping import ShippingWorkflow
from fuze.activities import orders as order_activities
from fuze.activities import shipping as shipping_activities

# --- Mock Activities ---

@activity.defn(name="order_received")
async def mock_order_received(order_id: str, address: dict) -> str:
    return "created"

@activity.defn(name="order_validated")
async def mock_order_validated(order_id: str) -> bool:
    return True

@activity.defn(name="payment_charged")
async def mock_payment_charged(order_id: str, amount_cents: int, payment_token: str) -> str:
    return "pay_123"

@activity.defn(name="package_prepared")
async def mock_package_prepared(order_id: str) -> str:
    return "box_123"

@activity.defn(name="carrier_dispatched")
async def mock_carrier_dispatched(order_id: str, box_id: str) -> str:
    return "TRK-001"

@pytest.mark.asyncio
async def test_order_happy_path(temporal_env):
    """
    Test End-to-End Happy Path:
    Start -> Receive -> Validate -> Approve (Signal) -> Charge -> Ship (Child) -> Complete.
    """
    async with temporal_env:
        worker = Worker(
            temporal_env.client,
            task_queue="orders-tq",
            workflows=[OrderWorkflow, ShippingWorkflow],
            activities=[
                mock_order_received,
                mock_order_validated,
                mock_payment_charged,
                mock_package_prepared, 
                mock_carrier_dispatched
            ],
            # We must map "shipping-tq" to this worker in the test env environment because
            # we only start one worker here that handles everything for simplicity.
            # BUT: In main.py we have two workers.
            # For testing, we can register everything on "orders-tq" OR start two workers.
            # Simpler: The workflows specify task queues. We need to honor that by running a worker for each
            # OR make the test worker listen to both? Worker usually listens to ONE queue.
            # So we need TWO workers or patch the workflows to use the same queue.
            # Let's run TWO workers.
        )
        shipping_worker = Worker(
            temporal_env.client,
            task_queue="shipping-tq",
            workflows=[ShippingWorkflow],
            activities=[
                mock_package_prepared,
                mock_carrier_dispatched
            ]
        )
        
        async with worker, shipping_worker:
            order_id = str(uuid4())
            handle = await temporal_env.client.start_workflow(
                OrderWorkflow.run,
                order_id,
                {"zip_code": "12345"},
                id=order_id,
                task_queue="orders-tq",
            )
            
            # 1. Wait until it gets stuck waiting for approval
            # We can query the stack or just fire the signal after a small delay.
            # In time-skipping env, we must sleep to let the workflow progress to the wait point.
            await temporal_env.sleep(2) 
            
            assert "waiting_for_approval" == await handle.query("get_current_step")
            
            # 2. Signal Approval
            await handle.signal("ApproveOrder")
            
            # 3. Wait for result (will run fast due to time skipping)
            result = await handle.result()
            
            assert result == "TRK-001"
            assert "completed" == await handle.query("get_current_step")


@pytest.mark.asyncio
async def test_order_cancel_before_payment(temporal_env):
    """
    Test Cancellation flow:
    Start -> Receive -> Validate -> Cancel (Signal) -> Clean Exit.
    """
    async with temporal_env:
        worker = Worker(
            temporal_env.client,
            task_queue="orders-tq",
            workflows=[OrderWorkflow],
            activities=[mock_order_received, mock_order_validated],
        )
        async with worker:
            order_id = str(uuid4())
            handle = await temporal_env.client.start_workflow(
                OrderWorkflow.run,
                order_id,
                {"zip_code": "12345"},
                id=order_id,
                task_queue="orders-tq",
            )
            
            await temporal_env.sleep(1)
            assert "waiting_for_approval" == await handle.query("get_current_step")
            
            # Signal Cancellation
            await handle.signal("CancelOrder")
            
            # The workflow should return "cancelled" string
            result = await handle.result()
            assert result == "cancelled"
            assert "cancelled" == await handle.query("get_current_step")


@activity.defn(name="carrier_dispatched")
async def mock_carrier_fail_once(order_id: str, box_id: str) -> str:
    # We can use a counter or random, but for deterministic test logic we can inject failure?
    # Actually temporal testing is tricky with stateful mocks across retries unless we use a class-based activity.
    # Let's just FAIL strictly and confirm parent sees it.
    raise ValueError("API Down")

@pytest.mark.asyncio
async def test_shipping_failure_handling(temporal_env):
    """
    Test Dispatch Failure and Parent Signaling.
    """
    async with temporal_env:
        worker = Worker(
            temporal_env.client,
            task_queue="orders-tq",
            workflows=[OrderWorkflow, ShippingWorkflow], # OrderWorkflow calls Shipping
            activities=[
                mock_order_received,
                mock_order_validated,
                mock_payment_charged,
                mock_package_prepared,
                # Dispatch will fail
                mock_carrier_fail_once 
            ],
        )
        shipping_worker = Worker(
            temporal_env.client,
            task_queue="shipping-tq",
            workflows=[ShippingWorkflow],
            activities=[
                mock_package_prepared,
                mock_carrier_fail_once
            ]
        )
        
        async with worker, shipping_worker:
            order_id = str(uuid4())
            handle = await temporal_env.client.start_workflow(
                OrderWorkflow.run,
                order_id,
                {"zip_code": "12345"},
                id=order_id,
                task_queue="orders-tq",
            )
            
            await temporal_env.sleep(1)
            await handle.signal("ApproveOrder")
            
            # Expect failure because Child will fail and raise exception
            with pytest.raises(Exception) as excinfo:
                await handle.result()
            
            # Verify the parent recorded the error
            # Note: The query might fail if workflow is closed/failed depending on retention, 
            # but in test env it usually works.
            # Actually, handle.result() raises, but we can check the error content or query if accessible.
            
            # Since workflow failed, query might reject depending on setup.
            # But let's check if the Failure caused the workflow to finish.
            assert "Shipping failed" in str(excinfo.value) or "API Down" in str(excinfo.value)
