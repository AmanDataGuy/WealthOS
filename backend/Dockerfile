# Dockerfile - the API (and the price worker, same image). Build context: backend/
FROM python:3.11-slim

WORKDIR /app
# LibreOffice Calc recalculates users' Excel templates (features/excel.py); headless, no GUI packages
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-calc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt
COPY app ./app

ENV PYTHONIOENCODING=utf-8
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
