from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FaxAttachment:
    """A file to attach when sending a fax or email: filename + bytes +
    content-type. Named for its original use in the WestFax REST API
    integration (since removed in favor of email-to-fax) - kept here since
    both the fax and mail send paths still use this exact shape for a
    downloaded GCS report blob or an uploaded file.
    """

    filename: str
    content: bytes
    content_type: str
