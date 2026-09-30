from opentelemetry import trace
from sqlalchemy import text

from app.database import engine

tracer = trace.get_tracer("service-a")


def persist_order(order_id, items, total_amount):
    with tracer.start_as_current_span(
            "order.persist"
    ) as span:
        # Atributo solicitado por la actividad
        span.set_attribute(
            "db.system",
            "postgresql"
        )

        with engine.begin() as connection:
            # 1. Guardar la orden
            connection.execute(
                text("""
                     INSERT INTO orders (id,
                                         total_amount)
                     VALUES (:id,
                             :total)
                     """),
                {
                    "id": order_id,
                    "total": total_amount
                }
            )

            # 2. Guardar los productos de la orden
            for item in items:
                connection.execute(
                    text("""
                         INSERT INTO order_items (order_id,
                                                  sku,
                                                  quantity,
                                                  unit_price)
                         VALUES (:order_id,
                                 :sku,
                                 :quantity,
                                 :unit_price)
                         """),
                    {
                        "order_id": order_id,
                        "sku": item.sku,
                        "quantity": item.quantity,
                        "unit_price": item.unit_price
                    }
                )
