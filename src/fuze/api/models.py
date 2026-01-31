from typing import Any, Dict, Optional
from uuid import UUID
from pydantic import BaseModel, Field

class OrderStartRequest(BaseModel):
    order_id: UUID = Field(..., description="Unique Order ID")
    address: Dict[str, Any] = Field(..., description="Shipping address")

class AddressUpdateRequest(BaseModel):
    new_address: Dict[str, Any] = Field(..., description="New shipping address")

class OrderStatusResponse(BaseModel):
    order_id: UUID
    db_state: str = Field(..., description="High-level state from DB")
    db_step: Optional[str] = Field(None, description="Granular step from DB")
    
    workflow_step: Optional[str] = Field(None, description="Step reported by Workflow Query")
    workflow_last_error: Optional[str] = Field(None, description="Last error reported by Workflow")
    
    is_running: bool
    retries: int = 0
