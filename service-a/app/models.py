from pydantic import BaseModel
from typing import List


class OrderItem(BaseModel):

    sku: str
    quantity: int
    unit_price: float


class CreateOrderRequest(BaseModel):

    items: List[OrderItem]
    payment_provider: str = "mock-payment"