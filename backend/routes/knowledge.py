import base64
import binascii
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_kb_service
from models import Workshop
from schemas import KnowledgeDocumentOut, KnowledgeUploadTaskOut, ValidateAdminRequest, ValidateResponse
from services.knowledge_base_service import KnowledgeBaseService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
_upload_tasks: dict[str, dict] = {}


class KnowledgeUploadRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=500)
    content_base64: str = Field(..., min_length=1)
    content_type: str = Field(default="application/octet-stream")
    workshop_id: int
    admin_code: str


def _friendly_upload_error(error: Exception) -> str:
    message = str(error) or "未知错误"
    lower = message.lower()
    if "不支持" in message:
        return message
    if "文件内容为空" in message or "无法解析" in message:
        return message
    if "sentence" in lower or "embedding" in lower or "向量" in message:
        return f"embedding 生成失败：{message}"
    if "lightrag" in lower or "知识库索引" in message or "storages" in lower:
        return f"知识库索引初始化失败：{message}"
    if "timeout" in lower or "tokenizer" in lower or "model" in lower:
        return f"网络超时或模型资源缺失：{message}"
    return f"上传失败：{message}"


def _now():
    return datetime.now(timezone.utc)


def _task_out(task: dict) -> KnowledgeUploadTaskOut:
    return KnowledgeUploadTaskOut(**task)


def _set_task(task_id: str, **updates):
    task = _upload_tasks[task_id]
    task.update(updates)
    task["updated_at"] = _now()


async def _validate_upload_request(data: KnowledgeUploadRequest, db: AsyncSession) -> bytes:
    result = await db.execute(select(Workshop).where(Workshop.id == data.workshop_id))
    w = result.scalar_one_or_none()
    if not w or w.kb_admin_code != data.admin_code:
        raise HTTPException(status_code=403, detail="Invalid admin code")

    try:
        content = base64.b64decode(data.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="文件内容编码无效，请重新选择文件上传")

    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大，最大支持 50MB")
    return content


async def _process_upload_task(
    task_id: str,
    data: KnowledgeUploadRequest,
    content: bytes,
    kb_service: KnowledgeBaseService,
):
    async def update_progress(stage: str, progress: int, message: str):
        _set_task(
            task_id,
            status="running",
            stage=stage,
            progress=max(0, min(progress, 99)),
            message=message,
        )

    try:
        await update_progress("uploading", 10, "正在上传文件")
        doc = await kb_service.upload(
            filename=data.filename,
            content=content,
            content_type=data.content_type,
            workshop_id=data.workshop_id,
            progress_callback=update_progress,
        )
        _set_task(
            task_id,
            status="success",
            stage="completed",
            progress=100,
            message="上传完成",
            error=None,
            document=KnowledgeDocumentOut.model_validate(doc),
        )
    except ValueError as e:
        _set_task(
            task_id,
            status="failed",
            stage="failed",
            progress=100,
            message="上传失败",
            error=_friendly_upload_error(e),
        )
    except RuntimeError as e:
        logger.warning("Knowledge upload task failed: %s", e)
        _set_task(
            task_id,
            status="failed",
            stage="failed",
            progress=100,
            message="上传失败",
            error=_friendly_upload_error(e),
        )
    except Exception as e:
        logger.exception("Unexpected knowledge upload task error")
        _set_task(
            task_id,
            status="failed",
            stage="failed",
            progress=100,
            message="上传失败",
            error=_friendly_upload_error(e),
        )


@router.post("/validate-admin", response_model=ValidateResponse)
async def validate_admin(data: ValidateAdminRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Workshop).where(Workshop.kb_admin_code == data.admin_code))
    w = result.scalar_one_or_none()
    if not w:
        return ValidateResponse(valid=False)
    return ValidateResponse(valid=True, workshop_id=w.id, workshop_title=w.title)


@router.post("/upload", response_model=KnowledgeDocumentOut)
async def upload_document(
    data: KnowledgeUploadRequest,
    db: AsyncSession = Depends(get_db),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    content = await _validate_upload_request(data, db)

    try:
        doc = await kb_service.upload(
            filename=data.filename,
            content=content,
            content_type=data.content_type,
            workshop_id=data.workshop_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=_friendly_upload_error(e))
    except RuntimeError as e:
        logger.warning("Knowledge upload failed: %s", e)
        raise HTTPException(status_code=500, detail=_friendly_upload_error(e))
    except Exception as e:
        logger.exception("Unexpected knowledge upload error")
        raise HTTPException(status_code=500, detail=_friendly_upload_error(e))
    return KnowledgeDocumentOut.model_validate(doc)


@router.post("/upload-tasks", response_model=KnowledgeUploadTaskOut, status_code=202)
async def create_upload_task(
    data: KnowledgeUploadRequest,
    db: AsyncSession = Depends(get_db),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    content = await _validate_upload_request(data, db)
    task_id = uuid.uuid4().hex
    now = _now()
    _upload_tasks[task_id] = {
        "task_id": task_id,
        "filename": data.filename,
        "status": "pending",
        "stage": "pending",
        "progress": 0,
        "message": "等待处理",
        "error": None,
        "document": None,
        "created_at": now,
        "updated_at": now,
    }
    asyncio.create_task(_process_upload_task(task_id, data, content, kb_service))
    return _task_out(_upload_tasks[task_id])


@router.get("/upload-tasks/{task_id}", response_model=KnowledgeUploadTaskOut)
async def get_upload_task(task_id: str):
    task = _upload_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="上传任务不存在或已过期")
    return _task_out(task)


@router.get("/documents", response_model=list[KnowledgeDocumentOut])
async def list_documents(
    workshop_id: int = Query(...),
    admin_code: str = Query(...),
    db: AsyncSession = Depends(get_db),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    result = await db.execute(select(Workshop).where(Workshop.id == workshop_id))
    w = result.scalar_one_or_none()
    if not w or w.kb_admin_code != admin_code:
        raise HTTPException(status_code=403, detail="Invalid admin code")

    docs = await kb_service.list_docs(workshop_id)
    return [KnowledgeDocumentOut.model_validate(d) for d in docs]


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: int,
    workshop_id: int = Query(...),
    admin_code: str = Query(...),
    db: AsyncSession = Depends(get_db),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    result = await db.execute(select(Workshop).where(Workshop.id == workshop_id))
    w = result.scalar_one_or_none()
    if not w or w.kb_admin_code != admin_code:
        raise HTTPException(status_code=403, detail="Invalid admin code")

    ok = await kb_service.delete(doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "deleted"}
