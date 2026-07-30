FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && pip install -v --no-cache-dir --retries 5 --timeout 120 -r requirements.txt
COPY . .
RUN addgroup --system app && adduser --system --ingroup app app && chown -R app:app /app
EXPOSE 8001
USER app
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1', 8001)); s.close()"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
