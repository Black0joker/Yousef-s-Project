# SAGE — the Flask chat app plus the standalone Token Harbor client, in one image.
#
#   docker build -t sage .
#   docker run --rm -p 5000:5000 --env-file .env -v sage-data:/app/instance sage
#
# The "Docker" section of README.md lists every option and the env vars that matter.

FROM python:3.12-slim

# No .pyc files, logs straight to stdout, and the dev reloader off.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FLASK_DEBUG=0 \
    PORT=5000

WORKDIR /app

# gunicorn is installed here rather than in requirements.txt because it cannot
# run on Windows, which is the local dev platform. Dependencies are copied on
# their own so app edits do not reinstall them.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt "gunicorn>=22,<24"

COPY . .

# Never ship a real .env: it carries TH_API_KEY/JWT_SECRET. .dockerignore keeps
# it out of the context, and runtime config arrives through --env-file or -e.

# Unprivileged user; the SQLite database and the rotating log both live in
# instance/, which is also the volume declared below. Werkzeug's scrypt hashing
# needs nothing from apt — the bundled sqlite3 and OpenSSL cover it.
RUN groupadd --system --gid 10001 sage \
 && useradd --system --uid 10001 --gid sage --home-dir /app --shell /usr/sbin/nologin sage \
 && mkdir -p /app/instance \
 && chown -R sage:sage /app

USER sage

# Conversations, memory, appointments, places, users, and logs all persist here.
VOLUME ["/app/instance"]

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '5000') + '/login', timeout=5)"]

# One worker with threads, on purpose: the active-stream registry
# (_ACTIVE_STREAMS), the shared gateway client, and the request-scoped SQLite
# connections all live in-process, so a second worker would break
# POST /api/chat/stop and split logins across processes. --timeout 0 because an
# SSE reply can stream for far longer than gunicorn's default 30s worker timeout.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-5000} --worker-class gthread --workers 1 --threads 8 --timeout 0 --access-logfile - --error-logfile - app:app"]
