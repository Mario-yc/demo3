import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from dependencies import get_ws_manager, get_ai_service, get_export_service
from models import (
    Workshop, Participant, Round, Question, Answer, GroupRoundResult,
    SynthesisResult, HostInput, KnowledgeDocument, WorkshopStatus, RoundStatus,
    GroupResultStatus,
)
from schemas import (
    WorkshopCreate, WorkshopCreateResponse, WorkshopMemberView, WorkshopHostView,
    WorkshopJoinRequest, ParticipantOut, ParticipantWithToken, RoundOut, QuestionOut, AnswerOut,
    GroupInfo, RoundInfo, GroupRoundResultOut, SynthesisResultOut, HostInputOut,
    HostInputCreate, RoundSettingsUpdate, GroupResultEdit, SynthesisResultEdit,
    KnowledgeDocumentOut, ExportResponse,
    ValidateHostRequest, ValidateInviteRequest, ValidateResponse,
    HostGroupLeaderSet,
)
from services.ai_service import DeepSeekService
from services.export_service import ExportService
from websocket_manager import WebSocketManager
from seed import ROUNDS_DATA

import secrets

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workshops", tags=["workshops"])

_workshop_action_locks: dict[tuple[int, str], asyncio.Lock] = {}


def _get_workshop_action_lock(workshop_id: int, action: str) -> asyncio.Lock:
    key = (workshop_id, action)
    if key not in _workshop_action_locks:
        _workshop_action_locks[key] = asyncio.Lock()
    return _workshop_action_locks[key]


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _round_timer_remaining(r: Round) -> Optional[int]:
    if not r.timer_started_at or not r.timer_phase:
        return None
    duration_minutes = r.discussion_time
    elapsed = int((datetime.now(timezone.utc) - _as_utc(r.timer_started_at)).total_seconds())
    return max(duration_minutes * 60 - elapsed, 0)


# ── helpers ──────────────────────────────────────────────────────────────

async def _load_workshop(workshop_id: int, db: AsyncSession) -> Workshop:
    result = await db.execute(
        select(Workshop)
        .options(
            selectinload(Workshop.participants),
            selectinload(Workshop.rounds).selectinload(Round.questions).selectinload(Question.answers).selectinload(Answer.participant),
            selectinload(Workshop.rounds).selectinload(Round.group_results),
            selectinload(Workshop.rounds).selectinload(Round.synthesis_results),
            selectinload(Workshop.rounds).selectinload(Round.host_inputs),
            selectinload(Workshop.host_inputs),
            selectinload(Workshop.synthesis_results),
            selectinload(Workshop.knowledge_docs),
        )
        .where(Workshop.id == workshop_id)
    )
    w = result.scalar_one_or_none()
    if not w:
        raise HTTPException(status_code=404, detail="Workshop not found")
    return w


async def _list_unified_knowledge_docs(db: AsyncSession) -> list[KnowledgeDocument]:
    result = await db.execute(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.is_deleted == False)
        .order_by(KnowledgeDocument.uploaded_at.desc())
    )
    return list(result.scalars().all())


async def _build_host_view_with_docs(w: Workshop, db: AsyncSession) -> WorkshopHostView:
    return _build_host_view(w, await _list_unified_knowledge_docs(db))


def _build_host_view(w: Workshop, knowledge_docs: Optional[list[KnowledgeDocument]] = None) -> WorkshopHostView:
    group_count = w.group_count or 4
    groups: dict[int, list] = {gid: [] for gid in range(1, group_count + 1)}
    for p in w.participants:
        groups.setdefault(p.group_id, []).append(p)
    group_infos = []
    for gid in sorted(groups):
        leader = next((p.name for p in groups[gid] if p.is_group_leader), None)
        group_infos.append(GroupInfo(
            group_id=gid,
            participant_count=len(groups[gid]),
            leader_name=leader,
            members=[ParticipantOut(id=p.id, workshop_id=p.workshop_id, name=p.name, group_id=p.group_id, is_group_leader=p.is_group_leader) for p in groups[gid]],
        ))

    round_infos = []
    for rd in sorted(w.rounds, key=lambda r: r.round_number):
        syn = next((sr for sr in rd.synthesis_results), None)
        hi = next((h for h in rd.host_inputs), None)
        answers = []
        for q in rd.questions:
            for a in q.answers:
                p = a.participant
                answers.append(AnswerOut(
                    id=a.id,
                    question_id=a.question_id,
                    participant_id=a.participant_id,
                    content=a.content,
                    created_at=a.created_at,
                    participant_name=p.name if p else None,
                    group_id=p.group_id if p else None,
                ))
        round_infos.append(RoundInfo(
            id=rd.id, round_number=rd.round_number, title=rd.title,
            objective=rd.objective, status=rd.status,
            discussion_time=rd.discussion_time, input_time=rd.input_time,
            timer_started_at=rd.timer_started_at, timer_phase=rd.timer_phase,
            timer_remaining_seconds=_round_timer_remaining(rd),
            questions=[QuestionOut(id=q.id, round_id=q.round_id, content=q.content, order=q.order) for q in rd.questions],
            answers=sorted(answers, key=lambda a: a.created_at),
            group_results=[GroupRoundResultOut.model_validate(gr) for gr in rd.group_results],
            synthesis=SynthesisResultOut.model_validate(syn) if syn else None,
            host_input=HostInputOut.model_validate(hi) if hi else None,
        ))

    return WorkshopHostView(
        id=w.id, title=w.title, host_name=w.host_name,
        invite_code=w.invite_code, host_code=w.host_code, kb_admin_code=w.kb_admin_code,
        group_count=group_count,
        current_round=w.current_round,
        flow_round_number=w.flow_round_number or w.current_round,
        is_review_mode=bool(w.is_review_mode),
        status=w.status, created_at=w.created_at,
        groups=group_infos, rounds=round_infos,
        knowledge_docs=[
            KnowledgeDocumentOut.model_validate(d)
            for d in (knowledge_docs if knowledge_docs is not None else w.knowledge_docs)
            if not d.is_deleted
        ],
    )


def _build_member_view(w: Workshop, participant: Optional[Participant] = None) -> WorkshopMemberView:
    rounds_out = []
    for rd in sorted(w.rounds, key=lambda r: r.round_number):
        rounds_out.append(RoundOut(
            id=rd.id, workshop_id=rd.workshop_id, round_number=rd.round_number,
            title=rd.title, objective=rd.objective, status=rd.status,
            discussion_time=rd.discussion_time, input_time=rd.input_time,
            timer_started_at=rd.timer_started_at, timer_phase=rd.timer_phase,
            timer_remaining_seconds=_round_timer_remaining(rd),
            questions=[QuestionOut(id=q.id, round_id=q.round_id, content=q.content, order=q.order) for q in rd.questions],
        ))
    p_out = None
    group_members = []
    if participant:
        p_out = ParticipantWithToken(
            id=participant.id, workshop_id=participant.workshop_id,
            name=participant.name, group_id=participant.group_id,
            is_group_leader=participant.is_group_leader,
            session_token=participant.session_token or "",
        )
        group_members = [
            ParticipantOut(
                id=p.id,
                workshop_id=p.workshop_id,
                name=p.name,
                group_id=p.group_id,
                is_group_leader=p.is_group_leader,
            )
            for p in sorted(w.participants, key=lambda item: item.id)
            if p.group_id == participant.group_id
        ]
    return WorkshopMemberView(
        id=w.id, title=w.title, host_name=w.host_name,
        invite_code=w.invite_code, group_count=w.group_count or 4, current_round=w.current_round,
        flow_round_number=w.flow_round_number or w.current_round,
        is_review_mode=bool(w.is_review_mode),
        status=w.status, created_at=w.created_at,
        participant=p_out, group_members=group_members, rounds=rounds_out,
    )


def _round_to_dict(r: Round) -> dict:
    return {
        "id": r.id, "round_number": r.round_number, "title": r.title,
        "objective": r.objective, "status": r.status.value if r.status else "locked",
        "discussion_time": r.discussion_time, "input_time": r.input_time,
        "timer_started_at": _as_utc(r.timer_started_at).isoformat().replace("+00:00", "Z") if r.timer_started_at else None,
        "timer_phase": r.timer_phase,
        "timer_remaining_seconds": _round_timer_remaining(r),
        "questions": [{"id": q.id, "content": q.content, "order": q.order} for q in r.questions],
    }


def _participant_out(p: Participant) -> ParticipantOut:
    return ParticipantOut(
        id=p.id,
        workshop_id=p.workshop_id,
        name=p.name,
        group_id=p.group_id,
        is_group_leader=p.is_group_leader,
    )


# ── endpoints ────────────────────────────────────────────────────────────

@router.post("", response_model=WorkshopCreateResponse, status_code=201)
async def create_workshop(data: WorkshopCreate, db: AsyncSession = Depends(get_db)):
    w = Workshop(
        title=data.title,
        host_name=data.host_name,
        group_count=data.group_count,
        current_round=1,
        flow_round_number=1,
        is_review_mode=False,
    )
    db.add(w)
    await db.flush()

    for rd in ROUNDS_DATA:
        st = RoundStatus.ACTIVE if rd["round_number"] == 1 else RoundStatus.LOCKED
        r = Round(
            workshop_id=w.id, round_number=rd["round_number"],
            title=rd["title"], objective=rd["objective"],
            status=st, discussion_time=rd["discussion_time"],
            input_time=rd["input_time"],
            timer_started_at=None, timer_phase=None,
        )
        db.add(r)
        await db.flush()
        for i, qc in enumerate(rd["questions"], 1):
            db.add(Question(round_id=r.id, content=qc, order=i))

    await db.commit()
    return WorkshopCreateResponse(
        id=w.id, title=w.title, host_name=w.host_name,
        group_count=w.group_count or 4,
        invite_code=w.invite_code, host_code=w.host_code, kb_admin_code=w.kb_admin_code,
        created_at=w.created_at,
    )


@router.post("/validate-host", response_model=ValidateResponse)
async def validate_host(data: ValidateHostRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Workshop).where(Workshop.host_code == data.host_code))
    w = result.scalar_one_or_none()
    if not w:
        return ValidateResponse(valid=False)
    return ValidateResponse(valid=True, workshop_id=w.id, workshop_title=w.title)


@router.post("/validate-invite", response_model=ValidateResponse)
async def validate_invite(data: ValidateInviteRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Workshop).where(Workshop.invite_code == data.invite_code))
    w = result.scalar_one_or_none()
    if not w or w.status == WorkshopStatus.COMPLETED:
        return ValidateResponse(valid=False)
    return ValidateResponse(valid=True, workshop_id=w.id, workshop_title=w.title)


@router.get("/{workshop_id}/host", response_model=WorkshopHostView)
async def get_host_view(workshop_id: int, code: str = Query(...), db: AsyncSession = Depends(get_db)):
    w = await _load_workshop(workshop_id, db)
    if w.host_code != code:
        raise HTTPException(status_code=403, detail="Invalid host code")
    return await _build_host_view_with_docs(w, db)


@router.get("/{workshop_id}", response_model=WorkshopMemberView)
async def get_workshop(
    workshop_id: int,
    participant_id: Optional[int] = Query(default=None),
    session_token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    w = await _load_workshop(workshop_id, db)
    participant = None
    if participant_id is not None and session_token:
        candidate = next((p for p in w.participants if p.id == participant_id), None)
        if candidate and candidate.session_token == session_token:
            participant = candidate
    return _build_member_view(w, participant)


@router.post("/{workshop_id}/join", response_model=ParticipantWithToken)
async def join_workshop(workshop_id: int, data: WorkshopJoinRequest, db: AsyncSession = Depends(get_db)):
    w = await _load_workshop(workshop_id, db)
    if w.status == WorkshopStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Workshop already completed")
    if w.invite_code != data.invite_code:
        raise HTTPException(status_code=403, detail="Invalid invite code")

    # Balanced group assignment
    group_count = w.group_count or 4
    counts = {gid: 0 for gid in range(1, group_count + 1)}
    for p in w.participants:
        counts[p.group_id] = counts.get(p.group_id, 0) + 1
    min_count = min(counts.values())
    candidates = [g for g, c in counts.items() if c == min_count]
    import random
    assigned_group = random.choice(candidates)

    is_leader = counts[assigned_group] == 0  # first member in group is leader

    token = secrets.token_hex(32)
    p = Participant(
        workshop_id=workshop_id, name=data.name,
        group_id=assigned_group, is_group_leader=is_leader,
        session_token=token,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return ParticipantWithToken(
        id=p.id, workshop_id=p.workshop_id, name=p.name,
        group_id=p.group_id, is_group_leader=p.is_group_leader,
        session_token=token,
    )


@router.post("/{workshop_id}/unlock-round", response_model=WorkshopHostView)
async def unlock_round(
    workshop_id: int, code: str = Query(...),
    db: AsyncSession = Depends(get_db),
    ws_manager: WebSocketManager = Depends(get_ws_manager),
):
    w = await _load_workshop(workshop_id, db)
    if w.host_code != code:
        raise HTTPException(status_code=403, detail="Invalid host code")
    if w.status == WorkshopStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="本次研讨已结束，当前仅支持查看会议资料。")

    flow_round = w.flow_round_number or w.current_round
    current = next((r for r in w.rounds if r.round_number == flow_round), None)
    if not current:
        raise HTTPException(status_code=400, detail="No current round")

    w.is_review_mode = False
    if flow_round < 4:
        current.status = RoundStatus.COMPLETED
        next_rd = next((r for r in w.rounds if r.round_number == flow_round + 1), None)
        if next_rd:
            next_rd.status = RoundStatus.ACTIVE
            next_rd.timer_started_at = None
            next_rd.timer_phase = None
            w.current_round = flow_round + 1
            w.flow_round_number = flow_round + 1
            await db.commit()
            w = await _load_workshop(workshop_id, db)
            active_rd = next((r for r in w.rounds if r.round_number == w.current_round), None)
            if active_rd:
                await ws_manager.broadcast_round_change(workshop_id, w.current_round, _round_to_dict(active_rd))
    else:
        current.status = RoundStatus.COMPLETED
        w.status = WorkshopStatus.COMPLETED
        w.current_round = flow_round
        w.flow_round_number = flow_round
        await db.commit()
        w = await _load_workshop(workshop_id, db)
        await ws_manager.broadcast_workshop_completed(workshop_id)

    return await _build_host_view_with_docs(w, db)


@router.post("/{workshop_id}/previous-round", response_model=WorkshopHostView)
async def previous_round(
    workshop_id: int, code: str = Query(...),
    db: AsyncSession = Depends(get_db),
    ws_manager: WebSocketManager = Depends(get_ws_manager),
):
    w = await _load_workshop(workshop_id, db)
    if w.host_code != code:
        raise HTTPException(status_code=403, detail="Invalid host code")
    if w.status == WorkshopStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="研讨已结束，不能回退轮次")
    if w.is_review_mode:
        raise HTTPException(status_code=400, detail="当前已在历史轮次查看模式")

    flow_round = w.flow_round_number or w.current_round
    if flow_round <= 1:
        raise HTTPException(status_code=400, detail="第一轮不能回到上一轮")

    review_round_number = flow_round - 1
    review_round = next((r for r in w.rounds if r.round_number == review_round_number), None)
    if not review_round:
        raise HTTPException(status_code=404, detail="上一轮不存在")

    w.current_round = review_round_number
    w.is_review_mode = True
    await db.commit()
    w = await _load_workshop(workshop_id, db)
    review_round = next((r for r in w.rounds if r.round_number == w.current_round), None)
    if review_round:
        await ws_manager.broadcast_round_change(workshop_id, w.current_round, _round_to_dict(review_round))
    return await _build_host_view_with_docs(w, db)


@router.post("/{workshop_id}/return-current-round", response_model=WorkshopHostView)
async def return_current_round(
    workshop_id: int, code: str = Query(...),
    db: AsyncSession = Depends(get_db),
    ws_manager: WebSocketManager = Depends(get_ws_manager),
):
    w = await _load_workshop(workshop_id, db)
    if w.host_code != code:
        raise HTTPException(status_code=403, detail="Invalid host code")
    if w.status == WorkshopStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="研讨已结束，不能切换轮次")

    flow_round = w.flow_round_number or w.current_round
    active_round = next((r for r in w.rounds if r.round_number == flow_round), None)
    if not active_round:
        raise HTTPException(status_code=404, detail="当前轮次不存在")

    w.current_round = flow_round
    w.is_review_mode = False
    await db.commit()
    w = await _load_workshop(workshop_id, db)
    active_round = next((r for r in w.rounds if r.round_number == w.current_round), None)
    if active_round:
        await ws_manager.broadcast_round_change(workshop_id, w.current_round, _round_to_dict(active_round))
    return await _build_host_view_with_docs(w, db)


@router.post("/{workshop_id}/timer/start", response_model=WorkshopHostView)
async def start_round_timer(
    workshop_id: int, code: str = Query(...),
    db: AsyncSession = Depends(get_db),
    ws_manager: WebSocketManager = Depends(get_ws_manager),
):
    w = await _load_workshop(workshop_id, db)
    if w.host_code != code:
        raise HTTPException(status_code=403, detail="Invalid host code")
    if w.status == WorkshopStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="本次研讨已结束，当前仅支持查看会议资料。")
    if w.is_review_mode:
        raise HTTPException(status_code=400, detail="当前为历史轮次查看模式，不能开始计时")

    current = next((r for r in w.rounds if r.round_number == w.current_round), None)
    if not current:
        raise HTTPException(status_code=400, detail="No current round")
    if current.status not in (RoundStatus.ACTIVE, RoundStatus.INPUT):
        raise HTTPException(status_code=400, detail="Current round is not timer-enabled")

    current.timer_started_at = datetime.now(timezone.utc)
    current.timer_phase = current.status.value
    await db.commit()

    w = await _load_workshop(workshop_id, db)
    current = next((r for r in w.rounds if r.round_number == w.current_round), None)
    if current:
        await ws_manager.broadcast_timer(
            workshop_id,
            _round_timer_remaining(current) or 0,
            current.timer_phase or current.status.value,
            current.round_number,
            current.id,
        )
    return await _build_host_view_with_docs(w, db)


@router.post("/{workshop_id}/round-settings", response_model=WorkshopHostView)
async def update_round_settings(
    workshop_id: int, data: RoundSettingsUpdate, code: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    w = await _load_workshop(workshop_id, db)
    if w.host_code != code:
        raise HTTPException(status_code=403, detail="Invalid host code")
    if w.status == WorkshopStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="本次研讨已结束，当前仅支持查看会议资料。")
    if w.is_review_mode:
        raise HTTPException(status_code=400, detail="当前为历史轮次查看模式，不能修改本轮时长")

    current = next((r for r in w.rounds if r.round_number == w.current_round), None)
    if not current:
        raise HTTPException(status_code=400, detail="No current round")
    if data.discussion_time is not None or data.input_time is not None:
        unified_time = data.discussion_time if data.discussion_time is not None else data.input_time
        current.discussion_time = unified_time
        current.input_time = unified_time
    await db.commit()
    return await _build_host_view_with_docs(await _load_workshop(workshop_id, db), db)


@router.post("/{workshop_id}/host-input", response_model=HostInputOut)
async def submit_host_input(
    workshop_id: int, data: HostInputCreate, code: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    w = await _load_workshop(workshop_id, db)
    if w.host_code != code:
        raise HTTPException(status_code=403, detail="Invalid host code")
    if w.status == WorkshopStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="本次研讨已结束，当前仅支持查看会议资料。")

    _ = round  # avoid shadow
    rd = await db.get(Round, data.round_id)
    if not rd or rd.workshop_id != workshop_id:
        raise HTTPException(status_code=404, detail="Round not found")

    # Upsert
    result = await db.execute(
        select(HostInput).where(HostInput.round_id == data.round_id, HostInput.workshop_id == workshop_id)
    )
    hi = result.scalar_one_or_none()
    if hi:
        hi.content = data.content
    else:
        hi = HostInput(workshop_id=workshop_id, round_id=data.round_id, content=data.content)
        db.add(hi)
    await db.commit()
    await db.refresh(hi)
    return HostInputOut.model_validate(hi)


@router.put("/{workshop_id}/group-results/{result_id}", response_model=GroupRoundResultOut)
async def edit_group_result(
    workshop_id: int, result_id: int, data: GroupResultEdit, code: str = Query(...),
    db: AsyncSession = Depends(get_db),
    ws_manager: WebSocketManager = Depends(get_ws_manager),
):
    w = await _load_workshop(workshop_id, db)
    if w.host_code != code:
        raise HTTPException(status_code=403, detail="Invalid host code")
    if w.status == WorkshopStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="本次研讨已结束，当前仅支持查看会议资料。")
    if not data.edited_content.strip():
        raise HTTPException(status_code=400, detail="编辑内容不能为空")
    gr = await db.get(GroupRoundResult, result_id)
    if not gr:
        raise HTTPException(status_code=404, detail="Result not found")
    rd = next((round_item for round_item in w.rounds if round_item.id == gr.round_id), None)
    if not rd:
        raise HTTPException(status_code=404, detail="Round not found")
    if gr.group_id < 1 or gr.group_id > (w.group_count or 4):
        raise HTTPException(status_code=400, detail="小组编号无效")
    if not gr.original_content:
        raise HTTPException(status_code=400, detail="该小组该轮次尚无 AI 提炼结果")
    gr.edited_content = data.edited_content.strip()
    gr.status = GroupResultStatus.EDITED
    await db.commit()
    await db.refresh(gr)
    out = GroupRoundResultOut.model_validate(gr)
    updated_at = _as_utc(gr.updated_at).isoformat().replace("+00:00", "Z") if gr.updated_at else None
    await ws_manager.broadcast_ai_result_status(
        workshop_id,
        gr.group_id,
        rd.round_number,
        gr.status.value,
        gr.validation_error,
        updated_at,
    )
    await ws_manager.broadcast_result_ready(workshop_id, gr.group_id, rd.round_number, out.model_dump(mode="json"))
    return out


@router.put("/{workshop_id}/synthesis/{round_id}", response_model=SynthesisResultOut)
async def edit_synthesis_result(
    workshop_id: int, round_id: int, data: SynthesisResultEdit, code: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    w = await _load_workshop(workshop_id, db)
    if w.host_code != code:
        raise HTTPException(status_code=403, detail="Invalid host code")
    if w.status == WorkshopStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="本次研讨已结束，当前仅支持查看会议资料。")
    if not data.edited_content.strip():
        raise HTTPException(status_code=400, detail="编辑内容不能为空")
    result = await db.execute(
        select(SynthesisResult).where(SynthesisResult.round_id == round_id, SynthesisResult.workshop_id == workshop_id)
    )
    sr = result.scalar_one_or_none()
    if not sr:
        raise HTTPException(status_code=404, detail="Synthesis result not found")
    sr.edited_content = data.edited_content.strip()
    sr.status = GroupResultStatus.EDITED
    await db.commit()
    await db.refresh(sr)
    return SynthesisResultOut.model_validate(sr)


@router.put("/{workshop_id}/groups/{group_id}/leader", response_model=WorkshopHostView)
async def set_group_leader_by_host(
    workshop_id: int,
    group_id: int,
    data: HostGroupLeaderSet,
    db: AsyncSession = Depends(get_db),
    ws_manager: WebSocketManager = Depends(get_ws_manager),
):
    w = await _load_workshop(workshop_id, db)
    if w.host_code != data.host_code:
        raise HTTPException(status_code=403, detail="Invalid host code")
    if w.status == WorkshopStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="本次研讨已结束，当前仅支持查看会议资料。")

    new_leader = next((p for p in w.participants if p.id == data.new_leader_participant_id), None)
    if not new_leader:
        raise HTTPException(status_code=404, detail="成员不存在")
    if new_leader.workshop_id != workshop_id or new_leader.group_id != group_id:
        raise HTTPException(status_code=400, detail="新组长必须属于当前研讨会和对应小组")

    group_members = [p for p in w.participants if p.group_id == group_id]
    old_leader = next((p for p in group_members if p.is_group_leader), None)
    for member in group_members:
        member.is_group_leader = member.id == new_leader.id

    await db.commit()
    w = await _load_workshop(workshop_id, db)
    updated_members = [p for p in w.participants if p.group_id == group_id]
    await ws_manager.broadcast_group_leader_changed(
        workshop_id,
        group_id,
        [_participant_out(member).model_dump() for member in sorted(updated_members, key=lambda item: item.id)],
        old_leader_participant_id=old_leader.id if old_leader else None,
        old_leader_name=old_leader.name if old_leader else None,
        new_leader_participant_id=new_leader.id,
        new_leader_name=new_leader.name,
        changed_by="host",
    )
    return await _build_host_view_with_docs(w, db)


@router.get("/{workshop_id}/export", response_model=ExportResponse)
async def export_workshop(
    workshop_id: int, code: str = Query(...),
    export_svc: ExportService = Depends(get_export_service),
    db: AsyncSession = Depends(get_db),
):
    w = await _load_workshop(workshop_id, db)
    if w.host_code != code:
        raise HTTPException(status_code=403, detail="Invalid host code")
    md = await export_svc.generate_markdown(workshop_id)
    safe_title = w.title.replace(" ", "_").replace("/", "_")
    return ExportResponse(markdown=md, filename=f"{safe_title}_完整记录.md")
