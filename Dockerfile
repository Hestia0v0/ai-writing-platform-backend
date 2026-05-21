FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

ARG SERVICE_DIR
ARG APP_MODULE=main:app
ARG PORT=8000

WORKDIR /app

RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY ${SERVICE_DIR}/requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ \
    && pip install -r /tmp/requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

COPY ${SERVICE_DIR}/ /app/

ENV APP_MODULE=${APP_MODULE} \
    PORT=${PORT}

EXPOSE 8000 8001 8002

CMD ["sh", "-c", "uvicorn ${APP_MODULE} --host 0.0.0.0 --port ${PORT}"]
