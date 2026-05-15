# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\clients\api_key_provider.py。"""

from __future__ import annotations

import threading

from app.core.settings import ModelProfile


# 定义ApiKeyProvider。
class ApiKeyProvider:
    """统一处理多 Key 轮询，避免每个客户端各自维护一套计数器。"""

    _key_counters: dict[str, int] = {}
    # 执行Lock相关逻辑。
    _counter_lock = threading.Lock()

    # 执行get ordered keys相关逻辑。
    def get_ordered_keys(self, profile: ModelProfile) -> list[str]:
        """返回按轮询顺序排列后的 Key 列表。"""
        api_keys = profile.api_keys
        if not api_keys:
            raise RuntimeError(f"模型配置缺少数据库 API Key：{profile.profile_key}")
        if len(api_keys) == 1:
            return api_keys
        counter_key = f"{profile.profile_key}:{profile.base_url}:{profile.model}"
        with self._counter_lock:
            # 执行get相关逻辑。
            start = self._key_counters.get(counter_key, 0)
            self._key_counters[counter_key] = (start + 1) % len(api_keys)
        return api_keys[start:] + api_keys[:start]
