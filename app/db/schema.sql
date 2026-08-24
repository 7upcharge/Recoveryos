-- RecoveryOS Day 1 Schema
-- Only three tables: webhook_events, payments, audit_events

CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    razorpay_event_id TEXT UNIQUE,
    event_type TEXT NOT NULL,
    payment_id TEXT,
    order_id TEXT,
    payload_json TEXT NOT NULL,
    received_at TEXT NOT NULL,
    event_created_at TEXT,
    signature_verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY,
    order_id TEXT,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    status TEXT NOT NULL,
    method TEXT,
    bank TEXT,
    first_attempt_at TEXT,
    last_status_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    is_disputed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER,
    timestamp TEXT NOT NULL,
    stage TEXT NOT NULL,
    actor TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail_json TEXT
);

-- Day 2: Recovery cases opened by the Risk Detector.
-- risk_rule stores comma-joined list of fired rules (e.g. "R1_DELAYED_AUTH,R3_REPEATED_FAILURES").
CREATE TABLE IF NOT EXISTS recovery_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id TEXT NOT NULL REFERENCES payments(payment_id),
    opened_at TEXT NOT NULL,
    risk_rule TEXT NOT NULL,
    risk_score INTEGER NOT NULL,
    risk_reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    closed_at TEXT
);

-- Day 3: Diagnoses table stored by Gemini Diagnoser.
CREATE TABLE IF NOT EXISTS diagnoses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES recovery_cases(id),
    diagnosed_at TEXT NOT NULL,
    likely_cause TEXT NOT NULL,
    confidence REAL NOT NULL,
    explanation TEXT NOT NULL,
    evidence_json TEXT,
    limitations TEXT,
    raw_model_output TEXT,
    model_name TEXT NOT NULL,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    fallback_reason TEXT
);

-- Day 4: Proposed actions table stored by Recovery Proposer.
CREATE TABLE IF NOT EXISTS proposed_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES recovery_cases(id),
    diagnosis_id INTEGER NOT NULL REFERENCES diagnoses(id),
    proposed_action TEXT NOT NULL,
    reason TEXT NOT NULL,
    diagnosis_used TEXT NOT NULL,
    diagnosis_confidence REAL NOT NULL,
    created_at TEXT NOT NULL,
    model_name TEXT,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    fallback_reason TEXT,
    status TEXT NOT NULL DEFAULT 'PROPOSED'
);


