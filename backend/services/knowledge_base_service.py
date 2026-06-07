import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import KnowledgeDocument

logger = logging.getLogger(__name__)

ALLOWED_TYPES = {
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "md": "text/markdown",
    "txt": "text/plain",
}
ALLOWED_EXTENSIONS = {"doc", "docx", "xls", "xlsx", "ppt", "pptx", "md", "txt"}
UNIFIED_KB_DIR_NAME = "unified"

_embed_model = None
_embed_lock = threading.Lock()


def _get_embedding_model():
    global _embed_model
    if _embed_model is not None:
        return _embed_model
    with _embed_lock:
        if _embed_model is not None:
            return _embed_model
        from sentence_transformers import SentenceTransformer
        model_name = getattr(settings, "LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
        logger.info(f"Loading local embedding model: {model_name}")
        _embed_model = SentenceTransformer(model_name)
        dim = _embed_model.get_sentence_embedding_dimension() if hasattr(_embed_model, "get_sentence_embedding_dimension") else _embed_model.get_embedding_dimension()
        logger.info(f"Embedding model loaded, dim={dim}")
        return _embed_model


ProgressCallback = Callable[[str, int, str], Awaitable[None] | None]


async def _emit_progress(callback: ProgressCallback | None, stage: str, progress: int, message: str):
    if not callback:
        return
    result = callback(stage, progress, message)
    if hasattr(result, "__await__"):
        await result


class LightRAGAdapter:
    """LightRAG 适配器 — 封装 LightRAG 实例的创建与调用。"""

    def __init__(self, working_dir: str, progress_callback: ProgressCallback | None = None):
        self._working_dir = working_dir
        self._progress_callback = progress_callback
        self._rag = None
        self._initialized = False

    async def _ensure_initialized(self):
        if self._initialized:
            return
        await _emit_progress(self._progress_callback, "model_loading", 20, "正在加载 embedding 模型")
        try:
            from lightrag import LightRAG, QueryParam
            from lightrag.utils import EmbeddingFunc

            os.makedirs(self._working_dir, exist_ok=True)
        except Exception as e:
            raise RuntimeError(f"知识库索引初始化失败：{e}") from e

        llm_api_key = settings.DEEPSEEK_API_KEY
        llm_base_url = settings.DEEPSEEK_BASE_URL.rstrip("/")
        chat_model = settings.DEEPSEEK_CHAT_MODEL

        import httpx

        # Direct httpx LLM func — bypasses LightRAG's openai_complete_if_cache
        # which sends response_format that DeepSeek rejects
        async def llm_func(prompt, system_prompt=None, history_messages=None, **kwargs):
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            if history_messages:
                messages.extend(history_messages)
            messages.append({"role": "user", "content": prompt})

            body = {
                "model": chat_model,
                "messages": messages,
                "max_tokens": kwargs.get("max_tokens", 4096),
                "temperature": kwargs.get("temperature", 0.7),
            }
            if kwargs.get("response_format"):
                body["response_format"] = {"type": "json_object"}

            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    f"{llm_base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {llm_api_key}"},
                    json=body,
                )
                if resp.status_code != 200:
                    logger.error(f"LLM API error {resp.status_code}: {resp.text[:300]}")
                    raise RuntimeError(f"LLM API error {resp.status_code}")
                data = resp.json()
                return data["choices"][0]["message"]["content"]

        # Local embedding via sentence-transformers (DeepSeek has no embedding API)
        try:
            embed_model = _get_embedding_model()
        except Exception as e:
            raise RuntimeError(f"embedding 模型加载失败：{e}") from e
        dim = embed_model.get_sentence_embedding_dimension() if hasattr(embed_model, "get_sentence_embedding_dimension") else embed_model.get_embedding_dimension()

        async def embed_func(texts):
            import asyncio
            return await asyncio.to_thread(embed_model.encode, texts, convert_to_numpy=True)

        embedding_func = EmbeddingFunc(
            embedding_dim=dim,
            max_token_size=512,
            func=embed_func,
        )

        await _emit_progress(self._progress_callback, "index_initializing", 30, "正在初始化知识库索引")
        try:
            self._rag = LightRAG(
                working_dir=self._working_dir,
                llm_model_func=llm_func,
                embedding_func=embedding_func,
                chunk_token_size=settings.KB_CHUNK_SIZE,
                chunk_overlap_token_size=settings.KB_CHUNK_OVERLAP,
            )
            await self._rag.initialize_storages()
        except Exception as e:
            raise RuntimeError(f"知识库索引初始化失败：{e}") from e
        self._initialized = True
        self.QueryParam = QueryParam

    async def insert(self, text: str, doc_id: str, file_path: str) -> str:
        await self._ensure_initialized()
        return await self._rag.ainsert(
            text,
            ids=doc_id,
            file_paths=file_path,
        )

    async def query(self, query: str, top_k: int = 5) -> str:
        await self._ensure_initialized()
        param = self.QueryParam(
            mode="mix",
            only_need_context=True,
            top_k=top_k,
        )
        result = await self._rag.aquery(query, param=param)
        return result if isinstance(result, str) else ""

    async def delete_doc(self, doc_id: str) -> None:
        await self._ensure_initialized()
        try:
            await self._rag.adelete_by_doc_id(doc_id)
        except Exception as e:
            logger.warning(f"LightRAG delete failed for {doc_id}: {e}")


class KnowledgeBaseService:

    def __init__(self, db: AsyncSession):
        self._db = db
        self._upload_dir = settings.KB_UPLOAD_DIR
        self._lightrag_base = settings.KB_LIGHTRAG_DIR
        os.makedirs(self._upload_dir, exist_ok=True)

    def _get_rag_adapter(self, workshop_id: int, progress_callback: ProgressCallback | None = None) -> LightRAGAdapter:
        return LightRAGAdapter(os.path.join(self._lightrag_base, UNIFIED_KB_DIR_NAME), progress_callback)

    async def upload(
        self,
        filename: str,
        content: bytes,
        content_type: str,
        workshop_id: int,
        progress_callback: ProgressCallback | None = None,
    ) -> KnowledgeDocument:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(f"不支持的文件格式: .{ext}，仅支持 {', '.join(sorted(ALLOWED_EXTENSIONS))}")

        stored_name = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{hashlib.md5(content).hexdigest()[:8]}.{ext}"
        file_path = os.path.join(self._upload_dir, stored_name)
        try:
            with open(file_path, "wb") as f:
                f.write(content)

            await _emit_progress(progress_callback, "uploaded", 10, "文件保存完成")
            await _emit_progress(progress_callback, "parsing", 40, "正在解析文件内容")
            text = self._extract_text(file_path, ext, filename)
            if not text.strip():
                raise ValueError(f"文件内容为空或无法解析: {filename}")

            doc_id = hashlib.md5(f"unified:{stored_name}".encode()).hexdigest()
            await _emit_progress(progress_callback, "chunking", 50, "正在切分文档内容")
            chunk_count = self._count_chunks(text)

            rag = self._get_rag_adapter(workshop_id, progress_callback)
            try:
                await _emit_progress(progress_callback, "extracting", 65, "正在抽取实体与关系")
                await rag.insert(text, doc_id=doc_id, file_path=file_path)
                await _emit_progress(progress_callback, "merging", 78, "正在合并知识图谱")
                await _emit_progress(progress_callback, "embedding", 90, "正在生成向量")
            except ValueError:
                raise
            except RuntimeError as e:
                raise RuntimeError(f"知识库索引写入失败：{e}") from e
            except Exception as e:
                message = str(e)
                if "tiktoken" in message.lower() or "token" in message.lower():
                    raise RuntimeError("网络超时或 tokenizer 资源缺失，请检查模型资源缓存后重试") from e
                raise RuntimeError(f"知识库索引写入失败：{message or '未知错误'}") from e

            doc = KnowledgeDocument(
                workshop_id=workshop_id,
                original_filename=filename,
                stored_filename=stored_name,
                file_size=len(content),
                content_type=content_type or ALLOWED_TYPES.get(ext, "application/octet-stream"),
                chunk_count=chunk_count,
                embedding_model=settings.LOCAL_EMBEDDING_MODEL,
                upload_params=json.dumps({
                    "storage": "lightrag",
                    "working_dir": os.path.join(self._lightrag_base, UNIFIED_KB_DIR_NAME),
                    "scope": "unified",
                }, ensure_ascii=False),
            )
            self._db.add(doc)
            await _emit_progress(progress_callback, "writing", 96, "正在写入知识库")
            await self._db.commit()
            await self._db.refresh(doc)
        except Exception:
            await self._db.rollback()
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError as cleanup_error:
                    logger.warning(f"Failed to cleanup orphan upload {file_path}: {cleanup_error}")
            raise

        logger.info(f"Uploaded {filename} via LightRAG: ~{chunk_count} chunks, {len(content)} bytes")
        return doc

    async def delete(self, doc_id: int) -> bool:
        result = await self._db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id))
        doc = result.scalar_one_or_none()
        if not doc:
            return False

        doc.is_deleted = True
        await self._db.commit()

        file_path = os.path.join(self._upload_dir, doc.stored_filename)
        if os.path.exists(file_path):
            os.remove(file_path)

        lr_doc_id = hashlib.md5(f"unified:{doc.stored_filename}".encode()).hexdigest()
        try:
            rag = self._get_rag_adapter(doc.workshop_id or 0)
            await rag.delete_doc(lr_doc_id)
        except Exception as e:
            logger.warning(f"LightRAG delete error for doc {doc_id}: {e}")
        return True

    async def list_docs(self, workshop_id: int) -> list[KnowledgeDocument]:
        result = await self._db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.is_deleted == False,
            ).order_by(KnowledgeDocument.uploaded_at.desc())
        )
        return list(result.scalars().all())

    async def search(self, query: str, workshop_id: int, top_k: int = 5) -> list[str]:
        try:
            rag = self._get_rag_adapter(workshop_id)
            context = await rag.query(query, top_k=top_k)
            if context and context.strip():
                return [context.strip()]
            return []
        except Exception as e:
            logger.warning(f"LightRAG search failed: {e}")
            return []

    def _count_chunks(self, text: str) -> int:
        paragraphs = re.split(r'\n\s*\n', text)
        count = 0
        current_len = 0
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if current_len + len(para) + 2 > settings.KB_CHUNK_SIZE and current_len > 0:
                count += 1
                current_len = len(para)
            else:
                current_len += len(para) + 2 if current_len else len(para)
        if current_len > 0:
            count += 1
        return count

    def _extract_text(self, file_path: str, ext: str, filename: str | None = None) -> str:
        display_name = filename or os.path.basename(file_path)
        if ext == "txt":
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                return self._with_source(display_name, f.read())
        if ext == "md":
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                return self._with_source(display_name, f.read())
        if ext == "doc":
            return self._read_legacy_office(file_path, display_name, "Word 文档")
        if ext == "docx":
            return self._read_docx(file_path, display_name)
        if ext == "xls":
            return self._read_xls(file_path, display_name)
        if ext == "xlsx":
            return self._read_xlsx(file_path, display_name)
        if ext == "ppt":
            return self._read_legacy_office(file_path, display_name, "PPT 演示文稿")
        if ext == "pptx":
            return self._read_pptx(file_path, display_name)
        return ""

    def _with_source(self, filename: str, text: str) -> str:
        return f"文件：{filename}\n\n{text.strip()}" if text.strip() else ""

    def _read_docx(self, path: str, filename: str) -> str:
        try:
            import zipfile
            from xml.etree import ElementTree
            with zipfile.ZipFile(path, 'r') as z:
                xml_content = z.read('word/document.xml')
            tree = ElementTree.fromstring(xml_content)
            paragraphs = []
            for p in tree.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
                texts = [t.text for t in p.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t') if t.text]
                if texts:
                    paragraphs.append(''.join(texts))
            return self._with_source(filename, '\n\n'.join(paragraphs))
        except Exception as e:
            logger.error(f"Failed to read docx: {e}")
            return ""

    def _read_xlsx(self, path: str, filename: str) -> str:
        try:
            import zipfile
            from xml.etree import ElementTree
            with zipfile.ZipFile(path, 'r') as z:
                namelist = set(z.namelist())
                sst = ""
                if 'xl/sharedStrings.xml' in namelist:
                    sst_tree = ElementTree.fromstring(z.read('xl/sharedStrings.xml'))
                    ns_s = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
                    sst = [''.join(t.text or '' for t in si.iter(f'{{{ns_s}}}t')) for si in sst_tree.iter(f'{{{ns_s}}}si')]
                sheets = self._xlsx_sheets(z, namelist)
                sections = []
                for sheet_name, sheet_path in sheets:
                    if sheet_path not in namelist:
                        continue
                    rows = self._xlsx_rows(ElementTree.fromstring(z.read(sheet_path)), sst)
                    table = self._rows_to_markdown(rows)
                    if table:
                        sections.append(f"工作表：{sheet_name}\n\n{table}")
            return self._with_source(filename, "\n\n".join(sections))
        except Exception as e:
            logger.error(f"Failed to read xlsx: {e}")
            return ""

    def _xlsx_sheets(self, archive, namelist: set[str]) -> list[tuple[str, str]]:
        if "xl/workbook.xml" not in namelist:
            return [("Sheet1", "xl/worksheets/sheet1.xml")]
        from xml.etree import ElementTree

        ns_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        ns_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        rels = {}
        if "xl/_rels/workbook.xml.rels" in namelist:
            rel_tree = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            for item in rel_tree:
                rel_id = item.get("Id")
                target = item.get("Target", "")
                if rel_id and target:
                    rels[rel_id] = "xl/" + target.lstrip("/")
        tree = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        sheets = []
        for index, sheet in enumerate(tree.iter(f"{{{ns_main}}}sheet"), start=1):
            name = sheet.get("name") or f"Sheet{index}"
            rel_id = sheet.get(f"{{{ns_rel}}}id")
            sheets.append((name, rels.get(rel_id, f"xl/worksheets/sheet{index}.xml")))
        return sheets or [("Sheet1", "xl/worksheets/sheet1.xml")]

    def _xlsx_rows(self, tree, shared_strings: list[str]) -> list[list[str]]:
        ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
        rows = []
        for row in tree.iter(f'{{{ns}}}row'):
            cells = []
            for c in row.iter(f'{{{ns}}}c'):
                inline = c.find(f'{{{ns}}}is')
                if inline is not None:
                    cells.append(''.join(t.text or '' for t in inline.iter(f'{{{ns}}}t')).strip())
                    continue
                v = c.find(f'{{{ns}}}v')
                if v is not None and v.text:
                    t = c.get('t', '')
                    cells.append(shared_strings[int(v.text)] if t == 's' and shared_strings else v.text)
                else:
                    cells.append('')
            while cells and not cells[-1]:
                cells.pop()
            if any(cell.strip() for cell in cells):
                rows.append(cells)
        return rows

    def _rows_to_markdown(self, rows: list[list[str]], max_rows: int = 200) -> str:
        if not rows:
            return ""
        width = max(len(row) for row in rows)
        normalized = [(row + [""] * (width - len(row)))[:width] for row in rows[:max_rows]]
        header = normalized[0]
        lines = [
            "| " + " | ".join(self._escape_table_cell(cell) for cell in header) + " |",
            "| " + " | ".join("---" for _ in header) + " |",
        ]
        for row in normalized[1:]:
            lines.append("| " + " | ".join(self._escape_table_cell(cell) for cell in row) + " |")
        if len(rows) > max_rows:
            lines.append(f"\n（已截断，仅保留前 {max_rows} 行，共 {len(rows)} 行）")
        return "\n".join(lines)

    def _escape_table_cell(self, value: str) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ").strip()

    def _read_xls(self, path: str, filename: str) -> str:
        try:
            import xlrd
        except Exception:
            return self._read_legacy_office(path, filename, "Excel 工作簿")
        try:
            workbook = xlrd.open_workbook(path)
            sections = []
            for sheet in workbook.sheets():
                rows = []
                for row_index in range(min(sheet.nrows, 200)):
                    rows.append([str(sheet.cell_value(row_index, col)).strip() for col in range(sheet.ncols)])
                table = self._rows_to_markdown(rows)
                if table:
                    sections.append(f"工作表：{sheet.name}\n\n{table}")
            return self._with_source(filename, "\n\n".join(sections))
        except Exception as e:
            logger.error(f"Failed to read xls: {e}")
            return ""

    def _read_pptx(self, path: str, filename: str) -> str:
        try:
            import zipfile
            from xml.etree import ElementTree
            with zipfile.ZipFile(path, 'r') as z:
                slides = [n for n in z.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')]
                sections = []
                for index, slide in enumerate(sorted(slides, key=self._slide_sort_key), start=1):
                    tree = ElementTree.fromstring(z.read(slide))
                    texts = []
                    for t in tree.iter('{http://schemas.openxmlformats.org/drawingml/2006/main}t'):
                        if t.text:
                            texts.append(t.text)
                    if texts:
                        sections.append(f"幻灯片 {index}\n\n" + "\n".join(texts))
                return self._with_source(filename, "\n\n".join(sections))
        except Exception as e:
            logger.error(f"Failed to read pptx: {e}")
            return ""

    def _slide_sort_key(self, name: str) -> int:
        match = re.search(r"slide(\d+)\.xml$", name)
        return int(match.group(1)) if match else 0

    def _read_legacy_office(self, path: str, filename: str, label: str) -> str:
        try:
            with open(path, "rb") as f:
                raw = f.read()
            ascii_text = re.findall(rb"[\x20-\x7E]{4,}", raw)
            utf16_text = re.findall(rb"(?:[\x20-\x7E]\x00){4,}", raw)
            parts = [item.decode("latin-1", errors="ignore") for item in ascii_text]
            parts.extend(item.decode("utf-16le", errors="ignore") for item in utf16_text)
            cleaned = []
            seen = set()
            for part in parts:
                text = re.sub(r"\s+", " ", part).strip()
                if text and text not in seen:
                    seen.add(text)
                    cleaned.append(text)
            body = "\n".join(cleaned[:500])
            if not body:
                return ""
            return self._with_source(filename, f"{label}（兼容模式提取）\n\n{body}")
        except Exception as e:
            logger.error(f"Failed to read legacy office file: {e}")
            return ""


_kb_service: Optional[KnowledgeBaseService] = None
