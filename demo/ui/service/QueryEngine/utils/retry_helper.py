# -*- coding: utf-8 -*-
"""
QueryEngine.utils.retry_helper
- 同步/异步通用的“优雅重试”装饰器 with_graceful_retry
- 兼容两种调用方式：
    1) with_graceful_retry(SEARCH_API_RETRY_CONFIG, default_return=...)
    2) with_graceful_retry(max_retry=..., initial_backoff=..., max_backoff=...)
"""

from __future__ import annotations
import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional, Tuple, Type, Union

# -------- 兼容常见 HTTP 客户端异常（若库不存在则忽略） --------
_httpx_errors: Tuple[Type[BaseException], ...] = tuple()
try:
    import httpx  # type: ignore
    _httpx_errors = (
        httpx.HTTPError,
        httpx.ConnectError,
        httpx.ReadError,
        getattr(httpx, "TimeoutException", TimeoutError),  # 某些版本命名不同
    )
except Exception:
    pass

_requests_errors: Tuple[Type[BaseException], ...] = tuple()
try:
    import requests  # type: ignore
    _requests_errors = (requests.RequestException,)  # type: ignore
except Exception:
    pass

DEFAULT_RETRY_EXC: Tuple[Type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
) + _httpx_errors + _requests_errors


@dataclass(frozen=True)
class RetryConfig:
    tries: int = 3            # 允许的重试次数（不含首轮）
    delay: float = 1.0        # 初始等待秒数
    backoff: float = 2.0      # 指数退避系数
    jitter: float = 0.25      # 抖动比例（0~1）
    max_delay: Optional[float] = 20.0
    exceptions: Tuple[Type[BaseException], ...] = DEFAULT_RETRY_EXC


# 针对搜索/抓取 API 的默认策略
SEARCH_API_RETRY_CONFIG = RetryConfig(
    tries=3,
    delay=1.0,
    backoff=2.0,
    jitter=0.35,
    max_delay=20.0,
    exceptions=DEFAULT_RETRY_EXC,
)


def _next_sleep(base: float, backoff: float, jitter: float, max_delay: Optional[float]) -> float:
    low = max(0.0, base * (1.0 - jitter))
    high = base * (1.0 + jitter)
    t = random.uniform(low, high)
    if max_delay is not None:
        t = min(t, max_delay)
    return max(0.0, t)


def with_graceful_retry(
    config: Union[RetryConfig, Dict[str, Any], None] = None,
    exceptions: Optional[Iterable[Type[BaseException]]] = None,
    giveup: Optional[Callable[[BaseException], bool]] = None,
    on_retry: Optional[Callable[[int, BaseException, float], None]] = None,
    *,
    # 兼容 search.py / 其它调用处可能传入的参数
    default_return: Any = None,
    max_retry: Optional[int] = None,
    initial_backoff: Optional[float] = None,
    max_backoff: Optional[float] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    用法示例：
        @with_graceful_retry(SEARCH_API_RETRY_CONFIG, default_return=...)
        def call_api(...): ...

        @with_graceful_retry(max_retry=2, initial_backoff=1.0, max_backoff=6.0)
        def call_llm(...): ...
    """
    # 1) 基于 config 构造 RetryConfig
    if config is None:
        cfg = SEARCH_API_RETRY_CONFIG
    elif isinstance(config, dict):
        cfg = RetryConfig(**{**SEARCH_API_RETRY_CONFIG.__dict__, **config})
    elif isinstance(config, RetryConfig):
        cfg = config
    else:
        raise TypeError("config must be None, dict, or RetryConfig")

    # 2) 兼容另外一套显式参数（若提供则覆盖）
    if max_retry is not None:
        cfg = RetryConfig(
            tries=int(max_retry),
            delay=float(initial_backoff if initial_backoff is not None else cfg.delay),
            backoff=cfg.backoff,
            jitter=cfg.jitter,
            max_delay=float(max_backoff) if max_backoff is not None else cfg.max_delay,
            exceptions=cfg.exceptions,
        )

    exc_types = tuple(exceptions) if exceptions else cfg.exceptions

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if asyncio.iscoroutinefunction(func):
            async def awrapper(*args: Any, **kwargs: Any) -> Any:
                attempt = 0
                delay = cfg.delay
                while True:
                    try:
                        return await func(*args, **kwargs)
                    except exc_types as e:  # type: ignore[misc]
                        # 停止条件
                        if giveup and giveup(e):
                            raise
                        if attempt >= cfg.tries:
                            if default_return is not None:
                                return default_return
                            raise
                        # 退避等待
                        sleep_s = _next_sleep(delay, cfg.backoff, cfg.jitter, cfg.max_delay)
                        if on_retry:
                            try:
                                on_retry(attempt + 1, e, sleep_s)
                            except Exception:
                                pass
                        await asyncio.sleep(sleep_s)
                        delay = min(delay * cfg.backoff, cfg.max_delay or delay * cfg.backoff)
                        attempt += 1
            return awrapper

        else:
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                attempt = 0
                delay = cfg.delay
                while True:
                    try:
                        return func(*args, **kwargs)
                    except exc_types as e:  # type: ignore[misc]
                        if giveup and giveup(e):
                            raise
                        if attempt >= cfg.tries:
                            if default_return is not None:
                                return default_return
                            raise
                        sleep_s = _next_sleep(delay, cfg.backoff, cfg.jitter, cfg.max_delay)
                        if on_retry:
                            try:
                                on_retry(attempt + 1, e, sleep_s)
                            except Exception:
                                pass
                        time.sleep(sleep_s)
                        delay = min(delay * cfg.backoff, cfg.max_delay or delay * cfg.backoff)
                        attempt += 1
            return wrapper
    return decorator
