# -*- coding: utf-8 -*-
"""
Deep Search Agent 主类（适配增强版）
- 多 LLM 提供商（zhipu/openai + siliconflow）
- 安全路由：风控触发时“安全降敏+硅基流动（敏感回退）”，普通报错用 OpenAI（通用回退）
- 保存阶段稳定化：draft/state 一定产出；deep_report 可选
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from .llms import OpenAILLM, BaseLLM
try:
    from .llms import ZhipuLLM  # type: ignore
except Exception:
    ZhipuLLM = None  # type: ignore

from .llms.silicon_llm import SiliconFlowLLM
from .llms.safe_llm import SafeRouterLLM

from .nodes import (
    ReportStructureNode,
    FirstSearchNode,
    ReflectionNode,
    FirstSummaryNode,
    ReflectionSummaryNode,
    ReportFormattingNode
)
from .state import State
from .tools import TavilyNewsAgency, TavilyResponse
from .utils import Config, load_config, format_search_results_for_prompt


def _safe_get(obj: Any, key: str, default: str = "") -> str:
    try:
        if isinstance(obj, dict):
            v = obj.get(key, default)
        else:
            v = getattr(obj, key, default)
        return "" if v is None else str(v)
    except Exception:
        return default


class DeepSearchAgent:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or load_config()
        # 兜底字段
        if not hasattr(self.config, "max_reflections"):
            self.config.max_reflections = int(os.getenv("QE_MAX_REFLECTIONS", "2"))
        if not hasattr(self.config, "max_content_length"):
            self.config.max_content_length = int(os.getenv("QE_MAX_CONTENT_LEN", "4000"))

        # 输出目录优先级：load_config -> 环境变量 -> 默认子目录
        out_dir_env = os.getenv("QUERY_OUTPUT_DIR")
        if out_dir_env:
            self.config.output_dir = out_dir_env
        if not getattr(self.config, "output_dir", None):
            self.config.output_dir = str(Path("./reports/query_engine_streamlit_reports").resolve())
        Path(self.config.output_dir).mkdir(parents=True, exist_ok=True)
        print(f"[QueryEngine] output_dir = {self.config.output_dir}")

        # —— 快模式开关 ——
        self.quick_mode = (os.getenv("QE_QUICK", "0").lower() in {"1", "true", "yes"})
        self.quick_tool = os.getenv("QE_QUICK_TOOL", "search_news_last_24_hours")
        self.quick_max_paras = int(os.getenv("QE_QUICK_MAX_PARAS", "2"))

        # 主模型 + 回退
        primary_llm, primary_name = self._initialize_llm_with_name()
        fallback_general = self._build_general_fallback(exclude=primary_name)
        fallback_sensitive = self._build_sensitive_fallback()
        self.llm_client = SafeRouterLLM(primary_llm,
                                        fallback_general=fallback_general,
                                        fallback_sensitive=fallback_sensitive)

        self.search_agency = TavilyNewsAgency(api_key=self.config.tavily_api_key)
        self._initialize_nodes()
        self.state = State()

        print("Query Agent已初始化")
        try:
            print(f"使用LLM: {self.llm_client.get_model_info()}")
        except Exception:
            pass
        print("搜索工具集: TavilyNewsAgency (支持6种搜索工具)")

    # ---------------- LLM 初始化 & 回退构造 ----------------
    def _initialize_llm_with_name(self) -> Tuple[BaseLLM, str]:
        prefer = (
            getattr(self.config, "default_llm_provider", None)
            or os.getenv("QUERY_PROVIDER")
            or os.getenv("NAGA_PROVIDER")
            or "zhipu"
        ).lower()

        order_map = {
            "zhipu": ["zhipu", "openai", "siliconflow"],
            "openai": ["openai", "zhipu", "siliconflow"],
            "siliconflow": ["siliconflow", "openai", "zhipu"],
        }
        order = order_map.get(prefer, ["zhipu", "openai", "siliconflow"])

        zhipu_key = getattr(self.config, "zhipu_api_key", None) or os.getenv("NAGA_API_KEY") or os.getenv("ZHIPU_API_KEY")
        openai_key = getattr(self.config, "openai_api_key", None) or os.getenv("OPENAI_API_KEY")
        silicon_key = getattr(self.config, "siliconflow_api_key", None) or os.getenv("SILICONFLOW_API_KEY")

        global_model_override = os.getenv("QUERY_MODEL_NAME") or os.getenv("NAGA_MODEL_NAME")

        def _model_for(provider: str) -> str:
            if global_model_override:
                return global_model_override
            if provider == "zhipu":
                return getattr(self.config, "zhipu_model", None) or "glm-4.5-flash"
            if provider == "openai":
                return getattr(self.config, "openai_model", None) or "gpt-4o-mini"
            if provider == "siliconflow":
                return getattr(self.config, "siliconflow_model", None) or "Qwen/Qwen3-8B"
            return "gpt-4o-mini"

        last_err: Optional[Exception] = None
        for prov in order:
            try:
                if prov == "zhipu" and ZhipuLLM and zhipu_key:
                    m = _model_for("zhipu")
                    try:
                        return ZhipuLLM(api_key=zhipu_key, model_name=m), "zhipu"  # type: ignore
                    except TypeError:
                        return ZhipuLLM(api_key=zhipu_key, model=m), "zhipu"  # type: ignore
                if prov == "openai" and openai_key:
                    return OpenAILLM(api_key=openai_key, model_name=_model_for("openai")), "openai"
                if prov == "siliconflow" and silicon_key:
                    return SiliconFlowLLM(
                        api_key=silicon_key,
                        model_name=_model_for("siliconflow"),
                        base_url=getattr(self.config, "siliconflow_base_url", "https://api.siliconflow.cn/v1")
                    ), "siliconflow"
            except Exception as e:
                last_err = e
                continue
        detail = f"（最后错误：{last_err}）" if last_err else ""
        raise ValueError(f"无法初始化任何可用的 LLM 提供商（尝试顺序：{order}）{detail}")

    def _build_general_fallback(self, exclude: str) -> List[BaseLLM]:
        fb: List[BaseLLM] = []
        if exclude != "openai":
            openai_key = getattr(self.config, "openai_api_key", None) or os.getenv("OPENAI_API_KEY")
            if openai_key:
                fb.append(OpenAILLM(api_key=openai_key, model_name=getattr(self.config, "openai_model", "gpt-4o-mini")))
        return fb

    def _build_sensitive_fallback(self) -> List[BaseLLM]:
        silicon_key = getattr(self.config, "siliconflow_api_key", None) or os.getenv("SILICONFLOW_API_KEY")
        if not silicon_key:
            return []
        return [SiliconFlowLLM(
            api_key=silicon_key,
            model_name=getattr(self.config, "siliconflow_model", "Qwen/Qwen3-8B"),
            base_url=getattr(self.config, "siliconflow_base_url", "https://api.siliconflow.cn/v1")
        )]

    # ---------------- 节点/工具 ----------------
    def _initialize_nodes(self):
        self.first_search_node = FirstSearchNode(self.llm_client)
        self.reflection_node = ReflectionNode(self.llm_client)
        self.first_summary_node = FirstSummaryNode(self.llm_client)
        self.reflection_summary_node = ReflectionSummaryNode(self.llm_client)
        self.report_formatting_node = ReportFormattingNode(self.llm_client)

    def _validate_date_format(self, date_str: str) -> bool:
        if not date_str:
            return False
        pattern = r'^\d{4}-\d{2}-\d{2}$'
        if not re.match(pattern, date_str):
            return False
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
            return True
        except ValueError:
            return False

    def execute_search_tool(self, tool_name: str, query: str, **kwargs) -> TavilyResponse:
        print(f"  → 执行搜索工具: {tool_name}")
        if tool_name == "basic_search_news":
            max_results = kwargs.get("max_results", 7)
            return self.search_agency.basic_search_news(query, max_results)
        elif tool_name == "deep_search_news":
            return self.search_agency.deep_search_news(query)
        elif tool_name == "search_news_last_24_hours":
            return self.search_agency.search_news_last_24_hours(query)
        elif tool_name == "search_news_last_week":
            return self.search_agency.search_news_last_week(query)
        elif tool_name == "search_images_for_news":
            return self.search_agency.search_images_for_news(query)
        elif tool_name == "search_news_by_date":
            start_date = kwargs.get("start_date")
            end_date = kwargs.get("end_date")
            if not start_date or not end_date:
                print("  ⚠️  search_news_by_date工具缺少时间参数，改用基础搜索")
                return self.search_agency.basic_search_news(query)
            return self.search_agency.search_news_by_date(query, start_date, end_date)
        else:
            print(f"  ⚠️  未知的搜索工具: {tool_name}，使用默认基础搜索")
            return self.search_agency.basic_search_news(query)

    # ---------------- 顶层流程 ----------------
    def research(self, query: str, save_report: bool = True) -> str:
        print(f"\n{'='*60}")
        print(f"开始深度研究: {query}")
        print(f"{'='*60}")
        try:
            self._generate_report_structure(query)
            self._process_paragraphs()
            final_report = self._generate_final_report()
            if save_report:
                try:
                    self._save_report(final_report)
                except Exception as se:
                    print(f"⚠️ 保存阶段发生非致命错误（已忽略以继续闭环）：{se}")
            print(f"\n{'='*60}")
            print("深度研究完成！")
            print(f"{'='*60}")
            return final_report
        except Exception as e:
            print(f"研究过程中发生错误: {str(e)}")
            raise e

    def _generate_report_structure(self, query: str):
        print(f"\n[步骤 1] 生成报告结构...")
        report_structure_node = ReportStructureNode(self.llm_client, query)
        self.state = report_structure_node.mutate_state(state=self.state)
        print(f"报告结构已生成，共 {len(self.state.paragraphs)} 个段落:")
        for i, paragraph in enumerate(self.state.paragraphs, 1):
            print(f"  {i}. {paragraph.title}")

    # === 快模式：只跑前 N 段 + 不反思 ===
    def _process_paragraphs(self):
        total_paragraphs = len(self.state.paragraphs)
        if self.quick_mode:
            total_paragraphs = min(total_paragraphs, self.quick_max_paras)

        for i in range(total_paragraphs):
            print(f"\n[步骤 2.{i+1}] 处理段落: {self.state.paragraphs[i].title}")
            print("-" * 50)
            self._initial_search_and_summary(i)

            if not self.quick_mode:
                self._reflection_loop(i)

            self.state.paragraphs[i].research.mark_completed()
            progress = (i + 1) / total_paragraphs * 100
            print(f"段落处理完成 ({progress:.1f}%)")

    # === 初搜 & 首次总结 ===
    def _initial_search_and_summary(self, paragraph_index: int):
        paragraph = self.state.paragraphs[paragraph_index]
        print("  - 生成搜索查询...")

        if self.quick_mode:
            search_query = paragraph.title.strip() or "金融科技 最新 进展"
            search_tool = self.quick_tool
            reasoning = "快模式：使用段落标题做精简查询，固定近时段搜索以快速返回少量结果。"
            search_output = {"search_query": search_query, "search_tool": search_tool, "reasoning": reasoning}
        else:
            search_input = {"title": paragraph.title, "content": paragraph.content}
            search_output = self.first_search_node.run(search_input)
            search_query = search_output["search_query"]
            search_tool = search_output.get("search_tool", "basic_search_news")
            reasoning = search_output["reasoning"]

        print(f"  - 搜索查询: {search_query}")
        print(f"  - 选择的工具: {search_tool}")
        print(f"  - 推理: {reasoning}")

        print("  - 执行网络搜索...")
        search_kwargs = {}
        if (not self.quick_mode) and search_tool == "search_news_by_date":
            start_date = search_output.get("start_date")
            end_date = search_output.get("end_date")
            if start_date and end_date and self._validate_date_format(start_date) and self._validate_date_format(end_date):
                search_kwargs["start_date"] = start_date
                search_kwargs["end_date"] = end_date
                print(f"  - 时间范围: {start_date} 到 {end_date}")
            else:
                print("  ⚠️  日期参数缺失或格式错误，改用基础搜索")
                search_tool = "basic_search_news"

        search_response = self.execute_search_tool(search_tool, search_query, **search_kwargs)

        search_results = []
        if search_response and search_response.results:
            max_results = 5 if self.quick_mode else min(len(search_response.results), 10)
            for result in search_response.results[:max_results]:
                search_results.append({
                    'title': result.title,
                    'url': result.url,
                    'content': result.content,
                    'score': result.score,
                    'raw_content': result.raw_content,
                    'published_date': result.published_date
                })

        if search_results:
            print(f"  - 找到 {len(search_results)} 个搜索结果")
            for j, result in enumerate(search_results, 1):
                date_info = f" (发布于: {result.get('published_date', 'N/A')})" if result.get('published_date') else ""
                print(f"    {j}. {result['title'][:50]}...{date_info}")
        else:
            print("  - 未找到搜索结果")

        paragraph.research.add_search_results(search_query, search_results)

        print("  - 生成初始总结...")
        summary_input = {
            "title": paragraph.title,
            "content": paragraph.content,
            "search_query": search_query,
            "search_results": format_search_results_for_prompt(
                search_results, self.config.max_content_length
            )
        }
        self.state = self.first_summary_node.mutate_state(
            summary_input, self.state, paragraph_index
        )
        print("  - 初始总结完成")

    def _reflection_loop(self, paragraph_index: int):
        paragraph = self.state.paragraphs[paragraph_index]
        for reflection_i in range(self.config.max_reflections):
            print(f"  - 反思 {reflection_i + 1}/{self.config.max_reflections}...")
            reflection_input = {
                "title": paragraph.title,
                "content": paragraph.content,
                "paragraph_latest_state": paragraph.research.latest_summary
            }
            reflection_output = self.reflection_node.run(reflection_input)
            search_query = reflection_output["search_query"]
            search_tool = reflection_output.get("search_tool", "basic_search_news")
            reasoning = reflection_output["reasoning"]
            print(f"    反思查询: {search_query}")
            print(f"    选择的工具: {search_tool}")
            print(f"    反思推理: {reasoning}")

            search_kwargs = {}
            if search_tool == "search_news_by_date":
                start_date = reflection_output.get("start_date")
                end_date = reflection_output.get("end_date")
                if start_date and end_date and self._validate_date_format(start_date) and self._validate_date_format(end_date):
                    search_kwargs["start_date"] = start_date
                    search_kwargs["end_date"] = end_date
                    print(f"    时间范围: {start_date} 到 {end_date}")
                else:
                    print(f"    ⚠️  日期参数缺失或格式错误，改用基础搜索")
                    search_tool = "basic_search_news"

            search_response = self.execute_search_tool(search_tool, search_query, **search_kwargs)

            search_results = []
            if search_response and search_response.results:
                max_results = min(len(search_response.results), 10)
                for result in search_response.results[:max_results]:
                    search_results.append({
                        'title': result.title,
                        'url': result.url,
                        'content': result.content,
                        'score': result.score,
                        'raw_content': result.raw_content,
                        'published_date': result.published_date
                    })

            if search_results:
                print(f"    找到 {len(search_results)} 个反思搜索结果")
                for j, result in enumerate(search_results, 1):
                    date_info = f" (发布于: {result.get('published_date', 'N/A')})" if result.get('published_date') else ""
                    print(f"      {j}. {result['title'][:50]}...{date_info}")
            else:
                print("    未找到反思搜索结果")

            paragraph.research.add_search_results(search_query, search_results)

            reflection_summary_input = {
                "title": paragraph.title,
                "content": paragraph.content,
                "search_query": search_query,
                "search_results": format_search_results_for_prompt(
                    search_results, self.config.max_content_length
                ),
                "paragraph_latest_state": paragraph.research.latest_summary
            }
            self.state = self.reflection_summary_node.mutate_state(
                reflection_summary_input, self.state, paragraph_index
            )
            print(f"    反思 {reflection_i + 1} 完成")

    def _generate_final_report(self) -> str:
        print(f"\n[步骤 3] 生成最终报告...")
        report_data = []
        for paragraph in self.state.paragraphs:
            report_data.append({
                "title": paragraph.title,
                "paragraph_latest_state": paragraph.research.latest_summary
            })
        try:
            final_report = self.report_formatting_node.run(report_data)
        except Exception as e:
            print(f"LLM格式化失败，使用备用方法: {str(e)}")
            final_report = self.report_formatting_node.format_report_manually(
                report_data, self.state.report_title
            )
        self.state.final_report = final_report
        self.state.mark_completed()
        print("最终报告生成完成")
        return final_report

    # ---------------- 保存 & 交接 ----------------
    def _build_draft_md(self) -> str:
        lines = [f"# {getattr(self.state, 'report_title', self.state.query) or '研究初稿'}", ""]
        for idx, para in enumerate(getattr(self.state, 'paragraphs', []) or [], start=1):
            title = getattr(para, "title", f"段落 {idx}") or f"段落 {idx}"
            try:
                latest = getattr(getattr(para, "research", None), "latest_summary", "") or ""
            except Exception:
                latest = ""

            lines.append(f"## {title}")
            lines.append("")
            if latest:
                lines.append(latest.strip())
                lines.append("")
            # 来源链接（最多 10 条）
            links_added = set()
            try:
                hist = getattr(getattr(para, "research", None), "search_history", []) or []
                for h in hist:
                    results = None
                    if isinstance(h, dict):
                        results = h.get("results")
                    else:
                        results = getattr(h, "results", None)
                    if not results:
                        continue
                    for r in results:
                        url = ""
                        if isinstance(r, dict):
                            url = (r.get("url") or "").strip()
                        else:
                            url = (getattr(r, "url", "") or "").strip()
                        if url and url not in links_added:
                            if "参考来源：" not in lines[-1]:
                                lines.append("参考来源：")
                            lines.append(f"- {url}")
                            links_added.add(url)
                            if len(links_added) >= 10:
                                break
                    if len(links_added) >= 10:
                        break
            except Exception:
                pass
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _save_report(self, report_content: str):
        """保存：默认只保存 draft 与 state；deep_search_report 可通过环境变量开启"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        query_raw = getattr(self.state, "query", "") or ""
        query_safe = "".join(c for c in query_raw if c.isalnum() or c in (" ", "-", "_")).rstrip().replace(" ", "_")[:60]

        out_dir = Path(self.config.output_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1) 保存 state（ReportEngine 必读）
        state_path = out_dir / f"state_{query_safe}_{timestamp}.json"
        try:
            self.state.save_to_file(str(state_path))
            print(f"状态已保存到: {state_path}")
        except Exception as e:
            print(f"⚠️ 保存状态失败: {e}")

        # 2) 保存 draft（ReportEngine 作为初稿输入）
        draft_md = self._build_draft_md()
        draft_path = out_dir / f"draft_{query_safe}_{timestamp}.md"
        try:
            draft_path.write_text(draft_md, encoding="utf-8")
            print(f"初稿已保存到: {draft_path}")
        except Exception as e:
            print(f"⚠️ 保存初稿失败: {e}")

        # 3) deep_search_report（**默认跳过**；如需保留可设 QE_SAVE_FINAL_MD=true）
        save_final = (os.getenv("QE_SAVE_FINAL_MD", "false").lower() in {"1", "true", "yes"})
        if save_final and report_content:
            md_path = out_dir / f"deep_search_report_{query_safe}_{timestamp}.md"
            try:
                md_path.write_text(report_content, encoding="utf-8")
                print(f"报告已保存到: {md_path}")
            except Exception as e:
                print(f"⚠️ 保存最终报告失败: {e}")

    # ---------------- 进度/状态 ----------------
    def get_progress_summary(self) -> Dict[str, Any]:
        return self.state.get_progress_summary()

    def load_state(self, filepath: str):
        self.state = State.load_from_file(filepath)
        print(f"状态已从 {filepath} 加载")

    def save_state(self, filepath: str):
        self.state.save_to_file(filepath)
        print(f"状态已保存到 {filepath}")


def create_agent(config_file: Optional[str] = None) -> "DeepSearchAgent":
    config = load_config(config_file)
    return DeepSearchAgent(config)
