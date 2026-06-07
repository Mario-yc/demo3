import datetime
import secrets
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, Enum as SAEnum
from sqlalchemy.orm import relationship
import enum

from database import Base


class WorkshopStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"


class RoundStatus(str, enum.Enum):
    LOCKED = "locked"
    ACTIVE = "active"
    INPUT = "input"
    CLOSING = "closing"
    COMPLETED = "completed"


class GroupResultStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    VALIDATION_FAILED = "validation_failed"
    EDITED = "edited"


def _random_code(length: int = 6) -> str:
    return secrets.token_hex(length // 2).upper()[:length]


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


class Workshop(Base):
    __tablename__ = "workshops"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(255), nullable=False, default="领导力共创研讨会")
    host_name = Column(String(100), nullable=False, default="主持人")
    invite_code = Column(String(6), nullable=False, unique=True, default=lambda: _random_code(6))
    host_code = Column(String(8), nullable=False, unique=True, default=lambda: _random_code(8))
    kb_admin_code = Column(String(8), nullable=False, unique=True, default=lambda: _random_code(8))
    group_count = Column(Integer, nullable=False, default=4)
    current_round = Column(Integer, default=1)
    flow_round_number = Column(Integer, nullable=False, default=1)
    is_review_mode = Column(Boolean, nullable=False, default=False)
    status = Column(SAEnum(WorkshopStatus), default=WorkshopStatus.ACTIVE)
    created_at = Column(DateTime, default=_utcnow)

    participants = relationship("Participant", back_populates="workshop", cascade="all, delete-orphan")
    rounds = relationship("Round", back_populates="workshop", cascade="all, delete-orphan", order_by="Round.round_number")
    host_inputs = relationship("HostInput", back_populates="workshop", cascade="all, delete-orphan")
    synthesis_results = relationship("SynthesisResult", back_populates="workshop", cascade="all, delete-orphan")
    knowledge_docs = relationship("KnowledgeDocument", back_populates="workshop")
    ai_questions = relationship("AIQuestionLog", back_populates="workshop", cascade="all, delete-orphan")


class Participant(Base):
    __tablename__ = "participants"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    workshop_id = Column(Integer, ForeignKey("workshops.id"), nullable=True)
    name = Column(String(100), nullable=False)
    group_id = Column(Integer, nullable=False)
    is_group_leader = Column(Boolean, default=False)
    session_token = Column(String(64), nullable=True)

    workshop = relationship("Workshop", back_populates="participants")
    answers = relationship("Answer", back_populates="participant", cascade="all, delete-orphan")
    ai_questions = relationship("AIQuestionLog", back_populates="participant", cascade="all, delete-orphan")


class Round(Base):
    __tablename__ = "rounds"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    workshop_id = Column(Integer, ForeignKey("workshops.id"), nullable=False)
    round_number = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    objective = Column(Text, nullable=True)
    status = Column(SAEnum(RoundStatus), default=RoundStatus.LOCKED)
    discussion_time = Column(Integer, default=15)
    input_time = Column(Integer, default=5)
    timer_started_at = Column(DateTime, nullable=True)
    timer_phase = Column(String(20), nullable=True)

    workshop = relationship("Workshop", back_populates="rounds")
    questions = relationship("Question", back_populates="round", cascade="all, delete-orphan", order_by="Question.order")
    group_results = relationship("GroupRoundResult", back_populates="round", cascade="all, delete-orphan")
    synthesis_results = relationship("SynthesisResult", back_populates="round", cascade="all, delete-orphan")
    host_inputs = relationship("HostInput", back_populates="round", cascade="all, delete-orphan")


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    round_id = Column(Integer, ForeignKey("rounds.id"), nullable=False)
    content = Column(Text, nullable=False)
    order = Column(Integer, nullable=False, default=0)

    round = relationship("Round", back_populates="questions")
    answers = relationship("Answer", back_populates="question", cascade="all, delete-orphan")


class Answer(Base):
    __tablename__ = "answers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    participant_id = Column(Integer, ForeignKey("participants.id"), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    question = relationship("Question", back_populates="answers")
    participant = relationship("Participant", back_populates="answers")


class GroupRoundResult(Base):
    __tablename__ = "group_round_results"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    round_id = Column(Integer, ForeignKey("rounds.id"), nullable=False)
    group_id = Column(Integer, nullable=False)
    status = Column(SAEnum(GroupResultStatus), default=GroupResultStatus.PENDING)
    original_content = Column(Text, nullable=True)
    edited_content = Column(Text, nullable=True)
    version = Column(Integer, default=1)
    validation_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    round = relationship("Round", back_populates="group_results")


class SynthesisResult(Base):
    __tablename__ = "synthesis_results"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    workshop_id = Column(Integer, ForeignKey("workshops.id"), nullable=False)
    round_id = Column(Integer, ForeignKey("rounds.id"), nullable=False)
    status = Column(SAEnum(GroupResultStatus), default=GroupResultStatus.PENDING)
    original_content = Column(Text, nullable=True)
    edited_content = Column(Text, nullable=True)
    validation_error = Column(Text, nullable=True)
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    workshop = relationship("Workshop", back_populates="synthesis_results")
    round = relationship("Round", back_populates="synthesis_results")


class HostInput(Base):
    __tablename__ = "host_inputs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    workshop_id = Column(Integer, ForeignKey("workshops.id"), nullable=False)
    round_id = Column(Integer, ForeignKey("rounds.id"), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    workshop = relationship("Workshop", back_populates="host_inputs")
    round = relationship("Round", back_populates="host_inputs")


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    workshop_id = Column(Integer, ForeignKey("workshops.id"), nullable=False)
    original_filename = Column(String(500), nullable=False)
    stored_filename = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=False)
    content_type = Column(String(120), nullable=False)
    chunk_count = Column(Integer, default=0)
    embedding_model = Column(String(100), nullable=False, default="text-embedding-3-small")
    upload_params = Column(Text, nullable=True)
    is_deleted = Column(Boolean, default=False)
    uploaded_at = Column(DateTime, default=_utcnow)

    workshop = relationship("Workshop", back_populates="knowledge_docs")


class AIQuestionLog(Base):
    __tablename__ = "ai_question_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    workshop_id = Column(Integer, ForeignKey("workshops.id"), nullable=False)
    participant_id = Column(Integer, ForeignKey("participants.id"), nullable=False)
    round_id = Column(Integer, ForeignKey("rounds.id"), nullable=True)
    group_id = Column(Integer, nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    workshop = relationship("Workshop", back_populates="ai_questions")
    participant = relationship("Participant", back_populates="ai_questions")
