FROM python:3.12-slim-bookworm

WORKDIR /app

# Override with --build-arg APT_MIRROR=<host> on networks where the default
# Fastly-fronted deb.debian.org is unreachable (e.g. some restricted-egress
# hosts block that CDN's IP ranges outright). Drops the security suite when
# switched, since third-party mirrors don't carry it — acceptable here since
# these are build-time toolchain packages, not runtime app dependencies.
ARG APT_MIRROR=deb.debian.org
RUN if [ "$APT_MIRROR" != "deb.debian.org" ]; then \
        sed -i -e '/bookworm-security/d' -e "s#deb.debian.org#${APT_MIRROR}#g" /etc/apt/sources.list; \
    fi && \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
