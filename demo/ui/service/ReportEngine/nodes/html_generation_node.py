# -*- coding: utf-8 -*-
"""
HTMLGenerationNode（适配 QueryEngine draft/state 的链路）
- 输入（两种写法都支持）：
    run(input_data={...})   ✅ 推荐
    run(data={...})         ✅ 兼容旧代码
  data / input_data 格式：
    {
      'query': str,
      'query_engine_report': str,      # QueryEngine 产出的 draft_*.md（优先）
      'media_engine_report': str,      # 可选
      'insight_engine_report': str,    # 可选
      'forum_logs': str,               # 可选
      'selected_template': str,        # TemplateSelectionNode 给出的模板（Markdown 亦可）
      'empty_input': bool,             # 上层判断是否完全无材料
    }
- 输出：总是 <html>...</html>，即便 LLM 失败也会用最小 HTML 兜底
"""

from __future__ import annotations

import os  # 修复旧版本 NameError
import re
import html
from typing import Any, Dict

MAX_INPUT_CHARS = 28000  # 控制传给 LLM 的最大字符数，避免超 token


class HTMLGenerationNode:
    def __init__(self, llm_client):
        """
        llm_client: 需实现 .invoke(system_prompt, user_prompt, max_tokens=...) -> str
        兼容 ZhipuLLM / GeminiLLM 包装器
        """
        self.llm = llm_client

    # ------------------------- 对外主入口 -------------------------
    def run(self, input_data: Dict[str, Any] = None, **kwargs) -> str:
        """
        执行 HTML 生成。
        兼容两种调用方式：
          - run(input_data=...)
          - run(data=...)        # 兼容旧代码
        """
        # 兼容别名：优先 input_data，其次 kwargs['input_data']，再次 kwargs['data']
        if input_data is None:
            input_data = kwargs.get("input_data") or kwargs.get("data") or {}

        # 强制本地直出（烟测/加速）：REPORT_FORCE_LOCAL=1
        if os.getenv("REPORT_FORCE_LOCAL", "0").lower() in {"1", "true", "yes"}:
            query = (input_data.get("query") or "").strip()
            q_report = (input_data.get("query_engine_report") or "").strip()
            if self._looks_like_markdown(q_report):
                html_out = self._wrap_html(self._md_to_html(q_report), title=query or "自动生成报告（本地直出）")
            else:
                html_out = self._wrap_html(self._pre_html(q_report or ""), title=query or "自动生成报告（本地直出）")
            return self._ensure_non_empty_sections(html_out)

        # 取数据 & 限制长度
        query = (input_data.get("query") or "").strip()
        template_md = (input_data.get("selected_template") or "").strip()

        q_report = self._clip(str(input_data.get("query_engine_report") or ""))
        m_report = self._clip(str(input_data.get("media_engine_report") or ""))
        i_report = self._clip(str(input_data.get("insight_engine_report") or ""))
        forum    = self._clip(str(input_data.get("forum_logs") or ""))

        empty_input = bool(input_data.get("empty_input"))

        # 完全无材料：用模板提纲或默认骨架生成最小 HTML
        if empty_input and not (q_report or m_report or i_report or forum):
            html_out = self._skeleton_html(query=query or "综合研究报告", template_md=template_md)
            return self._ensure_non_empty_sections(html_out)

        # 有材料：先尝试 LLM 直接产出 HTML
        try:
            sys_prompt = self._build_system_prompt()
            user_prompt = self._build_user_prompt(query, template_md, q_report, m_report, i_report, forum)
            result = self.llm.invoke(sys_prompt, user_prompt, max_tokens=8192)

            clean = self._strip_code_fences(str(result or "").strip())

            if self._looks_like_html(clean):
                html_out = clean
            elif self._looks_like_markdown(clean):
                html_out = self._wrap_html(self._md_to_html(clean), title=query or "自动生成报告")
            else:
                html_out = self._wrap_html(self._pre_html(clean), title=query or "自动生成报告")

            return self._ensure_non_empty_sections(html_out)

        except Exception:
            # LLM 失败：退化策略
            src = q_report or m_report or i_report or forum
            if self._looks_like_markdown(src):
                html_out = self._wrap_html(self._md_to_html(src), title=query or "自动生成报告（兜底）")
            else:
                html_out = self._wrap_html(self._pre_html(src), title=query or "自动生成报告（兜底）")
            return self._ensure_non_empty_sections(html_out)

    # ------------------------- Prompt 组装 -------------------------
    def _build_system_prompt(self) -> str:
        return (
            "你是一名严谨的研究报告整理助手。请将给定的研究材料与模板组织为结构化、可发布的 HTML 报告。\n"
            "硬性要求：\n"
            "1) 直接输出完整 HTML（<html>...），不要输出 Markdown、不要解释说明。\n"
            "2) 用中文、客观、精炼；小节清晰，H1/H2/H3 语义合理，目录可省略。\n"
            "3) 若材料中出现 URL，请在正文处使用 [1][2]… 的上标或方括号编号引用，并在“参考文献”末尾按编号列出超链接；不要臆造来源。\n"
            "4) 未提供的信息留空或略去，不要杜撰。\n"
            "5) 内容中避免政治评价、动员性语句，仅做事实性归纳与对比。"
        )

    def _build_user_prompt(
        self,
        query: str,
        template_md: str,
        q_report: str,
        m_report: str,
        i_report: str,
        forum: str
    ) -> str:
        def sec(name: str, content: str) -> str:
            bar = "=" * 12
            return f"\n{bar} {name} {bar}\n{content.strip()}\n"

        prompt = []
        prompt.append(f"【主题】\n{query or '综合研究报告'}\n")
        if template_md:
            prompt.append(sec("模板（Markdown 或占位）", template_md))
        if q_report:
            prompt.append(sec("QueryEngine 材料（优先）", q_report))
        if m_report:
            prompt.append(sec("Media 引擎材料", m_report))
        if i_report:
            prompt.append(sec("Insight 引擎材料", i_report))
        if forum:
            prompt.append(sec("论坛对话/操作日志（可选）", forum))

        prompt.append(
            "\n【输出格式】\n"
            "- 返回完整 <html> 文档；\n"
            "- 章节建议：摘要、背景、现状与证据、风险与不确定性、结论与可执行建议、参考文献；\n"
            "- 引用处以 [1][2]… 编号，并在“参考文献”区列出超链接（仅使用材料中真实存在的 URL）。"
        )
        return "\n".join(prompt)

    # ------------------------- 渲染兜底 -------------------------
    def _skeleton_html(self, query: str, template_md: str) -> str:
        """无材料时，基于模板（或默认清单）做最小 HTML 骨架"""
        sections = self._extract_h1_h2_from_md(template_md) or [
            ("h1", query),
            ("h2", "摘要"),
            ("h2", "背景"),
            ("h2", "现状与证据"),
            ("h2", "风险与不确定性"),
            ("h2", "结论与可执行建议"),
            ("h2", "参考文献")
        ]
        body = []
        used_h1 = False
        for tag, text in sections:
            safe = html.escape(text or "")
            if tag == "h1":
                if used_h1:
                    body.append(f"<h2>{safe}</h2>")
                else:
                    body.append(f"<h1>{safe}</h1>")
                    used_h1 = True
            elif tag in ("h2", "h3"):
                body.append(f"<{tag}>{safe}</{tag}>")
                body.append("<p></p>")
        return self._wrap_html("\n".join(body), title=query or "自动生成报告")

    # ------------------------- utils -------------------------
    def _clip(self, s: str, limit: int = MAX_INPUT_CHARS) -> str:
        s = s or ""
        if len(s) <= limit:
            return s
        return s[:limit] + "\n\n...[内容已截断，以控制输入长度]"

    def _strip_code_fences(self, s: str) -> str:
        # 去掉 ```html ... ``` 或 ``` ... ```
        s = re.sub(r"^```(?:html|HTML)?\s*", "", s.strip(), flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s.strip())
        return s.strip()

    def _looks_like_html(self, s: str) -> bool:
        return "<html" in s.lower() and "</html>" in s.lower()

    def _looks_like_markdown(self, s: str) -> bool:
        if not s:
            return False
        # 粗略判断：存在 Markdown 语法
        return bool(re.search(r"^#{1,6}\s|\n#{1,6}\s|[-*]\s|\n\d+\.\s|\[.+?\]\(.+?\)", s, flags=re.M))

    def _wrap_html(self, body: str, title: str = "Auto Report") -> str:
        return (
            "<!doctype html><html><head>"
            "<meta charset='utf-8'>"
            f"<title>{html.escape(title or 'Auto Report')}</title>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<style>"
            "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,PingFang SC,Microsoft YaHei,sans-serif;"
            "line-height:1.6;padding:24px;color:#111}"
            "h1{font-size:26px;margin:0 0 12px}h2{font-size:20px;margin:24px 0 8px}h3{font-size:16px;margin:18px 0 6px}"
            "p,li{font-size:14px}"
            "code,pre{background:#f7f7f7;border-radius:6px;padding:2px 6px}pre{padding:12px;overflow:auto}"
            "a{color:#0b5bd3;text-decoration:none}a:hover{text-decoration:underline}"
            "table{border-collapse:collapse;border:1px solid #eee}th,td{border:1px solid #eee;padding:6px 10px}"
            "sup.ref{font-size:11px;color:#666}"
            "</style></head><body>"
            f"{body}"
            "</body></html>"
        )

    def _pre_html(self, text: str) -> str:
        return f"<pre style='white-space:pre-wrap'>{html.escape(text or '')}</pre>"

    def _extract_h1_h2_from_md(self, md: str) -> list[tuple[str, str]]:
        """从模板里抽取 # / ## 作为骨架"""
        res: list[tuple[str, str]] = []
        for ln in (md or "").splitlines():
            m = re.match(r"^(#{1,6})\s+(.+?)\s*$", ln)
            if m:
                level = len(m.group(1))
                text = m.group(2)
                if level == 1:
                    res.append(("h1", text))
                elif level == 2:
                    res.append(("h2", text))
                elif level == 3:
                    res.append(("h3", text))
        return res

    # --------- 轻量 Markdown -> HTML（够用且无第三方依赖） ----------
    def _md_to_html(self, md: str) -> str:
        md = md or ""

        # 代码块 ``` ``` 
        def repl_codeblock(m):
            code = html.escape(m.group(1))
            return f"<pre><code>{code}</code></pre>"
        md = re.sub(r"```(?:[\w+-]+)?\n([\s\S]*?)\n```", repl_codeblock, md, flags=re.M)

        # 行内代码 `code`
        md = re.sub(r"`([^`]+?)`", lambda m: f"<code>{html.escape(m.group(1))}</code>", md)

        # 标题
        md = re.sub(r"^######\s+(.+)$", r"<h6>\1</h6>", md, flags=re.M)
        md = re.sub(r"^#####\s+(.+)$",  r"<h5>\1</h5>", md, flags=re.M)
        md = re.sub(r"^####\s+(.+)$",   r"<h4>\1</h4>", md, flags=re.M)
        md = re.sub(r"^###\s+(.+)$",    r"<h3>\1</h3>", md, flags=re.M)
        md = re.sub(r"^##\s+(.+)$",     r"<h2>\1</h2>", md, flags=re.M)
        md = re.sub(r"^#\s+(.+)$",      r"<h1>\1</h1>", md, flags=re.M)

        # 粗体 / 斜体
        md = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", md)
        md = re.sub(r"\*([^*\n]+)\*",   r"<em>\1</em>", md)

        # 链接 [text](url)
        def repl_link(m):
            text, url = m.group(1), m.group(2)
            return f"<a href=\"{html.escape(url)}\" target=\"_blank\" rel=\"noopener noreferrer\">{html.escape(text)}</a>"
        md = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl_link, md)

        # 无序列表
        md = self._md_bullets_to_html(md)

        # 有序列表
        md = self._md_ordered_to_html(md)

        # 段落：把剩余非 HTML 行块包 <p>
        md = self._md_paragraphize(md)

        return md

    def _md_bullets_to_html(self, text: str) -> str:
        lines = text.splitlines()
        out, buf = [], []

        def flush_buf():
            nonlocal out, buf
            if not buf:
                return
            out.append("<ul>")
            for item in buf:
                item = re.sub(r"^[-*]\s+", "", item).strip()
                out.append(f"<li>{item}</li>")
            out.append("</ul>")
            buf = []

        for ln in lines:
            if re.match(r"^[-*]\s+", ln):
                buf.append(ln)
            else:
                flush_buf()
                out.append(ln)
        flush_buf()
        return "\n".join(out)

    def _md_ordered_to_html(self, text: str) -> str:
        lines = text.splitlines()
        out, buf = [], []

        def flush_buf():
            nonlocal out, buf
            if not buf:
                return
            out.append("<ol>")
            for item in buf:
                item = re.sub(r"^\d+\.\s+", "", item).strip()
                out.append(f"<li>{item}</li>")
            out.append("</ol>")
            buf = []

        for ln in lines:
            if re.match(r"^\d+\.\s+", ln):
                buf.append(ln)
            else:
                flush_buf()
                out.append(ln)
        flush_buf()
        return "\n".join(out)

    def _md_paragraphize(self, text: str) -> str:
        # 将非 HTML 块按双换行分段，包裹 <p>
        blocks = re.split(r"\n\s*\n", text.strip())
        html_blocks = []
        for b in blocks:
            if not b.strip():
                continue
            if re.match(r"^\s*<", b):  # 已是 HTML
                html_blocks.append(b)
            else:
                # 避免把列表等再次包裹
                if re.match(r"^</?(ul|ol|li|h\d|pre|table|thead|tbody|tr|th|td|blockquote)", b.strip()):
                    html_blocks.append(b)
                else:
                    html_blocks.append(f"<p>{b.strip()}</p>")
        return "\n\n".join(html_blocks)

    # ------------------------- 后处理：避免空章节 -------------------------
    def _ensure_non_empty_sections(self, html_doc: str) -> str:
        """
        如果 <h2>...</h2> 与下一个 <h2> 或 </body> 之间几乎没有正文，则注入一段占位说明。
        """
        # 简单判断“几乎没有正文”：H2 后仅有空白或紧跟下一个 H2/结束标签
        pattern = re.compile(r"(<h2>([^<]+)</h2>)(\s*)(?=<h2>|</body>)", flags=re.IGNORECASE)

        def _fill(m):
            title = m.group(2)
            filler = (
                "<p>（占位说明）本节的材料尚未由上游研究引擎补充，当前版本先保证结构完整。"
                "如需快速出稿，可在 QueryEngine 关闭“快模式”或追加素材后再次生成。</p>\n"
            )
            return f"{m.group(1)}\n{filler}"

        return pattern.sub(_fill, html_doc)
