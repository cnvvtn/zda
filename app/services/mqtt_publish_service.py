# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\services\mqtt_publish_service.py。"""

from __future__ import annotations

import asyncio
import json
from threading import Event, Lock

import paho.mqtt.client as mqtt

from app.core.settings import settings


# 定义MqttPublishService。
class MqttPublishService:
    """把 assistant 消息发布到用户专属 MQTT 频道。"""

    # 执行init相关逻辑。
    def __init__(self) -> None:
        """在进程内复用一个 MQTT 连接，避免每条消息都重建 TCP 连接。"""
        self._client: mqtt.Client | None = None
        self._client_lock = Lock()
        self._publish_lock = Lock()
        self._connected_event = Event()
        self._connect_result_event = Event()
        self._connect_error_message: str | None = None
        self._connecting = False

    # 执行publish相关逻辑。
    async def publish(self, topic: str, payload: dict[str, object]) -> None:
        """把异步服务层调用桥接到同步 paho 客户端。"""
        # 执行to thread相关逻辑。
        await asyncio.to_thread(self._publish_blocking, topic, payload)

    # 执行aclose相关逻辑。
    async def aclose(self) -> None:
        """应用关闭时主动断开 MQTT 长连接。"""
        # 执行to thread相关逻辑。
        await asyncio.to_thread(self._close_blocking)

    # 执行publish blocking相关逻辑。
    def _publish_blocking(self, topic: str, payload: dict[str, object]) -> None:
        """复用进程内 MQTT 客户端发送消息，并串行等待 broker 确认完成。"""
        with self._publish_lock:
            # 执行ensure client相关逻辑。
            client = self._ensure_client()
            # 执行publish相关逻辑。
            publish_info = client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1)
            # 执行wait for publish相关逻辑。
            publish_info.wait_for_publish()
            if publish_info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError(f"MQTT publish failed: rc={publish_info.rc}")

    # 执行ensure client相关逻辑。
    def _ensure_client(self) -> mqtt.Client:
        """确保当前进程内存在一个可复用的已连接 MQTT 客户端。"""
        with self._client_lock:
            if self._client is None:
                # 执行build client相关逻辑。
                self._client = self._build_client()
            if self._connected_event.is_set():
                return self._client
            if not self._connecting:
                # 执行clear相关逻辑。
                self._connected_event.clear()
                # 执行clear相关逻辑。
                self._connect_result_event.clear()
                self._connect_error_message = None
                self._connecting = True
                # 执行connect相关逻辑。
                self._client.connect(settings.mqtt.host, settings.mqtt.port, keepalive=30)
        if not self._connect_result_event.wait(timeout=5):
            with self._client_lock:
                self._connecting = False
            raise TimeoutError("MQTT connect timeout")
        if not self._connected_event.is_set():
            raise RuntimeError(self._connect_error_message or "MQTT connect failed")
        return self._client

    # 执行build client相关逻辑。
    def _build_client(self) -> mqtt.Client:
        """构造并启动一个长期运行的 paho 客户端。"""
        # 执行Client相关逻辑。
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            protocol=mqtt.MQTTv311,
        )
        if settings.mqtt.username:
            # 执行username pw set相关逻辑。
            client.username_pw_set(settings.mqtt.username, settings.mqtt.password)
        client.on_connect = self._handle_connect
        client.on_disconnect = self._handle_disconnect
        # 执行loop start相关逻辑。
        client.loop_start()
        return client

    # 执行handle connect相关逻辑。
    def _handle_connect(self, client, userdata, flags, reason_code, properties) -> None:
        """连接完成后记录结果，让发布线程决定是继续还是抛错。"""
        with self._client_lock:
            self._connecting = False
        if reason_code == 0:
            # 执行set相关逻辑。
            self._connected_event.set()
        else:
            self._connect_error_message = f"MQTT connect failed: rc={reason_code}"
            # 执行clear相关逻辑。
            self._connected_event.clear()
        # 执行set相关逻辑。
        self._connect_result_event.set()

    # 执行handle disconnect相关逻辑。
    def _handle_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        """连接断开时立即清掉连接态，供下一次发布自动重连。"""
        # 执行clear相关逻辑。
        self._connected_event.clear()
        self._connecting = False

    # 执行close blocking相关逻辑。
    def _close_blocking(self) -> None:
        """释放进程内复用的 MQTT 客户端。"""
        with self._publish_lock:
            with self._client_lock:
                if self._client is None:
                    return
                try:
                    # 执行disconnect相关逻辑。
                    self._client.disconnect()
                finally:
                    # 执行loop stop相关逻辑。
                    self._client.loop_stop()
                    self._client = None
                    # 执行clear相关逻辑。
                    self._connected_event.clear()
                    # 执行clear相关逻辑。
                    self._connect_result_event.clear()
                    self._connect_error_message = None
                    self._connecting = False
