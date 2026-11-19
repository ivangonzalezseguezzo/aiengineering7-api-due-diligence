FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY due_diligence ./due_diligence
COPY scripts ./scripts

EXPOSE 8000
# La misma imagen corre la API (por defecto) y el worker (docker-compose.yml cambia el comando).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
