import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_ai_service, get_kb_service
from models import AIQuestionLog, Answer, GroupRoundResult, Participant, Question, Round, Workshop
from schemas import AIQuestionSubmit, AIQuestionOut
from services.ai_service import DeepSeekService
from services.knowledge_base_service import KnowledgeBaseService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workshops", tags=["ai-qa"])

MAX_CONTEXT_CHARS = 12000
MAX_HISTORY_ITEMS = 5
MAX_HISTORY_QUESTION_CHARS = 300
MAX_HISTORY_ANSWER_CHARS = 700
MAX_QUESTION_CHARS = 500
MAX_ANSWER_CHARS = 700
MAX_RESULT_CHARS = 2500
MAX_KB_CHUNK_CHARS = 1200


def _clip(text: Optional[str], limit: int) -> str:
    if not text:
        return ""
    normalized = str(text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "\n...（内容过长，已截断）"


def _append_section(parts: list[str], title: str, body: str, budget: int) -> int:
    body = body.strip()
    if not body or budget <= 0:
        return budget
    section = f"## {title}\n{body}"
    if len(section) > budget:
        section = section[:budget].rstrip() + "\n...（上下文过长，后续内容已截断）"
    parts.append(section)
    return max(0, budget - len(section))


async def _build_member_ai_context(
    db: AsyncSession,
    workshop_id: int,
    group_id: int,
    current_round: Optional[Round],
) -> tuple[str, int]:
    if not current_round:
        return "当前研讨会尚未进入有效轮次。", 0

    parts: list[str] = []
    budget = MAX_CONTEXT_CHARS

    result = await db.execute(
        select(Question)
        .where(Question.round_id == current_round.id)
        .order_by(Question.order, Question.id)
    )
    questions = list(result.scalars().all())
    question_lines = [
        f"{idx}. { _clip(question.content, MAX_QUESTION_CHARS) }"
        for idx, question in enumerate(questions, start=1)
    ]
    budget = _append_section(parts, "当前轮研讨问题", "\n".join(question_lines), budget)

    result = await db.execute(
        select(Answer, Participant, Question)
        .join(Participant, Answer.participant_id == Participant.id)
        .join(Question, Answer.question_id == Question.id)
        .where(
            Question.round_id == current_round.id,
            Participant.workshop_id == workshop_id,
            Participant.group_id == group_id,
        )
        .order_by(Question.order, Answer.created_at, Answer.id)
    )
    answer_lines = []
    for answer, participant, question in result.all():
        answer_lines.append(
            "\n".join(
                [
                    f"问题：{_clip(question.content, 180)}",
                    f"成员：{participant.name}",
                    f"回答：{_clip(answer.content, MAX_ANSWER_CHARS)}",
                ]
            )
        )
    budget = _append_section(
        parts,
        "本组当前轮成员回答（包含多次提交）",
        "\n\n".join(answer_lines) if answer_lines else "本组当前轮暂无成员回答。",
        budget,
    )

    result = await db.execute(
        select(GroupRoundResult).where(
            GroupRoundResult.round_id == current_round.id,
            GroupRoundResult.group_id == group_id,
        )
    )
    group_result = result.scalar_one_or_none()
    if group_result:
        result_lines = [
            f"状态：{group_result.status.value}",
            "优先参考结果："
            + _clip(group_result.edited_content or group_result.original_content or "暂无内容", MAX_RESULT_CHARS),
        ]
        if group_result.original_content:
            result_lines.append("原始 AI 提炼结果：\n" + _clip(group_result.original_content, MAX_RESULT_CHARS))
        if group_result.edited_content:
            result_lines.append("编辑后的 AI 提炼结果：\n" + _clip(group_result.edited_content, MAX_RESULT_CHARS))
        if group_result.validation_error:
            result_lines.append("提炼失败原因：\n" + _clip(group_result.validation_error, 600))
        result_body = "\n\n".join(result_lines)
    else:
        result_body = "本组当前轮尚无 AI 提炼结果。"
    budget = _append_section(parts, "本组当前轮 AI 提炼结果", result_body, budget)

    result = await db.execute(
        select(AIQuestionLog)
        .where(
            AIQuestionLog.workshop_id == workshop_id,
            AIQuestionLog.group_id == group_id,
            AIQuestionLog.round_id == current_round.id,
        )
        .order_by(AIQuestionLog.created_at.desc(), AIQuestionLog.id.desc())
        .limit(MAX_HISTORY_ITEMS)
    )
    history = list(reversed(result.scalars().all()))
    history_lines = []
    for item in history:
        history_lines.append(
            f"Q: {_clip(item.question, MAX_HISTORY_QUESTION_CHARS)}\n"
            f"A: {_clip(item.answer or '暂无回答', MAX_HISTORY_ANSWER_CHARS)}"
        )
    budget = _append_section(
        parts,
        f"本组当前轮最近 {MAX_HISTORY_ITEMS} 条 AI 问答历史",
        "\n\n".join(history_lines) if history_lines else "暂无 AI 问答历史。",
        budget,
    )

    return "\n\n---\n\n".join(parts), len(history)


@router.post("/{workshop_id}/ai-ask", response_model=AIQuestionOut)
async def ask_ai(
    workshop_id: int,
    data: AIQuestionSubmit,
    db: AsyncSession = Depends(get_db),
    ai_service: DeepSeekService = Depends(get_ai_service),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    participant = await db.get(Participant, data.participant_id)
    if not participant or participant.workshop_id != workshop_id:
        raise HTTPException(status_code=403, detail="Participant not in this workshop")

    workshop = await db.get(Workshop, workshop_id)
    if not workshop:
        raise HTTPException(status_code=404, detail="Workshop not found")
    result = await db.execute(
        select(Round).where(Round.workshop_id == workshop_id, Round.round_number == workshop.current_round)
    )
    current_round = result.scalar_one_or_none()

    group_context, history_count = await _build_member_ai_context(
        db,
        workshop_id,
        participant.group_id,
        current_round,
    )

    started = time.perf_counter()
    try:
        kb_chunks = await kb_service.search(data.question, workshop_id, top_k=3)
    except Exception as exc:
        logger.warning(
            "AI QA knowledge search failed: workshop=%s group=%s participant=%s error=%s",
            workshop_id,
            participant.group_id,
            participant.id,
            exc,
        )
        kb_chunks = []
    kb_chunks = [_clip(chunk, MAX_KB_CHUNK_CHARS) for chunk in kb_chunks[:3]]

    try:
        answer = await ai_service.answer_member_question(
            data.question,
            group_context,
            kb_chunks,
            current_round.title if current_round else None,
            participant.group_id,
        )
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "AI QA failed: workshop=%s group=%s participant=%s round=%s context_chars=%s history_count=%s elapsed_ms=%s error=%s",
            workshop_id,
            participant.group_id,
            participant.id,
            current_round.id if current_round else None,
            len(group_context),
            history_count,
            elapsed_ms,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail="AI 问答暂时无法返回结果，请稍后重试；你的问题已保留在输入框中。",
        ) from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "AI QA succeeded: workshop=%s group=%s participant=%s round=%s context_chars=%s history_count=%s kb_chunks=%s elapsed_ms=%s",
        workshop_id,
        participant.group_id,
        participant.id,
        current_round.id if current_round else None,
        len(group_context),
        history_count,
        len(kb_chunks),
        elapsed_ms,
    )

    log = AIQuestionLog(
        workshop_id=workshop_id, participant_id=data.participant_id,
        round_id=current_round.id if current_round else None,
        group_id=participant.group_id, question=data.question, answer=answer,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return AIQuestionOut(id=log.id, round_id=log.round_id, question=log.question, answer=log.answer, created_at=log.created_at)


@router.get("/{workshop_id}/ai-questions", response_model=list[AIQuestionOut])
async def get_ai_questions(
    workshop_id: int,
    participant_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    participant = await db.get(Participant, participant_id)
    if not participant or participant.workshop_id != workshop_id:
        raise HTTPException(status_code=403, detail="Not in this workshop")

    workshop = await db.get(Workshop, workshop_id)
    if not workshop:
        raise HTTPException(status_code=404, detail="Workshop not found")
    result = await db.execute(
        select(Round).where(Round.workshop_id == workshop_id, Round.round_number == workshop.current_round)
    )
    current_round = result.scalar_one_or_none()

    result = await db.execute(
        select(AIQuestionLog)
        .where(
            AIQuestionLog.participant_id == participant_id,
            AIQuestionLog.round_id == (current_round.id if current_round else None),
        )
        .order_by(AIQuestionLog.created_at.asc(), AIQuestionLog.id.asc())
    )
    logs = result.scalars().all()
    return [
        AIQuestionOut(id=l.id, round_id=l.round_id, question=l.question, answer=l.answer, created_at=l.created_at)
        for l in logs
    ]
