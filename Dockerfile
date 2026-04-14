FROM python:3.11-slim

WORKDIR /app

# Install system deps for pandas/tigeropen
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libssl-dev \
    libffi-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps (cached layer)
COPY requirements_docker.txt /app/requirements_docker.txt
COPY sync/requirements.txt /app/sync/requirements.txt
RUN pip install --no-cache-dir \
    -r requirements_docker.txt \
    -r sync/requirements.txt

# Copy source
COPY . .

# Credentials and state live here (mount from host)
VOLUME ["/root/.kairos-agent"]

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

EXPOSE 7432

CMD ["python3", "app_docker.py"]
