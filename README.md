# Fuze: Temporal + Python Production Boilerplate

High-performance, durable order processing system built with Python, Temporal, and PostgreSQL.

## Architecture

This system uses a **Durable Execution** pattern where state is managed by Temporal Workflows, while business data is persisted transactionally in PostgreSQL.

### Core Components

1.  **Temporal Workers (`fuze.worker`)**: The brain of the system.
    - Executes Workflows (`OrderWorkflow`, `ShippingWorkflow`).
    - Executes Activities (Side-effects like DB writes, API calls).
    - Ensures automatic retries, timeouts, and state recovery.
2.  **FastAPI Service (`fuze.api`)**: The gateway.
    - Exposes REST endpoints to start workflows and send signals.
    - Queries both DB (historical state) and Temporal (live state) for status.
3.  **PostgreSQL**: Single source of truth for business data.
    - Tables: `orders`, `payments`, `events`.
    - Strict schemas with Alembic migrations.

### Why Temporal?

For complex flows like Order Fulfillment, traditional queue-based systems (Celery/SQS) suffer from "Status Column Hell"—you end up managing hundreds of state flags and race conditions manually.

Temporal solves this by:
- **Durability**: If the worker crashes mid-workflow, it resumes exactly where it left off.
- **Visible State**: The code *is* the diagram.
- **Built-in Retries**: Network blips are handled automatically without custom retry logic.
- **Cancellation**: Systematic cancellation propagation down to child workflows and activities.

## Features

- **Idempotency Strategy**:
    - **Payment**: Uses strict token-based idempotency. The `payment_charged` activity checks if a `payment_token` exists in the DB before charging. If found, it returns the *existing* result, preventing double-charges even if the Activity retries 10,000 times.
- **Structured Logging**: JSON logs (via `structlog`) with trace propagation (`workflow_id`, `run_id`).
- **Graceful Shutdown**: Workers drain active tasks on SIGTERM.
- **Two-Phase Status**: API returns a hybrid view of durable DB state + live Workflow memory state.

## Getting Started

### Prerequisites
- Docker & Docker Compose
- Python 3.10+

### 1. Start Infrastructure
```bash
docker-compose up -d
```
This spins up Temporal and PostgreSQL. Wait about 30s for Temporal to initialize.

### 2. Setup Application
```bash
# Install dependencies
pip install -e .[dev]

# Run Migrations
alembic upgrade head
```

### 3. Run Components (In separate terminals)

**Terminal A: The Worker**
```bash
python -m fuze.worker.main
```
*Watch the JSON logs for startup info.*

**Terminal B: The API**
```bash
uvicorn fuze.api.app:app --reload
```
*Available at http://127.0.0.1:8000*

## Usage Guide

### Start an Order
```bash
curl -X POST http://127.0.0.1:8000/orders/123e4567-e89b-12d3-a456-426614174000/start \
  -H "Content-Type: application/json" \
  -d '{"order_id": "123e4567-e89b-12d3-a456-426614174000", "address": {"zip_code": "10001"}}'
```

### Approve Order (Signal)
The workflow pauses after validation to wait for approval.
```bash
curl -X POST http://127.0.0.1:8000/orders/123e4567-e89b-12d3-a456-426614174000/signals/approve
```

### Check Status
```bash
curl http://127.0.0.1:8000/orders/123e4567-e89b-12d3-a456-426614174000/status
```

## Failure Scenarios & Recovery

1.  **DB Outage**: Activities like `order_received` will fail. Temporal retries them indefinitely (as configured). Once DB is up, processing resumes.
2.  **Worker Crash**: Temporal server holds the open workflow task. When a new worker instance starts, it picks up the task and replays history to restore memory state.
3.  **Validation Failure**: The `validate_order` checks input rules. If failed, it raises a `NonRetryable` error, instantly failing the workflow without useless retries.
4.  **Shipping API Down**: The `ShippingWorkflow` stays in progress. Parent `OrderWorkflow` waits. When API recovers, the activity completes.
