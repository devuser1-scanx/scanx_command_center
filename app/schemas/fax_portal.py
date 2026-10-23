from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

AssigneeFilter = Literal["all", "me", "unassigned"]


class AssigneeResponse(BaseModel):
    user_id: int
    name: str
    initials: str


class BoardCardResponse(BaseModel):
    id: int
    row_version: int
    status: str
    status_changed_at: datetime
    created_at: datetime
    first_name: str | None
    last_name: str | None
    display_name: str
    clinic: str | None
    exam_1: str | None
    exam_2: str | None
    physician_name: str | None
    is_new: bool
    is_urgent: bool
    possible_duplicate: bool
    assignee: AssigneeResponse | None
    qualifying_call_count: int
    total_call_count: int
    # One entry per call placed, oldest first: "miss" counts toward the
    # 5-call limit, "other" does not.
    attempts: list[Literal["miss", "other"]]
    callback_at: datetime | None
    next_action_at: datetime | None
    # Earliest of callback_at / next_action_at, for open cards being worked.
    due_at: datetime | None
    booked_at: datetime | None
    booked_source: str | None


class BoardColumnResponse(BaseModel):
    code: str
    label: str
    board_order: int
    is_open: bool
    hide_after_days: int | None
    # Cards visible in this column under the current filters.
    visible_count: int
    # Older cards left out by hide_after_days (data is kept; "Show older"
    # brings them back).
    hidden_count: int
    showing_older: bool
    # True when more cards match than were returned.
    truncated: bool
    cards: list[BoardCardResponse]


class BoardSummaryResponse(BaseModel):
    received_today: int
    review: int
    overdue: int
    booked_today: int
    fax_provider: int


class BoardResponse(BaseModel):
    # False when ?since= was given and nothing changed: no columns returned.
    changed: bool
    server_time: datetime
    summary: BoardSummaryResponse | None = None
    columns: list[BoardColumnResponse] = Field(default_factory=list)


class MoveReferralRequest(BaseModel):
    to_status: str
    # Optimistic lock: the row_version the client last saw.
    row_version: int
    source: Literal["button", "drag"] = "drag"
    reason: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=2000)
    acuity_appointment_id: str | None = Field(default=None, max_length=50)
    confirmed: bool = False
    callback_at: datetime | None = None


class MoveReferralResponse(BaseModel):
    id: int
    from_status: str
    to_status: str
    row_version: int


class AssignReferralRequest(BaseModel):
    assigned_user_id: int | None
    row_version: int


class AssignReferralResponse(BaseModel):
    id: int
    assigned_user_id: int | None
    row_version: int


class AgentResponse(BaseModel):
    user_id: int
    name: str
    initials: str
    accepting_new: bool


class CallQueueItem(BaseModel):
    """One referral in the signed-in user's call queue."""

    referral_id: int
    priority: Literal["callback", "reply", "overdue", "new", "later"]
    # "Callback", "SMS reply", "Overdue", "New", or "Due 3 PM" for later today.
    priority_label: str
    patient_name: str
    phone: str | None
    phone_e164: str | None
    exam: str | None
    provider: str | None
    # When the fax became a referral; the page shows its age (22m, 1h, 3d).
    received_at: datetime
    # Unsuccessful calls so far, out of the call limit.
    attempts: int
    attempt_limit: int
    last_result: str | None
    # What to do next: "Call 3", "Call at 5:30 PM", "Reply to SMS - Call 3".
    next_action: str


class CallQueueResponse(BaseModel):
    items: list[CallQueueItem]
    # Referrals to work now (everything except "later today").
    due_now: int
    later_today: int


class FaxPortalCountsResponse(BaseModel):
    call_queue: int
    intake_review: int


# ---------------------------------------------------------------------
# Intake Review
# ---------------------------------------------------------------------


class IntakeQueueItem(BaseModel):
    id: int
    fax_id: int
    display_name: str
    created_at: datetime
    is_new: bool
    is_urgent: bool
    possible_duplicate: bool
    page_count: int | None
    assignee: AssigneeResponse | None


class IntakeQueueResponse(BaseModel):
    # Whole-queue counts for the tabs, whichever tab or page is showing.
    waiting_count: int
    duplicate_count: int
    # Pagination of the chosen tab.
    total: int
    page: int
    page_size: int
    total_pages: int
    items: list[IntakeQueueItem]


class IntakeDocument(BaseModel):
    idx: int
    name: str | None
    doc_type: str | None
    page_start: int | None
    page_end: int | None
    # Comprehend's per-document "PHI Summary". Read-only. Never logged.
    summary: str | None = None
    # False for a document that belongs to another patient on a multi-patient
    # fax: its summary is not shown for this card.
    for_patient: bool = True


class IntakeFax(BaseModel):
    id: int
    job_id: str
    page_count: int | None
    received_at: datetime
    originating_number: str | None
    # Comprehend's whole-fax "PHI Summary of Docs". Read-only. Never logged.
    summary: str | None
    # True once the PDF has been linked from the bucket; the PDF endpoint also
    # looks it up on demand, so False only means "not found yet".
    has_pdf: bool
    documents: list[IntakeDocument]


class IntakeFields(BaseModel):
    first_name: str | None
    last_name: str | None
    dob: date | None
    phone: str | None
    gender: str | None
    email: str | None
    physician_name: str | None
    physician_fax_no: str | None
    physician_email: str | None
    address: str | None = None
    # One of the Exam 1 reason options, and the free text written beside it.
    reason: str | None = None
    reason_other: str | None = None


class ClinicChoice(BaseModel):
    acuity_calendar_id: int
    name: str


class ExamChoice(BaseModel):
    acuity_appointment_type_id: int
    name: str
    # What the fax said, kept to improve prefill later.
    fax_text: str | None = None


class ClinicSuggestion(ClinicChoice):
    reason: str


class ClinicDistance(BaseModel):
    """How far a clinic is from the patient's address (ZIP code to ZIP code,
    in a straight line)."""

    acuity_calendar_id: int
    name: str
    miles: float


class IntakeSuggestions(BaseModel):
    clinic: ClinicSuggestion | None
    exam_1: ExamChoice | None
    exam_2: ExamChoice | None
    # Every clinic with its distance from the patient's address, nearest first;
    # empty when the address has no usable ZIP code.
    clinic_distances: list[ClinicDistance] = []


class IntakeDetailResponse(BaseModel):
    id: int
    row_version: int
    status: str
    is_new: bool
    is_urgent: bool
    duplicate_of_referral_id: int | None
    fields: IntakeFields
    clinic: ClinicChoice | None
    exam_1: ExamChoice | None
    exam_2: ExamChoice | None
    suggestions: IntakeSuggestions
    fax: IntakeFax


class IntakeUpdateRequest(BaseModel):
    """Only the fields present in the request are changed; an explicit null
    clears a value."""

    row_version: int
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    dob: date | None = None
    phone: str | None = Field(default=None, max_length=20)
    gender: str | None = Field(default=None, max_length=10)
    email: str | None = Field(default=None, max_length=150)
    physician_name: str | None = Field(default=None, max_length=255)
    physician_fax_no: str | None = Field(default=None, max_length=255)
    physician_email: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=500)
    reason: str | None = Field(default=None, max_length=255)
    reason_other: str | None = Field(default=None, max_length=2000)
    acuity_calendar_id: int | None = None
    exam_1_id: int | None = None
    exam_1_fax_text: str | None = Field(default=None, max_length=2000)
    exam_2_id: int | None = None
    exam_2_fax_text: str | None = Field(default=None, max_length=2000)


class IntakeUpdateResponse(BaseModel):
    id: int
    row_version: int
    changed_fields: list[str]


# ---------------------------------------------------------------------
# Acuity reference data (read from the synced tables, never live)
# ---------------------------------------------------------------------


class AcuityCalendarResponse(BaseModel):
    acuity_calendar_id: int
    name: str
    location: str | None
    timezone: str | None


class AcuityAppointmentTypeResponse(BaseModel):
    acuity_appointment_type_id: int
    name: str
    category: str | None
    duration_min: int | None
    price: str | None
    # False when a clinic was chosen and it does not offer this exam.
    available_at_calendar: bool
    # The options of this exam's "Reason for the appointment" dropdown in
    # Acuity, in Acuity's order; empty when it has none.
    reason_options: list[str] = []


class AcuitySyncStatusResponse(BaseModel):
    last_run_at: datetime | None
    last_status: str | None
    last_error: str | None
    calendars: int
    appointment_types: int


class AcuityRefreshResponse(BaseModel):
    status: str
    calendars_added: int
    calendars_updated: int
    calendars_removed: int
    types_added: int
    types_updated: int
    types_removed: int


# ---------------------------------------------------------------------
# Referral Case
# ---------------------------------------------------------------------

CALL_LIMIT = 5


class CaseFields(IntakeFields):
    """The Acuity booking-form fields, as held on the card."""

    reason: str | None
    reason_other: str | None


class CaseFax(BaseModel):
    id: int
    job_id: str
    page_count: int | None
    received_at: datetime
    has_pdf: bool


class CaseOutreach(BaseModel):
    qualifying_call_count: int
    total_call_count: int
    call_limit: int
    last_contact_at: datetime | None
    next_action_at: datetime | None
    next_action_type: str | None
    callback_at: datetime | None


class CallResultOption(BaseModel):
    code: str
    label: str
    counts_toward_limit_default: bool


class CaseCall(BaseModel):
    id: int
    started_at: datetime
    user_name: str | None
    result: str | None
    result_label: str | None
    counts_toward_limit: bool
    attempt_number: int | None
    other_reason: str | None
    notes: str | None
    twilio_status: str | None
    duration_s: int | None
    # "twilio" when placed through the click-to-call service, "manual" when an
    # agent logged a call made outside it.
    source: Literal["twilio", "manual"]


class CaseMessage(BaseModel):
    id: int
    direction: Literal["outbound", "inbound"]
    channel: Literal["rcs", "sms"]
    body: str
    status: str
    status_at: datetime
    created_at: datetime
    user_name: str | None
    template_code: str | None
    error_message: str | None


class TimelineEvent(BaseModel):
    at: datetime
    kind: Literal["status", "call", "message", "edit", "system"]
    title: str
    detail: str | None = None
    actor: str | None = None


class CaseDetailResponse(BaseModel):
    id: int
    row_version: int
    status: str
    status_label: str
    is_open: bool
    created_at: datetime
    fields: CaseFields
    clinic: ClinicChoice | None
    exam_1: ExamChoice | None
    exam_2: ExamChoice | None
    assignee: AssigneeResponse | None
    fax: CaseFax
    outreach: CaseOutreach
    phone_e164: str | None
    sms_opted_out: bool
    do_not_call: bool
    can_call: bool
    call_blocked_reason: str | None
    can_text: bool
    text_blocked_reason: str | None
    # Id of a call started but not yet given a result.
    open_call_id: int | None
    open_call_source: Literal["twilio", "manual"] | None
    # Twilio's progress on the open call (e.g. "ringing", "in-progress",
    # "patient:no-answer"), and the result it suggests. The agent still picks.
    open_call_status: str | None
    suggested_result: str | None
    # False until click-to-call is set up: calls placed outside it can be
    # logged by hand so results and the call limit still work.
    call_service_connected: bool
    # Whether the Call patient button can place a call for the person viewing:
    # also needs their own phone number, which Twilio rings first.
    can_dial: bool
    dial_blocked_reason: str | None
    result_options: list[CallResultOption]
    calls: list[CaseCall]
    messages: list[CaseMessage]
    timeline: list[TimelineEvent]


class StartCallResponse(BaseModel):
    call_id: int
    started_at: datetime


class DialCallResponse(BaseModel):
    """Click-to-call: the call row, and the short-lived token the browser
    connects to Twilio with (it passes `call_id` as the call's CallId)."""

    call_id: int
    started_at: datetime
    token: str


class CallResultRequest(BaseModel):
    result: str
    # Required for "other"; ignored for the other results.
    other_reason: str | None = Field(default=None, max_length=2000)
    # Only honoured for "other": whether it counts toward the 5-call limit.
    counts_toward_limit: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)
    # Required for "callback requested".
    callback_at: datetime | None = None
    # Overrides the default next touch (next business day, 9:00 AM Chicago).
    next_action_at: datetime | None = None
    # Required for "booked appointment".
    acuity_appointment_id: str | None = Field(default=None, max_length=50)


class CallResultResponse(BaseModel):
    call_id: int
    status: str
    row_version: int
    qualifying_call_count: int
    counts_toward_limit: bool
    moved_to: str | None
    call_limit_reached: bool


class DoNotCallResponse(BaseModel):
    status: str
    row_version: int


class SendMessageRequest(BaseModel):
    body: str = Field(min_length=1, max_length=1600)
    template_code: str | None = Field(default=None, max_length=50)


class SendMessageResponse(BaseModel):
    message_id: int
    status: str
    channel: Literal["rcs", "sms"]


class MessageTemplateResponse(BaseModel):
    code: str
    label: str
    language: str
    # The wording with this referral's details filled in.
    body: str
    # Details that were not available, left as {placeholders} in the body.
    missing: list[str]


class BookingMatch(BaseModel):
    appointment_id: str
    first_name: str | None
    last_name: str | None
    appointment_type: str | None
    date: datetime | None
    time: str | None
    canceled: bool
    already_linked: bool


class FaxBackDraftResponse(BaseModel):
    """What the "fax back to the referring physician" pop-up starts with."""

    # The fax number the referral came from (else the physician's number on the
    # card); blank when neither is known.
    destination_number: str
    destination_source: str
    subject: str
    # The message, including the list of contact attempts (no message text, and
    # "Agent" instead of any name). The person can edit all of it.
    body: str
    # Only a referral in Fax Provider Name Pending can move to Didn't Respond.
    can_mark_didnt_respond: bool
    # Whether the original fax PDF can be sent along with the message.
    original_fax_available: bool = False


class FaxBackRequest(BaseModel):
    destination_number: str = Field(min_length=1, max_length=100)
    subject: str | None = Field(default=None, max_length=300)
    body: str = Field(min_length=1, max_length=20000)
    # Send the original fax PDF along with the message.
    attach_original: bool = True
    # Also move the referral from Fax Provider Name Pending to Didn't Respond.
    mark_didnt_respond: bool = False


class FaxBackResponse(BaseModel):
    destination_number: str
    email_message_id: str | None
    moved_to: str | None
    row_version: int
    sent_at: datetime


class AvailableTime(BaseModel):
    """One open slot from Acuity. `time` is Acuity's own timestamp (sent back to
    book it); `label` is the time of day in the clinic's timezone."""

    time: str
    label: str


class DirectBookRequest(BaseModel):
    # A `time` exactly as returned by the availability list.
    time: str = Field(min_length=10, max_length=40)
    # Only needed when the patient's card has no email.
    email: str | None = Field(default=None, max_length=150)


class DirectBookResponse(BaseModel):
    appointment_id: str
    status: str
    row_version: int
    when_label: str


class BookingInfoResponse(BaseModel):
    # Acuity scheduling page for the chosen exam and clinic with the patient's
    # details prefilled; None when it cannot be built yet.
    booking_link: str | None
    booking_link_blocked_reason: str | None
    matches: list[BookingMatch]
    # Set when the appointment lookup could not run (e.g. the production
    # database is not configured); the appointment id can still be typed in.
    matches_unavailable: str | None = None


class ReferralSearchItem(BaseModel):
    id: int
    display_name: str
    phone: str | None
    status: str
    status_label: str
    created_at: datetime
    assignee: AssigneeResponse | None
