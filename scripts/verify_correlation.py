import json
import urllib.request
import urllib.error
import sys

def test_flow():
    print("Enviando petición de prueba entre microservicios...")
    url = "http://localhost:8000/orders"
    
    # Payload ajustado al modelo CreateOrderRequest de service-a
    data = json.dumps({
        "items": [
            {
                "product_id": "LAPTOP-001",
                "quantity": 1,
                "unit_price": 1500.00
            }
        ],
        "payment_provider": "pse"
    }).encode('utf-8')
    
    req = urllib.request.Request(
        url, 
        data=data, 
        headers={'Content-Type': 'application/json'}, 
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            body = response.read().decode('utf-8')
            print(f"✅ Respuesta recibida (Código {response.getcode()})")
            print(f"Cuerpo: {body}")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        print(f"⚠️ Error HTTP: {e.code} - {e.reason}")
        print(f"Detalle: {error_body}")
    except urllib.error.URLError as e:
        print(f"❌ Error al conectar con service-a: {e.reason}")

if __name__ == "__main__":
    test_flow()