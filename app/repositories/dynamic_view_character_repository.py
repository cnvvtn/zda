# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\repositories\dynamic_view_character_repository.py。"""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import DynamicViewCharacterArchive
from app.features.dynamic_view.schemas import DynamicViewCharacter, DynamicViewRoleItem


# 定义DynamicViewCharacterRepository。
class DynamicViewCharacterRepository:
    """动态视图角色仓储，负责按视图覆盖写入角色清单。"""

    # 执行replace archive characters相关逻辑。
    def replace_archive_characters(
        self,
        db: Session,
        *,
        owner_type: str,
        owner_id: int,
        characters: list[DynamicViewCharacter],
    ) -> None:
        """按动态视图主键覆盖写入角色列表，避免同一视图残留旧角色。"""
        # 执行execute相关逻辑。
        db.execute(
            # 执行delete相关逻辑。
            delete(DynamicViewCharacterArchive).where(
                DynamicViewCharacterArchive.owner_type == owner_type,
                DynamicViewCharacterArchive.owner_id == owner_id,
            )
        )
        if characters:
            # 执行add all相关逻辑。
            db.add_all(
                [
                    DynamicViewCharacterArchive(
                        owner_type=owner_type,
                        owner_id=owner_id,
                        role_name=character.role_name,
                        category_name=character.category_name,
                        icon=character.icon,
                        persona_prompt=character.persona_prompt,
                        personality=character.personality,
                        scenario=character.scenario,
                        nsfw_setting=character.nsfw_setting,
                        author=character.author,
                    )
                    for character in characters
                ]
            )
        # 执行commit相关逻辑。
        db.commit()

    # 执行list archive roles相关逻辑。
    def list_archive_roles(
        self,
        db: Session,
        *,
        owner_type: str,
        owner_id: int,
        limit: int = 12,
    ) -> list[DynamicViewRoleItem]:
        """读取动态视图详情页需要的角色卡片列表。"""
        role_archives = (
            # 执行query相关逻辑。
            db.query(DynamicViewCharacterArchive)
            .filter(
                DynamicViewCharacterArchive.owner_type == owner_type,
                DynamicViewCharacterArchive.owner_id == owner_id,
            )
            .order_by(DynamicViewCharacterArchive.id.asc())
            .limit(limit)
            .all()
        )
        return [
            DynamicViewRoleItem(
                roleId=role_archive.id,
                roleName=role_archive.role_name,
                personaPrompt=role_archive.persona_prompt,
                categoryName=role_archive.category_name,
                icon=role_archive.icon,
                personality=role_archive.personality,
                scenario=role_archive.scenario,
                nsfwSetting=role_archive.nsfw_setting,
                author=role_archive.author,
            )
            for role_archive in role_archives
        ]

    # 执行get archive role by id相关逻辑。
    def get_archive_role_by_id(
        self,
        db: Session,
        *,
        role_id: int,
    ) -> DynamicViewCharacterArchive | None:
        """按角色主键读取动态视图角色存档。"""
        return (
            # 执行query相关逻辑。
            db.query(DynamicViewCharacterArchive)
            .filter(DynamicViewCharacterArchive.id == role_id)
            .first()
        )

    # 执行delete archive characters相关逻辑。
    def delete_archive_characters(
        self,
        db: Session,
        *,
        owner_type: str,
        owner_id: int,
    ) -> None:
        """按视图归属删除整组角色，避免失败任务残留脏角色卡。"""
        # 执行execute相关逻辑。
        db.execute(
            # 执行delete相关逻辑。
            delete(DynamicViewCharacterArchive).where(
                DynamicViewCharacterArchive.owner_type == owner_type,
                DynamicViewCharacterArchive.owner_id == owner_id,
            )
        )
        # 执行commit相关逻辑。
        db.commit()
