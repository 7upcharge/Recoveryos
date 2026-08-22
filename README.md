# RecoveryOS

**AI-powered revenue recovery system for the Razorpay AI Buildathon 2026.**

RecoveryOS is a 12-day incremental build. This repository currently implements **Day 1: Project Scaffold + Webhook Ingestion**.

## What Day 1 Implements

- **Webhook ingestion endpoint**: `POST /webhooks/razorpay`
- **HMAC-SHA256 signature verification** against Razorpay's webhook secret
- **Idempotent event processing** — duplicate deliveries return 200 without side effects
- **Raw event storage** — immutable `webhook_events` table for replay/audit
- **Payment state normalization** — `payments` table updated from webhook data
- **Audit logging** — every ingested event creates an `audit_events` entry

### Supported Event Types

- `payment.created`
- `payment.authorized`
- `payment.captured`
- `payment.failed`
- `order.paid`

Unknown-but-signed events are stored without inventing payment state.

## Installation

```bash
# Clone and enter the project
cd recoveryos

# Create a virtual environment (recommended)
python -m venv venv
venv\Scripts\activate    # Windows
# source venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt
```

## Environment Configuration

Copy the example environment file and edit it:

```bash
copy .env.example .env
```

Set your Razorpay webhook secret:

```
RAZORPAY_WEBHOOK_SECRET=your_actual_webhook_secret
DATABASE_PATH=./recoveryos.db
FLASK_ENV=development
```

> **⚠️ The application will not start without `RAZORPAY_WEBHOOK_SECRET` set.**

## Database Initialization

```bash
python scripts/init_db.py
```

This creates the SQLite database with three tables:
- `webhook_events` — raw webhook event storage
- `payments` — normalized payment state
- `audit_events` — audit log

The script is idempotent — safe to run multiple times.

## Running Flask

```bash
python run.py
```

The development server starts on `http://localhost:5000`.

## Running Tests

```bash
pytest tests/ -v
```

Tests use isolated in-memory databases — no setup required.

## Testing the Webhook Endpoint

Generate a test signature and send a webhook:

```bash
# PowerShell example
$secret = "your_test_webhook_secret"
$body = '{"entity":"event","account_id":"acc_Test","event":"payment.failed","contains":["payment"],"id":"evt_Test001","created_at":1724300000,"payload":{"payment":{"entity":{"id":"pay_Test001","entity":"payment","amount":50000,"currency":"INR","status":"failed","order_id":"order_Test001","method":"upi","bank":null}}}}'

# Compute HMAC-SHA256 signature
$hmac = New-Object System.Security.Cryptography.HMACSHA256
$hmac.Key = [Text.Encoding]::UTF8.GetBytes($secret)
$sig = [BitConverter]::ToString($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($body))).Replace("-","").ToLower()

# Send the webhook
Invoke-RestMethod -Uri http://localhost:5000/webhooks/razorpay -Method POST -Body $body -ContentType "application/json" -Headers @{"X-Razorpay-Signature"=$sig}
```

Or with Python:

```python
import hmac, hashlib, json, requests

secret = "your_test_webhook_secret"
payload = {"entity": "event", "event": "payment.failed", "id": "evt_Test001",
           "payload": {"payment": {"entity": {"id": "pay_001", "amount": 50000,
           "currency": "INR", "status": "failed"}}}}
body = json.dumps(payload)
sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()

resp = requests.post("http://localhost:5000/webhooks/razorpay",
                     data=body, headers={"Content-Type": "application/json",
                                          "X-Razorpay-Signature": sig})
print(resp.status_code, resp.json())
```

## Project Structure

```
recoveryos/
├── app/
│   ├── __init__.py          # Flask application factory
│   ├── config.py            # Environment configuration
│   ├── webhooks/
│   │   ├── routes.py        # POST /webhooks/razorpay endpoint
│   │   └── signature.py     # HMAC-SHA256 verification
│   └── db/
│       ├── schema.sql       # Day 1 database schema
│       ├── database.py      # Connection + init helpers
│       └── repositories.py  # Data access functions
├── tests/
│   ├── conftest.py          # Shared fixtures
│   ├── test_webhook_signature.py
│   └── test_webhook_ingestion.py
├── scripts/
│   └── init_db.py           # Database initialization CLI
├── fixtures/
│   └── sample_webhook_payloads.json
├── requirements.txt
├── .env.example
├── run.py                   # Development server entry point
└── README.md
```

## Stack

- Python 3.11+
- Flask
- SQLite (raw `sqlite3`, no ORM)
- `python-dotenv`
- `pytest`
