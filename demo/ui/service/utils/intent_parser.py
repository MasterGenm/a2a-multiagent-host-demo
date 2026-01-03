# -*- coding: utf-8 -*-
"""
Intent Parser（意图解构模块）
- 作用：把自然语言 Query 转写为结构化任务 JSON（决定是否调用 QE、选用何种搜索策略、时间窗口等）
- 特点：OpenAI 兼容；读取现有 NAGA_* 环境变量；输出稳定 JSON；失败时有兜底启发式
- 放置路径：service/utils/intent_parser.py
"""

import os
import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

try:
    # OpenAI 兼容客户端（与你 main.py 用法一致）
    from openai import OpenAI
except Exception:  # 如果环境没装 openai 包，也允许先导入占位
    OpenAI = None  # type: ignore


# ---------- 工具函数 ----------
def _coalesce(*vals):
    for v in vals:
        if v:
            return v
    return None


def _default_base_for(provider: str) -> str:
    provider = (provider or "").lower()
    if provider == "zhipu":
        return "https://open.bigmodel.cn/api/paas/v4"
    if provider == "dashscope":
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if provider == "siliconflow":
        return "https://api.siliconflow.cn/v1"
    if provider == "openai":
        return "https://api.openai.com/v1"
    # 兜底：按 openai 兼容
    return "https://api.openai.com/v1"


def _extract_json_block(text: str) -> str:
    """
    从 LLM 输出中提取最外层 JSON 块；若失败，返回空串
    """
    if not text:
        return ""
    s = text.strip()
    i = s.find("{")
    j = s.rfind("}")
    if i == -1 or j == -1 or j <= i:
        return ""
    return s[i: j + 1]


def _safe_loads(s: str) -> Dict[str, Any]:
    try:
        return json.loads(s)
    except Exception:
        return {}


def _parse_time_window(user_text: str) -> Dict[str, Optional[str]]:
    """
    从用户文本里粗略解析时间窗口；若未发现则返回空
    """
    t = (user_text or "").lower()
    today = datetime.utcnow().date()
    # 常见中文表达
    if any(k in t for k in ["过去24小时", "近24小时", "24小时内"]):
        return {
            "time_window": "last_24h",
            "date_from": (today - timedelta(days=1)).isoformat(),
            "date_to": today.isoformat(),
        }
    if any(k in t for k in ["过去一周", "最近一周", "近一周", "7天", "近7天", "本周", "上周"]):
        return {
            "time_window": "last_7d",
            "date_from": (today - timedelta(days=7)).isoformat(),
            "date_to": today.isoformat(),
        }
    # 显式日期范围
    m = re.findall(r"(\d{4}-\d{2}-\d{2})", t)
    if len(m) >= 2:
        m.sort()
        return {"time_window": "date_range", "date_from": m[0], "date_to": m[-1]}
    return {"time_window": "all_time", "date_from": None, "date_to": None}


# ---------- 主类 ----------
class IntentParser:
    """
    统一“意图解构”入口：
    - parse(text, context) -> 结构化意图 dict
    - to_query_engine_inputs(plan) -> 给 QE 的最小输入（search_tool/ query/ start_date/ end_date）
    """

    SYSTEM_PROMPT = """
你是一个只负责“意图解析与路由”的工具 (Intent Router)。
你的任务是：把用户的自然语言请求，转换成一个 JSON 结构的「任务规划」。

【极其重要的约束】

1. 你 **绝对不要** 直接替助手回答问题，也不要提前下结论
   比如不要输出类似：
   - "notes": "告诉用户无法确定猫名字，因为上下文不足"
   - "notes": "建议告诉用户目前无法作答"
   这些话应该由后续的助手自己判断和说出，你只做「路由」和「归类」。

2. 你的工作仅限于：
   - 判断当前任务类型：task
   - 是否需要调用 QueryEngine：should_use_qe
   - 是否需要访问外部搜索 / 浏览器：needs_browsing
   - 提取适合检索的 queries
   - 推断时间范围（time_window / date_from / date_to）
   - 指定输出偏好（output）和约束（constraints）

3. 严格输出一个 JSON，对键使用双引号，不要输出任何多余文字。

【JSON 字段定义】

{
  "task": "research | quick_answer | report | coding | chat",
  "should_use_qe": true or false,
  "needs_browsing": true or false,

  "queries": [
    "适合用来检索的英文或中文查询词，优先保留原意",
    "可以有多个，但不要超过 3 个"
  ],

  "time_window": "auto | all | last_1d | last_7d | last_30d | last_90d | last_1y | custom",
  "date_from": "YYYY-MM-DD 或 null",
  "date_to": "YYYY-MM-DD 或 null",

  "sources": ["arxiv", "github", "news", "wikipedia", ...],
  "region": "cn | us | eu | global | auto",

  "output": {
    "format": "markdown | bullet_list | table | code | mixed",
    "citations": true or false,
    "max_length": "short | medium | long"
  },

  "constraints": {
    "language": "zh | en | auto",
    "avoid_topics": ["..."],
    "style": "严谨 | 口语 | 教学 | 总结"
  },

  "notes": "给后端 Orchestrator 的路由备注，可以简单说明为什么选择该 task/是否需要 QE；不要替助手给出最终回答或说『无法回答』。"
}

【注意】

- 如果用户只是闲聊或普通提问，优先使用:
  - task = "quick_answer" 或 "chat"
  - should_use_qe = false
- 只有在确实需要查资料、聚合信息时，才设置:
  - should_use_qe = true
- 任何情况下，都不要在 notes 里写「告诉用户 XX 做不到/信息不足」这类话。

现在请根据用户输入，输出一个满足以上规范的 JSON。
"""


    USER_WRAPPER = """用户请求：
{user_input}

（若已给出项目上下文，可参考）
上下文（可为空）：
{context_json}
"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        # 读取环境变量（与 main.py 对齐）
        self.provider = (provider or os.getenv("NAGA_PROVIDER") or "zhipu").strip().lower()
        self.base_url = _coalesce(base_url, os.getenv("NAGA_BASE_URL"), _default_base_for(self.provider))
        self.model = _coalesce(model, os.getenv("NAGA_MODEL_NAME"), "glm-4.5")
        self.api_key = _coalesce(
            api_key,
            os.getenv("NAGA_API_KEY"),
            os.getenv("ZHIPU_API_KEY"),
            os.getenv("OPENAI_API_KEY"),
            os.getenv("DASHSCOPE_API_KEY"),
            os.getenv("SILICONFLOW_API_KEY"),
        )

        if OpenAI is None:
            raise RuntimeError("openai 客户端不可用，请先安装 `pip install openai`")
        if not self.api_key:
            raise RuntimeError("缺少 API Key（请设置 NAGA_API_KEY 或对应 Provider 的 Key）")

        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    # ---- 公有方法 ----
    def parse(self, user_input: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        主入口：解析自然语言 -> 结构化意图
        """
        if not user_input or not user_input.strip():
            return self._fallback(user_input, context)

        payload_user = self.USER_WRAPPER.format(
            user_input=user_input.strip(),
            context_json=json.dumps(context or {}, ensure_ascii=False),
        )

        try:
            # 兼容新旧 openai 客户端
            if hasattr(self.client, "chat_completions"):
                resp = self.client.chat_completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": payload_user},
                    ],
                    temperature=0.1,
                )
                raw = (resp.choices[0].message.content or "").strip()
            else:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": payload_user},
                    ],
                    temperature=0.1,
                )
                raw = (resp.choices[0].message.content or "").strip()

            block = _extract_json_block(raw)
            data = _safe_loads(block)
            if not data:
                return self._fallback(user_input, context)
            return self._normalize(data, user_input)

        except Exception:
            return self._fallback(user_input, context)

    def to_query_engine_inputs(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        把意图 JSON 转成 QE 最小可用输入（供你的 QE 层直接食用）
        返回示例：
        {
          "should_use_qe": True,
          "search_tool": "basic_search_news|deep_search_news|search_news_last_24_hours|search_news_last_week|search_news_by_date",
          "query": "……",
          "start_date": "YYYY-MM-DD or None",
          "end_date": "YYYY-MM-DD or None"
        }
        """
        should = bool(plan.get("should_use_qe"))
        tw = (plan.get("time_window") or "all_time").lower()
        q = self._pick_query(plan.get("queries") or [])
        start = plan.get("date_from")
        end = plan.get("date_to")

        if tw == "last_24h":
            tool = "search_news_last_24_hours"
        elif tw == "last_7d":
            tool = "search_news_last_week"
        elif tw == "date_range" and start and end:
            tool = "search_news_by_date"
        else:
            # 默认基础搜索；如需更激进可改为 deep_search_news
            tool = "basic_search_news"

        return {
            "should_use_qe": should,
            "search_tool": tool,
            "query": q,
            "start_date": start,
            "end_date": end,
        }

    # ---- 内部方法 ----
    def _normalize(self, d: Dict[str, Any], user_input: str) -> Dict[str, Any]:
        # 填补默认值，保证字段完整
        tw_pack = (
            _parse_time_window(user_input)
            if not d.get("time_window")
            else {
                "time_window": d.get("time_window"),
                "date_from": d.get("date_from"),
                "date_to": d.get("date_to"),
            }
        )
        out = {
            "task": d.get("task") or self._guess_task(user_input),
            "should_use_qe": bool(d.get("should_use_qe")),
            "needs_browsing": bool(d.get("needs_browsing")),
            "queries": self._clean_queries(d.get("queries")),
            "time_window": tw_pack.get("time_window"),
            "date_from": tw_pack.get("date_from"),
            "date_to": tw_pack.get("date_to"),
            "sources": d.get("sources") or ["news"],
            "region": d.get("region") or "CN",
            "output": d.get("output") or {"format": "markdown", "length": "medium", "citations": "optional"},
            "constraints": d.get("constraints") or {"language": "zh", "max_links": 10, "dedupe": True},
            "notes": d.get("notes") or "",
        }

        # 若用户明显要求“要出处/最新”，先强制 should_use_qe = True & citations=required
        t = user_input or ""
        if any(
            k in t
            for k in [
                "给出处",
                "参考链接",
                "来源链接",
                "sources",
                "references",
                "事实核查",
                "查证",
                "新闻",
                "最新",
                "过去24小时",
                "过去一周",
                "近一周",
                "7天",
                "本周",
            ]
        ):
            out["should_use_qe"] = True
            if isinstance(out["output"], dict):
                out["output"]["citations"] = "required"

        # 纯“报告任务” → 关闭 QE（覆盖上面的强制），并把默认输出改为 html（更贴合你的 ReportEngine 模板）
        # 只要没有出现“先研究/研究并…”这些词，就当作纯报告
        if (out.get("task") or "").lower() == "report":
            if not any(k in t for k in ["先研究", "研究并", "研究后出报告", "研究+报告"]):
                out["should_use_qe"] = False
                if isinstance(out.get("output"), dict):
                    out["output"]["format"] = out["output"].get("format") or "html"
                    # citations 保持 optional 即可

        # 若 queries 为空，用用户原话兜底一条
        if not out["queries"]:
            out["queries"] = [self._strip_for_query(user_input)]

        return out

    def _fallback(self, user_input: str, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        tw = _parse_time_window(user_input or "")
        return {
            "task": self._guess_task(user_input or ""),
            "should_use_qe": self._should_qe(user_input or ""),
            "needs_browsing": self._should_qe(user_input or ""),
            "queries": [self._strip_for_query(user_input or "")],
            "time_window": tw["time_window"],
            "date_from": tw["date_from"],
            "date_to": tw["date_to"],
            "sources": ["news"],
            "region": "CN",
            "output": {"format": "markdown", "length": "medium", "citations": "optional"},
            "constraints": {"language": "zh", "max_links": 10, "dedupe": True},
            "notes": "",
        }

    @staticmethod
    def _strip_for_query(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip())

    @staticmethod
    def _clean_queries(qs: Any) -> List[str]:
        if not qs:
            return []
        if isinstance(qs, str):
            qs = [qs]
        uniq = []
        for q in qs:
            q = IntentParser._strip_for_query(str(q))
            if q and q not in uniq:
                uniq.append(q)
        return uniq[:3]

    @staticmethod
    def _pick_query(qs: List[str]) -> str:
        return qs[0] if qs else ""

    @staticmethod
    def _guess_task(text: str) -> str:
        t = text or ""
        # 明确先研究后报告（走 research）
        key_research_then_report = ["研究并", "先研究后报告", "研究后出报告", "先研究再报告", "研究+报告"]
        if any(k in t for k in key_research_then_report):
            return "research"

        # 纯报告任务（走 report）
        key_report_task = ["报告任务", "生成报告", "写报告", "出一份报告"]
        if any(k in t for k in key_report_task):
            return "report"

        # 其它常见研究信号（默认走 research）
        key_research = ["深度研究", "深度搜索", "资料检索", "舆情", "新闻", "给出处", "查证", "事实核查"]
        if any(k in t for k in key_research):
            return "research"

        return "chat"

    @staticmethod
    def _should_qe(text: str) -> bool:
        t = text or ""

        # 纯“报告任务”不走 QE（除非包含先研究/研究并…）
        if ("报告任务" in t or "生成报告" in t or "写报告" in t or "出一份报告" in t) and not any(
            k in t for k in ["先研究", "研究并", "研究后出报告", "研究+报告"]
        ):
            return False

        want_sources = ["给出处", "参考链接", "来源链接", "sources", "references", "查证", "事实核查"]
        time_signals = ["最新", "过去24小时", "近24小时", "过去一周", "最近一周", "近一周", "7天", "近7天", "本周", "上周"]
        news_terms = ["新闻", "报道", "资讯", "舆情", "媒体", "文章链接"]

        if any(k in t for k in want_sources):
            return True
        if any(k in t for k in time_signals) and any(n in t for n in news_terms):
            return True
        if re.search(r"\d{4}-\d{2}-\d{2}", t) and any(n in t for n in news_terms):
            return True
        return False


if __name__ == "__main__":
    # 简单自测（运行：python service/utils/intent_parser.py）
    demo_list = [
        "报告任务：请生成一份关于金融科技技术与应用发展趋势的简短报告",
        "研究并生成报告：请围绕“国内人工智能发展”做深度研究（要点/数据/风险+来源链接）",
        "帮我查最新相关新闻，给出处，过去一周",
    ]
    ip = IntentParser()
    for demo in demo_list:
        plan = ip.parse(demo)
        print("\n=== DEMO:", demo)
        print("=== INTENT ===")
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        qe_input = ip.to_query_engine_inputs(plan)
        print("=== QE INPUT ===")
        print(json.dumps(qe_input, ensure_ascii=False, indent=2))
