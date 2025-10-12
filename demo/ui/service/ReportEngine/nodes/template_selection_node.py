"""
模板选择节点（增强版）
- 目录自动解析：优先环境变量 REPORT_TEMPLATE_DIR，其次相对 nodes 的 ../report_template
- 支持 template_hint：上游可传入命中的模板名，直接返回
- 关键词优先：对“金融/金融科技/技术发展/路线/趋势”等命中时，先走规则选择新模板
- LLM 兜底：保留原有 LLM 选择与回退逻辑
"""

import os
import re
import json
from pathlib import Path
from typing import Dict, Any, List, Optional

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_TEMPLATE_SELECTION


_FIN_TECH_TEMPLATE = "金融科技技术发展报告模板"
_TECH_ROUTE_TEMPLATE = "技术发展路线与趋势评估模板"


class TemplateSelectionNode(BaseNode):
    """模板选择处理节点（增强）"""

    def __init__(self, llm_client, template_dir: Optional[str] = None):
        super().__init__(llm_client, "TemplateSelectionNode")
        self.template_dir = self._resolve_template_dir(template_dir)

    # ---------------- public ----------------
    def run(self, input_data: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """
        Args:
            input_data:
              - query: str
              - reports: list
              - forum_logs: str
              - template_hint: Optional[str]  # 上游直指模板名（不含 .md）
        """
        self.log_info("开始模板选择...")

        query: str = input_data.get("query", "") or ""
        reports = input_data.get("reports", [])
        forum_logs = input_data.get("forum_logs", "")
        template_hint: Optional[str] = input_data.get("template_hint")

        available_templates = self._get_available_templates()
        if not available_templates:
            self.log_info("未找到预设模板，使用内置默认模板")
            return self._get_fallback_template()

        # 1) 直接命中：template_hint
        if template_hint:
            hit = self._match_by_name(template_hint, available_templates)
            if hit:
                return hit

        # 2) 关键词优先：对金融/技术发展类直接选我们的新模板
        guessed = self._guess_template_by_keywords(query)
        if guessed:
            hit = self._match_by_name(guessed, available_templates)
            if hit:
                return hit

        # 3) LLM 选择兜底
        try:
            llm_result = self._llm_template_selection(query, reports, forum_logs, available_templates)
            if llm_result:
                return llm_result
        except Exception as e:
            self.log_error(f"LLM模板选择失败: {str(e)}")

        # 4) 依旧失败则回退
        return self._get_fallback_template()

    # ---------------- impl ----------------
    def _resolve_template_dir(self, template_dir: Optional[str]) -> str:
        # 优先：显式传参
        if template_dir:
            return template_dir
        # 其次：环境变量
        env_dir = os.getenv("REPORT_TEMPLATE_DIR")
        if env_dir:
            return env_dir
        # 默认：nodes/../report_template
        base = Path(__file__).resolve().parents[1] / "report_template"
        return str(base)

    def _guess_template_by_keywords(self, query: str) -> Optional[str]:
        q = (query or "").lower()
        # 金融科技/金融 + 技术/发展/趋势
        if re.search(r"(金融科技|fintech|金融)", q) and re.search(r"(技术|发展|趋势|路线|roadmap)", q):
            return _FIN_TECH_TEMPLATE
        # 泛“技术发展/路线/趋势”
        if re.search(r"(技术|tech).*(发展|趋势|路线|roadmap)", q):
            return _TECH_ROUTE_TEMPLATE
        return None

    def _match_by_name(self, name: str, templates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        norm = name.strip().replace(".md", "")
        for t in templates:
            tname = t["name"].replace(".md", "")
            if norm == tname or norm in tname or tname in norm:
                self.log_info(f"命中模板: {t['name']}")
                return {
                    "template_name": t["name"],
                    "template_content": t["content"],
                    "selection_reason": "规则/Hint命中"
                }
        return None

    def _llm_template_selection(self, query: str, reports: List[Any], forum_logs: str,
                                available_templates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        self.log_info("尝试使用LLM进行模板选择...")

        # 列表文本
        template_list = "\n".join([f"- {t['name']}: {t['description']}" for t in available_templates])

        # 报告摘要（截断）
        reports_summary = ""
        if reports:
            reports_summary = "\n\n=== 分析引擎报告内容 ===\n"
            for i, report in enumerate(reports, 1):
                if isinstance(report, dict):
                    content = report.get('content', str(report))
                elif hasattr(report, 'content'):
                    content = report.content
                else:
                    content = str(report)
                if len(content) > 1000:
                    content = content[:1000] + "...(内容已截断)"
                reports_summary += f"\n报告{i}内容:\n{content}\n"

        # 论坛摘要（截断）
        forum_summary = ""
        if forum_logs and str(forum_logs).strip():
            forum_content = forum_logs[:800] + ("...(讨论内容已截断)" if len(forum_logs) > 800 else "")
            forum_summary = "\n\n=== 三个引擎的讨论内容 ===\n" + forum_content

        user_message = f"""查询内容: {query}

报告数量: {len(reports)} 个分析引擎报告
论坛日志: {'有' if forum_logs else '无'}
{reports_summary}{forum_summary}

可用模板:
{template_list}

请根据查询/报告/论坛内容，选择最合适的模板；如果是金融或技术发展主题，优先选择对应模板。输出 JSON：
{{
  "template_name": "...",
  "selection_reason": "..."
}}"""

        response = self.llm_client.invoke(SYSTEM_PROMPT_TEMPLATE_SELECTION, user_message)
        if not response or not response.strip():
            self.log_error("LLM返回空响应")
            return None

        self.log_info(f"LLM原始响应: {response}")

        try:
            cleaned = self._clean_llm_response(response)
            result = json.loads(cleaned)
            selected = result.get("template_name", "")
            for t in available_templates:
                if selected == t["name"] or selected in t["name"]:
                    return {
                        "template_name": t["name"],
                        "template_content": t["content"],
                        "selection_reason": result.get("selection_reason", "LLM智能选择")
                    }
            self.log_error(f"LLM选择的模板不存在: {selected}")
            return None
        except json.JSONDecodeError:
            self.log_error("JSON解析失败，尝试从文本提取")
            return self._extract_template_from_text(response, available_templates)

    def _clean_llm_response(self, response: str) -> str:
        if '```json' in response:
            response = response.split('```json')[1].split('```')[0]
        elif '```' in response:
            response = response.split('```')[1].split('```')[0]
        return response.strip()

    def _extract_template_from_text(self, response: str, available_templates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        self.log_info("尝试从文本响应中提取模板信息")
        for t in available_templates:
            variants = [
                t['name'],
                t['name'].replace('.md', ''),
                t['name'].replace('模板', ''),
            ]
            for v in variants:
                if v and v in response:
                    return {
                        "template_name": t["name"],
                        "template_content": t["content"],
                        "selection_reason": "从文本响应中提取"
                    }
        return None

    def _get_available_templates(self) -> List[Dict[str, Any]]:
        templates: List[Dict[str, Any]] = []
        if not os.path.exists(self.template_dir):
            self.log_error(f"模板目录不存在: {self.template_dir}")
            return templates

        for filename in os.listdir(self.template_dir):
            if filename.endswith(".md"):
                p = os.path.join(self.template_dir, filename)
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        content = f.read()
                    name = filename.replace(".md", "")
                    description = self._extract_template_description(name)
                    templates.append({"name": name, "path": p, "content": content, "description": description})
                except Exception as e:
                    self.log_error(f"读取模板文件失败 {filename}: {str(e)}")
        return templates

    def _extract_template_description(self, template_name: str) -> str:
        n = template_name
        if any(k in n for k in ["金融科技", "FinTech", "金融"]):
            return "适用于金融科技/金融领域的技术发展与合规风控主题"
        if any(k in n for k in ["技术发展", "路线", "趋势", "roadmap"]):
            return "适用于通用技术路线/趋势评估与对比"
        if '企业品牌' in n:
            return "适用于企业品牌声誉和形象分析"
        elif '市场竞争' in n:
            return "适用于市场竞争格局和对手分析"
        elif '日常' in n or '定期' in n:
            return "适用于日常监测和定期汇报"
        elif '政策' in n or '行业' in n:
            return "适用于政策影响和行业动态分析"
        elif '热点' in n or '社会' in n:
            return "适用于社会热点和公共事件分析"
        elif '突发' in n or '危机' in n:
            return "适用于突发事件和危机公关"
        return "通用报告模板"

    def _get_fallback_template(self) -> Dict[str, Any]:
        self.log_info("未找到合适模板，回退到空模板（LLM自拟结构）")
        return {
            "template_name": "自由发挥模板",
            "template_content": "",
            "selection_reason": "未找到合适的预设模板"
        }
