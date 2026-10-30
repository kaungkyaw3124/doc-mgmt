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
- Before any of the above even gets a chance to matter, the file's actual
  bytes are sniffed against a handful of well-known binary signatures
  ("magic bytes") and checked against what the extension claims to be —
  see content_matches_extension() below. The extension/Content-Type
  allowlist alone only controls how a file is LABELED; it does nothing to
  stop someone uploading real HTML content named "quote.pdf" in the first
  place. That's a distinct attack (extension/content-type spoofing is not
  the same bug as extension/content MISMATCH) and needs its own check.
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


# --- content/magic-byte inspection ------------------------------------------
#
# Only the first few KB of a file are ever needed here — every signature
# below appears at (or very near) byte 0, and this is checked against a
# small `head` slice the caller reads before streaming the rest of the
# upload to storage, not the whole file.

_EXECUTABLE_SIGNATURES = (
    (b"MZ", "windows_pe"),  # .exe/.dll
    (b"\x7fELF", "elf"),  # Linux/Unix native binary
    (b"\xfe\xed\xfa\xce", "mach_o"),
    (b"\xfe\xed\xfa\xcf", "mach_o"),
    (b"\xce\xfa\xed\xfe", "mach_o"),
    (b"\xcf\xfa\xed\xfe", "mach_o"),
    (b"\xca\xfe\xba\xbe", "mach_o_fat"),  # also the Java class-file magic;
    # either way this is compiled/executable content, not a document.
    (b"#!", "shebang_script"),
)

# Signatures for the binary document/image/archive formats this app
# actually accepts. Multiple extensions can map to the same detected
# "kind" (e.g. .doc/.xls/.ppt are all the same legacy OLE container
# format; .docx/.xlsx/.pptx/.zip are all plain zip containers).
_BINARY_SIGNATURES = (
    (b"%PDF-", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),  # empty zip archive
    (b"PK\x07\x08", "zip"),  # spanned zip archive
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole"),  # legacy .doc/.xls/.ppt
    (b"Rar!\x1a\x07", "rar"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
)

# What detect_content_kind() is allowed to find for a given extension. An
# extension in this map that is NOT in this map's keys skips the check
# entirely (e.g. .txt/.csv have no reliable binary signature — free-form
# text is free-form text — so there's nothing meaningful to sniff there;
# they're still safe because they're served as text/plain, never executed).
_EXTENSION_EXPECTED_KINDS = {
    "pdf": {"pdf"},
    "png": {"png"},
    "jpg": {"jpeg"},
    "jpeg": {"jpeg"},
    "gif": {"gif"},
    "bmp": {"bmp"},
    "webp": {"webp"},
    "doc": {"ole"},
    "xls": {"ole"},
    "ppt": {"ole"},
    "docx": {"zip"},
    "xlsx": {"zip"},
    "pptx": {"zip"},
    "zip": {"zip"},
    "rar": {"rar"},
    "7z": {"7z"},
}


def _looks_like_webp(head: bytes) -> bool:
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def detect_content_kind(head: bytes) -> str | None:
    """Identifies a handful of well-known binary formats from their
    leading bytes. Returns None when nothing recognizable was found (plain
    text, an unrecognized/unusual format, or truncated/empty content) —
    that is NOT the same as "safe", callers decide what None means for
    their extension."""
    for magic, kind in _EXECUTABLE_SIGNATURES:
        if head.startswith(magic):
            return kind
    if _looks_like_webp(head):
        return "webp"
    for magic, kind in _BINARY_SIGNATURES:
        if head.startswith(magic):
            return kind
    return None


def content_matches_extension(filename: str | None, head: bytes) -> bool:
    """
    True if `head` (the file's leading bytes, as actually uploaded) is
    consistent with what `filename`'s extension claims to be — False if
    the upload should be rejected outright as a content/extension
    mismatch, independent of whatever Content-Type gets stored.

    This is deliberately stricter than safe_content_type()/
    should_force_download(): those two decide how to safely LABEL and
    SERVE a file whose content we accept; this decides whether to accept
    the file at all. A polyglot upload (real HTML byte-for-byte, just
    named "quote.pdf") is never something safe_content_type() alone can
    catch, because it only ever looks at the filename — it doesn't read
    a single byte of the file. Rejecting it here, before it's ever
    written to storage, is strictly stronger than relabeling it and
    hoping every future code path that touches it respects that label.
    """
    kind = detect_content_kind(head)

    # An executable/script signature is never acceptable, no matter what
    # extension the client attached to it.
    if kind in ("windows_pe", "elf", "mach_o", "mach_o_fat", "shebang_script"):
        return False

    ext = _extension(filename)
    expected = _EXTENSION_EXPECTED_KINDS.get(ext)
    if expected is None:
        # No binary signature is defined/required for this extension
        # (txt, csv, an unrecognized extension, or one of the
        # already-force-downloaded active-content extensions like
        # svg/html/xml/js — those are XML/text formats with no fixed
        # magic bytes to check, and are never rendered inline anyway).
        return True

    # A "safe" binary extension (pdf/png/jpg/.../doc/zip/...) whose
    # content doesn't match ANY recognized signature for that kind — or
    # matches a *different* kind's signature entirely — is a mismatch.
    # This is what catches "real HTML named evil.pdf", "a renamed .exe"
    # (already caught above, but also here since .exe extension isn't in
    # this policy's allowlist to begin with), "a JPEG renamed .png", etc.
    return kind in expected
