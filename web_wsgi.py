# web_wsgi.py — Gunicorn entrypoint for courtflow-web (render.yaml: web_wsgi:app).
#
# Thin shim so the start command `gunicorn web_wsgi:app` resolves the Flask app.
# No DB, no boot DDL — this service is pure static/host-switched serving.

from web_app import app  # noqa: F401

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5060)), debug=False)
