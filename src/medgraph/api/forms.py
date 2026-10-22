"""The app's forms, read into memory up to a size limit and parsed there.

Frameworks spool a large upload to the system's temporary folder. Uploads may be real records,
which stay in the data folder, so the body is read here instead, and refused as soon as it
passes its limit.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any
from urllib.parse import parse_qsl

from python_multipart.multipart import FormParser, parse_options_header

MAX_FIELDS = 20


class FormError(ValueError):
    """A form the app can't read: too large, the wrong encoding, or malformed."""


@dataclass
class Form:
    fields: dict[str, str] = field(default_factory=dict)
    files: dict[str, tuple[str, bytes]] = field(default_factory=dict)  # name -> (file name, data)

    def get(self, name: str) -> str:
        return self.fields.get(name, "")


async def read_body(stream: AsyncIterator[bytes], content_length: str | None, limit: int) -> bytes:
    """The whole body, refused once it is larger than ``limit`` bytes."""
    if content_length is not None and content_length.isdigit() and int(content_length) > limit:
        raise FormError("too large")
    chunks, size = [], 0
    async for chunk in stream:
        size += len(chunk)
        if size > limit:
            raise FormError("too large")
        chunks.append(chunk)
    return b"".join(chunks)


def parse_form(content_type: str, body: bytes) -> Form:
    """A URL-encoded or multipart form; files are kept in memory."""
    kind, params = parse_options_header(content_type)
    form = Form()
    if kind == b"application/x-www-form-urlencoded":
        try:
            pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, strict_parsing=False)
        except UnicodeDecodeError as exc:
            raise FormError("not UTF-8") from exc
        if len(pairs) > MAX_FIELDS:
            raise FormError("too many fields")
        form.fields = dict(pairs)
        return form
    if kind != b"multipart/form-data" or b"boundary" not in params:
        raise FormError("not a form")

    def on_field(f: Any) -> None:
        if f.field_name is not None and len(form.fields) < MAX_FIELDS:
            form.fields[f.field_name.decode("utf-8", "replace")] = (f.value or b"").decode(
                "utf-8", "replace"
            )

    def on_file(f: Any) -> None:
        obj = f.file_object
        data = obj.getvalue() if isinstance(obj, BytesIO) else b""
        name = (f.field_name or b"").decode("utf-8", "replace")
        form.files[name] = ((f.file_name or b"").decode("utf-8", "replace"), data)

    config = {"MAX_MEMORY_FILE_SIZE": len(body) + 1, "UPLOAD_DIR": None}
    try:
        parser = FormParser(
            "multipart/form-data", on_field, on_file, boundary=params[b"boundary"], config=config
        )
        parser.write(body)
        parser.finalize()
    except Exception as exc:  # python-multipart raises several kinds on malformed input
        raise FormError("malformed form") from exc
    return form
