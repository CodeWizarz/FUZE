import asyncio
from datetime import timedelta
from typing import Any, Optional, Dict

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

# Import activities
from fuze.activities import orders as order_activities
from fuze.workflows.shipping import ShippingWorkflow

# Activity Stubs
validate_order = workflow.activity_method(
    order_activities.order_validated,
    start_to_close_timeout=timedelta(seconds=10),
    retry_policy=RetryPolicy(
        initial_interval=timedelta(seconds=1),
        maximum_interval=timedelta(seconds=10),
        maximum_attempts=5, 
    )
)

order_received = workflow.activity_method(
    order_activities.order_received,
    start_to_close_timeout=timedelta(seconds=10),
    # Retry infinite for DB connection transient issues
)

payment_charged = workflow.activity_method(
    order_activities.payment_charged,
    start_to_close_timeout=timedelta(seconds=30),
    retry_policy=RetryPolicy(
        initial_interval=timedelta(seconds=1),
        maximum_interval=timedelta(seconds=60),
        # Infinite retries for connectivity issues. 
        # Application logic handles non-retryable failures.
    )
)

@workflow.defn(name="OrderWorkflow")
class OrderWorkflow:
    def __init__(self) -> None:
        self._current_step = "started"
        self._address: Dict[str, Any] = {}
        self._is_approved = False
        self._is_cancelled = False
        self._last_error: Optional[str] = None
        self._retries = 0 # Business level retries if tracked manually

    @workflow.run
    async def run(self, order_id: str, address: Dict[str, Any]) -> str:
        self._address = address
        workflow.logger.info("workflow_started", order_id=order_id)

        try:
            # 1. Order Received
            self._current_step = "receiving_order"
            await order_received(order_id, self._address)

            # 2. Validate Order
            self._current_step = "validating_order"
            await validate_order(order_id)
            
            # 3. Wait for Approval
            self._current_step = "waiting_for_approval"
            try:
                # Wait for approval signal OR timeout (e.g., 10 seconds for demo)
                # User asked for "enforcing max manual review window"
                await workflow.wait_condition(
                    lambda: self._is_approved or self._is_cancelled, 
                    timeout=timedelta(seconds=10) # Short for demo/test
                )
            except asyncio.TimeoutError:
                # Auto-cancel or fail if not approved in time
                self._last_error = "Approval timeout"
                workflow.logger.warning("approval_timeout", order_id=order_id)
                raise ApplicationError("Order timed out awaiting approval", non_retryable=True)

            if self._is_cancelled:
                self._current_step = "cancelled"
                workflow.logger.info("order_cancelled_by_signal", order_id=order_id)
                return "cancelled"

            # 4. Charge Payment
            self._current_step = "charging_payment"
            # Idempotency token could be order_id or a UUID generated inside here (deterministic)
            # For simplicity, we use order_id as the token or a derived value.
            payment_token = f"pay_{order_id}"
            
            # Hardcoded amount for demo
            await payment_charged(order_id, 1000, payment_token)

            # 5. Start Shipping (Child Workflow)
            self._current_step = "shipping"
            try:
                # Execute Child Workflow
                # We expect ShippingWorkflow to return a tracking number
                result = await workflow.execute_child_workflow(
                    ShippingWorkflow.run,
                    order_id,
                    id="ship_" + order_id, # Child Workflow ID for deduplication
                    task_queue="shipping-tq",
                    start_to_close_timeout=timedelta(minutes=5),
                    # Inherit parent cancellation
                    cancellation_type=workflow.ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED
                )
                self._current_step = "completed"
                return str(result)
                
            except Exception as e:
                self._last_error = f"Shipping failed: {str(e)}"
                raise

        except asyncio.CancelledError:
            self._current_step = "cancelled"
            workflow.logger.info("workflow_cancelled", order_id=order_id)
            # Run compensation logic here if needed (e.g., refund)
            # For now, just exit cleanly.
            raise
        except Exception as e:
            self._last_error = str(e)
            workflow.logger.error("workflow_failed", error=str(e))
            raise

    # --- Signals ---

    @workflow.signal(name="ApproveOrder")
    def approve_order(self) -> None:
        """Signal to approve the order."""
        self._is_approved = True

    @workflow.signal(name="CancelOrder")
    def cancel_order(self) -> None:
        """Signal to cancel the order."""
        self._is_cancelled = True

    @workflow.signal(name="UpdateAddress")
    def update_address(self, new_address: Dict[str, Any]) -> None:
        """Signal to update address."""
        if self._current_step in ["receiving_order", "validating_order", "waiting_for_approval"]:
            self._address = new_address
            workflow.logger.info("address_updated")
        else:
            workflow.logger.warning("address_update_rejected", reason="Order too far along")

    # --- Queries ---

    @workflow.query
    def get_current_step(self) -> str:
        return self._current_step

    @workflow.query
    def get_last_error(self) -> Optional[str]:
        return self._last_error

    @workflow.query
    def get_address(self) -> Dict[str, Any]:
        return self._address
