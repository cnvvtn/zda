# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\repositories\dynamic_view_progress_repository.py。"""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import DynamicViewProgressArchive


# 定义DynamicViewProgressRepository。
class DynamicViewProgressRepository:
    """动态视图线索进度仓储，按 user_id 记录单个用户在单条视图里的已解锁线索。"""

    # 执行list unlocked clue steps相关逻辑。
    def list_unlocked_clue_steps(
        self,
        db: Session,
        *,
        game_archive_id: int,
        user_id: str,
    ) -> dict[str, int]:
        """按首次点亮顺序返回 clue_key 到解锁步数的映射。"""
        archives = (
            # 执行query相关逻辑。
            db.query(DynamicViewProgressArchive)
            .filter(
                DynamicViewProgressArchive.game_archive_id == game_archive_id,
                DynamicViewProgressArchive.user_id == user_id,
                DynamicViewProgressArchive.is_unlocked == 1,
            )
            .order_by(
                DynamicViewProgressArchive.created_at.asc(),
                DynamicViewProgressArchive.id.asc(),
            )
            .all()
        )
        return {
            archive.clue_key: index
            for index, archive in enumerate(archives, start=1)
        }

    # 执行list unlocked clue keys相关逻辑。
    def list_unlocked_clue_keys(
        self,
        db: Session,
        *,
        game_archive_id: int,
        user_id: str,
    ) -> set[str]:
        """读取当前用户在指定动态视图里已点亮的线索键集合。"""
        archives = (
            # 执行query相关逻辑。
            db.query(DynamicViewProgressArchive)
            .filter(
                DynamicViewProgressArchive.game_archive_id == game_archive_id,
                DynamicViewProgressArchive.user_id == user_id,
                DynamicViewProgressArchive.is_unlocked == 1,
            )
            .all()
        )
        return {archive.clue_key for archive in archives}

    # 执行unlock clues相关逻辑。
    def unlock_clues(
        self,
        db: Session,
        *,
        game_archive_id: int,
        user_id: str,
        matched_message_id: str,
        clue_keys: list[str],
    ) -> list[str]:
        """批量点亮当前用户在指定动态视图里的线索，并返回本轮真正新增点亮的线索键。"""
        normalized_clue_keys = [clue_key.strip() for clue_key in clue_keys if clue_key.strip()]
        if not normalized_clue_keys:
            return []
        unlocked_clue_keys = self.list_unlocked_clue_keys(
            db,
            game_archive_id=game_archive_id,
            user_id=user_id,
        )
        new_clue_keys = [
            clue_key for clue_key in normalized_clue_keys if clue_key not in unlocked_clue_keys
        ]
        if not new_clue_keys:
            return []
        # 执行add all相关逻辑。
        db.add_all(
            [
                DynamicViewProgressArchive(
                    game_archive_id=game_archive_id,
                    user_id=user_id,
                    clue_key=clue_key,
                    is_unlocked=1,
                    matched_message_id=matched_message_id.strip(),
                )
                for clue_key in new_clue_keys
            ]
        )
        # 执行commit相关逻辑。
        db.commit()
        return new_clue_keys

    # 执行delete game progress相关逻辑。
    def delete_game_progress(
        self,
        db: Session,
        *,
        game_archive_id: int,
    ) -> None:
        """按游戏动态视图主键删除全部线索进度。"""
        # 执行execute相关逻辑。
        db.execute(
            # 执行delete相关逻辑。
            delete(DynamicViewProgressArchive).where(
                DynamicViewProgressArchive.game_archive_id == game_archive_id,
            )
        )
        # 执行commit相关逻辑。
        db.commit()
