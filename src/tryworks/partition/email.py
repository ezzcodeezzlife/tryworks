"""Partition .eml email messages with the standard library's email parser.

The body (HTML preferred, or plain text with ``content_source="text/plain"``) is partitioned
like any HTML or text document, and every element carries the message's sender, recipients,
subject and message id. Attachments are partitioned too, marked with ``attached_to_filename``.
"""

from __future__ import annotations

import datetime as dt
import io
import os
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from typing import IO, Any, Optional

from tryworks.documents.elements import Element, ElementMetadata
from tryworks.partition.common import FileType, apply_metadata, exactly_one, read_bytes

VALID_CONTENT_SOURCES = ("text/html", "text/plain")


def _addresses(msg: EmailMessage, header: str) -> Optional[list[str]]:
    values = msg.get_all(header)
    if not values:
        return None
    formatted = []
    for name, address in getaddresses([str(v) for v in values]):
        if not address:
            continue
        formatted.append(f"{name} <{address}>" if name else address)
    return formatted or None


def _sent_date(msg: EmailMessage) -> Optional[str]:
    date = msg.get("Date")
    if not date:
        return None
    try:
        sent = parsedate_to_datetime(str(date))
    except (TypeError, ValueError):
        return None
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=dt.timezone.utc)
    return sent.astimezone(dt.timezone.utc).isoformat()


def _body_text(msg: EmailMessage, content_source: str) -> tuple[Optional[str], Optional[str]]:
    """Return ``(subtype, text)`` of the preferred body part, or ``(None, None)``."""
    preference = ("plain", "html") if content_source == "text/plain" else ("html", "plain")
    part = msg.get_body(preferencelist=preference)
    if part is None:
        return None, None
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace")
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    return part.get_content_subtype(), content


@apply_metadata(FileType.EML)
def partition_email(
    filename: Optional[str] = None,
    *,
    file: Optional[IO[Any]] = None,
    content_source: str = "text/html",
    metadata_filename: Optional[str] = None,
    metadata_last_modified: Optional[str] = None,
    process_attachments: bool = True,
    **kwargs: Any,
) -> list[Element]:
    """Partition an RFC 822 (.eml) email message."""
    from tryworks.partition.html import partition_html
    from tryworks.partition.text import partition_text

    exactly_one(filename=filename, file=file)
    if content_source not in VALID_CONTENT_SOURCES:
        raise ValueError(
            f"{content_source!r} is not a valid value for content_source; must be one of {VALID_CONTENT_SOURCES}"
        )
    msg = BytesParser(policy=policy.default).parsebytes(read_bytes(filename=filename, file=file))
    assert isinstance(msg, EmailMessage)

    message_id = msg.get("Message-ID")
    email_fields = dict(
        bcc_recipient=_addresses(msg, "Bcc"),
        cc_recipient=_addresses(msg, "Cc"),
        email_message_id=str(message_id).strip().strip("<>") if message_id else None,
        sent_from=_addresses(msg, "From"),
        sent_to=_addresses(msg, "To"),
        subject=str(msg.get("Subject")) if msg.get("Subject") else None,
        last_modified=_sent_date(msg),
    )
    inner_kwargs = {k: v for k, v in kwargs.items() if k in ("languages", "detect_language_per_element", "language_fallback")}

    elements: list[Element] = []
    subtype, body = _body_text(msg, content_source)
    if body and body.strip():
        if subtype == "html":
            elements.extend(partition_html(text=body, **inner_kwargs))
        else:
            elements.extend(partition_text(text=body, **inner_kwargs))
    for element in elements:
        element.metadata.update(ElementMetadata(**email_fields))

    if process_attachments:
        from tryworks.partition.auto import UnsupportedFileFormatError, partition

        email_name = metadata_filename or filename
        attached_to = os.path.basename(str(email_name)) if email_name else None
        for attachment in msg.iter_attachments():
            name = attachment.get_filename()
            payload = attachment.get_payload(decode=True)
            if not payload:
                continue
            try:
                attached = partition(
                    file=io.BytesIO(payload),
                    metadata_filename=name,
                    content_type=attachment.get_content_type() if not name else None,
                    **inner_kwargs,
                )
            except (UnsupportedFileFormatError, ValueError):
                continue
            for element in attached:
                element.metadata.attached_to_filename = attached_to or name
                if email_fields["last_modified"]:
                    element.metadata.last_modified = email_fields["last_modified"]
                elements.append(element)
    return elements
