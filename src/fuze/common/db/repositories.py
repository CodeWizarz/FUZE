from typing import Optional, List
from uuid import UUID
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from structlog import get_logger

from fuze.common.db.models import Order, Payment, Event

logger = get_logger()

class OrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_order(self, order: Order) -> Order:
        """Create a new order."""
        self.session.add(order)
        await self.session.flush()
        return order

    async def get_order_by_id(self, order_id: UUID) -> Optional[Order]:
        """Fetch order by ID."""
        result = await self.session.execute(select(Order).where(Order.id == order_id))
        return result.scalar_one_or_none()

    async def update_order_state(self, order_id: UUID, state: str, step: Optional[str] = None) -> Optional[Order]:
        """
        Update the state and current step of an order.
        Returns the updated order or None if not found.
        """
        values = {"state": state}
        if step is not None:
            values["current_step"] = step
            
        stmt = (
            update(Order)
            .where(Order.id == order_id)
            .values(**values)
            .returning(Order)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def set_error(self, order_id: UUID, error: str) -> Optional[Order]:
        """Record an error on the order."""
        stmt = (
            update(Order)
            .where(Order.id == order_id)
            .values(last_error=error)
            .returning(Order)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class PaymentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_payment_by_idempotency_key(self, key: str) -> Optional[Payment]:
        """
        Check if a payment with this idempotency key already exists.
        Critical for ensuring we don't double-charge.
        """
        result = await self.session.execute(select(Payment).where(Payment.idempotency_key == key))
        return result.scalar_one_or_none()

    async def create_payment(self, payment: Payment) -> Payment:
        """
        Record a new payment.
        Raises IntegrityError if idempotency key conflict occurs (race condition protection).
        """
        self.session.add(payment)
        await self.session.flush()
        return payment


class EventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def log_event(self, event: Event) -> Event:
        """Append an event to the audit log."""
        self.session.add(event)
        await self.session.flush()
        logger.info("event_logged", event_type=event.type, order_id=str(event.order_id))
        return event

    async def get_events_for_order(self, order_id: UUID) -> List[Event]:
        """Retrieve all events for a specific order."""
        result = await self.session.execute(
            select(Event).where(Event.order_id == order_id).order_by(Event.ts)
        )
        return list(result.scalars().all())
