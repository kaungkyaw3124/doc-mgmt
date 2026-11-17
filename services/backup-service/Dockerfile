# Built FROM postgres:16 on purpose: pg_dump refuses to dump a server newer
# than itself, and the stack runs postgres:16 — this image's pg_dump/psql are
# exactly that version, with no extra apt repository. Python comes from Debian.
FROM postgres:16

# Same rule as the other services' Dockerfiles: apt over https (some
# networks block port 80), host overridable via APT_MIRROR. This image ships
# no CA certificates, so the bundle is borrowed from the python base image
# the other services already use; the pgdg list is dropped so apt needs only
# the Debian host (nothing below comes from it).
COPY --from=python:3.12-slim-bookworm /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
ARG APT_MIRROR=deb.debian.org
# Like the other services, the sources file is rewritten to the main + updates
# suites only: mirrors often lack the debian-security suite (a 404 there makes
# apt-get update fail), and these are build-time packages. The codename comes
# from the image itself (postgres:16 follows Debian stable).
RUN rm -f /etc/apt/sources.list.d/pgdg.list && \
    . /etc/os-release && \
    printf 'Types: deb\nURIs: https://%s/debian\nSuites: %s %s-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' "$APT_MIRROR" "$VERSION_CODENAME" "$VERSION_CODENAME" > /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN python3 -m venv /venv
ENV PATH="/venv/bin:$PATH"
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
# The postgres image's ENTRYPOINT would start a database server — not wanted.
ENTRYPOINT []
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
