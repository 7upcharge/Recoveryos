"""Gemini Client API wrapper for RecoveryOS Day 3.

Thin wrapper isolating the Gemini SDK calls. Configures structured response schema,
enforces timeouts, and converts SDK-level exceptions into GeminiUnavailableError.
"""

from __future__ import annotations

import json
import logging
import os
import concurrent.futures
from typing import Any

from app.core.diagnosis_schema import ALLOWED_DIAGNOSES

logger = logging.getLogger(__name__)


class GeminiUnavailableError(Exception):
    """Custom exception raised when Gemini API call fails, times out, or is unavailable."""

    def __init__(self, message: str, reason: str = "api_unavailable"):
        super().__init__(message)
        self.reason = reason


SYSTEM_PROMPT = f"""You are the RecoveryOS AI Payment Diagnosis Engine.
Your sole job is to analyze payment failure event histories and determine the likely root cause of the failure.

ALLOWED DIAGNOSIS CATEGORIES (You MUST strictly pick one of these exact values):
{sorted(list(ALLOWED_DIAGNOSES))}

STRICT EVIDENCE RULES:
1. You MUST NEVER invent or assume facts not explicitly present in the provided event history.
2. Example: If the event data does NOT contain explicit evidence of insufficient funds (e.g. error_code 'INSUFFICIENT_FUNDS' or specific bank message), you MUST NOT claim 'LOW_BALANCE' with high confidence.
3. If the available event history lacks conclusive evidence for a specific failure cause, you MUST select 'UNKNOWN' and explain that the event history does not contain enough data to determine the cause.
4. You MUST populate 'limitations' honestly, stating explicitly what facts or data were missing from the event log.
5. Provide a confidence score between 0.0 and 1.0 reflecting how strongly the event evidence supports your diagnosis.
"""


def _get_gemini_config() -> tuple[str, str, int]:
    """Retrieve Gemini API key, model name, and timeout from environment."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
    try:
        timeout_seconds = int(os.environ.get("GEMINI_TIMEOUT_SECONDS", "10"))
    except ValueError:
        timeout_seconds = 10

    if not api_key:
        raise GeminiUnavailableError(
            "GEMINI_API_KEY is not set in environment.", reason="api_unavailable"
        )

    return api_key, model_name, timeout_seconds


def _invoke_sdk(api_key: str, model_name: str, prompt_text: str) -> dict[str, Any]:
    """Invoke the Gemini SDK (google.genai or google.generativeai) for structured JSON output."""
    # Attempt using google.genai first (modern unified SDK)
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        
        response_schema = {
            "type": "OBJECT",
            "properties": {
                "diagnosis": {
                    "type": "STRING",
                    "enum": sorted(list(ALLOWED_DIAGNOSES)),
                },
                "confidence": {"type": "NUMBER"},
                "explanation": {"type": "STRING"},
                "evidence": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                },
                "limitations": {"type": "STRING"},
            },
            "required": ["diagnosis", "confidence", "explanation", "evidence", "limitations"],
        }

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=0.1,
        )

        response = client.models.generate_content(
            model=model_name,
            contents=prompt_text,
            config=config,
        )
        return json.loads(response.text)

    except ImportError:
        # Fallback to google.generativeai SDK if installed
        try:
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=SYSTEM_PROMPT,
                generation_config={"response_mime_type": "application/json", "temperature": 0.1},
            )
            response = model.generate_content(prompt_text)
            return json.loads(response.text)
        except ImportError as err:
            raise GeminiUnavailableError(
                "Neither 'google-genai' nor 'google-generativeai' library is installed.",
                reason="api_unavailable",
            ) from err
        except Exception as err:
            raise GeminiUnavailableError(
                f"Gemini API error: {err}", reason="api_unavailable"
            ) from err
    except json.JSONDecodeError as err:
        raise GeminiUnavailableError(
            f"Gemini returned invalid JSON: {err}", reason="malformed_output"
        ) from err
    except Exception as err:
        raise GeminiUnavailableError(
            f"Gemini SDK execution error: {err}", reason="api_unavailable"
        ) from err


def call_gemini_for_diagnosis(
    prompt_payload: dict[str, Any], timeout_seconds: int | None = None
) -> dict[str, Any]:
    """Send structured prompt to Gemini API with explicit timeout and error isolation.

    Args:
        prompt_payload: Serialized dictionary of DiagnosisInput data.
        timeout_seconds: Optional timeout override in seconds.

    Returns:
        Raw parsed dict response from Gemini.

    Raises:
        GeminiUnavailableError: If API call fails, times out, returns malformed JSON, or is unconfigured.
    """
    api_key, model_name, env_timeout = _get_gemini_config()
    effective_timeout = timeout_seconds if timeout_seconds is not None else env_timeout

    prompt_text = (
        "Analyze the following payment recovery case and event history to diagnose the root cause:\n\n"
        + json.dumps(prompt_payload, indent=2)
    )

    logger.debug("Calling Gemini API (model=%s, timeout=%ds)", model_name, effective_timeout)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_invoke_sdk, api_key, model_name, prompt_text)
        try:
            return future.result(timeout=effective_timeout)
        except concurrent.futures.TimeoutError as err:
            logger.warning("Gemini API call timed out after %d seconds.", effective_timeout)
            raise GeminiUnavailableError(
                f"Gemini API call timed out after {effective_timeout}s.", reason="timeout"
            ) from err
        except GeminiUnavailableError:
            raise
        except Exception as err:
            logger.warning("Gemini API call failed: %s", err)
            raise GeminiUnavailableError(
                f"Gemini API execution failure: {err}", reason="api_unavailable"
            ) from err
