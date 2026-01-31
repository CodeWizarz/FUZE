from uuid import UUID
from datetime import timedelta
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from fuze.common.db.session import get_db_session
from fuze.common.db.models import Order, Payment, Event
from fuze.common.db.repositories import OrderRepository, PaymentRepository, EventRepository

# -----------------------------------------------------------------------------
# Recommended Retry Policies (for Workflows to use):
# -----------------------------------------------------------------------------
# order_received:    Retry many times (DB might be temporary down).
# order_validated:   Retry many times (transient DB/Service issues).
# payment_charged:   Retry INFINITELY if idempotency key is used.
#                    NonRetryable: "InsufficientFunds" logic (if implemented).
# -----------------------------------------------------------------------------

@activity.defn
async def order_received(order_id: str, address: dict[str, Any]) -> str:
    """
    Persist initial order state. 
    Ideally called immediately after starting workflow to ensure DB record exists.
    """
    activity.logger.info("activity_started", activity="order_received", order_id=order_id)
    
    async with get_db_session() as session:
        repo = OrderRepository(session)
        event_repo = EventRepository(session)
        
        # Check if exists first (simple idempotency for creation)
        existing = await repo.get_order_by_id(UUID(order_id))
        if existing:
            activity.logger.info("order_already_exists", order_id=order_id)
            return "skipped"

        new_order = Order(
            id=UUID(order_id),
            state="CREATED",
            address_json=address,
            current_step="order_received"
        )
        await repo.create_order(new_order)
        
        await event_repo.log_event(Event(
            order_id=UUID(order_id),
            type="ORDER_CREATED",
            payload_json={"address": address}
        ))
    
    activity.logger.info("activity_completed", activity="order_received")
    return "created"


@activity.defn
async def order_validated(order_id: str) -> bool:
    """
    Validate the order (mock rule: address must have 'zip_code').
    """
    activity.logger.info("activity_started", activity="order_validated", order_id=order_id)
    
    async with get_db_session() as session:
        repo = OrderRepository(session)
        event_repo = EventRepository(session)
        
        order = await repo.get_order_by_id(UUID(order_id))
        if not order:
            # This is a critical failure. Order should exist.
            # We raise non-retryable because retrying won't fix missing data if it was supposed to be there.
            raise ApplicationError(f"Order {order_id} not found", non_retryable=True)
            
        # Log state change start
        await repo.update_order_state(order.id, state=order.state, step="validating")

        # --- Validation Logic ---
        address = order.address_json or {}
        if "zip_code" not in address:
            # Validation failure.
            await repo.set_error(order.id, "Missing zip_code")
            await event_repo.log_event(Event(
                order_id=order.id, type="VALIDATION_FAILED", payload_json={"reason": "Missing zip_code"}
            ))
            # Raise exception to trigger workflow compensation or failure.
            # It's an ApplicationError because it's a business logic failure, 
            # likely should be handled by workflow (try/catch) or considered fatal.
            raise ApplicationError("Validation failed: Missing zip_code", non_retryable=True)
        # ------------------------

        await repo.update_order_state(order.id, state="VALIDATED", step="validation_complete")
        await event_repo.log_event(Event(
            order_id=order.id, type="VALIDATION_SUCCESS"
        ))
        
    activity.logger.info("activity_completed", activity="order_validated")
    return True


@activity.defn
async def payment_charged(order_id: str, amount_cents: int, payment_token: str) -> str:
    """
    Charge the customer.
    Implements STRICT idempotency using 'payment_token' as the key.
    """
    activity.logger.info("activity_started", activity="payment_charged", order_id=order_id)
    
    async with get_db_session() as session:
        payment_repo = PaymentRepository(session)
        order_repo = OrderRepository(session)
        event_repo = EventRepository(session)

        # 1. Idempotency Check (SELECT before INSERT)
        # We use the payment_token (e.g. from upstream) as our idempotency key.
        existing_payment = await payment_repo.get_payment_by_idempotency_key(payment_token)
        if existing_payment:
            activity.logger.info("payment_idempotency_hit", key=payment_token)
            if existing_payment.status == "SUCCESS":
                return str(existing_payment.payment_id)
            elif existing_payment.status == "FAILED":
                # Previous attempt failed. We might decide to retry logic OR fail immediately.
                # Here we assume a record means a final terminal state for THAT key.
                raise ApplicationError(f"Payment previously failed for key {payment_token}", non_retryable=True)

        # 2. Update Order State
        await order_repo.update_order_state(UUID(order_id), state="CHARGING", step="processing_payment")

        # 3. Simulate External Payment Gateway Call
        # Here we would call Stripe/PayPal.
        # IF this fails with NetworkError -> Retry (Activity Retry Policy covers this)
        # IF this fails with Declined -> Raise ApplicationError(NonRetryable)
        
        # ... (Simulation) ...
        # For demo purposes, we assume success.
        
        # 4. Record Payment
        new_payment = Payment(
            order_id=UUID(order_id),
            amount=amount_cents,
            status="SUCCESS",
            idempotency_key=payment_token
        )
        await payment_repo.create_payment(new_payment)
        
        # 5. Update Order
        await order_repo.update_order_state(UUID(order_id), state="PAID", step="payment_complete")
        await event_repo.log_event(Event(
            order_id=UUID(order_id), 
            type="PAYMENT_PROCESSED", 
            payload_json={"amount": amount_cents, "payment_id": str(new_payment.payment_id)}
        ))

        return str(new_payment.payment_id)
