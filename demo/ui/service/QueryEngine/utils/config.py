# -*- coding: utf-8 -*-
"""
配置管理模块（QueryEngine 版，支持 Zhipu / OpenAI / SiliconFlow）
- Tavily 可选
- 默认 provider 仍为 zhipu；可用 NAGA_MODEL_NAME 或 QUERY_MODEL_NAME 覆盖
"""

import os
from dataclasses import dataclass
from typing import Optional

@dataclass
class Config:
    # API keys
    zhipu_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    siliconflow_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None  # 可选

    # 模型配置
    default_llm_provider: str = "zhipu"  # zhipu / openai / siliconflow
    zhipu_model: str = "glm-4.5-flash"
    openai_model: str = "gpt-4o-mini"
    siliconflow_model: str = "Qwen/Qwen3-8B"
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"

    # 搜索/输出/Agent
    search_timeout: int = 240
    max_content_length: int = 20000
    max_reflections: int = 2
    max_paragraphs: int = 5
    output_dir: str = "reports"
    save_intermediate_states: bool = True

    def validate(self) -> bool:
        prov = (self.default_llm_provider or "").lower()
        if prov == "zhipu" and not self.zhipu_api_key:
            print("错误: Zhipu API Key未设置（ZHIPU_API_KEY 或 NAGA_API_KEY）")
            return False
        if prov == "openai" and not self.openai_api_key:
            print("错误: OpenAI API Key未设置")
            return False
        if prov == "siliconflow" and not self.siliconflow_api_key:
            print("错误: SiliconFlow API Key未设置")
            return False
        if not self.tavily_api_key:
            print("提示: Tavily API Key未设置，联网检索将被跳过")
        return True

    @classmethod
    def from_file(cls, config_file: str) -> "Config":
        def _from_dict(d: dict) -> "Config":
            zhipu_key = os.getenv("NAGA_API_KEY") or os.getenv("ZHIPU_API_KEY") or d.get("ZHIPU_API_KEY")
            zhipu_model = os.getenv("NAGA_MODEL_NAME") or os.getenv("QUERY_MODEL_NAME") or d.get("ZHIPU_MODEL", "glm-4.6")
            silicon_key = os.getenv("SILICONFLOW_API_KEY") or d.get("SILICONFLOW_API_KEY")
            silicon_model = os.getenv("SILICONFLOW_MODEL") or d.get("SILICONFLOW_MODEL", "Qwen/Qwen3-8B")
            silicon_base = os.getenv("SILICONFLOW_BASE_URL") or d.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")

            return cls(
                zhipu_api_key=zhipu_key,
                openai_api_key=os.getenv("OPENAI_API_KEY") or d.get("OPENAI_API_KEY"),
                siliconflow_api_key=silicon_key,
                tavily_api_key=os.getenv("TAVILY_API_KEY") or d.get("TAVILY_API_KEY"),
                default_llm_provider=os.getenv("DEFAULT_LLM_PROVIDER") or d.get("DEFAULT_LLM_PROVIDER", "zhipu"),
                zhipu_model=zhipu_model,
                openai_model=os.getenv("OPENAI_MODEL") or d.get("OPENAI_MODEL", "gpt-4o-mini"),
                siliconflow_model=silicon_model,
                siliconflow_base_url=silicon_base,
                search_timeout=int(os.getenv("SEARCH_TIMEOUT") or d.get("SEARCH_TIMEOUT", 240)),
                max_content_length=int(os.getenv("SEARCH_CONTENT_MAX_LENGTH") or d.get("SEARCH_CONTENT_MAX_LENGTH", 20000)),
                max_reflections=int(os.getenv("MAX_REFLECTIONS") or d.get("MAX_REFLECTIONS", 2)),
                max_paragraphs=int(os.getenv("MAX_PARAGRAPHS") or d.get("MAX_PARAGRAPHS", 5)),
                output_dir=os.getenv("OUTPUT_DIR") or d.get("OUTPUT_DIR", "reports"),
                save_intermediate_states=(os.getenv("SAVE_INTERMEDIATE_STATES") or str(d.get("SAVE_INTERMEDIATE_STATES", "true"))).lower()=="true",
            )

        if config_file.endswith(".py"):
            import importlib.util
            spec = importlib.util.spec_from_file_location("qe_config", config_file)
            mod = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(mod)
            d = {k:v for k,v in vars(mod).items() if k.isupper()}
            return _from_dict(d)
        else:
            d = {}
            if os.path.exists(config_file):
                with open(config_file, "r", encoding="utf-8") as f:
                    for line in f:
                        s=line.strip()
                        if s and not s.startswith("#") and "=" in s:
                            k,v=s.split("=",1); d[k.strip()]=v.strip()
            return _from_dict(d)

def load_config(config_file: Optional[str] = None) -> Config:
    if config_file:
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"配置文件不存在: {config_file}")
        file_to_load = config_file
    else:
        for p in ["config.py", "config.env", ".env"]:
            if os.path.exists(p):
                file_to_load = p
                print(f"已找到配置文件: {p}")
                break
        else:
            file_to_load = ".env"
    cfg = Config.from_file(file_to_load)
    if not cfg.validate():
        raise ValueError("配置验证失败，请检查 API 密钥")
    return cfg

def print_config(config: Config):
    print("\n=== QueryEngine 配置 ===")
    print(f"LLM提供商: {config.default_llm_provider}")
    print(f"Zhipu模型: {config.zhipu_model}")
    print(f"OpenAI模型: {config.openai_model}")
    print(f"SiliconFlow模型: {config.siliconflow_model} @ {config.siliconflow_base_url}")
    print(f"搜索超时: {config.search_timeout}秒")
    print(f"最大内容长度: {config.max_content_length}")
    print(f"最大反思次数: {config.max_reflections}")
    print(f"最大段落数: {config.max_paragraphs}")
    print(f"输出目录: {config.output_dir}")
    print(f"保存中间状态: {config.save_intermediate_states}")
    print(f"Zhipu API Key: {'已设置' if config.zhipu_api_key else '未设置'}")
    print(f"OpenAI API Key: {'已设置' if config.openai_api_key else '未设置'}")
    print(f"SiliconFlow API Key: {'已设置' if config.siliconflow_api_key else '未设置'}")
    print(f"Tavily API Key: {'已设置' if config.tavily_api_key else '未设置（禁用联网检索）'}")
    print("========================\n")
