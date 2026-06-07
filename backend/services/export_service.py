import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models import (
    Workshop, Round, Question, Answer, KnowledgeDocument,
)

logger = logging.getLogger(__name__)


def _local_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone()


class ExportService:

    def __init__(self, db: AsyncSession):
        self._db = db

    async def generate_markdown(self, workshop_id: int) -> str:
        workshop = await self._load_workshop(workshop_id)
        if not workshop:
            raise ValueError(f"Workshop {workshop_id} not found")

        group_count = workshop.group_count or 4
        groups = {gid: [] for gid in range(1, group_count + 1)}
        for participant in workshop.participants:
            groups.setdefault(participant.group_id, []).append(participant)

        lines = [
            f"# {workshop.title} - 领导力共创研讨会完整记录",
            "",
            f"**主持人**: {workshop.host_name}",
            f"**邀请码**: {workshop.invite_code}",
            f"**创建时间**: {_local_time(workshop.created_at).strftime('%Y-%m-%d %H:%M')}",
            f"**状态**: {'进行中' if workshop.status.value == 'active' else '已结束'}",
            f"**组数**: {group_count}",
            f"**总轮次**: {len(workshop.rounds)}",
            "",
            "## 参会人员",
        ]

        for gid in range(1, group_count + 1):
            members = sorted(groups.get(gid, []), key=lambda item: item.id)
            lines.append(f"### 第{gid}组 ({len(members)}人)")
            if members:
                for participant in members:
                    leader = " (队长)" if participant.is_group_leader else ""
                    lines.append(f"- {participant.name}{leader}")
            else:
                lines.append("- 本组暂无成员")
            lines.append("")
        lines.append("---\n")

        for rd in sorted(workshop.rounds, key=lambda r: r.round_number):
            lines.append(f"# 第{rd.round_number}轮：{rd.title}")
            lines.append(f"**目标**: {rd.objective or '无'}")
            lines.append(f"**本轮时长**: {rd.discussion_time} 分钟")
            lines.append("")

            for q in rd.questions:
                lines.append(f"**Q{q.order}**: {q.content}")
            lines.append("")

            for gid in range(1, group_count + 1):
                lines.append(f"## 第{gid}组回答")
                group_has_answer = False
                for q in rd.questions:
                    answers = [
                        answer for answer in q.answers
                        if answer.participant and answer.participant.group_id == gid
                    ]
                    if not answers:
                        continue
                    group_has_answer = True
                    lines.append(f"### {q.content}")
                    for answer in sorted(answers, key=lambda item: item.created_at):
                        name = answer.participant.name if answer.participant else "未知成员"
                        lines.append(f"- **{name}**: {answer.content}")
                    lines.append("")
                if not group_has_answer:
                    lines.append("本组暂无提交内容\n")

            for gid in range(1, group_count + 1):
                group_results = [result for result in rd.group_results if result.group_id == gid]
                lines.append(f"## 第{gid}组 AI 提炼结果")
                if not group_results:
                    lines.append("本组暂无提交内容\n")
                    continue
                for result in sorted(group_results, key=lambda item: item.created_at):
                    if result.original_content:
                        lines.append("### 原始 AI 提炼结果")
                        lines.append(result.original_content)
                    if result.edited_content:
                        lines.append("### 编辑后的 AI 提炼结果")
                        lines.append(result.edited_content)
                    if not result.original_content and not result.edited_content:
                        lines.append("本组暂无提交内容")
                    lines.append("")

            if rd.synthesis_results:
                for sr in rd.synthesis_results:
                    lines.append("## 综合提炼结果")
                    if sr.original_content:
                        lines.append(sr.original_content)
                    if sr.edited_content:
                        lines.append("### 编辑后的综合提炼结果")
                        lines.append(sr.edited_content)
                    if not sr.original_content and not sr.edited_content:
                        lines.append("暂无综合提炼结果")
                    if sr.validation_error:
                        lines.append(f"失败原因：{sr.validation_error}")
                    lines.append("")
            else:
                lines.append("## 综合提炼结果")
                lines.append("暂无综合提炼结果\n")

            for hi in rd.host_inputs:
                lines.append("## 主持人输入")
                lines.append(hi.content)
                lines.append("")

            lines.append("---\n")

        docs = await self._list_knowledge_docs()
        if docs:
            lines.append("## 知识库文件清单")
            for doc in docs:
                lines.append(
                    f"- {doc.original_filename} ({doc.file_size} bytes, "
                    f"{doc.chunk_count} chunks, {doc.embedding_model})"
                )
            lines.append("")

        tz = datetime.now(timezone.utc).astimezone()
        lines.append(f"---\n*导出时间: {tz.strftime('%Y-%m-%d %H:%M:%S')}*")
        return "\n".join(lines)

    async def _list_knowledge_docs(self):
        result = await self._db.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.is_deleted == False)
            .order_by(KnowledgeDocument.uploaded_at.desc())
        )
        return list(result.scalars().all())

    async def _load_workshop(self, workshop_id: int):
        result = await self._db.execute(
            select(Workshop)
            .options(
                selectinload(Workshop.participants),
                selectinload(Workshop.rounds)
                .selectinload(Round.questions)
                .selectinload(Question.answers)
                .selectinload(Answer.participant),
                selectinload(Workshop.rounds)
                .selectinload(Round.group_results),
                selectinload(Workshop.rounds)
                .selectinload(Round.synthesis_results),
                selectinload(Workshop.rounds)
                .selectinload(Round.host_inputs),
                selectinload(Workshop.knowledge_docs),
            )
            .where(Workshop.id == workshop_id)
        )
        return result.scalar_one_or_none()
