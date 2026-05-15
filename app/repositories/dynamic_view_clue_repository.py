# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\repositories\dynamic_view_clue_repository.py。"""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import DynamicViewClueArchive
from app.features.dynamic_view.schemas import DynamicViewClueItem


# 定义DynamicViewClueRepository。
class DynamicViewClueRepository:
    """动态视图线索仓储，负责按游戏视图覆盖写入线索清单。"""

    # 执行replace game clues相关逻辑。
    def replace_game_clues(
        self,
        db: Session,
        *,
        game_archive_id: int,
        clues: list[DynamicViewClueItem],
    ) -> None:
        """按游戏动态视图主键覆盖写入线索列表。"""
        # 执行execute相关逻辑。
        db.execute(
            # 执行delete相关逻辑。
            delete(DynamicViewClueArchive).where(
                DynamicViewClueArchive.game_archive_id == game_archive_id,
            )
        )
        if clues:
            # 执行add all相关逻辑。
            db.add_all(
                [
                    DynamicViewClueArchive(
                        game_archive_id=game_archive_id,
                        clue_key=clue.clue_key,
                        clue_title=clue.clue_title,
                        clue_content=clue.clue_content,
                    )
                    for clue in clues
                ]
            )
        # 执行commit相关逻辑。
        db.commit()

    # 执行list game clues相关逻辑。
    def list_game_clues(
        self,
        db: Session,
        *,
        game_archive_id: int,
    ) -> list[DynamicViewClueItem]:
        """读取游戏动态视图的线索列表，不再按预设顺序做业务判断。"""
        archives = (
            # 执行query相关逻辑。
            db.query(DynamicViewClueArchive)
            .filter(DynamicViewClueArchive.game_archive_id == game_archive_id)
            .order_by(DynamicViewClueArchive.id.asc())
            .all()
        )
        return [
            DynamicViewClueItem(
                clueKey=archive.clue_key,
                clueTitle=archive.clue_title,
                clueContent=archive.clue_content,
                unlocked=False,
            )
            for archive in archives
        ]

    # 执行delete game clues相关逻辑。
    def delete_game_clues(
        self,
        db: Session,
        *,
        game_archive_id: int,
    ) -> None:
        """按游戏动态视图主键删除全部线索。"""
        # 执行execute相关逻辑。
        db.execute(
            # 执行delete相关逻辑。
            delete(DynamicViewClueArchive).where(
                DynamicViewClueArchive.game_archive_id == game_archive_id,
            )
        )
        # 执行commit相关逻辑。
        db.commit()
