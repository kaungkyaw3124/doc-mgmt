"""
Server-side upload Content-Type policy — never trust the client-supplied
Content-Type on an upload as the authoritative MIME type. A client can set
that header to anything regardless of the file's actual name or content
(e.g. upload real HTML with Content-Type: application/pdf, or a spoofed
Content-Type: text/html on any file), and if that value is stored and later
served back as-is, a browser opening the resulting presigned URL renders it
as that type — a stored-XSS vector once the file is "just" a link someone
else clicks (see docs/known-issues.md finding #5 / SECURITY_HARDENING_LOG.md
Task 5).

Policy, applied uniformly to every upload endpoint in this service:
- Content-Type is always derived server-side from the (sanitized) filename
  extension via a fixed allowlist — never taken from the client's
  Content-Type header.
- An extension not in the allowlist gets "application/octet-stream" (an
  unrecognized business-document type still gets accepted and stored — the
  app doesn't second-guess what kind of document a user attaches — but is
  served back as an opaque download, never rendered as anything specific).
- A short list of extensions capable of running as active content in a
  browser (SVG, HTML, XML, JS, ...) always force a download on serve,
  regardless of what their derived Content-Type is, so even a correctly-
  identified SVG/HTML file can't be opened inline from this app's origin.
"""

_SAFE_EXTENSIONS_TO_MIME = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
    "txt": "text/plain",
    "csv": "text/csv",
    "zip": "application/zip",
    "rar": "application/vnd.rar",
    "7z": "application/x-7z-compressed",
}

# Anything a browser can render/execute as active content — always
# downloaded, never opened inline, no matter what Content-Type it gets.
_FORCE_DOWNLOAD_EXTENSIONS = {
    "svg", "svgz", "html", "htm", "xhtml", "shtml", "mhtml", "xml", "js", "mjs",
}


def _extension(filename: str | None) -> str:
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def safe_content_type(filename: str | None) -> str:
    """The Content-Type to store/serve for this filename — NEVER the
    client-supplied one."""
    return _SAFE_EXTENSIONS_TO_MIME.get(_extension(filename), "application/octet-stream")


def should_force_download(filename: str | None) -> bool:
    """Whether a download of this filename must force
    Content-Disposition: attachment rather than letting the browser render
    it inline."""
    return _extension(filename) in _FORCE_DOWNLOAD_EXTENSIONS
