# -*- coding: utf-8 -*-
"""
Report Agent 主类（使用 zhipu 或 gemini）
- 读取上游 QueryEngine 的 draft/state，忽略 deep_search_report
- 最小输入可运行：只要有 draft_*.md 或 state_*.json 即可生成 HTML
- HTML 生成增加硬超时与兜底，避免“卡死”
- 新增 generate_report_from_files(...)：一键从文件生成报告，并返回保存的 HTML 路径
"""

from __future__ import annotations
import os
import re  # 用于清洗标题/重写抬头
import json
import logging
import html
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from .llms import BaseLLM, GeminiLLM
try:
    from .llms import ZhipuLLM  # type: ignore
except Exception:
    ZhipuLLM = None  # type: ignore

from .nodes import TemplateSelectionNode, HTMLGenerationNode
from .state import ReportState
from .utils.config import load_config, Config


class FileCountBaseline:
    """文件数量基准管理器（保留，但不再作为生成条件的唯一依据）"""

    def __init__(self):
        self.baseline_file = 'logs/report_baseline.json'
        self.baseline_data = self._load_baseline()

    def _load_baseline(self) -> Dict[str, int]:
        try:
            if os.path.exists(self.baseline_file):
                with open(self.baseline_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"加载基准数据失败: {e}")
        return {}

    def _save_baseline(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.baseline_file), exist_ok=True)
            with open(self.baseline_file, 'w', encoding='utf-8') as f:
                json.dump(self.baseline_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存基准数据失败: {e}")

    def initialize_baseline(self, directories: Dict[str, str]) -> Dict[str, int]:
        current_counts = {}
        for engine, directory in directories.items():
            if os.path.exists(directory):
                # query 目录：既统计 md 也统计 json（draft/state）
                if engine == "query":
                    files = [f for f in os.listdir(directory) if f.endswith('.md') or f.endswith('.json')]
                else:
                    files = [f for f in os.listdir(directory) if f.endswith('.md')]
                current_counts[engine] = len(files)
            else:
                current_counts[engine] = 0
        self.baseline_data = current_counts.copy()
        self._save_baseline()
        print(f"文件数量基准已初始化: {current_counts}")
        return current_counts

    def check_new_files(self, directories: Dict[str, str]) -> Dict[str, Any]:
        current_counts: Dict[str, int] = {}
        new_files_found: Dict[str, int] = {}
        all_have_new = True

        for engine, directory in directories.items():
            if os.path.exists(directory):
                if engine == "query":
                    files = [f for f in os.listdir(directory) if f.endswith('.md') or f.endswith('.json')]
                else:
                    files = [f for f in os.listdir(directory) if f.endswith('.md')]
                current_counts[engine] = len(files)
                baseline_count = self.baseline_data.get(engine, 0)
                if current_counts[engine] > baseline_count:
                    new_files_found[engine] = current_counts[engine] - baseline_count
                else:
                    new_files_found[engine] = 0
            else:
                current_counts[engine] = 0
                new_files_found[engine] = 0
                all_have_new = False  # 目录都没就绪

        return {
            'ready': all_have_new,
            'baseline_counts': self.baseline_data,
            'current_counts': current_counts,
            'new_files_found': new_files_found,
            'missing_engines': [e for e, cnt in new_files_found.items() if cnt == 0],
        }

    def _latest_by_pattern(self, directory: str, prefix: str, suffix: str) -> Optional[str]:
        if not os.path.exists(directory):
            return None
        candidates = [
            os.path.join(directory, f) for f in os.listdir(directory)
            if f.startswith(prefix) and f.endswith(suffix)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda p: os.path.getmtime(p))

    def get_latest_query_files(self, query_dir: str) -> Dict[str, Optional[str]]:
        """返回 query_dir 下最新的 draft/state 文件路径"""
        return {
            "draft": self._latest_by_pattern(query_dir, "draft_", ".md"),
            "state": self._latest_by_pattern(query_dir, "state_", ".json"),
        }


class ReportAgent:
    """Report Engine 的核心 Agent"""

    def __init__(self, config: Optional[Config] = None):
        self.config: Config = config or load_config()
        self._setup_logging()

        # 文件基线（仅用于提示，不再卡流程）
        self.file_baseline = FileCountBaseline()
        self._initialize_file_baseline()

        # LLM
        self.llm_client: BaseLLM = self._initialize_llm()

        # 节点
        self._initialize_nodes()

        # 状态 & 目录
        self.state = ReportState()
        os.makedirs(self.config.output_dir, exist_ok=True)

        # 记录最近一次保存的 HTML 路径（供 smoke test 直接返回）
        self._last_saved_html_path: Optional[str] = None

        self.logger.info("Report Agent已初始化")
        try:
            self.logger.info(f"使用LLM: {self.llm_client.get_model_info()}")
        except Exception:
            pass

    # ---------------- 日志 ----------------
    def _setup_logging(self) -> None:
        log_dir = os.path.dirname(self.config.log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        self.logger = logging.getLogger('ReportEngine')
        self.logger.setLevel(logging.INFO)

        if self.logger.handlers:
            self.logger.handlers.clear()

        fh = logging.FileHandler(self.config.log_file, encoding='utf-8')
        fh.setLevel(logging.INFO)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)

        fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        fh.setFormatter(fmt)
        ch.setFormatter(fmt)

        self.logger.addHandler(fh)
        self.logger.addHandler(ch)
        self.logger.propagate = False

    # ---------------- 基线 & LLM & 节点 ----------------
    def _initialize_file_baseline(self) -> None:
        directories = {
            'insight': self.config.insight_dir,
            'media': self.config.media_dir,
            'query': self.config.query_dir,
        }
        self.file_baseline.initialize_baseline(directories)

    def _initialize_llm(self) -> BaseLLM:
        provider = (os.getenv("REPORT_LLM_PROVIDER")
                    or getattr(self.config, "default_llm_provider", "")
                    or "zhipu").lower().strip()

        if provider == "zhipu":
            if ZhipuLLM is None:
                raise ImportError("ZhipuLLM 不可用：请提供 llms/zhipu_llm.py 并安装 openai>=1.x")
            api_key = os.getenv("ZHIPU_API_KEY") or os.getenv("NAGA_API_KEY") or ""
            model = (os.getenv("REPORT_ZHIPU_MODEL")
                     or os.getenv("NAGA_MODEL_NAME")
                     or "glm-4.5-flash")
            if not api_key:
                raise ValueError("未检测到智谱 API Key，请设置 ZHIPU_API_KEY（或 NAGA_API_KEY）")
            self.logger.info(f"LLM Provider=zhipu  Model={model}")
            return ZhipuLLM(api_key=api_key, model_name=model)  # type: ignore

        if provider == "gemini":
            api_key = (getattr(self.config, "gemini_api_key", None)
                       or os.getenv("GEMINI_API_KEY")
                       or "")
            model = (getattr(self.config, "gemini_model", None)
                     or os.getenv("GEMINI_MODEL")
                     or "gemini-1.5-pro-002")
            if not api_key:
                raise ValueError("未检测到 Gemini API Key，请设置 GEMINI_API_KEY")
            self.logger.info(f"LLM Provider=gemini  Model={model}")
            return GeminiLLM(api_key=api_key, model_name=model, config=self.config)

        raise ValueError(f"不支持的LLM提供商: {provider}")

    def _initialize_nodes(self) -> None:
        self.template_selection_node = TemplateSelectionNode(self.llm_client, self.config.template_dir)
        self.html_generation_node = HTMLGenerationNode(self.llm_client)

    # ---------------- 辅助：清洗标题 ----------------
    def _clean_title(self, s: str) -> str:
        """压平/去引号/去多空格，保证标题可做 <title> 与 H1 使用"""
        s = (s or "")
        s = re.sub(r"[\r\n]+", " ", s)     # 换行变空格
        s = re.sub(r"\s{2,}", " ", s)      # 多空格压一格
        s = s.strip(" '\"\t")              # 去首尾引号/空白
        return s

    def _normalize_title_and_draft(self, query: str, draft_text: str) -> Tuple[str, str]:
        """
        1) 尽量从 query 文本中提取 primary_query 作为干净标题
        2) 将 draft 顶部“关于... + 列表行”替换为标准 H1：# {clean_title}：深度研究报告
        """
        # 1) 从 query 里抽取 primary_query
        m = re.search(r"primary_query\s*:\s*(.+)", query, flags=re.IGNORECASE)
        if m:
            clean_title = m.group(1).strip().strip("'\"")
        else:
            clean_title = re.sub(r"[\r\n]+", " ", query).strip(" '\"")
            m2 = re.search(r"primary_query[^:]*:\s*(.+)", query, flags=re.IGNORECASE)
            if m2:
                clean_title = m2.group(1).strip().strip("'\"")
        if not clean_title:
            clean_title = "自动生成研究报告"

        # 2) 重写 draft 抬头（把“关于… + 列表”调试信息去掉）
        if draft_text:
            lines = draft_text.splitlines()
            if lines and lines[0].lstrip().startswith("# 关于"):
                i = 1
                while i < len(lines) and lines[i].lstrip().startswith("- "):
                    i += 1
                if i < len(lines) and "的深度研究报告" in lines[i]:
                    i += 1
                new_head = [f"# {clean_title}：深度研究报告", ""]
                lines = new_head + lines[i:]
                draft_text = "\n".join(lines)

        return clean_title, draft_text

    # ---------------- 对外功能 ----------------
    def generate_report(
        self,
        query: str,
        reports: List[Any],
        forum_logs: str = "",
        custom_template: str = "",
        save_report: bool = True
    ) -> str:
        start_time = datetime.now()

        # 先清洗标题，再入状态
        query = self._clean_title(query)
        self.state.metadata.query = query

        self.logger.info(f"开始生成报告: {query}")
        self.logger.info(f"输入数据 - 报告数量: {len(reports)}, 论坛日志长度: {len(forum_logs)}")

        try:
            # 1) 模板选择
            template_result = self._select_template(query, reports, forum_logs, custom_template)

            # 2) 生成 HTML（带硬超时和兜底）
            html_report = self._generate_html_report(query, reports, forum_logs, template_result)

            # 3) 保存
            if save_report:
                self._save_report(html_report)

            duration = (datetime.now() - start_time).total_seconds()
            self.state.metadata.generation_time = duration
            self.logger.info(f"报告生成完成，耗时: {duration:.2f} 秒")

            return html_report

        except Exception as e:
            self.logger.error(f"报告生成过程中发生错误: {e}")
            raise

    # === 一步到位入口（向下兼容 smoke 的参数名） ===
    def generate_report_from_files(
        self,
        query: Optional[str] = None,
        query_dir: Optional[str] = None,
        draft_path: Optional[str] = None,
        state_path: Optional[str] = None,
        forum_log_path: Optional[str] = None,
        custom_template: str = "",
        save_report: bool = True,
        # 兼容你当前 smoke 的命名：
        query_engine_draft: Optional[str] = None,
        query_engine_state: Optional[str] = None,
        forum_path: Optional[str] = None,
        save_html: Optional[bool] = None,
    ) -> str:
        """
        一键从文件生成报告（供 smoke_test_quick.py 与主控回调）
        返回：保存后的 HTML 文件路径（save_report=True 时）
        """
        # 兼容别名
        draft_path = draft_path or query_engine_draft
        state_path = state_path or query_engine_state
        forum_log_path = forum_log_path or forum_path
        if save_html is not None:
            save_report = bool(save_html)

        qdir = query_dir or self.config.query_dir

        # 组织文件路径
        file_paths: Dict[str, str] = {}
        if draft_path and os.path.exists(draft_path):
            file_paths["query_draft"] = draft_path
        if state_path and os.path.exists(state_path):
            file_paths["query_state"] = state_path
        if not file_paths:
            latest = self.file_baseline.get_latest_query_files(qdir)
            if latest.get("draft"):
                file_paths["query_draft"] = latest["draft"]  # type: ignore
            if latest.get("state"):
                file_paths["query_state"] = latest["state"]  # type: ignore
        if forum_log_path and os.path.exists(forum_log_path):
            file_paths["forum"] = forum_log_path

        # 读取文件内容
        loaded = self.load_input_files(file_paths)
        reports = loaded.get("reports", ["", "", ""])
        forum_logs = loaded.get("forum_logs", "")

        # 推断 query（若调用方未显式给）
        if not query:
            inferred = None
            sp = file_paths.get("query_state")
            if sp and os.path.exists(sp):
                try:
                    with open(sp, "r", encoding="utf-8") as f:
                        st = json.load(f)
                    inferred = st.get("report_title") or st.get("query")
                except Exception:
                    inferred = None
            query = inferred or "自动生成研究报告"

        # 清洗标题
        query = self._clean_title(query)

        # 生成并保存
        self._last_saved_html_path = None
        _ = self.generate_report(
            query=query,
            reports=reports,
            forum_logs=forum_logs,
            custom_template=custom_template,
            save_report=save_report
        )
        return self._last_saved_html_path or ""

    def _select_template(
        self,
        query: str,
        reports: List[Any],
        forum_logs: str,
        custom_template: str
    ) -> Dict[str, Any]:
        self.logger.info("选择报告模板...")
        if custom_template:
            self.logger.info("使用用户自定义模板")
            self.state.metadata.template_used = "custom"
            return {
                'template_name': 'custom',
                'template_content': custom_template,
                'selection_reason': '用户指定的自定义模板'
            }

        template_input = {'query': query, 'reports': reports, 'forum_logs': forum_logs}
        try:
            result = self.template_selection_node.run(template_input)
            self.state.metadata.template_used = result.get('template_name', 'unknown')
            self.logger.info(f"选择模板: {self.state.metadata.template_used}")

            # 若模板内容为空，自动回退到通用模板，保证章节骨架稳定
            tmpl = (result or {}).get('template_content', '') or ''
            if not tmpl.strip():
                self.logger.info("模板内容为空，自动回退到通用研究报告模板")
                result = {
                    'template_name': (self.state.metadata.template_used or 'unknown') + '（fallback）',
                    'template_content': self._get_fallback_template_content(),
                    'selection_reason': '模板内容为空，使用默认通用模板'
                }
                self.state.metadata.template_used = result['template_name']

            return result
        except Exception as e:
            self.logger.error(f"模板选择失败，使用默认模板: {e}")
            fallback = {
                'template_name': '通用研究报告模板',
                'template_content': self._get_fallback_template_content(),
                'selection_reason': '模板选择失败，使用默认通用模板'
            }
            self.state.metadata.template_used = fallback['template_name']
            return fallback

    def _mk_report_from_state(self, state_json_text: str) -> str:
        """把 state.json 里的段落 latest_summary 拼接成 Markdown 初稿"""
        try:
            st = json.loads(state_json_text)
            paras = st.get("paragraphs") or []
            lines = [f"# {st.get('report_title') or st.get('query') or '研究初稿'}", ""]
            for i, p in enumerate(paras, 1):
                title = p.get("title") or f"段落 {i}"
                latest = ((p.get("research") or {}).get("latest_summary")) or ""
                lines.append(f"## {title}\n")
                if latest:
                    lines.append(latest.strip())
                    lines.append("")
            return "\n".join(lines).strip() + "\n"
        except Exception:
            return ""

    def _simple_html_from_text(self, text: str) -> str:
        """最小兜底：把文本安全包成 <pre>"""
        esc = html.escape(text or "")
        return f"<!doctype html><html><head><meta charset='utf-8'><title>Auto Report</title></head><body><pre style='white-space:pre-wrap'>{esc}</pre></body></html>"

    def _run_with_timeout(self, fn, kwargs: Dict[str, Any], timeout_s: float):
        with ThreadPoolExecutor(max_workers=1) as exe:
            fut = exe.submit(lambda: fn(**kwargs))
            return fut.result(timeout=timeout_s)

    def _generate_html_report(
        self,
        query: str,
        reports: List[Any],
        forum_logs: str,
        template_result: Dict[str, Any]
    ) -> str:
        self.logger.info("生成 HTML 报告中...")

        # 将上游 QueryEngine 的 draft/state 优先纳入
        draft_text = ""

        # 外部传进来的三段（兼容老口径）
        query_report = str(reports[0]) if len(reports) > 0 and reports[0] else ""
        media_report = str(reports[1]) if len(reports) > 1 and reports[1] else ""
        insight_report = str(reports[2]) if len(reports) > 2 and reports[2] else ""

        # 若 query_report 为空，尝试在 query_dir 直接找最新的 draft/state
        if not query_report:
            latest = self.file_baseline.get_latest_query_files(self.config.query_dir)
            if latest.get("draft") and os.path.exists(latest["draft"]):  # type: ignore
                try:
                    draft_text = open(latest["draft"], "r", encoding="utf-8").read()  # type: ignore
                    self.logger.info(f"已加载草稿: {os.path.basename(latest['draft'])}")  # type: ignore
                except Exception as e:
                    self.logger.warning(f"加载草稿失败: {e}")
            if not draft_text and latest.get("state") and os.path.exists(latest["state"]):  # type: ignore
                try:
                    state_text = open(latest["state"], "r", encoding="utf-8").read()  # type: ignore
                    draft_from_state = self._mk_report_from_state(state_text)
                    if draft_from_state:
                        draft_text = draft_from_state
                        self.logger.info(f"已用 state.json 拼装初稿: {os.path.basename(latest['state'])}")  # type: ignore
                except Exception as e:
                    self.logger.warning(f"加载 state.json 失败: {e}")

        # 如果 query_report 还空，用 draft_text 兜底
        if not query_report:
            query_report = draft_text

        # ★ 在进入 HTML 生成前，重写标题与 draft 抬头
        query, query_report = self._normalize_title_and_draft(query, query_report)

        # 如果仍然为空，再做终极兜底（让 LLM 照主题最小成文）
        empty_input = (not query_report and not media_report and not insight_report and not forum_logs)

        html_input = {
            'query': query,
            'query_engine_report': query_report,
            'media_engine_report': media_report,
            'insight_engine_report': insight_report,
            'forum_logs': forum_logs,
            'selected_template': template_result.get('template_content', ''),
            'empty_input': empty_input,
        }

        # 硬超时 + 兜底
        timeout_s = float(getattr(self.config, "api_timeout", 900.0))
        try:
            html_content = self._run_with_timeout(
                self.html_generation_node.run,
                {"input_data": html_input},  # 标准入参名
                timeout_s=timeout_s
            )
            assert isinstance(html_content, str) and html_content.strip()
            self.state.html_content = html_content
            self.state.mark_completed()
            self.logger.info("HTML 报告生成完成")
            return html_content
        except TimeoutError:
            self.logger.error(f"HTML 生成超时（>{timeout_s}s），输出最小 HTML 兜底")
            fallback_src = query_report or draft_text or ("主题：" + (query or "综合报告"))
            html_content = self._simple_html_from_text(fallback_src)
            self.state.html_content = html_content
            self.state.mark_completed()
            return html_content
        except Exception as e:
            self.logger.error(f"HTML 生成失败，输出最小 HTML 兜底: {e}")
            fallback_src = query_report or draft_text or ("主题：" + (query or "综合报告"))
            html_content = self._simple_html_from_text(fallback_src)
            self.state.html_content = html_content
            self.state.mark_completed()
            return html_content

    def _get_fallback_template_content(self) -> str:
        return """# 通用研究报告

## 摘要
{summary}

## 背景
{background}

## 现状与证据
{status_and_evidence}

## 风险与不确定性
{risks}

## 结论与可执行建议
{recommendations}

## 参考文献
{references}
"""

    def _save_report(self, html_content: str) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        q = (self.state.metadata.query or "").strip()
        safe = "".join(c for c in q if c.isalnum() or c in (" ", "-", "_")).rstrip().replace(" ", "_")[:30]
        filename = f"final_report_{safe}_{ts}.html"
        path = os.path.join(self.config.output_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_content)
        self._last_saved_html_path = path
        self.logger.info(f"报告已保存到: {path}")

        # 保存状态
        state_filename = f"report_state_{safe}_{ts}.json"
        state_path = os.path.join(self.config.output_dir, state_filename)
        self.state.save_to_file(state_path)
        self.logger.info(f"状态已保存到: {state_path}")

    # ---------------- 进度/IO ----------------
    def get_progress_summary(self) -> Dict[str, Any]:
        return self.state.to_dict()

    def get_last_saved_html_path(self) -> str:
        """返回最近一次保存的 HTML 路径（若存在）"""
        return self._last_saved_html_path or ""

    def load_state(self, filepath: str) -> None:
        self.state = ReportState.load_from_file(filepath)
        self.logger.info(f"状态已从 {filepath} 加载")

    def save_state(self, filepath: str) -> None:
        self.state.save_to_file(filepath)
        self.logger.info(f"状态已保存到 {filepath}")

    def check_input_files(self, insight_dir: str, media_dir: str, query_dir: str, forum_log_path: str) -> Dict[str, Any]:
        # 仍做一次基于基线的检查（用于日志提示）
        directories = {'insight': insight_dir, 'media': media_dir, 'query': query_dir}
        check_result = self.file_baseline.check_new_files(directories)

        # 关键：只要 query_dir 有 draft 或 state，就认为“可生成”
        latest_query = self.file_baseline.get_latest_query_files(query_dir)
        has_draft = bool(latest_query.get("draft"))
        has_state = bool(latest_query.get("state"))
        forum_ready = os.path.exists(forum_log_path)

        result = {
            'ready': (has_draft or has_state),  # <- 只要 draft/state 任一存在就 ready
            'baseline_counts': check_result['baseline_counts'],
            'current_counts': check_result['current_counts'],
            'new_files_found': check_result['new_files_found'],
            'missing_files': [],
            'files_found': [],
            'latest_files': {},
        }

        if has_draft:
            result['files_found'].append(f"query draft: {os.path.basename(latest_query['draft'])}")  # type: ignore
        if has_state:
            result['files_found'].append(f"query state: {os.path.basename(latest_query['state'])}")  # type: ignore
        if not (has_draft or has_state):
            result['missing_files'].append("query: 未发现 draft_*.md 或 state_*.json")

        if forum_ready:
            result['files_found'].append(f"forum: {os.path.basename(forum_log_path)}")
        else:
            result['missing_files'].append("forum: 日志文件不存在")

        result['latest_files'] = {}
        if has_draft:
            result['latest_files']['query_draft'] = latest_query['draft']  # type: ignore
        if has_state:
            result['latest_files']['query_state'] = latest_query['state']  # type: ignore
        if forum_ready:
            result['latest_files']['forum'] = forum_log_path

        return result

    def load_input_files(self, file_paths: Dict[str, str]) -> Dict[str, Any]:
        """
        读取：优先 draft，其次 state（拼装成 draft），media/insight 为可选
        返回结构：{'reports': [query_report, media_report, insight_report], 'forum_logs': str}
        """
        content: Dict[str, Any] = {'reports': ["", "", ""], 'forum_logs': ''}

        # 1) draft / state -> query_report
        if file_paths.get('query_draft'):
            try:
                with open(file_paths['query_draft'], 'r', encoding='utf-8') as f:
                    content['reports'][0] = f.read()
                self.logger.info(f"已加载 query draft: {os.path.basename(file_paths['query_draft'])}")
            except Exception as e:
                self.logger.error(f"加载 query draft 失败: {e}")
        elif file_paths.get('query_state'):
            try:
                with open(file_paths['query_state'], 'r', encoding='utf-8') as f:
                    state_txt = f.read()
                content['reports'][0] = self._mk_report_from_state(state_txt)
                self.logger.info(f"已从 state.json 拼装 query 初稿: {os.path.basename(file_paths['query_state'])}")
            except Exception as e:
                self.logger.error(f"加载 query state 失败: {e}")

        # 2) media/insight（预留）
        # content['reports'][1] = ...
        # content['reports'][2] = ...

        # 3) forum
        if 'forum' in file_paths:
            try:
                with open(file_paths['forum'], 'r', encoding='utf-8') as f:
                    content['forum_logs'] = f.read()
                self.logger.info(f"已加载论坛日志: {len(content['forum_logs'])} 字符")
            except Exception as e:
                self.logger.error(f"加载论坛日志失败: {e}")

        return content


def create_agent(config_file: Optional[str] = None) -> ReportAgent:
    cfg = load_config(config_file)
    return ReportAgent(cfg)
