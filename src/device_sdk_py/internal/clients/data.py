# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Core Data Client - 向 EdgeX Core Data 发送读数/事件

对应 Go SDK: github.com/edgexfoundry/device-sdk-go/v4/internal/clients/data
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..common.consts import (
    DEVICE_SYSTEM_EVENT_TYPE,
    CONTENT_TYPE_JSON,
)
from ..common.utils import EdgexError, EdgexErrorKind, create_edgx_error
from ...internal.transformer.transform import Event, Reading


logger = logging.getLogger(__name__)


@dataclass
class CoreDataClientConfig:
    """Core Data 客户端配置"""
    base_url: str
    timeout: float = 10.0
    max_retries: int = 3
    backoff_factor: float = 0.5
    max_connections: int = 10
    jwt_token: Optional[str] = None
    ca_cert: Optional[str] = None
    client_cert: Optional[str] = None
    client_key: Optional[str] = None


@dataclass
class EventBatch:
    """事件批次，用于批量发送"""
    events: List[Event]
    created_at: float = field(default_factory=time.time)
    retry_count: int = 0


class CoreDataClient:
    """
    Core Data 客户端 - 负责向 Core Data 服务发送 Event/Reading
    
    功能：
    - 同步/异步发送 Event
    - 批量发送支持
    - 自动重试 + 指数退避
    - 熔断器保护
    - JWT Token 自动刷新
    - 熔断器状态监控
    """
    
    def __init__(
        self,
        config: CoreDataClientConfig,
        jwt_token_provider: Optional[Callable[[], Optional[str]]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.config = config
        self.jwt_token_provider = jwt_token_provider
        self.logger = logger or logging.getLogger(__name__)
        
        # HTTP 会话配置
        self.session = requests.Session()
        self._configure_session()
        
        # 熔断器状态
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._circuit_open = False
        self._circuit_lock = threading.RLock()
        
        # 重试配置
        self.retry_strategy = Retry(
            total=config.max_retries,
            backoff_factor=config.backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=self.retry_strategy,
            pool_connections=config.max_connections,
            pool_maxsize=config.max_connections,
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # 事件队列（用于批量发送）
        self._event_queue: List[Event] = []
        self._queue_lock = threading.RLock()
        self._flush_interval = 1.0  # 秒
        self._max_batch_size = 50
        self._flush_thread: Optional[threading.Thread] = None
        self._stop_flush = threading.Event()
        
    def _configure_session(self) -> None:
        """配置 HTTP 会话"""
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        if self.config.jwt_token:
            self.session.headers["Authorization"] = f"Bearer {self.config.jwt_token}"
        
        # SSL 配置
        if self.config.ca_cert:
            self.session.verify = self.config.ca_cert
        elif self.config.client_cert and self.config.client_key:
            self.session.cert = (self.config.client_cert, self.config.client_key)
    
    def _update_jwt_token(self) -> None:
        """更新 JWT Token"""
        if self.jwt_token_provider:
            token = self.jwt_token_provider()
            if token:
                self.session.headers["Authorization"] = f"Bearer {token}"
    
    def _get_event_url(self) -> str:
        """获取事件发送端点"""
        return urljoin(self.config.base_url.rstrip("/") + "/", "api/v3/event")
    
    def _check_circuit_breaker(self) -> bool:
        """检查熔断器状态"""
        with self._circuit_lock:
            if not self._circuit_open:
                return True
            # 尝试半开状态
            if time.time() - self._last_failure_time > 30:  # 30秒后尝试恢复
                self._circuit_open = False
                self._failure_count = 0
                self.logger.info("Circuit breaker half-open, attempting request")
                return True
            return False
    
    def _record_failure(self) -> None:
        """记录失败，更新熔断器"""
        with self._circuit_lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= 5:  # 连续5次失败打开熔断器
                self._circuit_open = True
                self.logger.warning("Circuit breaker opened due to repeated failures")
    
    def _record_success(self) -> None:
        """记录成功，重置熔断器"""
        with self._circuit_lock:
            self._failure_count = 0
            self._circuit_open = False
    
    def send_event(self, event: Event, timeout: Optional[float] = None) -> bool:
        """
        同步发送单个 Event
        
        Args:
            event: 要发送的 Event
            timeout: 超时时间（秒）
            
        Returns:
            bool: 发送成功返回 True
        """
        self._update_jwt_token()
        
        if not self._check_circuit_breaker():
            self.logger.warning("Circuit breaker open, dropping event")
            return False
        
        try:
            url = self._get_event_url()
            payload = event.model_dump(mode="json", exclude_none=True, by_alias=True)
            
            response = self.session.post(
                url,
                json=payload,
                timeout=timeout or self.config.timeout,
            )
            
            if response.status_code == 200 or response.status_code == 201:
                self._record_success()
                return True
            else:
                self._record_failure()
                self.logger.error(
                    "Failed to send event: status=%d, response=%s",
                    response.status_code, response.text
                )
                return False
                
        except requests.exceptions.Timeout:
            self._record_failure()
            self.logger.error("Send event timeout")
            return False
        except requests.exceptions.ConnectionError as e:
            self._record_failure()
            self.logger.error("Connection error sending event: %s", e)
            return False
        except Exception as e:
            self._record_failure()
            self.logger.exception("Unexpected error sending event: %s", e)
            return False
    
    def send_event_async(self, event: Event) -> None:
        """
        异步发送 Event（加入队列，由后台线程批量发送）
        """
        with self._queue_lock:
            self._event_queue.append(event)
            if len(self._event_queue) >= self._max_batch_size:
                self._flush_queue()
    
    def _flush_queue(self) -> None:
        """刷新事件队列"""
        if not self._event_queue:
            return
        
        batch = self._event_queue[:self._max_batch_size]
        self._event_queue = self._event_queue[self._max_batch_size:]
        
        try:
            self._send_batch(batch)
        except Exception as e:
            self.logger.exception("Failed to flush event queue: %s", e)
            # 重新加入队列
            with self._queue_lock:
                self._event_queue = batch + self._event_queue
    
    def _send_batch(self, events: List[Event]) -> bool:
        """发送批量事件"""
        self._update_jwt_token()
        
        if not self._check_circuit_breaker():
            self.logger.warning("Circuit breaker open, dropping batch")
            return False
        
        try:
            url = self._get_event_url()
            payload = [e.model_dump(mode="json", exclude_none=True, by_alias=True) for e in events]
            
            response = self.session.post(
                url,
                json=events,
                timeout=self.config.timeout,
            )
            
            if response.status_code == 200 or response.status_code == 201:
                self._record_success()
                return True
            else:
                self._record_failure()
                self.logger.error(
                    "Failed to send batch: status=%d, response=%s",
                    response.status_code, response.text
                )
                return False
                
        except Exception as e:
            self._record_failure()
            self.logger.exception("Failed to send batch: %s", e)
            return False
    
    def _flush_worker(self) -> None:
        """后台刷新线程"""
        while not self._stop_flush.is_set():
            time.sleep(self._flush_interval)
            if self._event_queue:
                self._flush_queue()
    
    def start_flush_worker(self) -> None:
        """启动后台刷新线程"""
        if self._flush_thread is None or not self._flush_thread.is_alive():
            self._stop_flush.clear()
            self._flush_thread = threading.Thread(target=self._flush_worker, daemon=True, name="coredata-flush")
            self._flush_thread.start()
    
    def stop_flush_worker(self) -> None:
        """停止后台刷新线程"""
        self._stop_flush.set()
        if self._flush_thread and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=5.0)
        # 最后刷新一次
        self._flush_queue()
    
    def close(self) -> None:
        """关闭客户端"""
        self.stop_flush_worker()
        self.session.close()
    
    def get_circuit_breaker_status(self) -> Dict[str, Any]:
        """获取熔断器状态"""
        with self._circuit_lock:
            return {
                "circuit_open": self._circuit_open,
                "failure_count": self._failure_count,
                "last_failure_time": self._last_failure_time,
            }


def create_coredata_client(
    base_url: str,
    jwt_token_provider: Optional[Callable[[], Optional[str]]] = None,
    timeout: float = 10.0,
    max_retries: int = 3,
    logger: Optional[logging.Logger] = None,
) -> CoreDataClient:
    """
    创建 Core Data 客户端的便捷函数
    """
    config = CoreDataClientConfig(
        base_url=base_url,
        timeout=timeout,
        max_retries=3,
    )
    return CoreDataClient(config, jwt_token_provider, logger)