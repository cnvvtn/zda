# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\features\dynamic_view\schemas.py。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.features.dynamic_view.subject_type_support import (
    validate_dynamic_view_subject_parent_type,
    validate_dynamic_view_subject_type,
)

_DYNAMIC_VIEW_TEMPLATE_TYPE_LANDSCAPE = "landscape_16_9"
_DYNAMIC_VIEW_TEMPLATE_TYPE_PORTRAIT = "portrait_9_16"
_DYNAMIC_VIEW_TASK_TYPE_CODE_DEFAULT = "1-1"
_DYNAMIC_VIEW_TASK_TYPE_CODE_VALUES = {"1-1", "1-2", "2-1", "2-2"}
_DYNAMIC_VIEW_TYPE_VALUES = {"game", "knowledge"}
_DYNAMIC_VIEW_AUTHOR_ID_DEFAULT = "system"
_DYNAMIC_VIEW_VIEW_TYPE_DEFAULT = "knowledge"
_DYNAMIC_VIEW_DEFAULT_TARGET_AUDIENCE = "Vivid and interesting Chinese lines"
_DYNAMIC_VIEW_DEFAULT_SUBTITLE_LANGUAGES = ["zh", "en"]
_DYNAMIC_VIEW_SUPPORTED_SUBTITLE_LANGUAGES = {"zh", "en", "ja", "ko"}
_DYNAMIC_VIEW_LANGUAGE_ALIASES = {
    "zh": "zh",
    "zh-cn": "zh",
    "zh_hans": "zh",
    "cn": "zh",
    "chinese": "zh",
    "中文": "zh",
    "en": "en",
    "english": "en",
    "ja": "ja",
    "japanese": "ja",
    "ko": "ko",
    "korean": "ko",
}


# 执行normalize dynamic view template type相关逻辑。
def normalize_dynamic_view_template_type(raw_template_type: str | None) -> str:
    """统一收口动态视图模板类型，只允许 16:9 横屏与 9:16 竖屏两种值。"""
    normalized_template_type = str(raw_template_type or "").strip().lower()
    if normalized_template_type in {
        _DYNAMIC_VIEW_TEMPLATE_TYPE_LANDSCAPE,
        _DYNAMIC_VIEW_TEMPLATE_TYPE_PORTRAIT,
    }:
        return normalized_template_type
    if normalized_template_type in {"16:9", "16_9", "landscape", "horizontal"}:
        return _DYNAMIC_VIEW_TEMPLATE_TYPE_LANDSCAPE
    if normalized_template_type in {"9:16", "9_16", "portrait", "vertical"}:
        return _DYNAMIC_VIEW_TEMPLATE_TYPE_PORTRAIT
    return _DYNAMIC_VIEW_TEMPLATE_TYPE_LANDSCAPE


# 执行normalize dynamic view task type code相关逻辑。
def normalize_dynamic_view_task_type_code(raw_type_code: str | None) -> str:
    """统一收口任务来源类型编码。"""
    normalized_type_code = str(raw_type_code or "").strip()
    if normalized_type_code in _DYNAMIC_VIEW_TASK_TYPE_CODE_VALUES:
        return normalized_type_code
    return _DYNAMIC_VIEW_TASK_TYPE_CODE_DEFAULT


# 执行resolve dynamic view type by task type code相关逻辑。
def resolve_dynamic_view_type_by_task_type_code(raw_type_code: str | None) -> str:
    """根据任务来源类型编码推导动态视图类型。"""
    normalized_type_code = normalize_dynamic_view_task_type_code(raw_type_code)
    if normalized_type_code in {"2-1", "2-2"}:
        return "knowledge"
    return "game"


# 执行normalize dynamic view flow version相关逻辑。
def normalize_dynamic_view_flow_version(raw_flow_version: int | None) -> int:
    """统一收口动态视图任务流程版本，只允许 v1 或 v2。"""
    if raw_flow_version == 1:
        return 1
    return 2


# 执行normalize dynamic view type相关逻辑。
def normalize_dynamic_view_type(raw_view_type: str | None) -> str:
    """统一收口动态视图类型，只保留当前生成链路支持的类型。"""
    normalized_view_type = str(raw_view_type or "").strip().lower()
    if normalized_view_type in _DYNAMIC_VIEW_TYPE_VALUES:
        return normalized_view_type
    return ""


# 执行normalize dynamic view author id相关逻辑。
def normalize_dynamic_view_author_id(raw_author_id: str | None) -> str:
    """统一收口动态视图作者 ID，空值使用系统作者。"""
    return str(raw_author_id or "").strip() or _DYNAMIC_VIEW_AUTHOR_ID_DEFAULT


# 执行normalize dynamic view target audience相关逻辑。
def normalize_dynamic_view_target_audience(raw_target_audience: str | None) -> str:
    """统一清洗动态视图目标受众口吻，空值回退到默认值。"""
    normalized_target_audience = str(raw_target_audience or "").strip()
    return normalized_target_audience or _DYNAMIC_VIEW_DEFAULT_TARGET_AUDIENCE


# 执行normalize dynamic view subtitle language code相关逻辑。
def normalize_dynamic_view_subtitle_language_code(raw_language_code: str | None) -> str:
    """统一清洗字幕语言代码，并把别名映射到稳定值。"""
    normalized_language_code = str(raw_language_code or "").strip().lower()
    if not normalized_language_code:
        return ""
    normalized_language = _DYNAMIC_VIEW_LANGUAGE_ALIASES.get(
        normalized_language_code,
        normalized_language_code,
    )
    if normalized_language in _DYNAMIC_VIEW_SUPPORTED_SUBTITLE_LANGUAGES:
        return normalized_language
    return ""


# 执行resolve dynamic view subtitle language name相关逻辑。
def resolve_dynamic_view_subtitle_language_name(raw_language_code: str | None) -> str:
    """把语言代码映射成 prompt 可读名称。"""
    normalized_language_code = normalize_dynamic_view_subtitle_language_code(
        raw_language_code
    )
    language_name_map = {
        "zh": "Chinese",
        "en": "English",
        "ja": "Japanese",
        "ko": "Korean",
    }
    return language_name_map.get(
        normalized_language_code,
        normalized_language_code.upper(),
    )


# 执行normalize dynamic view subtitle languages相关逻辑。
def normalize_dynamic_view_subtitle_languages(raw_subtitle_languages: list[str] | None) -> list[str]:
    """统一清洗动态视图字幕语言列表，保留顺序、去空去重，默认中英双语。"""
    normalized_subtitle_languages: list[str] = []
    seen_language_codes: set[str] = set()
    raw_values = raw_subtitle_languages or _DYNAMIC_VIEW_DEFAULT_SUBTITLE_LANGUAGES
    for raw_language in raw_values:
        normalized_language = normalize_dynamic_view_subtitle_language_code(raw_language)
        if not normalized_language:
            continue
        if normalized_language in seen_language_codes:
            continue
        seen_language_codes.add(normalized_language)
        normalized_subtitle_languages.append(normalized_language)
    if normalized_subtitle_languages:
        return normalized_subtitle_languages[:2]
    return list(_DYNAMIC_VIEW_DEFAULT_SUBTITLE_LANGUAGES)


# 定义DynamicViewClueItem。
class DynamicViewClueItem(BaseModel):
    """动态视图线索项，统一承接线索展示与点亮状态。"""

    model_config = ConfigDict(populate_by_name=True)

    clue_key: str = Field(alias="clueKey", min_length=1, max_length=64)
    clue_title: str = Field(alias="clueTitle", default="", max_length=255)
    clue_content: str = Field(alias="clueContent", min_length=1, max_length=1000)
    unlocked: bool = False
    unlock_step: int = Field(default=0, alias="unlockStep")


# 定义DynamicViewAudioConfig。
class DynamicViewAudioConfig(BaseModel):
    """动态视图音频配置，统一承接固定背景音乐或字幕音频的播放信息。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str = ""
    start_time: int = Field(default=0, alias="startTime")
    end_time: int = Field(default=0, alias="endTime")
    volume: int = 100
    path: str = ""
    kind: str = "background"


# 定义DynamicViewSceneSubtitle。
class DynamicViewSceneSubtitle(BaseModel):
    """动态视图字幕项，统一承接每幕的主文案与多语言字幕。"""

    model_config = ConfigDict(populate_by_name=True)

    vivid: str = ""
    ext: str = ""
    duration_ms: int = Field(default=0, alias="durationMs")
    translations: dict[str, str] = Field(default_factory=dict)

    # 执行validate translations相关逻辑。
    @field_validator("translations")
    @classmethod
    def validate_translations(cls, value: dict[str, str]) -> dict[str, str]:
        """统一清洗多语言字幕映射，只保留非空键值。"""
        normalized_translations: dict[str, str] = {}
        for raw_language_code, raw_text in value.items():
            normalized_language_code = str(raw_language_code or "").strip().lower()
            normalized_text = str(raw_text or "").strip()
            if not normalized_language_code or not normalized_text:
                continue
            normalized_translations[normalized_language_code] = normalized_text
        return normalized_translations

    # 执行serialize scene subtitle related logic。
    def model_dump(self, *args, **kwargs):
        """统一补出兼容字段，便于前端渐进迁移。"""
        dumped_data = super().model_dump(*args, **kwargs)
        dumped_data["translations"] = dict(self.translations)
        for language_code, translation_text in self.translations.items():
            dumped_data[language_code] = translation_text
        return dumped_data


# 定义DynamicViewListItem。
class DynamicViewListItem(BaseModel):
    """动态视图首页列表项，统一承接 game 与 knowledge 两类卡片字段。"""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    view_type: str = Field(default="game", alias="viewType")
    title: str = ""
    topic: str = ""
    subject_parent_type: str = Field(default="", alias="subjectParentType")
    subject_type: str = Field(default="", alias="subjectType")
    summary: str = ""
    detail: str = ""
    view_count: int = Field(default=0, alias="viewCount")
    comment_count: int = Field(default=0, alias="commentCount")
    total_duration_ms: int = Field(default=0, alias="totalDurationMs")
    final_question: str = Field(default="", alias="finalQuestion")
    clue_count: int = Field(default=0, alias="clueCount")
    knowledge_ready: bool = Field(default=False, alias="knowledgeReady")
    html_url: str = Field(default="", alias="htmlUrl")

    # 执行validate list item view type相关逻辑。
    @field_validator("view_type")
    @classmethod
    def validate_view_type(cls, value: str) -> str:
        """统一校验并规范化动态视图列表项类型。"""
        return normalize_dynamic_view_type(value) or "game"

    # 执行validate list item subject parent type相关逻辑。
    @field_validator("subject_parent_type")
    @classmethod
    def validate_subject_parent_type(cls, value: str) -> str:
        """统一校验并规范化动态视图列表项父分类。"""
        return validate_dynamic_view_subject_parent_type(value)

    # 执行validate list item subject type相关逻辑。
    @field_validator("subject_type")
    @classmethod
    def validate_subject_type(cls, value: str) -> str:
        """统一校验并规范化动态视图列表项子分类。"""
        return validate_dynamic_view_subject_type(value)


# 定义DynamicViewPayload。
class DynamicViewPayload(BaseModel):
    """动态视图最终载荷，统一承接游戏视图与知识视图需要的核心字段。"""

    model_config = ConfigDict(populate_by_name=True)

    view_type: str = Field(default="game", alias="viewType")
    template_type: str = Field(
        default=_DYNAMIC_VIEW_TEMPLATE_TYPE_LANDSCAPE,
        alias="templateType",
    )
    render_mode: str = Field(default="html", alias="renderMode")
    status: str = "ready"
    preview_text: str = Field(default="", alias="previewText")
    game_view_id: int | None = Field(default=None, alias="gameViewId")
    knowledge_view_id: int | None = Field(default=None, alias="knowledgeViewId")
    knowledge_generation_status: str = Field(
        default="idle",
        alias="knowledgeGenerationStatus",
    )
    knowledge_ready: bool = Field(default=False, alias="knowledgeReady")
    title: str
    topic: str
    subject_parent_type: str = Field(default="", alias="subjectParentType")
    subject_type: str = Field(default="", alias="subjectType")
    summary: str
    detail: str = ""
    final_question: str = Field(default="", alias="finalQuestion")
    current_unlocked_clue_count: int = Field(default=0, alias="currentUnlockedClueCount")
    total_clue_count: int = Field(default=0, alias="totalClueCount")
    all_clues_unlocked: bool = Field(default=False, alias="allCluesUnlocked")
    clues: list[DynamicViewClueItem] = Field(default_factory=list)
    view_count: int = Field(default=0, alias="viewCount")
    comment_count: int = Field(default=0, alias="commentCount")
    scene_subtitles: list[DynamicViewSceneSubtitle] = Field(
        default_factory=list,
        alias="sceneSubtitles",
    )
    total_duration_ms: int = Field(default=0, alias="totalDurationMs")
    html: str = ""
    html_url: str = Field(default="", alias="htmlUrl")
    audio: DynamicViewAudioConfig | None = None
    subtitle_audio: DynamicViewAudioConfig | None = Field(default=None, alias="subtitleAudio")

    # 执行validate template type相关逻辑。
    @field_validator("template_type")
    @classmethod
    def validate_template_type(cls, value: str) -> str:
        """统一校验并规范化动态视图模板类型。"""
        return normalize_dynamic_view_template_type(value)

    # 执行validate payload subject parent type相关逻辑。
    @field_validator("subject_parent_type")
    @classmethod
    def validate_subject_parent_type(cls, value: str) -> str:
        """统一校验并规范化动态视图载荷父分类。"""
        return validate_dynamic_view_subject_parent_type(value)

    # 执行validate payload subject type相关逻辑。
    @field_validator("subject_type")
    @classmethod
    def validate_subject_type(cls, value: str) -> str:
        """统一校验并规范化动态视图载荷子分类。"""
        return validate_dynamic_view_subject_type(value)

    # 执行model post init相关逻辑。
    def model_post_init(self, __context) -> None:
        """统一清理音频配置，避免空对象继续透出到前端。"""
        if self.audio is not None and (
            not self.audio.name.strip() or not self.audio.path.strip()
        ):
            self.audio = None
        if self.subtitle_audio is not None and (
            not self.subtitle_audio.name.strip() or not self.subtitle_audio.path.strip()
        ):
            self.subtitle_audio = None


# 定义DynamicViewMetadata。
class DynamicViewMetadata(BaseModel):
    """动态视图元数据结构化结果，统一承接副标题、摘要与题材分类。"""

    subtitle: str = Field(min_length=1, max_length=28)
    detail: str = Field(min_length=1, max_length=1200)
    summary: str = Field(min_length=1, max_length=60)
    subject_parent_type: str = Field(min_length=1, max_length=32)
    subject_type: str = Field(min_length=1, max_length=32)

    # 执行validate subject parent type相关逻辑。
    @field_validator("subject_parent_type")
    @classmethod
    def validate_subject_parent_type(cls, value: str) -> str:
        """统一校验并规范化动态视图父分类。"""
        return validate_dynamic_view_subject_parent_type(value)

    # 执行validate subject type相关逻辑。
    @field_validator("subject_type")
    @classmethod
    def validate_subject_type(cls, value: str) -> str:
        """统一校验并规范化动态视图题材类型。"""
        return validate_dynamic_view_subject_type(value)


# 定义DynamicViewKnowledgeDetail。
class DynamicViewKnowledgeDetail(BaseModel):
    """知识动态视图的详情讲解结构化结果。"""

    detail: str = Field(min_length=1, max_length=5000)


# 定义DynamicViewMetadataClue。
class DynamicViewMetadataClue(BaseModel):
    """动态视图元数据阶段返回的线索项。"""

    clue_key: str = Field(min_length=1, max_length=64)
    clue_title: str = Field(min_length=1, max_length=255)
    clue_content: str = Field(min_length=1, max_length=1000)


# 定义DynamicViewGameMetadataBundle。
class DynamicViewGameMetadataBundle(BaseModel):
    """游戏动态视图元数据与线索联合抽取结果。"""

    topic: str = Field(min_length=1, max_length=24)
    subtitle: str = Field(min_length=1, max_length=28)
    detail: str = Field(min_length=1, max_length=1200)
    summary: str = Field(min_length=1, max_length=60)
    subject_parent_type: str = Field(min_length=1, max_length=32)
    subject_type: str = Field(min_length=1, max_length=32)
    clues: list[DynamicViewMetadataClue] = Field(default_factory=list, max_length=8)

    # 执行validate subject parent type相关逻辑。
    @field_validator("subject_parent_type")
    @classmethod
    def validate_subject_parent_type(cls, value: str) -> str:
        """统一校验并规范化动态视图父分类。"""
        return validate_dynamic_view_subject_parent_type(value)

    # 执行validate subject type相关逻辑。
    @field_validator("subject_type")
    @classmethod
    def validate_subject_type(cls, value: str) -> str:
        """统一校验并规范化动态视图题材类型。"""
        return validate_dynamic_view_subject_type(value)


# 定义DynamicViewCharacter。
class DynamicViewCharacter(BaseModel):
    """动态视图角色项，统一承接后续可问答的关键实体。"""

    role_name: str = Field(min_length=1, max_length=128)
    category_name: str = Field(min_length=1, max_length=64)
    icon: str = Field(min_length=1, max_length=32)
    persona_prompt: str = Field(min_length=1, max_length=4000)
    personality: str = Field(min_length=1, max_length=4000)
    scenario: str = Field(min_length=1, max_length=4000)
    nsfw_setting: str = Field(min_length=1, max_length=4000)
    author: str = Field(min_length=1, max_length=64)


# 定义DynamicViewCharacterSceneBundle。
class DynamicViewCharacterSceneBundle(BaseModel):
    """从分镜文本中抽取出的动态视图角色列表。"""

    characters: list[DynamicViewCharacter] = Field(default_factory=list, max_length=8)


# 定义DynamicViewRoleItem。
class DynamicViewRoleItem(BaseModel):
    """动态视图详情页里的角色卡片项。"""

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(alias="roleId")
    role_name: str = Field(alias="roleName")
    persona_prompt: str = Field(default="", alias="personaPrompt")
    category_name: str = Field(default="", alias="categoryName")
    icon: str = ""
    personality: str = ""
    scenario: str = ""
    nsfw_setting: str = Field(default="", alias="nsfwSetting")
    author: str = "system"


# 定义DynamicViewCommentUser。
class DynamicViewCommentUser(BaseModel):
    """动态视图评论里的轻量用户信息。"""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(alias="userId")
    nickname: str
    avatar: str = ""
    ip_location: str = Field(default="", alias="ipLocation")


# 定义DynamicViewCommentItem。
class DynamicViewCommentItem(BaseModel):
    """动态视图详情页评论项。"""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    pid: int = 0
    content: str
    like_count: int = Field(default=0, alias="likeCount")
    reply_count: int = Field(default=0, alias="replyCount")
    created_at: str = Field(alias="createdAt")
    user: DynamicViewCommentUser
    children: list["DynamicViewCommentItem"] = Field(default_factory=list)


# 执行model rebuild相关逻辑。
DynamicViewCommentItem.model_rebuild()


# 定义DynamicViewCommentCreateRequest。
class DynamicViewCommentCreateRequest(BaseModel):
    """动态视图评论创建请求，支持顶级评论和回复评论。"""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(min_length=1, alias="userId")
    content: str = Field(min_length=1, max_length=1000)
    pid: int = Field(default=0, ge=0)


# 定义DynamicViewCommentPage。
class DynamicViewCommentPage(BaseModel):
    """动态视图评论分页结果。"""

    model_config = ConfigDict(populate_by_name=True)

    items: list[DynamicViewCommentItem] = Field(default_factory=list)
    next_cursor: int | None = Field(default=None, alias="nextCursor")
    has_more: bool = Field(default=False, alias="hasMore")


# 定义DynamicViewDetailBootstrap。
class DynamicViewDetailBootstrap(BaseModel):
    """动态视图详情页首屏初始化结果。"""

    model_config = ConfigDict(populate_by_name=True)

    payload: DynamicViewPayload
    roles: list[DynamicViewRoleItem] = Field(default_factory=list)
    comments: list[DynamicViewCommentItem] = Field(default_factory=list)
    next_comment_cursor: int | None = Field(default=None, alias="nextCommentCursor")
    has_more_comments: bool = Field(default=False, alias="hasMoreComments")


# 定义DynamicViewCreateRequest。
class DynamicViewCreateRequest(BaseModel):
    """动态视图实时创建请求，承接主题、自定义视图参数和模板类型。"""

    model_config = ConfigDict(populate_by_name=True)

    topic: str = Field(min_length=1, max_length=100)
    view_type: str = Field(
        default=_DYNAMIC_VIEW_VIEW_TYPE_DEFAULT,
        alias="type",
        min_length=1,
        max_length=32,
    )
    author_id: str = Field(
        default=_DYNAMIC_VIEW_AUTHOR_ID_DEFAULT,
        alias="authorId",
        min_length=1,
        max_length=64,
    )
    scene_count_min: int = Field(default=8, alias="sceneCountMin", ge=6, le=12)
    template_type: str = Field(
        default=_DYNAMIC_VIEW_TEMPLATE_TYPE_LANDSCAPE,
        alias="templateType",
    )
    visual_style_override: str = Field(
        default="",
        alias="visualStyleOverride",
        max_length=200,
    )
    target_audience: str = Field(
        default=_DYNAMIC_VIEW_DEFAULT_TARGET_AUDIENCE,
        alias="targetAudience",
        max_length=200,
    )
    subtitle_languages: list[str] = Field(
        default_factory=lambda: list(_DYNAMIC_VIEW_DEFAULT_SUBTITLE_LANGUAGES),
        alias="subtitleLanguages",
        max_length=8,
    )
    plan_code: str = Field(default="free", alias="planCode", max_length=32)
    model_level: str = Field(default="basic", alias="modelLevel", max_length=32)
    browser_fingerprint: str = Field(default="", alias="browserFingerprint", max_length=128)
    start_immediately: bool = Field(default=True, alias="startImmediately")

    # 执行validate view type相关逻辑。
    @field_validator("view_type")
    @classmethod
    def validate_view_type(cls, value: str) -> str:
        """统一校验并规范化动态视图类型。"""
        normalized_view_type = normalize_dynamic_view_type(value)
        if normalized_view_type:
            return normalized_view_type
        raise ValueError("type 必须是 game 或 knowledge")

    # 执行validate author id相关逻辑。
    @field_validator("author_id")
    @classmethod
    def validate_author_id(cls, value: str) -> str:
        """统一校验并规范化动态视图作者 ID。"""
        return normalize_dynamic_view_author_id(value)

    # 执行validate template type相关逻辑。
    @field_validator("template_type")
    @classmethod
    def validate_template_type(cls, value: str) -> str:
        """统一校验并规范化动态视图创建请求里的模板类型。"""
        return normalize_dynamic_view_template_type(value)

    # 执行validate visual style override相关逻辑。
    @field_validator("visual_style_override")
    @classmethod
    def validate_visual_style_override(cls, value: str) -> str:
        """统一清洗动态视图风格覆盖文本。"""
        return str(value or "").strip()

    # 执行validate target audience相关逻辑。
    @field_validator("target_audience")
    @classmethod
    def validate_target_audience(cls, value: str) -> str:
        """统一清洗动态视图目标受众口吻。"""
        return normalize_dynamic_view_target_audience(value)

    # 执行validate subtitle languages相关逻辑。
    @field_validator("subtitle_languages")
    @classmethod
    def validate_subtitle_languages(cls, value: list[str]) -> list[str]:
        """统一清洗动态视图字幕语言列表。"""
        return normalize_dynamic_view_subtitle_languages(value)

    # 执行validate browser fingerprint相关逻辑。
    @field_validator("browser_fingerprint")
    @classmethod
    def validate_browser_fingerprint(cls, value: str) -> str:
        """统一清洗动态视图匿名浏览器指纹。"""
        return str(value or "").strip()[:128]


# 定义DynamicViewTopicAnalysisResult。
class DynamicViewTopicAnalysisResult(BaseModel):
    """动态视图主题前置分析结果，只判断主题是否违规。"""

    model_config = ConfigDict(populate_by_name=True)

    topic: str = Field(min_length=1, max_length=300)
    is_complete: bool = Field(alias="isComplete")
    is_violation: bool = Field(alias="isViolation")
    displayable: bool
    displayable_reason: str = Field(default="", alias="displayableReason", max_length=400)
    analysis_status: str = Field(default="passed", alias="analysisStatus", max_length=64)
    decision_summary: str = Field(default="", alias="decisionSummary", max_length=500)

    # 执行validate topic相关逻辑。
    @field_validator("topic")
    @classmethod
    def validate_topic(cls, value: str) -> str:
        """统一清洗主题文本。"""
        return str(value or "").strip()

    # 执行validate displayable reason相关逻辑。
    @field_validator("displayable_reason")
    @classmethod
    def validate_displayable_reason(cls, value: str) -> str:
        """统一清洗合规判断理由。"""
        return str(value or "").strip()

    # 执行validate analysis status相关逻辑。
    @field_validator("analysis_status")
    @classmethod
    def validate_analysis_status(cls, value: str) -> str:
        """统一清洗主题分析状态。"""
        normalized_status = str(value or "").strip()
        return normalized_status or "passed"

    # 执行validate decision summary相关逻辑。
    @field_validator("decision_summary")
    @classmethod
    def validate_decision_summary(cls, value: str) -> str:
        """统一清洗分析决策摘要。"""
        return str(value or "").strip()


# 定义DynamicViewSourceTaskItem。
class DynamicViewSourceTaskItem(BaseModel):
    """动态视图任务来源项，统一承接任务主题文案。"""

    model_config = ConfigDict(populate_by_name=True)

    topic: str = Field(min_length=1, max_length=300)
    view_type: str = Field(default="", alias="type", max_length=32)
    author_id: str = Field(
        default=_DYNAMIC_VIEW_AUTHOR_ID_DEFAULT,
        alias="authorId",
        max_length=64,
    )
    type_code: str = Field(
        default=_DYNAMIC_VIEW_TASK_TYPE_CODE_DEFAULT,
        alias="typeCode",
        min_length=1,
        max_length=16,
    )

    # 执行validate view type相关逻辑。
    @field_validator("view_type")
    @classmethod
    def validate_view_type(cls, value: str) -> str:
        """统一校验并规范化动态视图类型。"""
        return normalize_dynamic_view_type(value)

    # 执行validate author id相关逻辑。
    @field_validator("author_id")
    @classmethod
    def validate_author_id(cls, value: str) -> str:
        """统一校验并规范化动态视图作者 ID。"""
        return normalize_dynamic_view_author_id(value)

    # 执行validate type code相关逻辑。
    @field_validator("type_code")
    @classmethod
    def validate_type_code(cls, value: str) -> str:
        """统一校验并规范化任务类型编码。"""
        return normalize_dynamic_view_task_type_code(value)


# 定义DynamicViewTaskCreateItem。
class DynamicViewTaskCreateItem(BaseModel):
    """手动入库动态视图任务项，要求调用方显式传入数据库必需字段。"""

    model_config = ConfigDict(populate_by_name=True)

    topic: str = Field(min_length=1, max_length=300)
    type_code: str = Field(alias="typeCode", min_length=1, max_length=16)
    author_id: str = Field(alias="authorId", min_length=1, max_length=64)

    # 执行validate type code相关逻辑。
    @field_validator("type_code")
    @classmethod
    def validate_type_code(cls, value: str) -> str:
        """手动入库时要求显式传入合法任务类型编码。"""
        normalized_type_code = str(value or "").strip()
        if normalized_type_code in _DYNAMIC_VIEW_TASK_TYPE_CODE_VALUES:
            return normalized_type_code
        raise ValueError("typeCode 必须是 1-1、1-2、2-1 或 2-2")

    # 执行validate author id相关逻辑。
    @field_validator("author_id")
    @classmethod
    def validate_author_id(cls, value: str) -> str:
        """手动入库时要求显式传入作者 ID。"""
        normalized_author_id = str(value or "").strip()
        if normalized_author_id:
            return normalized_author_id
        raise ValueError("authorId 不能为空")


# 定义DynamicViewTaskGenerationBundle。
class DynamicViewTaskGenerationBundle(BaseModel):
    """动态视图任务批量生成结果，统一承接 LLM 返回的任务候选列表。"""

    tasks: list[DynamicViewSourceTaskItem] = Field(
        default_factory=list,
        min_length=1,
        max_length=100,
    )


# 定义DynamicViewTaskGenerateRequest。
class DynamicViewTaskGenerateRequest(BaseModel):
    """手动触发动态视图任务入库时使用的请求参数。"""

    model_config = ConfigDict(populate_by_name=True)

    count: int = Field(default=2, ge=1, le=5)


# 定义DynamicViewTaskCreateRequest。
class DynamicViewTaskCreateRequest(BaseModel):
    """手动输入动态视图任务并批量入库时使用的请求参数。"""

    model_config = ConfigDict(populate_by_name=True)

    tasks: list[DynamicViewTaskCreateItem] = Field(
        default_factory=list,
        min_length=1,
        max_length=100,
    )


# 定义DynamicViewTaskGenerateResponse。
class DynamicViewTaskGenerateResponse(BaseModel):
    """手动触发动态视图任务入库后的结果摘要。"""

    model_config = ConfigDict(populate_by_name=True)

    requested_count: int = Field(alias="requestedCount")
    generated_count: int = Field(alias="generatedCount")
    inserted_count: int = Field(alias="insertedCount")
    skipped_count: int = Field(alias="skippedCount")
    tasks: list[DynamicViewSourceTaskItem] = Field(default_factory=list)


# 定义DynamicViewStreamEvent。
class DynamicViewStreamEvent(BaseModel):
    """动态视图流式创建事件，供 Flutter 逐卡片渲染节点状态与数据流。"""

    model_config = ConfigDict(populate_by_name=True)

    event: str = "progress"
    stage: str
    message: str
    node_title: str | None = Field(default=None, alias="nodeTitle")
    node_status: str | None = Field(default=None, alias="nodeStatus")
    stream_char_count: int | None = Field(default=None, alias="streamCharCount")
    progress: float | None = None
    generation_status: str = Field(default="", alias="generationStatus")
    is_final: bool = Field(alias="isFinal")
    payload: DynamicViewPayload


# 定义DynamicViewTaskSnapshot。
class DynamicViewTaskSnapshot(BaseModel):
    """动态视图后台任务快照，供前端轮询恢复创建状态。"""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")
    request_id: str = Field(default="", alias="requestId")
    author_id: str = Field(alias="authorId")
    topic: str
    scene_count_min: int = Field(alias="sceneCountMin")
    stage: str
    message: str
    node_title: str | None = Field(default=None, alias="nodeTitle")
    node_status: str | None = Field(default=None, alias="nodeStatus")
    stream_char_count: int | None = Field(default=None, alias="streamCharCount")
    progress: float | None = None
    model_level: str = Field(default="basic", alias="modelLevel")
    generation_status: str = Field(default="queued", alias="generationStatus")
    credit_cost: int = Field(default=0, alias="creditCost")
    average_generation_duration_ms: int = Field(default=150000, alias="averageGenerationDurationMs")
    is_final: bool = Field(alias="isFinal")
    is_terminal: bool = Field(alias="isTerminal")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    payload: DynamicViewPayload


# 定义DynamicViewClueMatchResult。
class DynamicViewClueMatchResult(BaseModel):
    """动态视图线索命中结果，统一承接用户输入命中的线索键。"""

    matched_clue_keys: list[str] = Field(default_factory=list, alias="matchedClueKeys")


# 定义DynamicViewRevealPrincipleRequest。
class DynamicViewRevealPrincipleRequest(BaseModel):
    """动态视图揭晓原理请求。"""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(min_length=1, alias="userId")
