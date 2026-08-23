# Optional container build (works on Render "Docker" runtime, Fly, Railway, any host).
# The native Python path via render.yaml is simpler; use this if you prefer Docker
# or want real web push (uncomment pywebpush).
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 JETTIP_DB=/var/data/jettip.db SCHEDULER_ENABLED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Real web push (optional): uncomment the next line
# RUN pip install --no-cache-dir pywebpush

COPY . .
RUN mkdir -p /var/data

# $PORT is provided by the host; default 8077 for local `docker run`.
ENV PORT=8077
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
