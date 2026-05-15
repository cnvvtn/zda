# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\repositories\dynamic_view_comment_repository.py。"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import and_, delete, desc
from sqlalchemy.orm import Query, Session

from app.db.models import AppUser, DynamicViewComment
from app.features.dynamic_view.schemas import (
    DynamicViewCommentItem,
    DynamicViewCommentPage,
    DynamicViewCommentUser,
)


# 定义DynamicViewCommentRepository。
class DynamicViewCommentRepository:
    """动态视图评论仓储，负责顶级评论分页与回复树装配。"""

    # 执行list archive comments相关逻辑。
    def list_archive_comments(
        self,
        db: Session,
        *,
        archive_id: int,
        view_type: str,
        cursor_id: int | None = None,
        limit: int = 10,
    ) -> DynamicViewCommentPage:
        """按顶级评论分页返回评论树，回复评论统一挂在父评论 children 下。"""
        # 执行max相关逻辑。
        normalized_limit = max(1, min(limit, 30))
        root_query = self._build_archive_comment_query(
            db,
            archive_id=archive_id,
            view_type=view_type,
        ).filter(
            DynamicViewComment.pid == 0,
        )
        if cursor_id is not None:
            # 执行filter相关逻辑。
            root_query = root_query.filter(DynamicViewComment.id < cursor_id)
        root_rows = (
            # 执行order by相关逻辑。
            root_query.order_by(
                desc(DynamicViewComment.is_pinned),
                desc(DynamicViewComment.created_at),
                desc(DynamicViewComment.id),
            )
            .limit(normalized_limit + 1)
            .all()
        )
        # 执行len相关逻辑。
        has_more = len(root_rows) > normalized_limit
        visible_root_rows = root_rows[:normalized_limit]
        root_ids = [int(comment.id) for comment, _ in visible_root_rows]
        reply_rows_by_parent_id = self._load_reply_rows_by_parent_ids(
            db,
            archive_id=archive_id,
            view_type=view_type,
            parent_ids=root_ids,
        )
        items = [
            self._build_comment_item(
                comment=comment,
                user=user,
                reply_rows_by_parent_id=reply_rows_by_parent_id,
            )
            for comment, user in visible_root_rows
        ]
        next_cursor = items[-1].id if has_more and items else None
        return DynamicViewCommentPage(
            items=items,
            nextCursor=next_cursor,
            hasMore=has_more,
        )

    # 执行count archive comments相关逻辑。
    def count_archive_comments(
        self,
        db: Session,
        *,
        archive_id: int,
        view_type: str,
    ) -> int:
        """统计单条动态视图的有效评论总数，包含回复评论。"""
        return (
            # 执行query相关逻辑。
            db.query(DynamicViewComment)
            .filter(
                # 执行and相关逻辑。
                and_(
                    DynamicViewComment.archive_id == archive_id,
                    DynamicViewComment.view_type == view_type,
                    DynamicViewComment.status == 1,
                )
            )
            .count()
        )

    # 执行delete archive comments相关逻辑。
    def delete_archive_comments(
        self,
        db: Session,
        *,
        archive_id: int,
        view_type: str,
    ) -> None:
        """按动态视图主键和类型删除全部评论。"""
        db.execute(
            delete(DynamicViewComment).where(
                DynamicViewComment.archive_id == archive_id,
                DynamicViewComment.view_type == view_type,
            )
        )
        db.commit()

    # 执行create archive comment相关逻辑。
    def create_archive_comment(
        self,
        db: Session,
        *,
        archive_id: int,
        view_type: str,
        user_key: str,
        content: str,
        pid: int = 0,
    ) -> DynamicViewCommentItem:
        """创建一条动态视图评论或回复，并返回可直接给前端展示的树节点。"""
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("评论内容不能为空")
        author = self._get_active_user_by_user_key(db, user_key=user_key)
        parent_comment: DynamicViewComment | None = None
        if pid > 0:
            parent_comment = self._get_archive_comment_or_raise(
                db,
                archive_id=archive_id,
                view_type=view_type,
                comment_id=pid,
            )
        comment = DynamicViewComment(
            archive_id=archive_id,
            view_type=view_type,
            app_user_id=int(author.id),
            pid=max(0, pid),
            content=normalized_content,
            like_count=0,
            reply_count=0,
            status=1,
            is_pinned=0,
            ip_address=author.ip_address or "",
            ip_location=author.ip_location or "",
        )
        # 执行add相关逻辑。
        db.add(comment)
        if parent_comment is not None:
            parent_comment.reply_count += 1
        # 当前仓储只负责把评论和父评论回复数写入当前事务，统一由服务层决定提交时机。
        db.flush()
        # 执行refresh相关逻辑。
        db.refresh(comment)
        if parent_comment is not None:
            # 执行refresh相关逻辑。
            db.refresh(parent_comment)
        return self._build_comment_item(
            comment=comment,
            user=author,
            reply_rows_by_parent_id={},
        )

    # 执行build Archive Comment Query相关逻辑。
    def _build_archive_comment_query(
        self,
        db: Session,
        *,
        archive_id: int,
        view_type: str,
    ) -> Query:
        """构建评论基础查询，统一收口用户与状态过滤条件。"""
        return (
            # 执行query相关逻辑。
            db.query(DynamicViewComment, AppUser)
            .join(AppUser, AppUser.id == DynamicViewComment.app_user_id)
            .filter(
                DynamicViewComment.archive_id == archive_id,
                DynamicViewComment.view_type == view_type,
                DynamicViewComment.status == 1,
                AppUser.status == 1,
                AppUser.deleted == 0,
            )
        )

    # 执行get Active User By User Key相关逻辑。
    def _get_active_user_by_user_key(self, db: Session, *, user_key: str) -> AppUser:
        """按业务用户标识读取有效评论用户，缺失时直接抛错。"""
        user = (
            db.query(AppUser)
            .filter(
                AppUser.user_key == user_key,
                AppUser.status == 1,
                AppUser.deleted == 0,
            )
            .first()
        )
        if user is None:
            raise ValueError(f"未找到可用评论用户：user_key={user_key}")
        return user

    # 执行get Archive Comment Or Raise相关逻辑。
    def _get_archive_comment_or_raise(
        self,
        db: Session,
        *,
        archive_id: int,
        view_type: str,
        comment_id: int,
    ) -> DynamicViewComment:
        """按评论主键读取当前动态视图下的有效评论，缺失时直接抛错。"""
        comment = (
            db.query(DynamicViewComment)
            .filter(
                DynamicViewComment.id == comment_id,
                DynamicViewComment.archive_id == archive_id,
                DynamicViewComment.view_type == view_type,
                DynamicViewComment.status == 1,
            )
            .first()
        )
        if comment is None:
            raise ValueError(
                f"未找到可回复的动态视图评论：archive_id={archive_id}, comment_id={comment_id}"
            )
        return comment

    # 执行load Reply Rows By Parent Ids相关逻辑。
    def _load_reply_rows_by_parent_ids(
        self,
        db: Session,
        *,
        archive_id: int,
        view_type: str,
        parent_ids: list[int],
    ) -> dict[int, list[tuple[DynamicViewComment, AppUser]]]:
        """按父评论批量递归抓取回复，直到当前页根评论的整棵回复树完整展开。"""
        reply_rows_by_parent_id: dict[
            int, list[tuple[DynamicViewComment, AppUser]]
        ] = defaultdict(list)
        pending_parent_ids = [parent_id for parent_id in parent_ids if parent_id > 0]
        visited_parent_ids: set[int] = set()
        while pending_parent_ids:
            batch_parent_ids = [
                parent_id
                for parent_id in pending_parent_ids
                if parent_id not in visited_parent_ids
            ]
            if not batch_parent_ids:
                break
            visited_parent_ids.update(batch_parent_ids)
            reply_rows = (
                self._build_archive_comment_query(
                    db,
                    archive_id=archive_id,
                    view_type=view_type,
                )
                .filter(DynamicViewComment.pid.in_(batch_parent_ids))
                .order_by(
                    DynamicViewComment.created_at.asc(),
                    DynamicViewComment.id.asc(),
                )
                .all()
            )
            pending_parent_ids = []
            for comment, user in reply_rows:
                parent_id = int(comment.pid or 0)
                reply_rows_by_parent_id[parent_id].append((comment, user))
                pending_parent_ids.append(int(comment.id))
        return dict(reply_rows_by_parent_id)

    # 执行build Comment Item相关逻辑。
    def _build_comment_item(
        self,
        *,
        comment: DynamicViewComment,
        user: AppUser,
        reply_rows_by_parent_id: dict[int, list[tuple[DynamicViewComment, AppUser]]],
    ) -> DynamicViewCommentItem:
        """把数据库评论行递归转换成接口评论树节点。"""
        comment_id = int(comment.id)
        return DynamicViewCommentItem(
            id=comment_id,
            pid=int(comment.pid or 0),
            content=comment.content,
            likeCount=comment.like_count,
            replyCount=comment.reply_count,
            createdAt=comment.created_at.isoformat(),
            user=DynamicViewCommentUser(
                userId=user.user_key,
                nickname=user.nickname,
                avatar=user.avatar,
                ipLocation=comment.ip_location or user.ip_location,
            ),
            children=[
                self._build_comment_item(
                    comment=child_comment,
                    user=child_user,
                    reply_rows_by_parent_id=reply_rows_by_parent_id,
                )
                for child_comment, child_user in reply_rows_by_parent_id.get(
                    comment_id, []
                )
            ],
        )
