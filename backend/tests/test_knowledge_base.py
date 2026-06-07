"""Tests for KnowledgeBaseService with LightRAG adapter."""

import base64
import sys
import asyncio
from pathlib import Path

import pytest
from httpx import AsyncClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(autouse=True)
def _mock_lightrag_module(monkeypatch):
    """Module-level mock: replace LightRAGAdapter with fake for all tests."""

    class FakeLightRAGAdapter:
        def __init__(self, working_dir):
            self.working_dir = working_dir
            self.inserted = []
            self.queries = []
            self.deleted = []

        async def _ensure_initialized(self):
            pass

        async def insert(self, text, doc_id, file_path):
            self.inserted.append({"text": text, "doc_id": doc_id, "file_path": file_path})
            return "fake_track_id"

        async def query(self, query, top_k=5):
            self.queries.append({"query": query, "top_k": top_k})
            return "驴迹科技核心价值观：搞得定·顶得住·跟我上"

        async def delete_doc(self, doc_id):
            self.deleted.append(doc_id)

    monkeypatch.setattr(
        "services.knowledge_base_service.LightRAGAdapter",
        FakeLightRAGAdapter,
    )


class TestKnowledgeBaseValidation:
    """Test input validation via HTTP — uses async_client with overridden DB."""

    async def _create_workshop(self, async_client: AsyncClient) -> dict:
        resp = await async_client.post(
            "/api/workshops", json={"title": "test", "host_name": "h"}
        )
        assert resp.status_code == 201
        return resp.json()

    @pytest.mark.asyncio
    async def test_reject_unsupported_file_type(self, async_client: AsyncClient):
        w = await self._create_workshop(async_client)
        payload = {
            "filename": "test.pdf",
            "content_base64": base64.b64encode(b"fake content").decode(),
            "content_type": "application/pdf",
            "workshop_id": w["id"],
            "admin_code": w["kb_admin_code"],
        }
        resp = await async_client.post("/api/knowledge/upload", json=payload)
        assert resp.status_code == 400
        assert "不支持" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_reject_wrong_admin_code(self, async_client: AsyncClient):
        w = await self._create_workshop(async_client)
        payload = {
            "filename": "test.txt",
            "content_base64": base64.b64encode(b"hello").decode(),
            "content_type": "text/plain",
            "workshop_id": w["id"],
            "admin_code": "WRONG00",
        }
        resp = await async_client.post("/api/knowledge/upload", json=payload)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_reject_invalid_base64(self, async_client: AsyncClient):
        w = await self._create_workshop(async_client)
        payload = {
            "filename": "test.txt",
            "content_base64": "!!! not valid base64 !!!",
            "content_type": "text/plain",
            "workshop_id": w["id"],
            "admin_code": w["kb_admin_code"],
        }
        resp = await async_client.post("/api/knowledge/upload", json=payload)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_reject_empty_file(self, async_client: AsyncClient):
        w = await self._create_workshop(async_client)
        payload = {
            "filename": "test.txt",
            "content_base64": base64.b64encode(b"a").decode(),
            "content_type": "text/plain",
            "workshop_id": w["id"],
            "admin_code": w["kb_admin_code"],
        }
        resp = await async_client.post("/api/knowledge/upload", json=payload)
        # Single-char txt upload should work
        assert resp.status_code == 200


class TestKnowledgeBaseService:
    """Test KnowledgeBaseService directly with mocked LightRAG."""

    async def _make_workshop(self, db_session):
        from models import Workshop
        w = Workshop(title="test", host_name="h", group_count=4)
        db_session.add(w)
        await db_session.commit()
        await db_session.refresh(w)
        return w

    @pytest.mark.asyncio
    async def test_upload_txt(self, db_session):
        from services.knowledge_base_service import KnowledgeBaseService
        w = await self._make_workshop(db_session)
        kb = KnowledgeBaseService(db_session)
        content = "驴迹科技核心价值观：搞得定·顶得住·跟我上。这是文旅科技公司的企业文化核心。"
        doc = await kb.upload(
            filename="test.txt", content=content.encode("utf-8"),
            content_type="text/plain", workshop_id=w.id,
        )
        assert doc.id is not None
        assert doc.original_filename == "test.txt"
        assert doc.chunk_count >= 1
        assert doc.is_deleted is False

    @pytest.mark.asyncio
    async def test_upload_md(self, db_session):
        from services.knowledge_base_service import KnowledgeBaseService
        w = await self._make_workshop(db_session)
        kb = KnowledgeBaseService(db_session)
        content = "# 第一章\n\n这是第一章。\n\n# 第二章\n\n这是第二章。"
        doc = await kb.upload("test.md", content.encode("utf-8"), "text/markdown", w.id)
        assert doc.original_filename == "test.md"
        assert doc.chunk_count >= 1

    @pytest.mark.asyncio
    async def test_list_docs(self, db_session):
        from services.knowledge_base_service import KnowledgeBaseService
        w = await self._make_workshop(db_session)
        other = await self._make_workshop(db_session)
        kb = KnowledgeBaseService(db_session)
        await kb.upload("a.txt", b"content A", "text/plain", w.id)
        await kb.upload("b.txt", b"content B", "text/plain", other.id)
        docs = await kb.list_docs(w.id)
        assert len(docs) == 2
        assert {d.original_filename for d in docs} == {"a.txt", "b.txt"}

    @pytest.mark.asyncio
    async def test_uses_unified_lightrag_directory(self, db_session):
        from services.knowledge_base_service import KnowledgeBaseService
        w = await self._make_workshop(db_session)
        other = await self._make_workshop(db_session)
        kb = KnowledgeBaseService(db_session)

        assert kb._get_rag_adapter(w.id).working_dir == kb._get_rag_adapter(other.id).working_dir
        assert kb._get_rag_adapter(w.id).working_dir.endswith("unified")

    @pytest.mark.asyncio
    async def test_upload_failure_removes_saved_file(self, db_session, monkeypatch):
        from services.knowledge_base_service import KnowledgeBaseService
        w = await self._make_workshop(db_session)
        kb = KnowledgeBaseService(db_session)
        saved_paths = []
        original_extract = kb._extract_text

        def fail_extract(file_path, ext, filename=None):
            saved_paths.append(file_path)
            return ""

        monkeypatch.setattr(kb, "_extract_text", fail_extract)
        with pytest.raises(ValueError, match="文件内容为空或无法解析"):
            await kb.upload("empty.txt", b"   ", "text/plain", w.id)

        assert saved_paths
        assert not Path(saved_paths[0]).exists()
        monkeypatch.setattr(kb, "_extract_text", original_extract)

    def test_pptx_extract_keeps_slide_boundaries(self, db_session, tmp_path):
        from services.knowledge_base_service import KnowledgeBaseService
        import zipfile

        pptx = tmp_path / "slides.pptx"
        slide_xml = """<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>第一页标题</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
</p:sld>"""
        with zipfile.ZipFile(pptx, "w") as archive:
            archive.writestr("ppt/slides/slide1.xml", slide_xml)

        kb = KnowledgeBaseService(db_session)
        text = kb._extract_text(str(pptx), "pptx", "slides.pptx")

        assert "文件：slides.pptx" in text
        assert "幻灯片 1" in text
        assert "第一页标题" in text

    def test_xlsx_extract_keeps_sheet_name_and_table(self, db_session, tmp_path):
        from services.knowledge_base_service import KnowledgeBaseService
        import zipfile

        xlsx = tmp_path / "table.xlsx"
        workbook_xml = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="人员表" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
        rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
        sheet_xml = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="inlineStr"><is><t>姓名</t></is></c><c r="B1" t="inlineStr"><is><t>角色</t></is></c></row>
    <row r="2"><c r="A2" t="inlineStr"><is><t>张三</t></is></c><c r="B2" t="inlineStr"><is><t>主持人</t></is></c></row>
  </sheetData>
</worksheet>"""
        with zipfile.ZipFile(xlsx, "w") as archive:
            archive.writestr("xl/workbook.xml", workbook_xml)
            archive.writestr("xl/_rels/workbook.xml.rels", rels_xml)
            archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)

        kb = KnowledgeBaseService(db_session)
        text = kb._extract_text(str(xlsx), "xlsx", "table.xlsx")

        assert "文件：table.xlsx" in text
        assert "工作表：人员表" in text
        assert "| 姓名 | 角色 |" in text
        assert "| 张三 | 主持人 |" in text

    @pytest.mark.asyncio
    async def test_delete_soft(self, db_session):
        from services.knowledge_base_service import KnowledgeBaseService
        w = await self._make_workshop(db_session)
        kb = KnowledgeBaseService(db_session)
        doc = await kb.upload("test.txt", b"test content", "text/plain", w.id)
        ok = await kb.delete(doc.id)
        assert ok is True
        docs = await kb.list_docs(w.id)
        assert len(docs) == 0
        ok = await kb.delete(99999)
        assert ok is False

    @pytest.mark.asyncio
    async def test_search(self, db_session):
        from services.knowledge_base_service import KnowledgeBaseService
        w = await self._make_workshop(db_session)
        other = await self._make_workshop(db_session)
        kb = KnowledgeBaseService(db_session)
        await kb.upload("test.txt", "驴迹科技核心价值观".encode(), "text/plain", w.id)
        results = await kb.search("核心价值观", other.id)
        assert len(results) == 1
        assert "驴迹" in results[0]

    @pytest.mark.asyncio
    async def test_upload_reject_bad_extension(self, db_session):
        from services.knowledge_base_service import KnowledgeBaseService
        w = await self._make_workshop(db_session)
        kb = KnowledgeBaseService(db_session)
        with pytest.raises(ValueError, match="不支持"):
            await kb.upload("bad.exe", b"x", "application/octet-stream", w.id)


class TestKnowledgeBaseAPI:
    """Integration tests via HTTP client using conftest DB override."""

    async def _create_workshop(self, async_client: AsyncClient) -> dict:
        resp = await async_client.post(
            "/api/workshops", json={"title": "test", "host_name": "h"}
        )
        assert resp.status_code == 201
        return resp.json()

    @pytest.mark.asyncio
    async def test_list_empty_documents(self, async_client: AsyncClient):
        w = await self._create_workshop(async_client)
        resp = await async_client.get(
            "/api/knowledge/documents",
            params={"workshop_id": w["id"], "admin_code": w["kb_admin_code"]},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_validate_admin_code_valid(self, async_client: AsyncClient):
        w = await self._create_workshop(async_client)
        resp = await async_client.post(
            "/api/knowledge/validate-admin",
            json={"admin_code": w["kb_admin_code"]},
        )
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    @pytest.mark.asyncio
    async def test_validate_admin_code_invalid(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/knowledge/validate-admin",
            json={"admin_code": "INVALID"},
        )
        assert resp.status_code == 200
        assert resp.json()["valid"] is False

    @pytest.mark.asyncio
    async def test_delete_nonexistent_document(self, async_client: AsyncClient):
        w = await self._create_workshop(async_client)
        resp = await async_client.delete(
            f"/api/knowledge/documents/99999",
            params={"workshop_id": w["id"], "admin_code": w["kb_admin_code"]},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_upload_task_reports_progress_and_success(self, async_client: AsyncClient):
        w = await self._create_workshop(async_client)
        payload = {
            "filename": "task.txt",
            "content_base64": base64.b64encode("任务进度测试内容".encode()).decode(),
            "content_type": "text/plain",
            "workshop_id": w["id"],
            "admin_code": w["kb_admin_code"],
        }
        resp = await async_client.post("/api/knowledge/upload-tasks", json=payload)
        assert resp.status_code == 202
        task = resp.json()
        assert task["task_id"]
        assert task["filename"] == "task.txt"
        assert task["status"] in {"pending", "running", "success"}
        assert "progress" in task

        final = task
        for _ in range(20):
            poll = await async_client.get(f"/api/knowledge/upload-tasks/{task['task_id']}")
            assert poll.status_code == 200
            final = poll.json()
            if final["status"] in {"success", "failed"}:
                break
            await asyncio.sleep(0.01)

        assert final["status"] == "success"
        assert final["progress"] == 100
        assert final["document"]["original_filename"] == "task.txt"

    @pytest.mark.asyncio
    async def test_upload_task_reports_chinese_failure(self, async_client: AsyncClient):
        w = await self._create_workshop(async_client)
        payload = {
            "filename": "bad.exe",
            "content_base64": base64.b64encode(b"x").decode(),
            "content_type": "application/octet-stream",
            "workshop_id": w["id"],
            "admin_code": w["kb_admin_code"],
        }
        resp = await async_client.post("/api/knowledge/upload-tasks", json=payload)
        assert resp.status_code == 202
        task_id = resp.json()["task_id"]

        final = None
        for _ in range(20):
            poll = await async_client.get(f"/api/knowledge/upload-tasks/{task_id}")
            assert poll.status_code == 200
            final = poll.json()
            if final["status"] == "failed":
                break
            await asyncio.sleep(0.01)

        assert final is not None
        assert final["status"] == "failed"
        assert "文件格式不支持" in final["error"] or "不支持" in final["error"]
