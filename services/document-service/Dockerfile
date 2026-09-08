FROM python:3.12-slim-bookworm

WORKDIR /app

# libpq-dev + gcc needed to build psycopg2 if a wheel isn't available for the platform
# The rest (pango, cairo, gdk-pixbuf, fonts) are required by WeasyPrint for PDF generation
#
# Override with --build-arg APT_MIRROR=<host> on networks where the default
# Fastly-fronted deb.debian.org is unreachable (e.g. some restricted-egress
# hosts block that CDN's IP ranges outright). Drops the security suite when
# switched, since third-party mirrors don't carry it — acceptable here since
# these are build-time toolchain packages, not runtime app dependencies.
ARG APT_MIRROR=deb.debian.org
RUN if [ "$APT_MIRROR" != "deb.debian.org" ]; then \
        if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
            printf 'Types: deb\nURIs: http://%s/debian\nSuites: bookworm bookworm-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' "$APT_MIRROR" > /etc/apt/sources.list.d/debian.sources; \
        elif [ -f /etc/apt/sources.list ]; then \
            printf 'deb http://%s/debian bookworm main\ndeb http://%s/debian bookworm-updates main\n' "$APT_MIRROR" "$APT_MIRROR" > /etc/apt/sources.list; \
        fi; \
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
