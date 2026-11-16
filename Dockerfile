FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# El código se copia como fallback, pero en compose se monta como volumen (./:/app)
# para que editar tools/ solo requiera reiniciar, no reconstruir.
COPY . .

CMD ["python", "server.py"]
