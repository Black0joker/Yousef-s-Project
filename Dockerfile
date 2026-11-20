# SAGE — the Flask chat app plus the standalone Token Harbor client, in one image.
#
#   docker build -t sage .
#   docker run --rm -p 5000:5000 --env-file .env -v sage-data:/app/instance sage
#
# README.md's "Deployment" section lists every option and the env vars that matter.

FROM python:3.12-slim

# No .pyc files, logs straight to stdout, and the dev reloader off.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FLASK_DEBUG=0 \
    PORT=5000

WORKDIR /app

# Dependencies on their own layer so app edits do not reinstall them. gunicorn
# comes from requirements.txt so Nixpacks deploys and this image agree.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Never ship a real .env: it carries TH_API_KEY/JWT_SECRET. .dockerignore keeps
# it out of the context, and runtime config arrives through --env-file or -e.

# Run as root on purpose. Railway (like most platforms) bind-mounts volumes as
# root:root, so an unprivileged user cannot create the SQLite file inside
# /app/instance and the worker dies during import with "unable to open database
# file". Keeping a non-root runtime would need an entrypoint that chowns the
# mount and then drops privileges — which still starts as root. See README's
# "Deployment" section.
RUN mkdir -p /app/instance

# Conversations, memory, appointments, places, users, and logs all persist here.
# Railway does not support the Dockerfile VOLUME instruction. Attach a Railway
# Volume mounted at /app/instance in the service settings so the SQLite database
# and logs survive redeploys.
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '5000') + '/login', timeout=5)"]

# One worker with threads, on purpose: the active-stream registry
# (_ACTIVE_STREAMS), the shared gateway client, and the request-scoped SQLite
# connections all live in-process, so a second worker would break
# POST /api/chat/stop and split logins across processes. --timeout 0 because an
# SSE reply can stream for far longer than gunicorn's default 30s worker timeout.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-5000} --worker-class gthread --workers 1 --threads 8 --timeout 0 --access-logfile - --error-logfile - app:app"]
