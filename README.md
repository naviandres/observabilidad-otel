descargar dependencias
pip install -r requirements.txt

ejecutar
app.main:app --app-dir service-a --host 0.0.0.0 --port 8000
app.main:app --app-dir service-b --host 0.0.0.0 --port 8001

docs api
http://localhost:8000/docs

metricas
service-a->
http://localhost:8000/metrics

service-b->
http://localhost:8001/metrics