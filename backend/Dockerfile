FROM python:3.11.9-slim
RUN apt-get update -qq && apt-get install -y -qq cron && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
