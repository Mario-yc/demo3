import logging
import asyncio
from typing import List, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from dependencies import get_ws_manager, get_ai_service
from models import (
    Round, Question, Answer, Participant, GroupRoundResult,
    SynthesisResult, GroupResultStatus, Workshop, HostInput,
    WorkshopStatus,
)
from schemas import (
    AnswerSubmit, AnswerOut, QuestionOut, GroupRoundResultOut,
    SynthesisResultOut, GroupResultEdit, GroupResultMemberEdit,
    GroupAITrigger, GroupLeaderTransfer, ParticipantOut,
)
from services.ai_service import DeepSeekService
from websocket_manager import WebSocketManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["rounds"])

_group_ai_locks: dict[tuple[int, int, int], asyncio.Lock] = {}
_synthesis_locks: dict[int, asyncio.Lock] = {}


def _get_group_ai_lock(workshop_id: int, group_id: int, round_id: int) -> asyncio.Lock:
    key = (workshop_id, group_id, round_id)
    if key not in _group_ai_locks:
        _group_ai_locks[key] = asyncio.Lock()
    return _group_ai_locks[key]


def _get_synthesis_lock(round_id: int) -> asyncio.Lock:
    if round_id not in _synthesis_locks:
        _synthesis_locks[round_id] = asyncio.Lock()
    return _synthesis_locks[round_id]


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timer_remaining_seconds(rd: Round) -> Optional[int]:
    if not rd.timer_started_at:
        return None
    elapsed = int((datetime.now(timezone.utc) - _as_utc(rd.timer_started_at)).total_seconds())
    return max((rd.discussion_time or 0) * 60 - elapsed, 0)


def _is_timer_expired(rd: Round) -> bool:
    remaining = _timer_remaining_seconds(rd)
    return remaining is not None and remaining <= 0


def _participant_out(p: Participant) -> ParticipantOut:
    return ParticipantOut(
        id=p.id,
        workshop_id=p.workshop_id,
        name=p.name,
        group_id=p.group_id,
        is_group_leader=p.is_group_leader,
    )


def _result_updated_at(gr: GroupRoundResult) -> Optional[str]:
    if not gr.updated_at:
        return None
    return _as_utc(gr.updated_at).isoformat().replace("+00:00", "Z")


# ── group question/answer endpoints ─────────────────────────────────────

@router.get("/groups/{group_id}/questions", response_model=List[QuestionOut])
async def get_group_questions(
    group_id: int,
    workshop_id: int = Query(...),
    round_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    w = await db.get(Workshop, workshop_id)
    if not w:
        raise HTTPException(status_code=404, detail="Workshop not found")
    if round_id is not None:
        active_round = await db.get(Round, round_id)
        if not active_round or active_round.workshop_id != workshop_id:
            raise HTTPException(status_code=404, detail="Round not found")
    else:
        result = await db.execute(
            select(Round).where(
                Round.workshop_id == workshop_id,
                Round.round_number == w.current_round,
            )
        )
        active_round = result.scalar_one_or_none()
    if not active_round:
        raise HTTPException(status_code=404, detail="No active round")
    result = await db.execute(
        select(Question).where(Question.round_id == active_round.id).order_by(Question.order)
    )
    questions = result.scalars().all()
    return [QuestionOut(id=q.id, round_id=q.round_id, content=q.content, order=q.order) for q in questions]


@router.post("/groups/{group_id}/answers", response_model=AnswerOut, status_code=201)
async def submit_group_answer(
    group_id: int, data: AnswerSubmit,
    db: AsyncSession = Depends(get_db),
    ws_manager: WebSocketManager = Depends(get_ws_manager),
):
    participant = await db.get(Participant, data.participant_id)
    if (
        not participant
        or participant.group_id != group_id
        or participant.session_token != data.session_token
    ):
        raise HTTPException(status_code=403, detail="Invalid participant")
    if not participant.is_group_leader:
        raise HTTPException(status_code=403, detail="Only group leader can submit answers")

    question = await db.get(Question, data.question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    rd = await db.get(Round, question.round_id)
    if not rd:
        raise HTTPException(status_code=404, detail="Round not found")
    if participant.workshop_id != rd.workshop_id:
        raise HTTPException(status_code=403, detail="Participant not in this workshop")
    w = await db.get(Workshop, rd.workshop_id)
    if w and w.is_review_mode:
        raise HTTPException(status_code=403, detail="当前为历史轮次查看模式，不能提交回答")
    if rd.status not in ("active", "input"):
        raise HTTPException(status_code=400, detail="Round is not accepting answers")
    if _is_timer_expired(rd):
        raise HTTPException(status_code=400, detail="Time is up; answers are closed")

    answer = Answer(question_id=data.question_id, participant_id=data.participant_id, content=data.content)
    db.add(answer)
    await db.commit()
    await db.refresh(answer)

    answer_out = AnswerOut(
        id=answer.id, question_id=answer.question_id, participant_id=answer.participant_id,
        content=answer.content, created_at=answer.created_at,
        participant_name=participant.name, group_id=participant.group_id,
    )
    await ws_manager.broadcast_new_answer(rd.workshop_id, group_id, answer_out.model_dump())
    return answer_out


@router.get("/groups/{group_id}/answers", response_model=List[AnswerOut])
async def get_group_answers(
    group_id: int,
    workshop_id: int = Query(...),
    round_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    w = await db.get(Workshop, workshop_id)
    if not w:
        raise HTTPException(status_code=404, detail="Workshop not found")
    if round_id is not None:
        active_round = await db.get(Round, round_id)
        if not active_round or active_round.workshop_id != workshop_id:
            raise HTTPException(status_code=404, detail="Round not found")
    else:
        result = await db.execute(
            select(Round).where(
                Round.workshop_id == workshop_id,
                Round.round_number == w.current_round,
            )
        )
        active_round = result.scalar_one_or_none()
    if not active_round:
        return []

    result = await db.execute(
        select(Question).where(Question.round_id == active_round.id).order_by(Question.order)
    )
    questions = result.scalars().all()
    q_ids = [q.id for q in questions]

    result = await db.execute(
        select(Answer, Participant)
        .join(Participant, Answer.participant_id == Participant.id)
        .where(Answer.question_id.in_(q_ids), Participant.group_id == group_id)
        .order_by(Answer.created_at)
    )
    return [
        AnswerOut(
            id=a.id, question_id=a.question_id, participant_id=a.participant_id,
            content=a.content, created_at=a.created_at,
            participant_name=p.name, group_id=p.group_id,
        )
        for a, p in result.all()
    ]


@router.put("/groups/{group_id}/leader", response_model=List[ParticipantOut])
async def transfer_group_leader(
    group_id: int,
    data: GroupLeaderTransfer,
    db: AsyncSession = Depends(get_db),
    ws_manager: WebSocketManager = Depends(get_ws_manager),
):
    participant = await db.get(Participant, data.participant_id)
    if (
        not participant
        or participant.workshop_id != data.workshop_id
        or participant.group_id != group_id
        or participant.session_token != data.session_token
    ):
        raise HTTPException(status_code=403, detail="Invalid participant")
    if not participant.is_group_leader:
        raise HTTPException(status_code=403, detail="Only group leader can transfer leadership")

    new_leader = await db.get(Participant, data.new_leader_participant_id)
    if (
        not new_leader
        or new_leader.workshop_id != data.workshop_id
        or new_leader.group_id != group_id
    ):
        raise HTTPException(status_code=400, detail="New leader must be in this group")

    old_leader = participant
    result = await db.execute(
        select(Participant)
        .where(Participant.workshop_id == data.workshop_id, Participant.group_id == group_id)
        .order_by(Participant.id)
    )
    members = list(result.scalars().all())
    for member in members:
        member.is_group_leader = member.id == new_leader.id

    await db.commit()
    result = await db.execute(
        select(Participant)
        .where(Participant.workshop_id == data.workshop_id, Participant.group_id == group_id)
        .order_by(Participant.id)
    )
    updated_members = list(result.scalars().all())
    out = [_participant_out(member) for member in updated_members]
    await ws_manager.broadcast_group_leader_changed(
        data.workshop_id,
        group_id,
        [member.model_dump() for member in out],
        old_leader_participant_id=old_leader.id,
        old_leader_name=old_leader.name,
        new_leader_participant_id=new_leader.id,
        new_leader_name=new_leader.name,
        changed_by="member",
    )
    return out


# ── AI generation ───────────────────────────────────────────────────────

@router.post("/groups/{group_id}/ai-generate", response_model=GroupRoundResultOut)
async def trigger_group_ai(
    group_id: int, data: GroupAITrigger, workshop_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
    ai_service: DeepSeekService = Depends(get_ai_service),
    ws_manager: WebSocketManager = Depends(get_ws_manager),
):
    w = await db.get(Workshop, workshop_id)
    if not w:
        raise HTTPException(status_code=404, detail="Workshop not found")
    if w.is_review_mode:
        raise HTTPException(status_code=403, detail="当前为历史轮次查看模式，不能重新触发 AI 提炼")

    participant = await db.get(Participant, data.participant_id)
    if (
        not participant
        or participant.workshop_id != workshop_id
        or participant.group_id != group_id
        or participant.session_token != data.session_token
    ):
        raise HTTPException(status_code=403, detail="Invalid participant")
    if not participant.is_group_leader:
        raise HTTPException(status_code=403, detail="Only group leader can trigger AI extraction")

    if data.round_id is not None:
        active_round = await db.get(Round, data.round_id)
        if not active_round or active_round.workshop_id != workshop_id:
            raise HTTPException(status_code=404, detail="Round not found")
        if active_round.round_number != w.current_round:
            raise HTTPException(status_code=400, detail="AI extraction must be triggered for the current round")
    else:
        result = await db.execute(
            select(Round).where(
                Round.workshop_id == workshop_id,
                Round.round_number == w.current_round,
            )
        )
        active_round = result.scalar_one_or_none()
    if not active_round:
        raise HTTPException(status_code=404, detail="No active round")

    lock = _get_group_ai_lock(workshop_id, group_id, active_round.id)
    if lock.locked():
        raise HTTPException(status_code=400, detail="AI already generating")

    async with lock:
        return await _trigger_group_ai_locked(
            group_id,
            data,
            workshop_id,
            db,
            ai_service,
            ws_manager,
            w,
            active_round,
        )


async def _trigger_group_ai_locked(
    group_id: int,
    data: GroupAITrigger,
    workshop_id: int,
    db: AsyncSession,
    ai_service: DeepSeekService,
    ws_manager: WebSocketManager,
    w: Workshop,
    active_round: Round,
) -> GroupRoundResultOut:
    # Check existing
    result = await db.execute(
        select(GroupRoundResult).where(
            GroupRoundResult.round_id == active_round.id,
            GroupRoundResult.group_id == group_id,
        )
    )
    gr = result.scalar_one_or_none()
    if gr and gr.status == GroupResultStatus.PROCESSING:
        raise HTTPException(status_code=400, detail="AI already generating")

    if not gr:
        gr = GroupRoundResult(round_id=active_round.id, group_id=group_id, status=GroupResultStatus.PROCESSING)
        db.add(gr)
    else:
        gr.status = GroupResultStatus.PROCESSING
        gr.validation_error = None
    await db.commit()
    await db.refresh(gr)
    await ws_manager.broadcast_ai_result_status(
        workshop_id,
        group_id,
        active_round.round_number,
        gr.status.value,
        gr.validation_error,
        _result_updated_at(gr),
    )

    # Gather answers
    result = await db.execute(
        select(Question).where(Question.round_id == active_round.id).order_by(Question.order)
    )
    questions = result.scalars().all()
    q_ids = [q.id for q in questions]

    result = await db.execute(
        select(Answer, Participant, Question)
        .join(Participant, Answer.participant_id == Participant.id)
        .join(Question, Answer.question_id == Question.id)
        .where(Answer.question_id.in_(q_ids), Participant.group_id == group_id)
        .order_by(Question.order, Answer.created_at)
    )
    rows = result.all()
    if not rows:
        gr.status = GroupResultStatus.VALIDATION_FAILED
        gr.validation_error = "本组暂无回答，无法进行 AI 提炼"
        await db.commit()
        await db.refresh(gr)
        await ws_manager.broadcast_ai_result_status(
            workshop_id,
            group_id,
            active_round.round_number,
            gr.status.value,
            gr.validation_error,
            _result_updated_at(gr),
        )
        raise HTTPException(status_code=400, detail=gr.validation_error)

    answers_text = ""
    for a, _p, q in rows:
        answers_text += f"**{q.content}**\n回答：{a.content}\n\n"

    try:
        round_num = active_round.round_number
        if round_num == 1:
            content, version, err = await ai_service.generate_group_dimensions(answers_text, group_id)
        elif round_num == 2:
            framework = await _get_host_input(workshop_id, 1, db)
            content, version, err = await ai_service.generate_group_layer_table(answers_text, framework, group_id)
        elif round_num == 3:
            consensus = await _get_synthesis_or_host_input(workshop_id, 2, db)
            content, version, err = await ai_service.generate_group_behaviors(answers_text, consensus, group_id)
        else:
            model_draft = await _get_synthesis_or_host_input(workshop_id, 3, db)
            content, version, err = await ai_service.generate_group_applications(answers_text, model_draft, group_id)

        gr.original_content = content
        gr.version = version
        gr.validation_error = err
        if err:
            gr.status = GroupResultStatus.VALIDATION_FAILED
        else:
            gr.status = GroupResultStatus.READY
    except Exception as e:
        logger.error(f"AI generation failed: {e}")
        gr.status = GroupResultStatus.VALIDATION_FAILED
        gr.validation_error = str(e)

    await db.commit()
    await db.refresh(gr)

    out = GroupRoundResultOut.model_validate(gr)
    result_data = out.model_dump(mode="json")
    await ws_manager.broadcast_ai_result_status(
        workshop_id,
        group_id,
        round_num,
        gr.status.value,
        gr.validation_error,
        _result_updated_at(gr),
    )
    await ws_manager.broadcast_result_ready(workshop_id, group_id, round_num, result_data)
    return out


@router.get("/groups/{group_id}/ai-result", response_model=GroupRoundResultOut)
async def get_group_ai_result(
    group_id: int,
    workshop_id: int = Query(...),
    round_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if round_id is not None:
        active_round = await db.get(Round, round_id)
        if not active_round or active_round.workshop_id != workshop_id:
            raise HTTPException(status_code=404, detail="Round not found")
    else:
        result = await db.execute(
            select(Round).where(Round.workshop_id == workshop_id, Round.round_number == select(Workshop.current_round).where(Workshop.id == workshop_id).scalar_subquery())
        )
        active_round = result.scalar_one_or_none()
    if not active_round:
        raise HTTPException(status_code=404, detail="No active round")

    result = await db.execute(
        select(GroupRoundResult).where(
            GroupRoundResult.round_id == active_round.id, GroupRoundResult.group_id == group_id,
        ).order_by(GroupRoundResult.created_at.desc()).limit(1)
    )
    gr = result.scalar_one_or_none()
    if not gr:
        raise HTTPException(status_code=404, detail="No result yet")
    return GroupRoundResultOut.model_validate(gr)


@router.put("/groups/{group_id}/ai-result", response_model=GroupRoundResultOut)
async def edit_group_ai_result(
    group_id: int,
    data: GroupResultMemberEdit,
    workshop_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
    ws_manager: WebSocketManager = Depends(get_ws_manager),
):
    participant = await db.get(Participant, data.participant_id)
    if (
        not participant
        or participant.workshop_id != workshop_id
        or participant.group_id != group_id
        or participant.session_token != data.session_token
    ):
        raise HTTPException(status_code=403, detail="Invalid participant")
    if not participant.is_group_leader:
        raise HTTPException(status_code=403, detail="Only group leader can edit AI result")

    w = await db.get(Workshop, workshop_id)
    if not w:
        raise HTTPException(status_code=404, detail="Workshop not found")
    if w.is_review_mode:
        raise HTTPException(status_code=403, detail="当前为历史轮次查看模式，不能编辑 AI 提炼结果")
    if not data.edited_content.strip():
        raise HTTPException(status_code=400, detail="编辑内容不能为空")

    if data.round_id is not None:
        active_round = await db.get(Round, data.round_id)
        if not active_round or active_round.workshop_id != workshop_id:
            raise HTTPException(status_code=404, detail="Round not found")
    else:
        result = await db.execute(
            select(Round).where(Round.workshop_id == workshop_id, Round.round_number == w.current_round)
        )
        active_round = result.scalar_one_or_none()
    if not active_round:
        raise HTTPException(status_code=404, detail="No active round")

    result = await db.execute(
        select(GroupRoundResult).where(
            GroupRoundResult.round_id == active_round.id,
            GroupRoundResult.group_id == group_id,
        ).order_by(GroupRoundResult.created_at.desc()).limit(1)
    )
    gr = result.scalar_one_or_none()
    if not gr:
        raise HTTPException(status_code=404, detail="No result yet")

    gr.edited_content = data.edited_content.strip()
    gr.status = GroupResultStatus.EDITED
    await db.commit()
    await db.refresh(gr)

    out = GroupRoundResultOut.model_validate(gr)
    await ws_manager.broadcast_ai_result_status(
        workshop_id,
        group_id,
        active_round.round_number,
        gr.status.value,
        gr.validation_error,
        _result_updated_at(gr),
    )
    await ws_manager.broadcast_result_ready(workshop_id, group_id, active_round.round_number, out.model_dump(mode="json"))
    return out


# ── synthesis ───────────────────────────────────────────────────────────

@router.post("/rounds/{round_id}/synthesize", response_model=SynthesisResultOut)
async def trigger_synthesis(
    round_id: int,
    db: AsyncSession = Depends(get_db),
    ai_service: DeepSeekService = Depends(get_ai_service),
    ws_manager: WebSocketManager = Depends(get_ws_manager),
):
    rd = await db.get(Round, round_id)
    if not rd:
        raise HTTPException(status_code=404, detail="Round not found")
    w = await db.get(Workshop, rd.workshop_id)
    if w and w.status == WorkshopStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="本次研讨已结束，当前仅支持查看会议资料。")

    lock = _get_synthesis_lock(round_id)
    if lock.locked():
        raise HTTPException(status_code=400, detail="综合提炼正在生成中，请稍后再试")
    async with lock:
        return await _trigger_synthesis_locked(round_id, rd, db, ai_service, ws_manager)


async def _trigger_synthesis_locked(
    round_id: int,
    rd: Round,
    db: AsyncSession,
    ai_service: DeepSeekService,
    ws_manager: WebSocketManager,
) -> SynthesisResultOut:

    result = await db.execute(
        select(GroupRoundResult).where(GroupRoundResult.round_id == round_id)
    )
    group_results = result.scalars().all()
    ready_results = [g for g in group_results if g.original_content and g.status in (GroupResultStatus.READY, GroupResultStatus.EDITED)]
    if len(ready_results) < 2:
        raise HTTPException(
            status_code=400,
            detail="当前可综合的提炼结果不足，请至少等待两个小组提交提炼结果后再进行综合提炼。",
        )

    # Upsert synthesis result
    result = await db.execute(
        select(SynthesisResult).where(SynthesisResult.round_id == round_id, SynthesisResult.workshop_id == rd.workshop_id)
    )
    sr = result.scalar_one_or_none()
    if not sr:
        sr = SynthesisResult(workshop_id=rd.workshop_id, round_id=round_id, status=GroupResultStatus.PROCESSING)
        db.add(sr)
    else:
        sr.status = GroupResultStatus.PROCESSING
        sr.validation_error = None
    await db.commit()
    await db.refresh(sr)

    groups_data = [{"group_id": g.group_id, "content": g.edited_content or g.original_content} for g in ready_results]

    try:
        if rd.round_number == 1:
            content, version, err = await ai_service.synthesize_dimensions(groups_data)
        elif rd.round_number == 2:
            content, version, err = await ai_service.synthesize_layer_table(groups_data)
        elif rd.round_number == 3:
            content, version, err = await ai_service.synthesize_behaviors(groups_data)
        else:
            content, version, err = await ai_service.synthesize_applications(groups_data)

        sr.original_content = content
        sr.version = version
        sr.validation_error = err
        sr.status = GroupResultStatus.VALIDATION_FAILED if err else GroupResultStatus.READY
    except Exception as e:
        logger.error(f"Synthesis failed: {e}")
        sr.status = GroupResultStatus.VALIDATION_FAILED
        sr.validation_error = str(e)

    await db.commit()
    await db.refresh(sr)

    out = SynthesisResultOut.model_validate(sr)
    await ws_manager.broadcast_synthesis_ready(rd.workshop_id, rd.round_number, out.model_dump())
    return out


@router.get("/rounds/{round_id}/synthesis", response_model=SynthesisResultOut)
async def get_synthesis(round_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SynthesisResult).where(SynthesisResult.round_id == round_id).order_by(SynthesisResult.created_at.desc()).limit(1)
    )
    sr = result.scalar_one_or_none()
    if not sr:
        raise HTTPException(status_code=404, detail="No synthesis yet")
    return SynthesisResultOut.model_validate(sr)


# ── helpers ──────────────────────────────────────────────────────────────

async def _get_host_input(workshop_id: int, round_number: int, db: AsyncSession) -> str:
    result = await db.execute(
        select(Round).where(Round.workshop_id == workshop_id, Round.round_number == round_number)
    )
    rd = result.scalar_one_or_none()
    if not rd:
        return ""
    result = await db.execute(
        select(HostInput).where(HostInput.round_id == rd.id).order_by(HostInput.created_at.desc()).limit(1)
    )
    hi = result.scalar_one_or_none()
    return hi.content if hi else ""


async def _get_synthesis_or_host_input(workshop_id: int, round_number: int, db: AsyncSession) -> str:
    result = await db.execute(
        select(Round).where(Round.workshop_id == workshop_id, Round.round_number == round_number)
    )
    rd = result.scalar_one_or_none()
    if not rd:
        return ""
    result = await db.execute(
        select(SynthesisResult).where(SynthesisResult.round_id == rd.id).order_by(SynthesisResult.created_at.desc()).limit(1)
    )
    sr = result.scalar_one_or_none()
    if sr and sr.original_content:
        return sr.edited_content or sr.original_content
    return await _get_host_input(workshop_id, round_number, db)
