"""Validation for files received directly from multipart requests."""

import os

from django.core.exceptions import ValidationError


DEFAULT_MAX_BYTES = 10 * 1024 * 1024
SIGNATURES = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".xlsx": (b"PK\x03\x04",),
    ".docx": (b"PK\x03\x04",),
}


def validate_uploaded_file(upload, *, extensions, mime_types=None, max_bytes=DEFAULT_MAX_BYTES):
    """Reject oversized, misleading, or executable-looking uploads."""
    if not upload:
        return
    extension = os.path.splitext(upload.name or "")[1].lower()
    allowed_extensions = {value.lower() for value in extensions}
    if extension not in allowed_extensions:
        raise ValidationError("Unsupported file type.")
    if upload.size > max_bytes:
        raise ValidationError("The uploaded file is too large.")
    if mime_types and upload.content_type and upload.content_type.lower() not in {value.lower() for value in mime_types}:
        raise ValidationError("The uploaded file content type is not allowed.")

    signatures = SIGNATURES.get(extension)
    if signatures:
        position = upload.tell()
        header = upload.read(16)
        upload.seek(position)
        if not any(header.startswith(signature) for signature in signatures):
            raise ValidationError("The uploaded file signature does not match its type.")
