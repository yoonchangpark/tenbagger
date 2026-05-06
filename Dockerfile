FROM python:3.11-slim

RUN apt-get update -qq && apt-get install -y -qq libpq-dev gcc && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

WORKDIR /app/backend

CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT
