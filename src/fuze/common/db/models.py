from datetime import datetime
from typing import Optional, Any
from uuid import UUID, uuid4

from sqlalchemy import String, TIMESTAMP, Integer, JSON, ForeignKey, func, text, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import AsyncAttrs

class Base(AsyncAttrs, DeclarativeBase):
    pass

class Order(Base):
    """
    Represents a customer order in the system.
    
    The 'state' field tracks the high-level business status (e.g., CREATED, PROCESSING, COMPLETED).
    The 'current_step' tracks detailed workflow progress (e.g., 'validating_payment', 'checking_inventory').
    """
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    state: Mapped[str] = mapped_column(String(50), nullable=False, index=True, default="CREATED")
    
    # Store complex address data as JSONB (Postgres) or JSON
    address_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    
    current_step: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), 
        server_default=func.now(), 
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), 
        server_default=func.now(), 
        onupdate=func.now(), 
        nullable=False
    )
    
    # Relationships
    payments: Mapped[list["Payment"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    events: Mapped[list["Event"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class Payment(Base):
    """
    Represents a payment transaction.
    
    Idempotency Guarantee:
    - The 'idempotency_key' is unique.
    - Before charging, the system checks if a payment with this key exists.
    - If it exists, we return the existing result instead of charging again.
    """
    __tablename__ = "payments"

    payment_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    
    status: Mapped[str] = mapped_column(String(50), nullable=False) # SUCCESS, FAILED, PENDING
    amount: Mapped[int] = mapped_column(Integer, nullable=False) # In cents
    
    # Critical for ensuring we don't double-charge
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), 
        server_default=func.now(), 
        nullable=False
    )

    # Relationships
    order: Mapped["Order"] = relationship(back_populates="payments")


class Event(Base):
    """
    Immutable audit log of system events.
    
    Used for debugging, audit trails, and potentially event sourcing patterns.
    """
    __tablename__ = "events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    
    ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), 
        server_default=func.now(), 
        nullable=False
    )

    # Relationships
    order: Mapped["Order"] = relationship(back_populates="events")
