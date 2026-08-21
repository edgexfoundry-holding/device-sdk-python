# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

# Device Simple Example - Dockerfile
# Build with: docker build -f examples/simple/Dockerfile --build-context root=. -t device-simple .
# From repository root.

FROM python:3.12-alpine3.20

# TODO: Change Copyright to your company if open sourcing or remove label
LABEL license='SPDX-License-Identifier: Apache-2.0' \
  copyright='Copyright (c) 2026: YIQISOFT'

RUN apk add --update --no-cache \
    gcc \
    musl-dev \
    libpq-dev

# Ensure using latest versions of all installed packages to avoid any recent CVEs
RUN apk --no-cache upgrade

# Must specify root to the parent directory of device-sdk-python source folder with --build-context argument
# when running docker build command
COPY --from=root / /repo

# Install SDK from repo root (where pyproject.toml expects package_dir = "src")
# Optionally set a pip index mirror appropriate for your region, e.g.:
# RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir /repo

# Copy example service and resources
COPY ./res ./res
COPY ./device_service.py .

# Create non-root user
RUN addgroup -S edgex && adduser -S edgex -G edgex
RUN chown -R edgex:edgex /res /device_service.py
USER edgex

# Environment variables (overridden by docker-compose / EdgeX compose)
ENV PYTHONPATH=/usr/local/lib/python3.12/site-packages \
    EDGEX_MESSAGEBUS_HOST=127.0.0.1 \
    EDGEX_MESSAGEBUS_PORT=1883 \
    EDGEX_MESSAGEBUS_TOPIC=edgex

# Expose default device service port
EXPOSE 59990

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:59990/api/v3/ping', timeout=3)" || exit 1

# Default command runs the simple service
CMD ["python", "./device_service.py"]