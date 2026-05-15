# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\core\constants.py。"""

from __future__ import annotations


# 定义ChatConstants。
class ChatConstants:
    """统一维护聊天域常量，避免多处散落硬编码。"""

    DEFAULT_SESSION_TITLE = "知搭"
    DEFAULT_REPLY_TYPE = "MESSAGE"
    ASSISTANT_CHUNK_EVENT_TYPE = "assistant_chunk"
    ASSISTANT_TURN_COMPLETE_EVENT_TYPE = "assistant_turn_complete"
    ASSISTANT_TURN_FAILED_EVENT_TYPE = "assistant_turn_failed"
    DYNAMIC_VIEW_CLUE_UNLOCKED_EVENT_TYPE = "dynamic_view_clue_unlocked"

