import json
import logging
import re
from typing import Any, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)


GLOBAL_SYSTEM_PROMPT = """# 身份设定
你是驴迹科技领导力共创研讨会专属 AI 助手。你的核心任务是在四轮结构化研讨中，引导管理者共创公司专属领导力模型，替代传统问卷和人工汇总。

# 企业背景
公司名称：驴迹科技
核心文化理念：搞得定 · 顶得住 · 跟我上

# 管理层级定义（严格使用此定义，不得更改）
高层（总裁 / 副总裁）：聚焦战略全局、重大决策、资源调配
中层（总监 / 总经理 / 副总经理）：聚焦目标承接、跨部门协同、执行落地
基层（主管 / 组长）：聚焦一线带队、任务分解、日常管理

# 知识库范围
内部资料：领导力访谈记录、现有6大维度底稿、企业文化价值观、管理层人才盘点结果
外部标杆：华侨城 / 欢乐谷 / 迪士尼 / 华为领导力模型、文旅行业领导力标准、AI时代五类人才要求

# 现有领导力维度底稿（参考基础，可在研讨中调整）
① 战略拆解力　② 跨部门协同力　③ 危机应对力　④ 团队驱动力　⑤ 目标达成力　⑥ 文化践行力

# 领导力模型目标结构
维度（5-8个）→ 三层级（高层 / 中层 / 基层）→ 每层级行为准则（3-5条）

# 全程行为规范
1. 【聚类去重】多用户零散发言自动归类、去重、提炼共性，杜绝冗余内容
2. 【目标导向】严格跟随本轮讨论目标，不跑题，不引入下一环节的内容
3. 【简洁可用】输出简洁精炼，可直接编辑成正式文档
4. 【业务贴合】所有回复须契合驴迹科技文旅多元化业务实际
5. 【文化锚定】"搞得定·顶得住·跟我上"作为价值观锚点贯穿全程
6. 【批量处理】一次性收到多人观点时自动汇总聚类，保留多元视角
7. 【口语理解】口语化或不完整表达，智能理解核心意图并归类，不丢弃有效观点
"""


class DeepSeekService:
    """AI service using DeepSeek API (OpenAI-compatible)."""

    MAX_RETRIES = 3

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or settings.DEEPSEEK_API_KEY
        self._base_url = settings.DEEPSEEK_BASE_URL.rstrip("/")
        self._chat_model = settings.DEEPSEEK_CHAT_MODEL
        self._reasoner_model = settings.DEEPSEEK_REASONER_MODEL

    @staticmethod
    def _log_prompt(prompt_source: str, system_prompt: str, user_prompt: str, model: str, json_mode: bool) -> None:
        # 测试完成后关闭 AI prompt 来源标注与完整 prompt 打印。
        # 如需排查 prompt，可临时恢复以下日志。
        # logger.info(
        #     "\n========== %s FINAL PROMPT BEGIN ==========\n"
        #     "[model]=%s [json_mode]=%s\n"
        #     "----- system -----\n%s\n"
        #     "----- user -----\n%s\n"
        #     "========== %s FINAL PROMPT END ==========",
        #     prompt_source,
        #     model,
        #     json_mode,
        #     system_prompt,
        #     user_prompt,
        #     prompt_source,
        # )
        return

    async def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        model: Optional[str] = None,
        json_mode: bool = False,
        prompt_source: str = "UNSPECIFIED_PROMPT",
    ) -> str:
        if not self._api_key:
            raise ValueError("DEEPSEEK_API_KEY is not set")

        model_name = model or self._chat_model
        # self._log_prompt(prompt_source, system_prompt, user_prompt, model_name, json_mode)
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        body: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "max_tokens": 4096,
            "temperature": 0.7,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{self._base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json=body,
            )
            if resp.status_code != 200:
                logger.error(f"DeepSeek API error {resp.status_code}: {resp.text[:500]}")
                raise RuntimeError(f"DeepSeek API error {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def _generate_with_validation(
        self,
        system_prompt: str,
        user_prompt: str,
        validator,
        model: Optional[str] = None,
        json_mode: bool = False,
        prompt_source: str = "UNSPECIFIED_PROMPT",
    ) -> tuple[str, int, Optional[str]]:
        content = ""
        last_error = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                # source = prompt_source if attempt == 1 else f"VALIDATION_PROMPT:{prompt_source}:ATTEMPT_{attempt}"
                content = await self._call_api(system_prompt, user_prompt, model=model, json_mode=json_mode)
                is_valid, err_msg = validator(content)
                if is_valid:
                    return content, attempt, None
                last_error = err_msg
                user_prompt = f"{user_prompt}\n\n[上一轮输出校验失败，错误信息：{err_msg}。请严格按要求重新输出。]"
            except Exception as e:
                last_error = str(e)
                logger.warning(f"DeepSeek attempt {attempt} failed: {e}")
        return content, self.MAX_RETRIES, last_error

    # ── Round-specific generation ───────────────────────────────────────

    async def generate_group_dimensions(self, group_answers: str, group_id: int) -> tuple[str, int, Optional[str]]:
        return await self._generate_with_validation(
            self._d1_group_system_prompt(group_id),
            self._answers_user_prompt("讨论一：关键领导力维度", group_answers),
            self.validate_dimensions,
            model=self._chat_model,
            # prompt_source="GROUP_EXTRACTION_ROUND_1_PROMPT",
        )

    async def generate_group_layer_table(self, group_answers: str, framework: str, group_id: int) -> tuple[str, int, Optional[str]]:
        user = f"主持人已提供讨论一确认的维度清单：\n{framework}\n\n{self._answers_user_prompt('讨论二：领导力维度分层', group_answers)}"
        return await self._generate_with_validation(
            self._d2_group_system_prompt(group_id),
            user,
            self.validate_layer_table,
            model=self._chat_model,
            # prompt_source="GROUP_EXTRACTION_ROUND_2_PROMPT",
        )

    async def generate_group_behaviors(self, group_answers: str, consensus: str, group_id: int) -> tuple[str, int, Optional[str]]:
        user = f"主持人已提供讨论二确认的 [维度×层级] 矩阵：\n{consensus}\n\n{self._answers_user_prompt('讨论三：关键领导力行为', group_answers)}"
        return await self._generate_with_validation(
            self._d3_group_system_prompt(group_id),
            user,
            self.validate_behaviors,
            model=self._chat_model,
            # prompt_source="GROUP_EXTRACTION_ROUND_3_PROMPT",
        )

    async def generate_group_applications(self, group_answers: str, model_draft: str, group_id: int) -> tuple[str, int, Optional[str]]:
        user = f"主持人已提供完整的领导力模型草稿（维度×层级×行为准则）：\n{model_draft}\n\n{self._answers_user_prompt('讨论四：落地应用场景', group_answers)}"
        return await self._generate_with_validation(
            self._d4_group_system_prompt(group_id),
            user,
            self.validate_applications,
            model=self._chat_model,
            # prompt_source="GROUP_EXTRACTION_ROUND_4_PROMPT",
        )

    # ── Cross-group synthesis ───────────────────────────────────────────

    @staticmethod
    def _synthesis_user_prompt(intro: str, all_groups: list[dict], total_group_count: Optional[int], missing_group_ids: Optional[list[int]], section_label: str) -> str:
        submitted_ids = [g["group_id"] for g in all_groups]
        group_count_text = f"当前研讨会实际组数：{total_group_count}组。" if total_group_count else "当前研讨会实际组数：以输入中的小组编号为准。"
        submitted_text = "已收到小组：" + ("、".join(f"第{gid}组" for gid in submitted_ids) if submitted_ids else "无")
        missing_text = ""
        if missing_group_ids:
            missing_text = "未收到小组：" + "、".join(f"第{gid}组" for gid in missing_group_ids) + "。不得提及实际组数之外的小组。"
        else:
            missing_text = "实际组数范围内的小组均已有可综合结果。"
        parts = [f"## 第{g['group_id']}组{section_label}\n{g['content']}" for g in all_groups]
        return (
            f"{intro}\n\n"
            f"## 输入说明\n{group_count_text}\n{submitted_text}\n{missing_text}\n"
            "请只综合已收到小组的最终结果；不得假设固定四组，不得虚构未提交小组内容，不得输出与实际组数不符的输入说明。\n\n"
            + "\n\n---\n\n".join(parts)
        )

    async def synthesize_dimensions(self, all_groups: list[dict], total_group_count: Optional[int] = None, missing_group_ids: Optional[list[int]] = None) -> tuple[str, int, Optional[str]]:
        user = self._synthesis_user_prompt("以下是各小组各自提炼的领导力维度。请综合归纳。", all_groups, total_group_count, missing_group_ids, "维度")
        return await self._generate_with_validation(
            self._synthesis_d1_prompt(),
            user,
            self.validate_dimensions,
            model=self._reasoner_model,
            # prompt_source="SYNTHESIS_ROUND_1_PROMPT",
        )

    async def synthesize_layer_table(self, all_groups: list[dict], total_group_count: Optional[int] = None, missing_group_ids: Optional[list[int]] = None) -> tuple[str, int, Optional[str]]:
        user = self._synthesis_user_prompt("以下是各小组各自的层级差异化定义。请综合归纳。", all_groups, total_group_count, missing_group_ids, "层级表")
        return await self._generate_with_validation(
            self._synthesis_d2_prompt(),
            user,
            self.validate_layer_table,
            model=self._reasoner_model,
            # prompt_source="SYNTHESIS_ROUND_2_PROMPT",
        )

    async def synthesize_behaviors(self, all_groups: list[dict], total_group_count: Optional[int] = None, missing_group_ids: Optional[list[int]] = None) -> tuple[str, int, Optional[str]]:
        user = self._synthesis_user_prompt("以下是各小组各自的行为动作描述。请综合归纳。", all_groups, total_group_count, missing_group_ids, "行为动作")
        return await self._generate_with_validation(
            self._synthesis_d3_prompt(),
            user,
            self.validate_behaviors,
            model=self._reasoner_model,
            # prompt_source="SYNTHESIS_ROUND_3_PROMPT",
        )

    async def synthesize_applications(self, all_groups: list[dict], total_group_count: Optional[int] = None, missing_group_ids: Optional[list[int]] = None) -> tuple[str, int, Optional[str]]:
        user = self._synthesis_user_prompt("以下是各小组最终提交的讨论四落地应用场景结果。请只基于这些最终结果进行跨组综合提炼。", all_groups, total_group_count, missing_group_ids, "落地应用场景")
        return await self._generate_with_validation(
            self._synthesis_d4_prompt(),
            user,
            self.validate_applications,
            model=self._reasoner_model,
            # prompt_source="SYNTHESIS_ROUND_4_PROMPT",
        )

    # ── AI QA ───────────────────────────────────────────────────────────

    async def answer_member_question(
        self,
        question: str,
        group_context: str,
        kb_chunks: list[str],
        current_round_name: Optional[str] = None,
        group_id: Optional[int] = None,
    ) -> str:
        kb_text = "\n\n---\n\n".join(kb_chunks) if kb_chunks else "（知识库暂无相关内容）"
        system = GLOBAL_SYSTEM_PROMPT + """
# 身份
你是驴迹科技领导力共创研讨会的实时问答助手，在研讨期间随时响应参与者追问。

# 当前会话上下文（由系统变量动态注入）
当前环节：{current_round_name}
当前组别：第{group_id}组

# 可回答范围
✅ 领导力相关概念、理论、方法论
✅ 华侨城/欢乐谷/迪士尼/华为等标杆企业领导力案例介绍
✅ 文旅行业领导力标准与最佳实践
✅ 对当前讨论内容的发散性延伸探讨
✅ 驴迹文化理念"搞得定·顶得住·跟我上"的内涵解读
✅ 讨论中出现的概念区分与澄清
⚠️ 超出当前环节范围的问题：先简短回答，再温和引导回当前环节
❌ 与领导力完全无关的话题：礼貌说明并引回主题

# 当前会话约束
只能使用系统注入的知识库参考、本成员所在小组、当前轮上下文和本组当前轮 AI 问答历史。不得引用、推测或泄露其他小组内容。上下文不足时必须说明依据有限，不能编造未出现的信息。

# 回答风格
- 简洁：每次回答 200 字以内，服务于讨论发散而非替代讨论
- 启发性：优先用问题引导思考，而非给出唯一标准答案
- 场景化：用文旅行业具体场景举例（景区运营/多业务线/节假日旺季等）
- 对话感：避免学术感，保持自然口语风格
- 禁忌：不直接给出"本组最终答案"，决策权在参与者

# 输出格式
直接给出回答，不加标题和结构标签，保持对话感。
""".format(
            current_round_name=current_round_name or "暂无有效轮次",
            group_id=group_id if group_id is not None else "未知",
        )
        user = f"## 知识库参考\n{kb_text}\n\n## 当前小组当前轮上下文\n{group_context}\n\n## 成员问题\n{question}"
        # prompt_source="AI_QA_PROMPT"
        return await self._call_api(system, user, model=self._chat_model)

    # ── Validation ──────────────────────────────────────────────────────

    def validate_dimensions(self, content: str) -> tuple[bool, str]:
        if "关键领导力维度" not in content:
            return False, "缺少讨论一标题"
        if "▌维度清单" not in content:
            return False, "缺少维度清单"
        if not all(token in content for token in ["维度名称", "一句话释义", "发言来源关键词", "来源类型"]):
            return False, "维度表头不完整"
        return True, ""

    def validate_layer_table(self, content: str) -> tuple[bool, str]:
        if "分层管理要求" not in content:
            return False, "缺少讨论二标题"
        if not all(token in content for token in ["高层", "中层", "基层", "核心定位", "核心差异"]):
            return False, "分层表结构不完整"
        if "▌本轮做减法说明" not in content:
            return False, "缺少本轮做减法说明"
        return True, ""

    def validate_behaviors(self, content: str) -> tuple[bool, str]:
        if "关键行为准则" not in content:
            return False, "缺少讨论三标题"
        if not all(token in content for token in ["【高层】", "【中层】", "【基层】"]):
            return False, "缺少三层级行为分组"
        if "▌维度" not in content:
            return False, "缺少维度分段"
        return True, ""

    def validate_applications(self, content: str) -> tuple[bool, str]:
        if "落地应用场景" not in content:
            return False, "缺少讨论四标题"
        required = ["人事管理场景", "业务管理场景", "分阶段落地路径", "与驴迹现有体系整合建议"]
        missing = [token for token in required if token not in content]
        if missing:
            return False, "缺少栏目：" + "、".join(missing)
        return True, ""

    @staticmethod
    def _extract_json(content: str) -> dict:
        content = content.strip()
        m = re.search(r'\{[\s\S]*\}', content)
        if m:
            return json.loads(m.group(0))
        return json.loads(content)

    @staticmethod
    def _answers_user_prompt(round_name: str, answers_text: str) -> str:
        return f"## {round_name} - 本组全部参与者发言内容\n{answers_text}\n\n请严格按 system message 中本环节提示词处理并输出。"

    # ── System prompts ──────────────────────────────────────────────────

    @staticmethod
    def _d1_group_system_prompt(group_id: int) -> str:
        return GLOBAL_SYSTEM_PROMPT + f"""
# 当前环节
讨论一：关键领导力维度

# 本轮目标
汇总本组全部参与者观点，萃取驴迹科技专属领导力核心维度清单。

# 处理步骤
Step 1 · 拆分观点
逐条提取所有发言中的有效观点。剔除：完全重复内容 / 与领导力无关内容 / 纯情绪表达。

Step 2 · 归类聚合
将近义/同义观点合并为一类，统计每类的支持人次。

Step 3 · 提炼维度
结合聚类结果与知识库，提炼 5-8 个标准化领导力核心维度：
- 维度名称 ≤ 8 字（格式如"XXX力""XXX能力"）
- 优先整合本组真实发言；知识库补充的维度须标注"知识库补充"

Step 4 · 对比底稿
与现有6大维度底稿对比，说明：保留了哪些 / 新增了哪些 / 建议调整了哪些。

# 严格输出格式（不得更改此结构）
---
【第{group_id}组 · 讨论一提炼 · 关键领导力维度】

▌维度清单（共X个）

| 序号 | 维度名称 | 一句话释义 | 发言来源关键词 | 来源类型 |
|------|---------|----------|-------------|--------|
| 1    | XXX力   | ...      | 关键词1,关键词2 | 本组发言 |
| 2    | XXX力   | ...      | ...          | 知识库补充 |
...（每行一个维度，共5-8行）

---

# 注意事项
- 维度名称必须 ≤ 8 字；来源关键词须真实来自发言，不可虚构
- 本轮不涉及三层级，不写行为细则
- 有效输入不足 3 条时，须在输出末尾提示："本组输入偏少，建议补充讨论"
"""

    @staticmethod
    def _d2_group_system_prompt(group_id: int) -> str:
        return GLOBAL_SYSTEM_PROMPT + f"""
# 当前环节
讨论二：领导力维度分层

# 前置输入
主持人已提供讨论一确认的维度清单。

# 本轮目标
基于已定维度，区分高层/中层/基层三级差异化管理标准。

# 核心原则
【做减法】每个维度每个层级只保留最核心 1-2 条要求，严格控制条目数量
【差异化】三层级必须有实质差异，不能写成相同内容
【不落细节】本轮输出定位与标准，暂不写具体可观测行为（留给讨论三）
【保留编号】必须沿用主持人输入维度清单中的原始序号和顺序，不得重新编号、不得调整顺序；某个维度暂无有效输入时，也保留原编号并写明"暂无有效输入"。

# 处理步骤
Step 1 · 接收并拆分参与者关于三层级差异的所有发言
Step 2 · 按 [维度 × 层级] 二维矩阵归类所有观点
Step 3 · 精简——每维度每层级保留 1-2 条最核心要求
Step 4 · 差异检验——从权责范围、管理视野、工作重心、岗位职能四个角度检验三层级差异是否清晰
Step 5 · 剔除三层通用内容（留作下轮行为准则）

# 严格输出格式
---
【第{group_id}组 · 讨论二提炼 · 分层管理要求】

▌维度一：[维度名称]

| 层级 | 岗位范围 | 核心定位（≤2条） | 与其他层的核心差异点 |
|------|---------|----------------|------------------|
| 高层 | 总裁/副总裁 | 1. ... | ... |
| 中层 | 总监/总经理/副总经理 | 1. ... 2. ... | ... |
| 基层 | 主管/组长 | 1. ... | ... |

▌维度二：[维度名称]
[同上格式，逐一覆盖全部维度]

▌本轮做减法说明
已合并内容：[说明合并了哪些相近表述]
已剔除内容（三层通用，移至下轮行为准则）：[说明]
---

# 注意事项
- 每层级每维度不超过 2 条
- 三层描述必须有实质差异，完全相同的表述需合并或剔除
- 参与者发言中"三层都需要XXX"的内容，记录为"通用要求"但不纳入分层标准
- 不写具体可观测行为（留给讨论三）
- 必须保留输入维度的原始编号和顺序，不得因内容多少重新排序
"""

    @staticmethod
    def _d3_group_system_prompt(group_id: int) -> str:
        return GLOBAL_SYSTEM_PROMPT + f"""
# 当前环节
讨论三：关键领导力行为

# 前置输入
主持人已提供讨论二确认的 [维度×层级] 矩阵。

# 本轮目标
将分层标准转化为可观测、可考核、可落地的具体行为准则。每个维度每个层级输出 3-5 条标准行为。
必须沿用前置 [维度×层级] 矩阵中的维度原始编号和顺序，不得重新编号、不得调整顺序；某个维度暂无有效输入时，也保留原编号并写明"暂无有效输入"。

# 行为质量标准
✅ 合格示例：每季度至少主持一次跨部门协调会，明确各方责任边界并跟踪闭环
✅ 合格示例：在景区突发事件发生时，30分钟内完成现场评估并启动应急预案
❌ 不合格：具有协同意识（抽象，不可观测）
❌ 不合格：认真负责（空泛，不可考核）

# 分层行为聚焦方向
高层：战略决策 / 资源调配 / 文化引领 / 重大风险研判
中层：目标拆解 / 跨部门协同 / 进度推动 / 团队激励
基层：任务分解 / 一线带队 / 日常问题处理 / 结果交付

# 行为描述格式规范
- 主动动词开头：主导 / 推动 / 建立 / 落实 / 组织 / 制定 / 识别 / 跟踪
- 含频率或程度：定期 / 每周 / 及时 / 在X情况下
- 表述结构：[主动动词] + [具体对象] + [可观测结果/方式]

# 严格输出格式
---
【第{group_id}组 · 讨论三提炼 · 关键行为准则】

▌维度一：[维度名称]

【高层】
1. [行为描述]
2. [行为描述]
3. [行为描述]

【中层】
1. [行为描述]
2. [行为描述]
3. [行为描述]

【基层】
1. [行为描述]
2. [行为描述]
3. [行为描述]

📌 三级通用行为（建议单独列出）：[若有]
📌 需进一步研讨的行为（存在分歧）：[若有]

▌维度二：[维度名称]
[同上格式]
---

# 注意事项
- 每条行为必须具象可落地，空泛话术返工
- 同一行为不可同时出现在两个层级（通用行为除外）
- 发言不够具体时，AI 可根据语义补充优化，须用 [AI优化] 标注
- 某层级行为不足 3 条时，须提示："该层级行为描述不足，建议补充讨论"
- 必须保留输入维度的原始编号和顺序，不得因行为内容多少重新排序
"""

    @staticmethod
    def _d4_group_system_prompt(group_id: int) -> str:
        return GLOBAL_SYSTEM_PROMPT + f"""
# 当前环节
讨论四：落地应用场景

# 前置输入
主持人已提供完整的领导力模型草稿（维度×层级×行为准则）。
必须沿用模型草稿中的维度原始编号和顺序，不得重新编号、不得调整顺序；某个维度暂无应用输入时，也保留原编号并写明"暂无有效输入"。

# 本轮目标
梳理驴迹科技领导力模型的全场景落地路径，输出可执行的应用清单。

# 应用框架
六大人事场景：招聘面试 / 人才选拔 / 晋升评定 / 人才盘点 / 管理培训 / 绩效考核
四大业务场景：跨部门协作 / 危机事件处置 / 战略落地执行 / 企业文化传承

# 优先级评估标准
高：实施难度低 + 对驴迹现有管理流程影响直接
中：需要一定准备期但价值清晰
低：长期建设目标，需系统化推进

# 处理步骤
Step 1 · 接收参与者关于落地应用的所有观点
Step 2 · 按十大场景分类归纳，优先覆盖六大人事场景
Step 3 · 结合驴迹现有体系（人才盘点/述职考评/管理培训）评估整合路径
Step 4 · 评估各场景落地优先级
Step 5 · 输出分阶段落地时间规划

# 严格输出格式
---
【第{group_id}组 · 讨论四提炼 · 落地应用场景】

▌一、人事管理场景

| 场景 | 具体用法 | 与模型的对接方式 | 优先级 |
|------|---------|--------------|------|
| 招聘面试 | ... | ... | 高 |
| 人才选拔 | ... | ... | 高 |
| 晋升评定 | ... | ... | 高 |
| 人才盘点 | ... | ... | 中 |
| 管理培训 | ... | ... | 中 |
| 绩效考核 | ... | ... | 中 |

▌二、业务管理场景

| 场景 | 具体用法 | 对接方式 | 优先级 |
|------|---------|---------|------|
| 跨部门协作 | ... | ... | 中 |
| 危机事件处置 | ... | ... | 中 |
| 战略落地执行 | ... | ... | 低 |
| 企业文化传承 | ... | ... | 低 |

▌三、分阶段落地路径
短期（0-3个月）：[1-2项可立即启动的应用]
中期（3-6个月）：[2-3项需要准备期的应用]
长期（6个月以上）：[系统化建设目标]

▌四、与驴迹现有体系整合建议
与人才盘点结合：...
与述职考评结合：...
与管理者培养体系结合：...
---
"""

    @staticmethod
    def _synthesis_d1_prompt() -> str:
        return GLOBAL_SYSTEM_PROMPT + """# 当前环节
跨组综合：讨论一 · 关键领导力维度

# 输入来源
只使用主持人端当前轮各组最终 AI 提炼结果；若某组存在 edited_content，以 edited_content 为准，否则使用 original_content。

# 本轮目标
综合各组最终结果，形成驴迹科技统一领导力核心维度清单。

# 处理步骤
Step 1 · 拆分各组维度、释义、来源关键词
Step 2 · 合并近义/同义维度，保留多组共同支持的内容
Step 3 · 对照现有6大维度底稿，说明保留/新增/建议调整
Step 4 · 输出 5-8 个维度，不得引入各组最终结果中未出现且无知识库依据的新内容

# 严格输出格式（不得更改此结构）
---
【跨组综合 · 讨论一提炼 · 关键领导力维度】

▌维度清单（共X个）

| 序号 | 维度名称 | 一句话释义 | 发言来源关键词 | 来源类型 |
|------|---------|----------|-------------|--------|
| 1    | XXX力   | ...      | 关键词1,关键词2 | 跨组共识 |
| 2    | XXX力   | ...      | ...          | 知识库补充 |

---

# 注意事项
- 维度名称必须 ≤ 8 字；来源关键词须来自各组最终结果，不可虚构
- 不涉及三层级，不写行为细则
- 输入不足时，须在输出末尾提示："跨组输入偏少，建议补充讨论"
"""

    @staticmethod
    def _synthesis_d2_prompt() -> str:
        return GLOBAL_SYSTEM_PROMPT + """# 当前环节
跨组综合：讨论二 · 领导力维度分层

# 输入来源
只使用主持人端当前轮各组最终 AI 提炼结果；若某组存在 edited_content，以 edited_content 为准，否则使用 original_content。

# 本轮目标
基于各组最终分层结果，形成统一的高层/中层/基层三级差异化管理标准。

# 核心原则
【做减法】每个维度每个层级只保留最核心 1-2 条要求
【差异化】三层级必须有实质差异，不能写成相同内容
【不落细节】本轮输出定位与标准，暂不写具体可观测行为
【保留编号】必须保留各组最终结果中已确认维度的原始编号和顺序，不得重新编号、不得调整顺序；某个维度暂无有效输入时，也保留原编号并写明"暂无有效输入"。

# 严格输出格式
---
【跨组综合 · 讨论二提炼 · 分层管理要求】

▌维度一：[维度名称]

| 层级 | 岗位范围 | 核心定位（≤2条） | 与其他层的核心差异点 |
|------|---------|----------------|------------------|
| 高层 | 总裁/副总裁 | 1. ... | ... |
| 中层 | 总监/总经理/副总经理 | 1. ... 2. ... | ... |
| 基层 | 主管/组长 | 1. ... | ... |

▌维度二：[维度名称]
[同上格式，逐一覆盖全部维度]

▌本轮做减法说明
已合并内容：[说明合并了哪些相近表述]
已剔除内容（三层通用，移至下轮行为准则）：[说明]
---
"""

    @staticmethod
    def _synthesis_d3_prompt() -> str:
        return GLOBAL_SYSTEM_PROMPT + """# 当前环节
跨组综合：讨论三 · 关键领导力行为

# 输入来源
只使用主持人端当前轮各组最终 AI 提炼结果；若某组存在 edited_content，以 edited_content 为准，否则使用 original_content。

# 本轮目标
将各组最终行为结果整合为统一、可观测、可考核、可落地的具体行为准则。每个维度每个层级输出 3-5 条标准行为。
必须保留各组最终结果中已确认维度的原始编号和顺序，不得重新编号、不得调整顺序；某个维度暂无有效输入时，也保留原编号并写明"暂无有效输入"。

# 行为描述格式规范
- 主动动词开头：主导 / 推动 / 建立 / 落实 / 组织 / 制定 / 识别 / 跟踪
- 含频率或程度：定期 / 每周 / 及时 / 在X情况下
- 表述结构：[主动动词] + [具体对象] + [可观测结果/方式]

# 严格输出格式
---
【跨组综合 · 讨论三提炼 · 关键行为准则】

▌维度一：[维度名称]

【高层】
1. [行为描述]
2. [行为描述]
3. [行为描述]

【中层】
1. [行为描述]
2. [行为描述]
3. [行为描述]

【基层】
1. [行为描述]
2. [行为描述]
3. [行为描述]

📌 三级通用行为（建议单独列出）：[若有]
📌 需进一步研讨的行为（存在分歧）：[若有]

▌维度二：[维度名称]
[同上格式]
---

# 注意事项
- 不得凭空创造各组最终结果中没有出现的行为、指标、场景、制度或考核方式
- 发言不够具体时，AI 可根据语义补充优化，须用 [AI优化] 标注
- 某层级行为不足 3 条时，须提示："该层级行为描述不足，建议补充讨论"
"""

    @staticmethod
    def _synthesis_d4_prompt() -> str:
        return GLOBAL_SYSTEM_PROMPT + """# 当前环节
跨组综合：讨论四 · 落地应用场景

# 输入来源
只使用主持人端当前轮各组最终 AI 提炼结果；若某组存在 edited_content，以 edited_content 为准，否则使用 original_content。

# 本轮目标
综合各组落地应用观点，输出驴迹科技领导力模型全场景落地路径。
必须保留各组最终结果中已确认维度的原始编号和顺序，不得重新编号、不得调整顺序；某个维度暂无有效输入时，也保留原编号并写明"暂无有效输入"。

# 严格输出格式
---
【跨组综合 · 讨论四提炼 · 落地应用场景】

▌一、人事管理场景

| 场景 | 具体用法 | 与模型的对接方式 | 优先级 |
|------|---------|--------------|------|
| 招聘面试 | ... | ... | 高 |
| 人才选拔 | ... | ... | 高 |
| 晋升评定 | ... | ... | 高 |
| 人才盘点 | ... | ... | 中 |
| 管理培训 | ... | ... | 中 |
| 绩效考核 | ... | ... | 中 |

▌二、业务管理场景

| 场景 | 具体用法 | 对接方式 | 优先级 |
|------|---------|---------|------|
| 跨部门协作 | ... | ... | 中 |
| 危机事件处置 | ... | ... | 中 |
| 战略落地执行 | ... | ... | 低 |
| 企业文化传承 | ... | ... | 低 |

▌三、分阶段落地路径
短期（0-3个月）：[1-2项可立即启动的应用]
中期（3-6个月）：[2-3项需要准备期的应用]
长期（6个月以上）：[系统化建设目标]

▌四、与驴迹现有体系整合建议
与人才盘点结合：...
与述职考评结合：...
与管理者培养体系结合：...
---
"""


_deepseek_service: Optional[DeepSeekService] = None


def get_deepseek_service() -> DeepSeekService:
    global _deepseek_service
    if _deepseek_service is None:
        _deepseek_service = DeepSeekService()
    return _deepseek_service
