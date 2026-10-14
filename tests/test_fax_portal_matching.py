from __future__ import annotations

from app.services.fax_portal_matching import suggest_clinic, suggest_exams

TYPES = [
    (1, "Abdominal Ultrasound (Complete)"),
    (2, "Abdominal Ultrasound (Limited)"),
    (3, "Kidney & Bladder Ultrasound"),
    (4, "FibroScan / Liver Elastography"),
    (5, "Carotid Doppler Ultrasound"),
    (6, "Scrotal Ultrasound"),
    (7, "Abdominal Aorta Ultrasound"),
    (8, "Thyroid Ultrasound"),
]


def names(matches) -> list[str]:
    return [m.name for m in matches]


def test_matches_the_exam_named_in_the_text() -> None:
    texts = ["A referral for a scrotal ultrasound for patient John Doe, to be scheduled soon."]

    matches = suggest_exams(texts, TYPES)

    assert names(matches) == ["Scrotal Ultrasound"]
    assert "scrotal ultrasound" in matches[0].fax_text.lower()


def test_two_exams_in_one_fax() -> None:
    texts = [
        "Orders a bilateral carotid Doppler ultrasound for dizziness and a limited "
        "abdominal ultrasound for right upper quadrant pain."
    ]

    assert names(suggest_exams(texts, TYPES)) == [
        "Carotid Doppler Ultrasound",
        "Abdominal Ultrasound (Limited)",
    ]


def test_qualifiers_choose_between_near_identical_types() -> None:
    limited = suggest_exams(["US ABD LTD limited abdominal ultrasound"], TYPES)
    complete = suggest_exams(["complete abdominal ultrasound requested"], TYPES)

    assert names(limited) == ["Abdominal Ultrasound (Limited)"]
    assert names(complete) == ["Abdominal Ultrasound (Complete)"]
    # Complete and Limited are one exam, never two suggestions.
    assert len(limited) == 1


def test_a_more_specific_exam_beats_its_shorter_name() -> None:
    texts = ["Abdominal aorta ultrasound to screen for aneurysm."]

    assert names(suggest_exams(texts, TYPES)) == ["Abdominal Aorta Ultrasound"]


def test_every_distinguishing_word_must_be_present() -> None:
    # "kidney" alone does not match "Kidney & Bladder".
    assert suggest_exams(["Patient has a kidney stone."], TYPES) == []
    assert names(suggest_exams(["kidneys and bladder ultrasound"], TYPES)) == [
        "Kidney & Bladder Ultrasound"
    ]


def test_generic_words_alone_do_not_match() -> None:
    assert suggest_exams(["Please schedule an ultrasound exam."], TYPES) == []


def test_no_text_no_suggestions() -> None:
    assert suggest_exams([], TYPES) == []
    assert suggest_exams(["", ""], TYPES) == []
    assert suggest_exams(["scrotal ultrasound"], []) == []


def test_snippet_is_the_matching_sentence() -> None:
    texts = ["Cover sheet. Please do a thyroid ultrasound for the patient. Thanks."]

    assert (
        suggest_exams(texts, TYPES)[0].fax_text == "Please do a thyroid ultrasound for the patient."
    )


# Clinics are stored without the trailing word "Location".
CALENDARS = [(10, "Dallas"), (11, "Fairview")]


def test_clinic_named_in_full() -> None:
    match = suggest_clinic(["Please book at the Fairview Location next week."], CALENDARS)

    assert match is not None
    assert (match.calendar_id, match.name) == (11, "Fairview")


def test_clinic_named_with_scanx() -> None:
    match = suggest_clinic(["Refer to ScanX Fairview for the scan."], CALENDARS)

    assert match is not None
    assert match.calendar_id == 11


def test_a_bare_city_elsewhere_is_not_enough() -> None:
    # The provider is in Dallas; that says nothing about which clinic to use.
    texts = ["Referral from Dallas Digestive Health, Dallas TX, to ScanX for an ultrasound."]

    assert suggest_clinic(texts, CALENDARS) is None


def test_a_bare_clinic_name_is_not_enough() -> None:
    # Clinics are stored as "Dallas": the word alone must not match.
    assert (
        suggest_clinic(["Dr Lee, Dallas Gastroenterology, 5000 Main St, Dallas TX."], CALENDARS)
        is None
    )
    assert suggest_clinic(["Patient lives in Fairview."], CALENDARS) is None


def test_two_clinics_named_means_no_suggestion() -> None:
    texts = ["Either the Dallas Location or the Fairview Location is fine."]

    assert suggest_clinic(texts, CALENDARS) is None


def test_no_clinic_in_text() -> None:
    assert suggest_clinic(["Routine referral."], CALENDARS) is None
    assert suggest_clinic([], CALENDARS) is None
