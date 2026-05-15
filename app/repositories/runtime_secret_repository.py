# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\repositories\runtime_secret_repository.py。"""

from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import WebsiteContent


RUNTIME_SECRET_CONTENT_KEY = "runtime_secrets"


# 定义RuntimeSecretRepository。
class RuntimeSecretRepository:
    """从数据库读取运行密钥配置。"""

    # 执行get secret data相关逻辑。
    def get_secret_data(self, db: Session) -> dict[str, str]:
        """从 website_content.runtime_secrets 读取运行密钥 JSON。"""
        record = (
            db.query(WebsiteContent)
            .filter(WebsiteContent.content_key == RUNTIME_SECRET_CONTENT_KEY)
            .first()
        )
        if record is None:
            raise HTTPException(status_code=500, detail="数据库 runtime_secrets 未配置")
        try:
            raw_secret_data = json.loads(record.content_json)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="数据库 runtime_secrets 不是合法 JSON")
        if not isinstance(raw_secret_data, dict):
            raise HTTPException(status_code=500, detail="数据库 runtime_secrets 必须是 JSON 对象")
        return {str(key): str(value).strip() for key, value in raw_secret_data.items() if value is not None}

    # 执行get secret相关逻辑。
    def get_secret(self, db: Session, secret_key: str) -> str:
        """从运行密钥 JSON 读取单个密钥。"""
        secret_data = self.get_secret_data(db)
        if secret_key not in secret_data:
            raise HTTPException(status_code=500, detail=f"数据库 runtime_secrets.{secret_key} 未配置")
        value = secret_data[secret_key]
        if not value:
            raise HTTPException(status_code=500, detail=f"数据库 runtime_secrets.{secret_key} 未配置")
        return str(value).strip()
