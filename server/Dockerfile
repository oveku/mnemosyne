FROM python:3.12-slim

WORKDIR /app
COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app/ /app/

ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/server.py"]
