from __future__ import annotations

from typing import NamedTuple

# What the Patient Inbox can send beyond a plain text: the same Twilio Content
# Templates, exam cards and booking links as the standalone Inbox service.

# Plain text (reply or first message): variable 1 is the message.
TEXT_CONTENT_SID = "HX3603a90abf832fc18645cf20482e3220"

# Generic sales card: variables 1 = image link, 2 = booking link.
GENERIC_SALES_CONTENT_SID = "HX7531d527e70135864e213e280b8dbd4b"

# Missed-call card: variable 1 = image link.
MISSED_CALL_CONTENT_SIDS = {
    "en": "HX0e1fe3f0896ff0ef4e754203b9718661",
    "es": "HXceb2720c1eee7e2a3f10d3279e01b66b",
}
MISSED_CALL_IMAGE = "sales/missed_call.jpg"
MISSED_CALL_LANGUAGES = (("en", "English"), ("es", "Spanish"))

CARD_TITLES = ("Mr.", "Ms.")

_BOOKING = "https://scanx.as.me/schedule/b616ed5a/appointment/{id}?appointmentTypeIds[]={id}"


class Exam(NamedTuple):
    key: str
    name: str
    image: str
    booking_link: str
    # Whether the generic sales card is offered for it (the exam RCS card is
    # offered for all of them).
    generic_sales: bool


def _exam(key: str, name: str, appointment_type_id: int, *, generic_sales: bool = True) -> Exam:
    return Exam(
        key=key,
        name=name,
        image=f"sales/{key}.jpg",
        booking_link=_BOOKING.format(id=appointment_type_id),
        generic_sales=generic_sales,
    )


EXAMS = (
    _exam("fibroscan", "FibroScan", 85160962),
    _exam("thyroid", "Thyroid", 84531018),
    _exam("abdominal", "Abdominal", 84883473),
    _exam("transvaginal", "Transvaginal", 89585589),
    _exam("aaascreening", "AAA Screening", 84886871),
    _exam("carotid", "Carotid", 84886849),
    _exam("echocardiogram", "Echocardiogram", 84887039),
    _exam("gallbladder", "Gallbladder", 86131984),
    _exam("hernia", "Hernia", 84886948),
    _exam("kidney", "Kidney", 86406434),
    _exam("legarteriesdoppler", "Leg Arteries Doppler", 84886893),
    _exam("legveindoppler", "Leg Vein Doppler", 84886880),
    # Liver shares FibroScan's booking link in the standalone Inbox; kept as is.
    _exam("liver", "Liver", 85160962),
    _exam("softtissue", "Soft Tissue", 89587244),
    _exam("spleen", "Spleen", 89102714),
    _exam("scrotal", "Scrotal", 84886931, generic_sales=False),
    _exam("pelvic", "Pelvic", 93409614, generic_sales=False),
    _exam("transabdominal", "Transabdominal", 84885375, generic_sales=False),
)

EXAMS_BY_KEY = {exam.key: exam for exam in EXAMS}


def find_exam(key: str, *, generic_sales: bool = False) -> Exam | None:
    exam = EXAMS_BY_KEY.get(key.strip().lower())

    if exam is None or (generic_sales and not exam.generic_sales):
        return None

    return exam
