"""Vercel serverless entry point.

Vercel's @vercel/python builder expects a WSGI-compatible `app` object
in this module. We import the Flask app created by our factory.
"""

from app import create_app

app = create_app()
