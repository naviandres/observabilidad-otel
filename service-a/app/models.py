from pydantic import BaseModel
from typing import List


class OrderItem(BaseModel):

    sku: str = "LAPTOP-001"
    quantity: int = 1
    unit_price: float = 2500000


class CreateOrderRequest(BaseModel):

    items: List[OrderItem]
    payment_provider: str = "mock-payment"