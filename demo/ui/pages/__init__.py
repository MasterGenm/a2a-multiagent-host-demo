# pages/__init__.py — 安全版：不做页面注册，不强制导入可选页面
# 由 main（test01_main.py）负责注册页面

# 可选：如果有公共导出，就简单导出 conversation 模块，别 import settings
from . import conversation as conversation

__all__ = ["conversation"]

# 如果你确实需要在 __init__ 里做页面注册，至少要保护性导入：
# try:
#     from . import settings  # 仅当 state.state 里已有 SettingsState 才会成功
# except Exception as e:
#     print("[pages] optional import skipped:", repr(e))
