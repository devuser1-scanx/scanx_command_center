from __future__ import annotations

import re
from functools import lru_cache

from google.cloud import storage

from app.core.config import settings

# WestFax Comprehend's GCS connector writes each fax PDF to the path given by
# the connector's "File Path Template". This code relies on the template
# STARTING WITH {JobId}/, for example
#
#     {JobId}/{ProductId}/InboundFax-{DateTime}-{SplitIndex}.{FileFormat}
#
# {JobId} is the same UUID the webhook sends as "Job ID" (cc_fp_fax.job_id), so
# every file of a fax sits under "<job id>/" and can be found with one exact
# prefix lookup. Anything after the first folder can be changed freely.


class FaxPdfNotFoundError(Exception):
    pass


_WHOLE_FAX = re.compile(r"-0*1\.pdf$", re.IGNORECASE)


@lru_cache
def _client() -> storage.Client:
    return storage.Client()


def find_fax_pdf(job_id: str) -> str | None:
    """Returns the object name of the PDF for a Comprehend Job ID, or None if it
    has not landed (yet).

    If the connector also writes split documents (SplitIndex 002, 003, ...),
    the whole-fax file (index 001) is preferred, since the page ranges Comprehend
    reports refer to the whole fax.
    """
    if not job_id:
        return None

    pdfs = [
        str(blob.name)
        for blob in _client().list_blobs(settings.comprehend_gcs_bucket, prefix=f"{job_id}/")
        if str(blob.name).lower().endswith(".pdf")
    ]

    if not pdfs:
        return None

    return sorted(pdfs, key=lambda name: (_WHOLE_FAX.search(name) is None, name))[0]


def to_gcs_uri(object_name: str) -> str:
    return f"gs://{settings.comprehend_gcs_bucket}/{object_name}"


def download_fax_pdf(gcs_uri: str) -> bytes:
    """Reads one PDF. Only objects in the configured bucket are served, so a
    bad value in cc_fp_fax.gcs_uri cannot make the API read elsewhere."""
    prefix = f"gs://{settings.comprehend_gcs_bucket}/"

    if not gcs_uri.startswith(prefix):
        raise FaxPdfNotFoundError("The PDF is not in the configured bucket.")

    blob = _client().bucket(settings.comprehend_gcs_bucket).blob(gcs_uri[len(prefix) :])

    if not blob.exists():
        raise FaxPdfNotFoundError("The PDF object no longer exists.")

    return blob.download_as_bytes()
