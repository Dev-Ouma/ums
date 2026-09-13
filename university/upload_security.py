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
        ext_list = ", ".join(sorted(allowed_extensions))
        raise ValidationError(f"Unsupported file type. Only {ext_list} files are allowed.")
    if upload.size > max_bytes:
        limit_mb = max_bytes / (1024 * 1024)
        actual_mb = upload.size / (1024 * 1024)
        raise ValidationError(f"The file size ({actual_mb:.1f}MB) exceeds the maximum limit of {limit_mb:.0f}MB.")
    if mime_types and upload.content_type and upload.content_type.lower() not in {value.lower() for value in mime_types}:
        raise ValidationError("The file format is invalid. Ensure you are uploading a genuine document.")

    signatures = SIGNATURES.get(extension)
    if signatures:
        position = upload.tell()
        header = upload.read(16)
        upload.seek(position)
        if not any(header.startswith(signature) for signature in signatures):
            raise ValidationError("The file format signature is invalid. Ensure the file is a genuine uncorrupted document.")
