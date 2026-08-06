# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

# Device Simple Example - Makefile
# Run from examples/simple directory

.PHONY: help install test run docker-build docker-run docker-stop clean

# Default target
help:
	@echo "Device Simple Example - Makefile"
	@echo ""
	@echo "Usage:"
	@echo "  make install        Install SDK and dependencies in local venv"
	@echo "  make test           Run unit tests"
	@echo "  make run            Run device-simple service locally"
	@echo "  make docker-build   Build Docker image"
	@echo "  make docker-run     Run with docker-compose (requires MQTT broker)"
	@echo "  make docker-stop    Stop docker-compose services"
	@echo "  make clean          Remove build artifacts"

# Install SDK and dependencies in local venv
install:
	@echo "Installing device-sdk-py in development mode..."
	cd ../.. && python -m pip install -e .

# Path to virtual environment Python (relative to project root after cd ../..)
VENV_PYTHON := .venv/bin/python

# Run unit tests
test:
	@echo "Running unit tests..."
	cd ../.. && $(VENV_PYTHON) -m unittest discover tests -v

# Run device-simple service locally
run:
	@echo "Starting device-simple service..."
	@echo "Prerequisites: MQTT broker on localhost:1883"
	cd ../.. && $(VENV_PYTHON) -m device_service

# Build Docker image
docker-build:
	@echo "Building Docker image..."
	docker build -f Dockerfile --build-context root=../.. -t device-simple:latest .

# Run with docker-compose (no build)
docker-run:
	@echo "Starting services with docker-compose..."
	docker compose -f docker-compose.yaml up

# Stop docker-compose services
docker-stop:
	@echo "Stopping docker-compose services..."
	docker compose -f docker-compose.yaml down

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	rm -rf __pycache__ .pytest_cache .coverage htmlcov
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true