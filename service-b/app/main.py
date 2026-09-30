import asyncio

from fastapi import FastAPI

from sqlalchemy import text

from opentelemetry.instrumentation.fastapi import (
    FastAPIInstrumentor
)

from opentelemetry.instrumentation.sqlalchemy import (
    SQLAlchemyInstrumentor
)

from app.telemetry import configure_telemetry

from app.database import engine


tracer, logger = configure_telemetry(
    "service-b"
)


app = FastAPI(
    title="Inventory Service"
)


FastAPIInstrumentor.instrument_app(
    app
)

SQLAlchemyInstrumentor().instrument(
    engine=engine
)


@app.post("/inventory/reserve")
async def reserve_inventory(request: dict):

    items = request["items"]

    with tracer.start_as_current_span(
            "inventory.reserve"
    ) as reserve_span:

        reserve_span.set_attribute(
            "inventory.sku_count",
            len(items)
        )

        with tracer.start_as_current_span(
                "inventory.query"
        ):

            await asyncio.sleep(0.020)

            with engine.begin() as connection:

                for item in items:

                    result = connection.execute(
                        text("""
                             SELECT quantity
                             FROM inventory
                             WHERE sku = :sku
                                 FOR UPDATE
                             """),
                        {
                            "sku": item["sku"]
                        }
                    )

                    row = result.fetchone()

                    if row is None:

                        return {
                            "reserved": False,
                            "reason": (
                                f"SKU {item['sku']} "
                                "not found"
                            )
                        }

                    current_quantity = row[0]

                    if current_quantity < item["quantity"]:

                        return {
                            "reserved": False,
                            "reason": (
                                f"Insufficient stock "
                                f"for {item['sku']}"
                            )
                        }

                # ----------------------------
                # Disminuir inventario
                # ----------------------------

                for item in items:

                    connection.execute(
                        text("""
                             UPDATE inventory
                             SET quantity =
                                     quantity - :quantity
                             WHERE sku = :sku
                             """),
                        {
                            "sku": item["sku"],
                            "quantity": item["quantity"]
                        }
                    )

        logger.info(
            "Inventory successfully reserved"
        )

        return {
            "reserved": True
        }