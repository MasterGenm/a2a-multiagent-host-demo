# ui/mcpserver/system.py
# 轻量级 MCP 空实现：满足 NagaConversation 的接口，不做任何实际注册
from typing import List, Dict, Any, Optional

class MCPManager:
    def __init__(self, *args, **kwargs) -> None:
        self._services: Dict[str, Any] = {}

    # 供 NagaConversation 调用；这里返回空列表即可
    def auto_register_services(self) -> List[str]:
        # 如果以后要真的接 MCP 服务，这里改成扫描 & 注册逻辑即可
        return []

    # 如果代码里枚举服务，给一个空列表
    def list_services(self) -> List[str]:
        return list(self._services.keys())

    # 如果按名获取服务，返回 None
    def get_service(self, name: str) -> Optional[Any]:
        return self._services.get(name)

    # 兼容可能的属性读取
    @property
    def services(self) -> Dict[str, Any]:
        return self._services
