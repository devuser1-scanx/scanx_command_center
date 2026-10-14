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


class IntakeSuggestions(BaseModel):
    clinic: ClinicSuggestion | None
    exam_1: ExamChoice | None
    exam_2: ExamChoice | None


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
