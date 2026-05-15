# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\core\logging_config.py。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from uvicorn.config import LOGGING_CONFIG


# 执行build log config相关逻辑。
def build_log_config() -> dict[str, Any]:
    """构建统一的 Uvicorn 与应用日志配置，避免控制台只剩默认 access log。"""
    # 执行deepcopy相关逻辑。
    log_config = deepcopy(LOGGING_CONFIG)
    log_config["disable_existing_loggers"] = False
    log_config["formatters"]["default"] = {
        "()": "uvicorn.logging.DefaultFormatter",
        "fmt": "%(asctime)s | %(levelprefix)s | %(name)s | %(message)s",
        "datefmt": "%Y-%m-%d %H:%M:%S",
        "use_colors": True,
    }
    log_config["formatters"]["access"] = {
        "()": "uvicorn.logging.AccessFormatter",
        "fmt": "%(asctime)s | %(levelprefix)s | %(name)s | %(client_addr)s | %(request_line)s | %(status_code)s",
        "datefmt": "%Y-%m-%d %H:%M:%S",
        "use_colors": True,
    }
    log_config["handlers"]["default"] = {
        "formatter": "default",
        "class": "logging.StreamHandler",
        "stream": "ext://sys.stdout",
    }
    log_config["handlers"]["access"] = {
        "formatter": "access",
        "class": "logging.StreamHandler",
        "stream": "ext://sys.stdout",
    }
    log_config["loggers"][""] = {
        "handlers": ["default"],
        "level": "INFO",
    }
    log_config["loggers"]["app"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    log_config["loggers"]["uvicorn"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    log_config["loggers"]["uvicorn.error"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    log_config["loggers"]["uvicorn.access"] = {
        "handlers": ["access"],
        "level": "INFO",
        "propagate": False,
    }
    log_config["loggers"]["openai._base_client"] = {
        "handlers": ["default"],
        "level": "WARNING",
        "propagate": False,
    }
    return log_config
