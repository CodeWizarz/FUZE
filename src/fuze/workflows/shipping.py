from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

# Import activity definitions
from fuze.activities import shipping as shipping_activities

# Activity Options
package_prepared = workflow.activity_method(
    shipping_activities.package_prepared,
    start_to_close_timeout=timedelta(minutes=1),
    retry_policy=RetryPolicy(
        initial_interval=timedelta(seconds=1),
        maximum_interval=timedelta(seconds=10),
        maximum_attempts=10,  # Warehouse glitches usually resolve quickly
    )
)

carrier_dispatched = workflow.activity_method(
    shipping_activities.carrier_dispatched,
    start_to_close_timeout=timedelta(minutes=5),
    retry_policy=RetryPolicy(
        initial_interval=timedelta(seconds=5),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(minutes=1),
        maximum_attempts=5,  # External API, retry with backoff
    )
)

@workflow.defn(name="ShippingWorkflow")
class ShippingWorkflow:
    @workflow.run
    async def run(self, order_id: str) -> str:
        workflow.logger.info("shipping_workflow_started", order_id=order_id)

        # Step 1: Prepare Package
        try:
            box_id = await package_prepared(order_id)
        except Exception as e:
            workflow.logger.error("packaging_failed", error=str(e))
            raise

        # Step 2: Dispatch Carrier
        try:
            tracking_number = await carrier_dispatched(order_id, box_id)
            workflow.logger.info("shipping_completed", tracking_number=tracking_number)
            return tracking_number

        except Exception as e:
            # Signal parent workflow with failure reason
            workflow.logger.error("dispatch_failed_signaling_parent", error=str(e))
            
            parent = workflow.get_external_workflow_handle(
                workflow.info().parent_workflow_execution
            )
            
            # Note: Parent must have a signal handler for "ShippingFailed" if it intends to receive it.
            # If not, this signal will just be buffered or dropped depending on configuration.
            # We treat this as 'best effort' notification before failing.
            await parent.signal("ShippingFailed", str(e))
            
            # We still raise the exception so this child workflow marks as Failed.
            # The parent will catch ChildWorkflowFailure regardless of the signal.
            raise e
