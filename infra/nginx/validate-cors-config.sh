#!/bin/sh
# Task 7 fail-safe: production must never boot with a missing/placeholder
# CORS_ALLOWED_ORIGIN. Mirrors the "warn in dev, refuse to start in
# production" pattern every FastAPI service already applies to its own
# secrets (see services/*/app/core/secrets_check.py) — kept as its own
# tiny, directly-testable script rather than inlined into the entrypoint
# one-liner, precisely so it CAN be run directly in CI without booting the
# whole stack (see .github/workflows/tests.yml).
set -eu

environment="$(printf '%s' "${ENVIRONMENT:-development}" | tr '[:upper:]' '[:lower:]')"
origin="${CORS_ALLOWED_ORIGIN:-}"

if [ "$environment" != "production" ]; then
    if [ -z "$origin" ]; then
        echo "SECURITY: CORS_ALLOWED_ORIGIN is not set — no cross-origin browser request will ever be granted CORS headers (same-origin traffic is unaffected). This is fine in development, but will be refused at startup in production." >&2
    fi
    exit 0
fi

case "$origin" in
    https://*)
        # A real https:// origin was given — still reject the two known
        # dev-only placeholders (http://localhost:8080, the value CI/dev
        # actually uses, can never satisfy this case anyway since it's
        # not https://, but this stays literal here too rather than
        # relying only on the scheme check — never let a dev value pass
        # for one reason alone).
        case "$origin" in
            https://localhost*|https://127.0.0.1*)
                echo "SECURITY: CORS_ALLOWED_ORIGIN ('$origin') looks like a local/dev placeholder, not a real production origin. Refusing to start in production." >&2
                exit 1
                ;;
        esac
        ;;
    *)
        echo "SECURITY: CORS_ALLOWED_ORIGIN must be set to a real https:// origin in production (got: '${origin:-<empty>}'). Refusing to start in production." >&2
        exit 1
        ;;
esac

exit 0
