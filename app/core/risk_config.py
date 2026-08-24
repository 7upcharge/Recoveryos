"""Configurable risk detection thresholds.

All thresholds are loaded from environment variables at import time, with
sensible defaults. This is the single source of truth for every numeric
threshold used by the rule functions in risk_detector.py — no threshold
is ever hardcoded inside a rule function.

Environment Variables:
    DELAYED_AUTH_THRESHOLD_SECONDS:
        Maximum acceptable seconds between a payment.failed (or first event)
        and payment.authorized before R1 fires. Default: 90

    REPEATED_FAILURE_WINDOW_MINUTES:
        The sliding window width (in minutes) within which repeated
        payment.failed events are counted for R3. Default: 10

    REPEATED_FAILURE_COUNT:
        Minimum number of payment.failed events within the sliding window
        required for R3 to fire. Default: 3

    MIN_ATTEMPTS_FOR_INTENT:
        Minimum number of distinct failed/authorized attempts on an order
        for R4 to consider intent signal. Default: 2
"""

import os

DELAYED_AUTH_THRESHOLD_SECONDS = int(os.getenv("DELAYED_AUTH_THRESHOLD_SECONDS", "90"))
REPEATED_FAILURE_WINDOW_MINUTES = int(os.getenv("REPEATED_FAILURE_WINDOW_MINUTES", "10"))
REPEATED_FAILURE_COUNT = int(os.getenv("REPEATED_FAILURE_COUNT", "3"))
MIN_ATTEMPTS_FOR_INTENT = int(os.getenv("MIN_ATTEMPTS_FOR_INTENT", "2"))
