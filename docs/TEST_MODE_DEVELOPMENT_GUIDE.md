# RecoveryOS — Live Razorpay Test Mode Development & Tunneling Guide

This guide describes how to configure, start, and verify a real **Razorpay TEST MODE** payment workflow locally with RecoveryOS using a public HTTPS tunnel (e.g. ngrok or Cloudflare Tunnel).

> [!IMPORTANT]
> **Strict Security Directives**:
> - **TEST MODE Only**: Never use live/production payment credentials.
> - **Strict Signature Enforcement**: `RAZORPAY_WEBHOOK_SECRET` is required. Webhook signature verification is **never** bypassed or weakened.
> - **No Secrets in Code/Git**: Store all keys strictly in your local `.env` file.

---

## 1. Local Environment Setup (`.env`)

Copy `.env.example` to `.env` in the project root:
```bash
cp .env.example .env
```

Set your local environment variables in `.env`:
```env
# Razorpay Test Mode Credentials (obtained from Razorpay Dashboard -> Settings -> API Keys)
RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxx────────

# Razorpay Webhook Secret (configured in Razorpay Dashboard -> Settings -> Webhooks)
RAZORPAY_WEBHOOK_SECRET=your_test_webhook_secret_here

# Local SQLite Database Path
DATABASE_PATH=./recoveryos.db
FLASK_ENV=development

# Gemini AI Diagnosis Credentials (Day 3)
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash

# Optional: Your Public Tunnel Base URL (set once tunnel is launched)
PUBLIC_WEBHOOK_BASE_URL=https://your-subdomain.ngrok-free.app
```

---

## 2. Start Local RecoveryOS Server

Run the local Flask application:
```bash
python run.py
```
*(Runs by default on `http://127.0.0.1:5000`)*.

Verify local health check:
```bash
curl http://localhost:5000/api/health
```

---

## 3. Expose Localhost via Public HTTPS Tunnel

Razorpay webhooks require a publicly accessible **HTTPS** endpoint. Use either ngrok or Cloudflare Tunnel:

### Option A: ngrok (Recommended)
```bash
ngrok http 5000
```
Output example:
```
Forwarding https://abc1234.ngrok-free.app -> http://localhost:5000
```

### Option B: Cloudflare Tunnel
```bash
cloudflared tunnel --url http://localhost:5000
```

Copy the generated public HTTPS URL (e.g. `https://abc1234.ngrok-free.app`) and update `PUBLIC_WEBHOOK_BASE_URL` in your `.env` file.

---

## 4. Configure Razorpay Webhook in Razorpay Dashboard

1. Log into your **Razorpay Dashboard**.
2. Ensure the top toggle is set to **TEST MODE** (amber badge).
3. Navigate to **Account & Settings** -> **Webhooks** -> **Add New Webhook**.
4. Set **Webhook URL**:
   ```
   https://<your-tunnel-url>/webhooks/razorpay
   ```
   *(e.g., `https://abc1234.ngrok-free.app/webhooks/razorpay`)*
5. Set **Secret**: Enter the exact secret string defined in your `.env` file (`RAZORPAY_WEBHOOK_SECRET`).
6. Select **Active Events**:
   - `payment.failed`
   - `payment.authorized`
   - `payment.captured`
   - `order.paid`
7. Click **Save Webhook**.

---

## 5. Trigger a Test Payment

1. Create a Test Payment via Razorpay Test Checkout, Payment Links, or API.
2. In Test Mode checkout, select a failing payment method (e.g., failure test cards or bank failure responses) or simulate delayed authorization.
3. Razorpay will automatically dispatch a signed webhook payload to your tunnel URL (`https://<your-tunnel-url>/webhooks/razorpay`).

---

## 6. Inspect Results in Control Center Dashboard

Open your browser to:
```
http://localhost:5000/
```

Observability steps:
1. **Live Webhook Card**: Confirm `Webhooks Received in SQLite` incremented and latest event type appears.
2. **Pipeline Stage Flow**: Watch count move from `Payments` to `At Risk`.
3. **Cases Table**: Click on the opened recovery case (e.g. `#1`).
4. **Case Detail Page**:
   - **Payment Overview**: Verify amount, status, order ID.
   - **Event Timeline**: Verify chronological `payment.failed` / `payment.authorized` steps.
   - **Risk Evaluation**: Check risk score (e.g. `60 / 100`) and triggered rules (e.g. `R3_REPEATED_FAILURES`).
   - **Audit Log Trail**: View full event ingestion & detection logs.

---

## 7. Run AI Diagnosis & Proposal Pipeline

To run Day 3 AI Diagnosis on open test cases:
```bash
python scripts/run_diagnoser_on_case.py <case_id>
```

To run Day 4 Recovery Proposal:
```bash
python scripts/run_proposer_on_case.py <case_id>
```

Refresh `http://localhost:5000/cases/<case_id>` to view the AI Diagnosis (`AI-generated` purple badge) and Proposed Action (`PROPOSED` purple badge).

---

## 8. Resetting Local Test Database

To clear test/demo data cleanly before a fresh verification run:
```bash
python scripts/seed_demo.py --reset
```
*(Clears synthetic demo and test events without touching application configuration)*.
