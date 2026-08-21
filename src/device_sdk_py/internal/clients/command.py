# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Core Command Client - 通过 EdgeX Core Command 下发命令

对应 Go SDK: github.com/edgexfoundry/device-sdk-go/v4/internal/clients/command
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

from ..common.consts import CONTENT_TYPE_JSON
from ..common.utils import EdgexError, EdgexErrorKind, create_edgx_error
from ...models import CommandRequest, CommandValue


logger = logging.getLogger(__name__)


@dataclass
class CoreCommandClientConfig:
    """Core Command 客户端配置"""
    base_url: str
    timeout: float = 10.0
    max_retries: int = 3
    backoff_factor: float = 0.5
    max_connections: int = 10
    jwt_token: Optional[str] = None
    ca_cert: Optional[str] = None
    client_cert: Optional[str] = None
    client_key: Optional[str] = None


class CoreCommandClient:
    """
    Core Command 客户端 - 通过 Core Command 服务下发命令到设备
    
    功能：
    - 同步/异步发送命令请求
    - 自动重试 + 指数退避
    - 熔断器保护
    - JWT Token 自动刷新
    - 熔断器状态监控
    """
    
    def __init__(
        self,
        config: CoreCommandClientConfig,
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
            allowed_methods=["POST", "PUT"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=self.retry_strategy,
            pool_connections=config.max_connections,
            pool_maxsize=config.max_connections,
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
    def _configure_session(self) -> None:
        """配置 HTTP 会话"""
        self.session.headers.update({
            "Content-Type": CONTENT_TYPE_JSON,
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
    
    def _get_command_url(self, device_name: str, command_name: str) -> str:
        """获取命令发送端点"""
        base = self.config.base_url.rstrip("/") + "/"
        return urljoin(base, f"api/v3/device/name/{device_name}/command/{command_name}")
    
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
    
    def _get_command_url(self, device_name: str, command_name: str) -> str:
        """获取命令发送端点"""
        base = self.config.base_url.rstrip("/") + "/"
        return urljoin(base, f"api/v3/device/name/{device_name}/command/{command_name}")
    
    def send_command(self, device_name: str, command_name: str, 
                     request: CommandRequest, timeout: Optional[float] = None) -> Optional[List[CommandValue]]:
        """
        同步发送命令请求
        
        Args:
            device_name: 设备名称
            command_name: 命令名称
            request: 命令请求参数
            timeout: 超时时间（秒）
            
        Returns:
            List[CommandValue] | None: 成功返回命令响应值列表，失败返回 None
        """
        self._update_jwt_token()
        
        if not self._check_circuit_breaker():
            self.logger.warning("Circuit breaker open, dropping command")
            return None
        
        try:
            url = self._get_command_url(device_name, command_name)
            payload = request.model_dump(mode="json", exclude_none=True, by_alias=True)
            
            response = self.session.put(
                url,
                json=payload,
                timeout=timeout or self.config.timeout,
            )
            
            if response.status_code == 200 or response.status_code == 201:
                self._record_success()
                try:
                    response_data = response.json()
                    # 解析响应中的 CommandValue 列表
                    command_values = []
                    for item in response_data.get("commandValues", []):
                        cmd_val = CommandValue(**item)
                        command_values.append(cmd_val)
                    return command_values
                except Exception as e:
                    self.logger.error("Failed to parse command response: %s", e)
                    return None
            else:
                self._record_failure()
                self.logger.error(
                    "Failed to send command: status=%d, response=%s",
                    response.status_code, response.text
                )
                return None
                
        except requests.exceptions.Timeout:
            self._record_failure()
            self.logger.error("Send command timeout")
            return None
        except requests.exceptions.ConnectionError as e:
            self._record_failure()
            self.logger.error("Connection error sending command: %s", e)
            return None
        except Exception as e:
            self._record_failure()
            self.logger.exception("Unexpected error sending command: %s", e)
            return None
    
    def send_command_async(self, device_name: str, command_name: str,
                           request: CommandRequest, callback: Optional[Callable[[Optional[List[CommandValue]]], None]] = None) -> None:
        """
        异步发送命令（在后台线程执行，通过回调返回结果）
        """
        def _async_send():
            result = self.send_command(device_name, command_name, request)
            if callback:
                try:
                    callback(result)
                except Exception as e:
                    self.logger.exception("Callback error: %s", e)
        
        thread = threading.Thread(target=_async_send, daemon=True, name="corecommand-send")
        thread.start()
    
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
    
    def _update_jwt_token(self) -> None:
        """更新 JWT Token"""
        if self.jwt_token_provider:
            token = self.jwt_token_provider()
            if token:
                self.session.headers["Authorization"] = f"Bearer {token}"
    
    def close(self) -> None:
        """关闭客户端"""
        self.session.close()
    
    def get_circuit_breaker_status(self) -> Dict[str, Any]:
        """获取熔断器状态"""
        with self._circuit_lock:
            return {
                "circuit_open": self._circuit_open,
                "failure_count": self._failure_count,
                "last_failure_time": self._last_failure_time,
            }


def create_corecommand_client(
    base_url: str,
    jwt_token_provider: Optional[Callable[[], Optional[str]]] = None,
    timeout: float = 10.0,
    max_retries: int = 3,
    logger: Optional[logging.Logger] = None,
) -> CoreCommandClient:
    """
    创建 Core Command 客户端的便捷函数
    """
    config = CoreCommandClientConfig(
        base_url=base_url,
        timeout=timeout,
        max_retries=3,
    )
    return CoreCommandClient(config, jwt_token_provider, logger)