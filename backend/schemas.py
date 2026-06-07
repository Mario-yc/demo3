from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timezone
from models import WorkshopStatus, RoundStatus, GroupResultStatus


def _serialize_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class APIModel(BaseModel):
    model_config = {"json_encoders": {datetime: _serialize_datetime}}


# ---------- Workshop ----------
class WorkshopCreate(APIModel):
    title: str = Field(default="领导力共创研讨会", min_length=1, max_length=255)
    host_name: str = Field(..., min_length=1, max_length=100)
    group_count: int = Field(default=4, ge=1, le=10)


class WorkshopCreateResponse(APIModel):
    id: int
    title: str
    host_name: str
    group_count: int
    invite_code: str
    host_code: str
    kb_admin_code: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ValidateHostRequest(APIModel):
    host_code: str


class ValidateInviteRequest(APIModel):
    invite_code: str


class ValidateResponse(APIModel):
    valid: bool
    workshop_id: Optional[int] = None
    workshop_title: Optional[str] = None


class WorkshopJoinRequest(APIModel):
    name: str = Field(..., min_length=1, max_length=100)
    invite_code: str


# ---------- Participant ----------
class ParticipantOut(APIModel):
    id: int
    workshop_id: int
    name: str
    group_id: int
    is_group_leader: bool

    model_config = {"from_attributes": True}


class ParticipantWithToken(ParticipantOut):
    session_token: str


# ---------- Round ----------
class RoundOut(APIModel):
    id: int
    workshop_id: int
    round_number: int
    title: str
    objective: Optional[str] = None
    status: RoundStatus
    discussion_time: int
    input_time: int
    timer_started_at: Optional[datetime] = None
    timer_phase: Optional[str] = None
    timer_remaining_seconds: Optional[int] = None
    questions: List["QuestionOut"] = []

    model_config = {"from_attributes": True}


# ---------- Question ----------
class QuestionOut(APIModel):
    id: int
    round_id: int
    content: str
    order: int

    model_config = {"from_attributes": True}


# ---------- Answer ----------
class AnswerSubmit(APIModel):
    participant_id: int
    session_token: str
    question_id: int
    content: str = Field(..., min_length=1)


class AnswerOut(APIModel):
    id: int
    question_id: int
    participant_id: int
    content: str
    created_at: datetime
    participant_name: Optional[str] = None
    group_id: Optional[int] = None

    model_config = {"from_attributes": True}


# ---------- Group Round Result ----------
class GroupRoundResultOut(APIModel):
    id: int
    round_id: int
    group_id: int
    status: GroupResultStatus
    original_content: Optional[str] = None
    edited_content: Optional[str] = None
    version: int
    validation_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GroupResultEdit(APIModel):
    edited_content: str = Field(..., min_length=1)


class GroupResultMemberEdit(APIModel):
    participant_id: int
    session_token: str
    edited_content: str = Field(..., min_length=1)


class GroupAITrigger(APIModel):
    participant_id: int
    session_token: str


class GroupLeaderTransfer(APIModel):
    workshop_id: int
    participant_id: int
    session_token: str
    new_leader_participant_id: int


class HostGroupLeaderSet(APIModel):
    host_code: str
    new_leader_participant_id: int


# ---------- Synthesis Result ----------
class SynthesisResultOut(APIModel):
    id: int
    workshop_id: int
    round_id: int
    status: GroupResultStatus
    original_content: Optional[str] = None
    edited_content: Optional[str] = None
    validation_error: Optional[str] = None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SynthesisResultEdit(APIModel):
    edited_content: str = Field(..., min_length=1)


# ---------- Host Input ----------
class HostInputCreate(APIModel):
    round_id: int
    content: str = Field(..., min_length=1)


class HostInputOut(APIModel):
    id: int
    workshop_id: int
    round_id: int
    content: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------- Round Settings ----------
class RoundSettingsUpdate(APIModel):
    discussion_time: Optional[int] = Field(default=None, ge=1, le=120)
    input_time: Optional[int] = Field(default=None, ge=1, le=120)


# ---------- Group Info (for host dashboard) ----------
class GroupInfo(APIModel):
    group_id: int
    participant_count: int
    leader_name: Optional[str] = None
    members: List[ParticipantOut] = []


class RoundInfo(APIModel):
    id: int
    round_number: int
    title: str
    objective: Optional[str] = None
    status: RoundStatus
    discussion_time: int
    input_time: int
    timer_started_at: Optional[datetime] = None
    timer_phase: Optional[str] = None
    timer_remaining_seconds: Optional[int] = None
    questions: List[QuestionOut] = []
    answers: List[AnswerOut] = []
    group_results: List[GroupRoundResultOut] = []
    synthesis: Optional[SynthesisResultOut] = None
    host_input: Optional[HostInputOut] = None


# ---------- Workshop Views ----------
class WorkshopMemberView(APIModel):
    id: int
    title: str
    host_name: str
    invite_code: str
    group_count: int
    current_round: int
    flow_round_number: int
    is_review_mode: bool
    status: WorkshopStatus
    created_at: datetime
    participant: Optional[ParticipantWithToken] = None
    group_members: List[ParticipantOut] = []
    rounds: List[RoundOut] = []

    model_config = {"from_attributes": True}


class WorkshopHostView(APIModel):
    id: int
    title: str
    host_name: str
    invite_code: str
    host_code: str
    kb_admin_code: str
    group_count: int
    current_round: int
    flow_round_number: int
    is_review_mode: bool
    status: WorkshopStatus
    created_at: datetime
    groups: List[GroupInfo] = []
    rounds: List[RoundInfo] = []
    knowledge_docs: List["KnowledgeDocumentOut"] = []

    model_config = {"from_attributes": True}


# ---------- Knowledge Base ----------
class ValidateAdminRequest(APIModel):
    admin_code: str


class KnowledgeDocumentOut(APIModel):
    id: int
    workshop_id: Optional[int] = None
    original_filename: str
    file_size: int
    content_type: str
    chunk_count: int
    embedding_model: str
    upload_params: Optional[str] = None
    is_deleted: bool
    uploaded_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeUploadTaskOut(APIModel):
    task_id: str
    filename: str
    status: str
    stage: str
    progress: int
    message: str
    error: Optional[str] = None
    document: Optional[KnowledgeDocumentOut] = None
    created_at: datetime
    updated_at: datetime


# ---------- AI QA ----------
class AIQuestionSubmit(APIModel):
    participant_id: int
    question: str = Field(..., min_length=1)


class AIQuestionOut(APIModel):
    id: int
    round_id: Optional[int] = None
    question: str
    answer: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------- WebSocket ----------
class WSMessage(APIModel):
    type: str
    data: dict


# ---------- Markdown Export ----------
class ExportResponse(APIModel):
    markdown: str
    filename: str
