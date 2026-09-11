FROM python:3.12-slim-bookworm

WORKDIR /app

# libpq-dev + gcc needed to build psycopg2 if a wheel isn't available for the platform
# The rest (pango, cairo, gdk-pixbuf, fonts) are required by WeasyPrint for PDF generation
#
# Always rewritten to https:// (below), regardless of mirror — this base
# image's own default sources.list uses plain http://, and a real
# deployment hit "Connection refused" on port 80 to deb.debian.org's
# Fastly-fronted IPs from a restricted-egress network that allows 443 but
# not 80 (confirmed working over https from the same host). Also supports
# pointing at an entirely different mirror via --build-arg
# APT_MIRROR=<host> for networks where even the default host is
# unreachable outright. Drops the security suite either way, since a
# rewritten single-suite sources file is simplest and these are
# build-time toolchain packages, not runtime app dependencies.
ARG APT_MIRROR=deb.debian.org
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        printf 'Types: deb\nURIs: https://%s/debian\nSuites: bookworm bookworm-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' "$APT_MIRROR" > /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
        printf 'deb https://%s/debian bookworm main\ndeb https://%s/debian bookworm-updates main\n' "$APT_MIRROR" "$APT_MIRROR" > /etc/apt/sources.list; \
    fi && \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
