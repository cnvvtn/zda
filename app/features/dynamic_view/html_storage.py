# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\features\dynamic_view\html_storage.py。"""

from __future__ import annotations

import shutil
from pathlib import Path

_DYNAMIC_VIEW_HTML_ROOT = Path(__file__).resolve().parents[3] / "storage" / "dynamic_view_html"
_DYNAMIC_VIEW_RECYCLE_HTML_ROOT = Path(__file__).resolve().parents[3] / "storage" / "dynamic_view_recycle_bin" / "dynamic_view_html"
_DYNAMIC_VIEW_MUSIC_ROOT = Path(__file__).resolve().parents[3] / "music"
_DYNAMIC_VIEW_RECYCLE_MUSIC_ROOT = Path(__file__).resolve().parents[3] / "storage" / "dynamic_view_recycle_bin" / "music"


# 执行resolve dynamic view html file path相关逻辑。
def resolve_dynamic_view_html_file_path(*, view_type: str, archive_id: int) -> Path:
    """按动态视图类型与存档 ID 解析最终 HTML 文件路径。"""
    normalized_view_type = view_type.strip().lower()
    if normalized_view_type not in {"game", "knowledge"}:
        raise ValueError(f"未知的动态视图 HTML 类型：view_type={view_type}")
    normalized_archive_id = int(archive_id)
    if normalized_archive_id <= 0:
        raise ValueError(f"动态视图 HTML 存档 ID 非法：archive_id={archive_id}")
    return _DYNAMIC_VIEW_HTML_ROOT / normalized_view_type / f"{normalized_archive_id}.html"


# 执行build dynamic view html relative path相关逻辑。
def build_dynamic_view_html_relative_path(*, view_type: str, archive_id: int) -> str:
    """按动态视图类型与存档 ID 构造存入数据库的相对 HTML 路径。"""
    normalized_view_type = view_type.strip().lower()
    normalized_archive_id = int(archive_id)
    if normalized_view_type not in {"game", "knowledge"}:
        raise ValueError(f"未知的动态视图 HTML 类型：view_type={view_type}")
    if normalized_archive_id <= 0:
        raise ValueError(f"动态视图 HTML 存档 ID 非法：archive_id={archive_id}")
    return f"{normalized_view_type}/{normalized_archive_id}.html"


# 执行resolve dynamic view html path from relative path相关逻辑。
def resolve_dynamic_view_html_path_from_relative_path(relative_path: str) -> Path:
    """把数据库中的相对 HTML 路径解析为受限的绝对文件路径。"""
    normalized_relative_path = relative_path.strip().replace("\\", "/")
    if not normalized_relative_path:
        raise ValueError("动态视图 HTML 路径为空")
    resolved_path = (_DYNAMIC_VIEW_HTML_ROOT / normalized_relative_path).resolve()
    html_root = _DYNAMIC_VIEW_HTML_ROOT.resolve()
    try:
        resolved_path.relative_to(html_root)
    except ValueError as error:
        raise ValueError(f"动态视图 HTML 路径非法：path={relative_path}") from error
    return resolved_path


# 执行resolve dynamic view recycled html path from relative path相关逻辑。
def resolve_dynamic_view_recycled_html_path_from_relative_path(relative_path: str) -> Path:
    """把数据库中的相对 HTML 路径解析为回收站内的受限文件路径。"""
    normalized_relative_path = relative_path.strip().replace("\\", "/")
    if not normalized_relative_path:
        raise ValueError("动态视图 HTML 路径为空")
    resolved_path = (_DYNAMIC_VIEW_RECYCLE_HTML_ROOT / normalized_relative_path).resolve()
    recycle_root = _DYNAMIC_VIEW_RECYCLE_HTML_ROOT.resolve()
    try:
        resolved_path.relative_to(recycle_root)
    except ValueError as error:
        raise ValueError(f"动态视图回收站 HTML 路径非法：path={relative_path}") from error
    return resolved_path


# 执行write dynamic view html file相关逻辑。
def write_dynamic_view_html_file(*, view_type: str, archive_id: int, html: str) -> str:
    """把最终 HTML 落盘到固定文件，并返回数据库使用的相对路径。"""
    file_path = resolve_dynamic_view_html_file_path(view_type=view_type, archive_id=archive_id)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(html, encoding="utf-8")
    return build_dynamic_view_html_relative_path(view_type=view_type, archive_id=archive_id)


# 执行delete dynamic view html file相关逻辑。
def delete_dynamic_view_html_file(relative_path: str) -> None:
    """删除指定相对路径对应的 HTML 文件；文件不存在时直接忽略。"""
    resolved_path = resolve_dynamic_view_html_path_from_relative_path(relative_path)
    if resolved_path.exists():
        resolved_path.unlink()


# 执行move dynamic view html file to recycle bin相关逻辑。
def move_dynamic_view_html_file_to_recycle_bin(relative_path: str) -> None:
    """把指定 HTML 文件移入回收站；源文件不存在时直接忽略。"""
    source_path = resolve_dynamic_view_html_path_from_relative_path(relative_path)
    if not source_path.exists():
        return
    recycled_path = resolve_dynamic_view_recycled_html_path_from_relative_path(relative_path)
    recycled_path.parent.mkdir(parents=True, exist_ok=True)
    if recycled_path.exists():
        recycled_path.unlink()
    shutil.move(str(source_path), str(recycled_path))


# 执行restore dynamic view html file from recycle bin相关逻辑。
def restore_dynamic_view_html_file_from_recycle_bin(relative_path: str) -> None:
    """把指定 HTML 文件从回收站恢复到正式目录；回收站文件不存在时直接忽略。"""
    recycled_path = resolve_dynamic_view_recycled_html_path_from_relative_path(relative_path)
    if not recycled_path.exists():
        return
    target_path = resolve_dynamic_view_html_path_from_relative_path(relative_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        target_path.unlink()
    shutil.move(str(recycled_path), str(target_path))


# 执行delete dynamic view recycled html file相关逻辑。
def delete_dynamic_view_recycled_html_file(relative_path: str) -> None:
    """删除回收站内指定 HTML 文件；文件不存在时直接忽略。"""
    recycled_path = resolve_dynamic_view_recycled_html_path_from_relative_path(relative_path)
    if recycled_path.exists():
        recycled_path.unlink()


# 执行resolve dynamic view music path相关逻辑。
def resolve_dynamic_view_music_path(relative_path: str) -> Path:
    """把音乐相对路径解析为受限的绝对文件路径。"""
    normalized_relative_path = relative_path.strip().replace("\\", "/")
    if not normalized_relative_path:
        raise ValueError("动态视图音频路径为空")
    resolved_path = (_DYNAMIC_VIEW_MUSIC_ROOT / normalized_relative_path).resolve()
    music_root = _DYNAMIC_VIEW_MUSIC_ROOT.resolve()
    try:
        resolved_path.relative_to(music_root)
    except ValueError as error:
        raise ValueError(f"动态视图音频路径非法：path={relative_path}") from error
    return resolved_path


# 执行resolve dynamic view recycled music path相关逻辑。
def resolve_dynamic_view_recycled_music_path(relative_path: str) -> Path:
    """把音乐相对路径解析为回收站内的受限文件路径。"""
    normalized_relative_path = relative_path.strip().replace("\\", "/")
    if not normalized_relative_path:
        raise ValueError("动态视图音频路径为空")
    resolved_path = (_DYNAMIC_VIEW_RECYCLE_MUSIC_ROOT / normalized_relative_path).resolve()
    music_recycle_root = _DYNAMIC_VIEW_RECYCLE_MUSIC_ROOT.resolve()
    try:
        resolved_path.relative_to(music_recycle_root)
    except ValueError as error:
        raise ValueError(f"动态视图回收站音频路径非法：path={relative_path}") from error
    return resolved_path


# 执行move dynamic view music file to recycle bin相关逻辑。
def move_dynamic_view_music_file_to_recycle_bin(relative_path: str) -> None:
    """把指定音频文件移入回收站；源文件不存在时直接忽略。"""
    source_path = resolve_dynamic_view_music_path(relative_path)
    if not source_path.exists():
        return
    recycled_path = resolve_dynamic_view_recycled_music_path(relative_path)
    recycled_path.parent.mkdir(parents=True, exist_ok=True)
    if recycled_path.exists():
        recycled_path.unlink()
    shutil.move(str(source_path), str(recycled_path))


# 执行restore dynamic view music file from recycle bin相关逻辑。
def restore_dynamic_view_music_file_from_recycle_bin(relative_path: str) -> None:
    """把指定音频文件从回收站恢复到正式目录；回收站文件不存在时直接忽略。"""
    recycled_path = resolve_dynamic_view_recycled_music_path(relative_path)
    if not recycled_path.exists():
        return
    target_path = resolve_dynamic_view_music_path(relative_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        target_path.unlink()
    shutil.move(str(recycled_path), str(target_path))


# 执行delete dynamic view recycled music file相关逻辑。
def delete_dynamic_view_recycled_music_file(relative_path: str) -> None:
    """删除回收站内指定音频文件；文件不存在时直接忽略。"""
    recycled_path = resolve_dynamic_view_recycled_music_path(relative_path)
    if recycled_path.exists():
        recycled_path.unlink()
