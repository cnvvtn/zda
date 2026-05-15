# SPDX-License-Identifier: CC-BY-NC-ND-4.0
# Copyright (c) 2026 知搭 ZDA

r"""文件说明：app\db\models.py。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.mysql import LONGTEXT, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import ChatConstants
from app.db.session import Base


# 定义ChatSession。
class ChatSession(Base):
    """Python 自己维护的会话存档表，供 AI 链路使用。"""

    __tablename__ = "chat_session"
    __table_args__ = {"comment": "聊天会话存档表，记录每个会话的基础信息与最近摘要。"}

    # 自增主键只承担数据库内部关联职责，统一改为 BIGINT，避免后续消息量增长后不够用。
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="会话表自增主键"
    )
    # Flutter、本地存储、Spring、Python 都依赖这个逻辑会话 ID 进行串联。
    conversation_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, comment="逻辑会话 ID"
    )
    # 当前登录用户或设备的业务标识，用来隔离不同用户的数据。
    user_id: Mapped[str] = mapped_column(String(64), index=True, comment="用户业务 ID")
    # 会话标题默认是角色名，后续也可以用于会话列表展示。
    title: Mapped[str] = mapped_column(
        String(255),
        default=ChatConstants.DEFAULT_SESSION_TITLE,
        comment="会话标题",
    )
    # 最近一条消息摘要，供列表快速展示，不承担完整内容存储职责。
    snippet: Mapped[str] = mapped_column(String(500), default="", comment="会话摘要")
    # 会话关联的视图名称，供 Flutter 会话列表底部标签和后续统计分析复用。
    view_name: Mapped[str] = mapped_column(
        String(300),
        default="",
        comment="会话关联视图名称",
    )
    # 预留未读数，当前主链路主要由本地展示决定，这里保留作存档字段。
    unread: Mapped[int] = mapped_column(Integer, default=0, comment="未读消息数")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP"),
        comment="会话创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="会话最后更新时间",
    )


# 定义ChatMessage。
class ChatMessage(Base):
    """Python 自己维护的消息存档表，按逻辑消息 ID 去重。"""

    __tablename__ = "chat_message"
    __table_args__ = {"comment": "聊天消息存档表，统一保存用户消息和 assistant 消息。"}

    # 消息表主键同样改为 BIGINT，自增值只用于数据库内部排序与索引。
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="消息表自增主键"
    )
    # 业务消息 ID 由发送端或 Python 生成，用来跨端幂等去重。
    message_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, comment="业务消息 ID"
    )
    # 逻辑会话 ID，用于把同一轮对话消息串起来。
    conversation_id: Mapped[str] = mapped_column(
        String(64), index=True, comment="逻辑会话 ID"
    )
    # 消息所属用户，用于跨用户隔离与查询。
    user_id: Mapped[str] = mapped_column(String(64), index=True, comment="用户业务 ID")
    # 角色值通常是 user 或 assistant，供上下文拼装与展示判断。
    role: Mapped[str] = mapped_column(String(32), comment="消息角色")
    # 完整消息正文，前端显示和模型上下文都基于这个字段。
    content: Mapped[str] = mapped_column(Text, comment="消息正文")
    # 结构化载荷与大体积原始内容单独落在这里，避免污染普通聊天正文和上下文。
    raw_content: Mapped[str | None] = mapped_column(
        "raw_content",
        LONGTEXT,
        nullable=True,
        comment="消息原始内容",
    )
    # 回复类型用于区分普通消息、建议、系统回复等扩展场景。
    reply_type: Mapped[str] = mapped_column(
        String(32),
        default=ChatConstants.DEFAULT_REPLY_TYPE,
        comment="回复类型",
    )
    # 被引用的上一段文本，没有引用时允许为空。
    quoted_content: Mapped[str | None] = mapped_column(
        "quoted_text",
        Text,
        nullable=True,
        comment="引用文本",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="消息创建时间",
    )


# 定义DynamicViewArchive。
class DynamicViewArchive(Base):
    """动态视图主表，统一保存每日剧情众创、解谜游戏视图和知识视图。"""

    __tablename__ = "dynamic_view"
    __table_args__ = {"comment": "动态视图统一主表。"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="动态视图表自增主键"
    )
    type: Mapped[str] = mapped_column(
        String(32),
        index=True,
        comment="视图类型",
    )
    author_id: Mapped[str] = mapped_column(
        String(64),
        index=True,
        comment="作者用户ID",
    )
    game_archive_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
        comment="知识视图关联的解谜游戏视图ID",
    )
    topic: Mapped[str] = mapped_column(String(300), comment="动态视图主题")
    source_topic: Mapped[str] = mapped_column(
        String(255),
        default="",
        comment="动态视图真实科普主题",
    )
    template_type: Mapped[str] = mapped_column(
        String(32),
        default="landscape_16_9",
        comment="动态视图模板类型",
    )
    subject_type: Mapped[str] = mapped_column(
        String(128), default="", comment="动态视图题材或学科类型"
    )
    subtitle: Mapped[str] = mapped_column(String(500), default="", comment="动态视图副标题")
    detail: Mapped[str] = mapped_column(LONGTEXT, default="", comment="动态视图详细扩展")
    summary: Mapped[str] = mapped_column(LONGTEXT, default="", comment="动态视图摘要")
    html_content: Mapped[str] = mapped_column(
        "html_content",
        LONGTEXT,
        default="",
        comment="动态视图 HTML 文件相对路径",
    )
    audio_name: Mapped[str] = mapped_column(
        String(500),
        default="",
        comment="动态视图音频相对路径",
    )
    audio_start_time: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        comment="动态视图音频开始时间（毫秒）",
    )
    audio_end_time: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        comment="动态视图音频结束时间（毫秒）",
    )
    audio_volume: Mapped[int] = mapped_column(
        Integer,
        default=100,
        comment="动态视图音频音量（0-100）",
    )
    subtitle_audio_name: Mapped[str] = mapped_column(
        String(500),
        default="",
        comment="动态视图字幕音频相对路径",
    )
    subtitle_audio_volume: Mapped[int] = mapped_column(
        Integer,
        default=100,
        comment="动态视图字幕音频音量（0-100）",
    )
    scene_subtitles_json: Mapped[str] = mapped_column(
        "scene_subtitles_json",
        LONGTEXT,
        default="[]",
        comment="动态视图分镜字幕 JSON 存档",
    )
    total_duration_ms: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        comment="动态视图总时长（毫秒）",
    )
    scene_count_min: Mapped[int] = mapped_column(
        Integer,
        default=8,
        comment="创建时请求的最少分镜数",
    )
    final_question: Mapped[str] = mapped_column(
        String(1000),
        default="",
        comment="解谜游戏视图最终提问",
    )
    clue_count: Mapped[int] = mapped_column(Integer, default=0, comment="线索总数")
    knowledge_archive_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="解谜游戏视图关联的知识视图ID",
    )
    knowledge_generation_status: Mapped[str] = mapped_column(
        String(32),
        default="idle",
        comment="知识视图生成状态",
    )
    view_count: Mapped[int] = mapped_column(Integer, default=0, comment="观看次数")
    comment_count: Mapped[int] = mapped_column(Integer, default=0, comment="评论数量")
    status: Mapped[str] = mapped_column(String(32), default="ready", comment="动态视图状态")
    is_deleted: Mapped[int] = mapped_column(
        Integer,
        default=0,
        index=True,
        comment="是否进入回收站",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
        comment="虚拟删除时间",
    )
    physical_delete_after: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
        index=True,
        comment="允许物理删除时间",
    )
    source_type: Mapped[str] = mapped_column(
        String(32),
        default="",
        comment="任务来源类型",
    )
    source_model: Mapped[str] = mapped_column(
        String(128),
        default="",
        comment="生成该任务时使用的模型名",
    )
    type_code: Mapped[str] = mapped_column(
        String(16),
        default="",
        comment="任务类型编码：1-1(v1横屏)/1-2(v1竖屏)/2-1(v2横屏)/2-2(v2竖屏)",
    )
    generation_task_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="关联的动态视图后台任务 ID",
    )
    error_message: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
        comment="任务失败摘要",
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
        comment="任务开始处理时间",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
        comment="任务处理完成时间",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="动态视图创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="动态视图更新时间",
    )


DynamicViewGameArchive = DynamicViewArchive
DynamicViewKnowledgeArchive = DynamicViewArchive


# 定义WebsiteGenerationSession。
class WebsiteGenerationSession(Base):
    """官网生成页会话表，保存已登录用户在网站生成窗口创建过的主题会话。"""

    __tablename__ = "website_generation_session"
    __table_args__ = {"comment": "官网生成页会话表。"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="会话表自增主键"
    )
    user_id: Mapped[str] = mapped_column(
        String(64),
        index=True,
        comment="用户业务 ID",
    )
    topic: Mapped[str] = mapped_column(String(255), comment="会话主题")
    source: Mapped[str] = mapped_column(
        String(32),
        default="website",
        comment="会话来源",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="会话创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="会话更新时间",
    )


# 定义WebsiteGenerationSessionTask。
class WebsiteGenerationSessionTask(Base):
    """官网生成页会话任务表，保存一个会话下的多次生成任务。"""

    __tablename__ = "website_generation_session_task"
    __table_args__ = (
        UniqueConstraint("user_id", "task_id", name="uk_website_gen_session_task_user_task"),
        Index("idx_website_gen_session_task_session", "session_id", "updated_at"),
        {"comment": "官网生成页会话任务表。"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="会话任务表自增主键"
    )
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("website_generation_session.id", ondelete="CASCADE"),
        index=True,
        comment="关联的官网生成会话 ID",
    )
    user_id: Mapped[str] = mapped_column(String(64), index=True, comment="用户业务 ID")
    topic: Mapped[str] = mapped_column(String(255), comment="任务主题")
    task_id: Mapped[str] = mapped_column(String(64), index=True, comment="关联的动态视图任务 ID")
    source: Mapped[str] = mapped_column(String(32), default="website", comment="会话来源")
    stage: Mapped[str] = mapped_column(String(64), default="queued", comment="生成阶段")
    message: Mapped[str] = mapped_column(String(1000), default="", comment="生成状态文案")
    node_status: Mapped[str] = mapped_column(String(64), default="", comment="当前节点状态")
    payload_status: Mapped[str] = mapped_column(String(64), default="", comment="载荷状态")
    is_terminal: Mapped[int] = mapped_column(Integer, default=0, comment="是否终态")
    html_url: Mapped[str] = mapped_column(String(1000), default="", comment="生成结果 HTML 地址")
    snapshot_json: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True, comment="最近一次任务快照 JSON")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="会话任务创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="会话任务更新时间",
    )


# 定义DynamicViewGenerationRequest。
class DynamicViewGenerationRequest(Base):
    """动态视图生成请求记录表，按 requestId 保存每次生成的请求和模型参数。"""

    __tablename__ = "dynamic_view_generation_request"
    __table_args__ = (
        UniqueConstraint("request_id", name="uk_dynamic_generation_request_id"),
        Index("idx_dynamic_generation_identity", "is_logged_in", "user_id", "created_at"),
        Index("idx_dynamic_generation_guest", "is_logged_in", "ip_address", "created_at"),
        Index("idx_dynamic_generation_guest_id", "is_logged_in", "guest_id", "created_at"),
        Index("idx_dynamic_generation_fingerprint", "is_logged_in", "browser_fingerprint", "created_at"),
        Index("idx_dynamic_generation_ip_prefix", "is_logged_in", "ip_prefix", "created_at"),
        {"comment": "动态视图生成请求记录表。"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="请求记录表自增主键"
    )
    user_id: Mapped[str] = mapped_column(
        String(64),
        default="",
        index=True,
        comment="登录用户业务 ID，未登录为空",
    )
    is_logged_in: Mapped[int] = mapped_column(
        Integer,
        default=0,
        index=True,
        comment="是否登录用户：1 是，0 否",
    )
    ip_address: Mapped[str] = mapped_column(
        String(64),
        default="",
        index=True,
        comment="请求 IP",
    )
    ip_prefix: Mapped[str] = mapped_column(String(64), default="", comment="请求 IP 段")
    guest_id: Mapped[str] = mapped_column(String(64), default="", comment="未登录访客 ID")
    browser_fingerprint: Mapped[str] = mapped_column(String(128), default="", comment="浏览器指纹")
    user_agent_hash: Mapped[str] = mapped_column(String(64), default="", comment="User-Agent 哈希")
    topic: Mapped[str] = mapped_column(String(255), default="", comment="生成主题")
    request_id: Mapped[str] = mapped_column(
        String(64),
        default="",
        index=True,
        comment="动态视图生成 requestId",
    )
    task_id: Mapped[str] = mapped_column(String(64), default="", index=True, comment="关联的动态视图任务 ID")
    model_level: Mapped[str] = mapped_column(String(32), default="", index=True, comment="模型等级")
    model_name: Mapped[str] = mapped_column(String(128), default="", comment="本次生成使用的模型名称")
    temperature: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True, comment="本次生成 temperature")
    top_p: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True, comment="本次生成 top_p")
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="本次生成 max_tokens")
    stream: Mapped[int] = mapped_column(Integer, default=0, comment="是否流式请求模型")
    view_type: Mapped[str] = mapped_column(String(32), default="", comment="视图类型")
    template_type: Mapped[str] = mapped_column(String(32), default="", comment="模板类型")
    scene_count_min: Mapped[int] = mapped_column(Integer, default=0, comment="最小分镜数量")
    source_type: Mapped[str] = mapped_column(String(32), default="", comment="来源类型")
    plan_code: Mapped[str] = mapped_column(String(32), default="", comment="套餐编码")
    credit_cost: Mapped[Decimal] = mapped_column(Numeric(30, 0), default=Decimal("0"), comment="本次生成预扣 Credit")
    generation_status: Mapped[str] = mapped_column(String(32), default="created", index=True, comment="生成记录状态")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="请求创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="请求更新时间",
    )


# 定义ZpayPaymentOrder。
class ZpayPaymentOrder(Base):
    """ZPAY 支付订单表，保存套餐购买订单与最终支付状态。"""

    __tablename__ = "zpay_payment_order"
    __table_args__ = (
        Index("idx_zpay_order_user_status", "user_id", "status"),
        Index("idx_zpay_order_status_created", "status", "created_at"),
        {"comment": "ZPAY 支付订单表。"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="支付订单表自增主键"
    )
    out_trade_no: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, comment="商户订单号"
    )
    pid: Mapped[str] = mapped_column(String(64), index=True, comment="ZPAY 商户 ID")
    zpay_trade_no: Mapped[str] = mapped_column(
        String(64), default="", index=True, comment="ZPAY/易支付订单号"
    )
    zpay_order_id: Mapped[str] = mapped_column(
        String(64), default="", comment="ZPAY 内部订单号"
    )
    plan_code: Mapped[str] = mapped_column(String(32), index=True, comment="套餐编码")
    plan_name: Mapped[str] = mapped_column(String(128), comment="套餐名称")
    money: Mapped[Decimal] = mapped_column(Numeric(10, 2), comment="订单金额")
    pay_type: Mapped[str] = mapped_column(String(16), comment="支付方式")
    user_id: Mapped[str] = mapped_column(String(64), default="", index=True, comment="用户业务 ID")
    client_ip: Mapped[str] = mapped_column(String(64), default="", index=True, comment="下单 IP")
    browser_fingerprint: Mapped[str] = mapped_column(
        String(128), default="", index=True, comment="浏览器指纹"
    )
    page_url: Mapped[str] = mapped_column(String(1000), default="", comment="发起支付页面")
    status: Mapped[str] = mapped_column(
        String(32), default="pending", index=True, comment="订单状态：pending/paid/closed"
    )
    notify_verified: Mapped[int] = mapped_column(Integer, default=0, comment="是否收到已验签通知")
    notify_count: Mapped[int] = mapped_column(Integer, default=0, comment="通知次数")
    raw_notify: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True, comment="最近一次通知载荷")
    paid_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True, comment="支付完成时间")
    last_notify_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP, nullable=True, comment="最近通知时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="订单创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="订单更新时间",
    )


# 定义ZpayPaymentEvent。
class ZpayPaymentEvent(Base):
    """ZPAY 支付安全事件表，记录通知、下单和异常验签事件。"""

    __tablename__ = "zpay_payment_event"
    __table_args__ = (
        Index("idx_zpay_event_order_created", "out_trade_no", "created_at"),
        Index("idx_zpay_event_type_created", "event_type", "created_at"),
        {"comment": "ZPAY 支付事件审计表。"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="支付事件表自增主键"
    )
    out_trade_no: Mapped[str] = mapped_column(String(32), default="", index=True, comment="商户订单号")
    event_type: Mapped[str] = mapped_column(String(32), index=True, comment="事件类型")
    request_ip: Mapped[str] = mapped_column(String(64), default="", index=True, comment="请求 IP")
    verified: Mapped[int] = mapped_column(Integer, default=0, index=True, comment="是否通过验签")
    payload_hash: Mapped[str] = mapped_column(String(64), default="", comment="载荷 SHA256 摘要")
    raw_payload: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True, comment="事件载荷")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="事件创建时间",
    )


# 定义ZpayPaymentRateLimit。
class ZpayPaymentRateLimit(Base):
    """ZPAY 支付限流计数表，使用 MySQL 原子 upsert 记录窗口计数。"""

    __tablename__ = "zpay_payment_rate_limit"
    __table_args__ = (
        UniqueConstraint("scope", "bucket_key", "window_start", name="uk_zpay_rate_bucket"),
        Index("idx_zpay_rate_scope_updated", "scope", "updated_at"),
        {"comment": "ZPAY 支付限流计数表。"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="限流表自增主键"
    )
    scope: Mapped[str] = mapped_column(String(32), comment="限流作用域")
    bucket_key: Mapped[str] = mapped_column(String(128), comment="限流主体哈希")
    window_start: Mapped[datetime] = mapped_column(TIMESTAMP, comment="限流窗口开始时间")
    request_count: Mapped[int] = mapped_column(Integer, default=0, comment="窗口内请求数")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="计数更新时间",
    )


# 定义ZdaMembershipEntitlement。
class ZdaMembershipEntitlement(Base):
    """会员权益表，支付成功后按用户聚合当前可用套餐和额度。"""

    __tablename__ = "zda_membership_entitlement"
    __table_args__ = {"comment": "知搭会员权益表。"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="权益表自增主键"
    )
    user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, comment="用户业务 ID")
    plan_code: Mapped[str] = mapped_column(String(32), index=True, comment="套餐编码")
    plan_name: Mapped[str] = mapped_column(String(128), comment="套餐名称")
    source_order_no: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, comment="最近一次支付订单号"
    )
    credit_total: Mapped[Decimal] = mapped_column(Numeric(30, 0), default=Decimal("0"), comment="套餐总 Credit")
    credit_remaining: Mapped[Decimal] = mapped_column(Numeric(30, 0), default=Decimal("0"), comment="剩余 Credit")
    model_level: Mapped[str] = mapped_column(String(32), default="basic", comment="模型等级")
    priority_level: Mapped[int] = mapped_column(Integer, default=0, comment="优先级等级")
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True, comment="权益过期时间")
    status: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="权益状态")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="权益创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="权益更新时间",
    )


# 定义ZdaCreditLedger。
class ZdaCreditLedger(Base):
    """Credit 流水表，每条发放记录独立保存余额和有效期。"""

    __tablename__ = "zda_credit_ledger"
    __table_args__ = (
        UniqueConstraint("user_id", "source_type", "source_key", name="uk_credit_user_source"),
        Index("idx_credit_user_expire", "user_id", "status", "expires_at"),
        {"comment": "知搭 Credit 流水表。"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="Credit 流水自增主键"
    )
    user_id: Mapped[str] = mapped_column(String(64), index=True, comment="用户业务 ID")
    source_type: Mapped[str] = mapped_column(String(32), comment="来源类型：free_daily/payment")
    source_key: Mapped[str] = mapped_column(String(128), comment="来源唯一键")
    source_order_no: Mapped[str] = mapped_column(String(32), default="", index=True, comment="支付订单号")
    plan_code: Mapped[str] = mapped_column(String(32), default="", index=True, comment="套餐编码")
    plan_name: Mapped[str] = mapped_column(String(128), default="", comment="套餐名称")
    credit_total: Mapped[Decimal] = mapped_column(Numeric(30, 0), default=Decimal("0"), comment="发放 Credit")
    credit_remaining: Mapped[Decimal] = mapped_column(Numeric(30, 0), default=Decimal("0"), comment="剩余 Credit")
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP, index=True, comment="Credit 过期时间")
    status: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="流水状态")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="流水创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="流水更新时间",
    )


# 定义ZdaCreditRedeemCode。
class ZdaCreditRedeemCode(Base):
    """Credit 兑换码表，保存可兑换额度、有效期和使用次数。"""

    __tablename__ = "zda_credit_redeem_code"
    __table_args__ = (
        UniqueConstraint("code", name="uk_credit_redeem_code"),
        Index("idx_credit_redeem_status_expire", "status", "expires_at"),
        {"comment": "知搭 Credit 兑换码表。"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="兑换码自增主键"
    )
    code: Mapped[str] = mapped_column(String(64), comment="兑换码明文，统一转大写保存")
    credit_total: Mapped[Decimal] = mapped_column(Numeric(30, 0), default=Decimal("0"), comment="兑换发放 Credit")
    valid_days: Mapped[int] = mapped_column(Integer, default=30, comment="兑换后 Credit 有效天数")
    max_uses: Mapped[int] = mapped_column(Integer, default=1, comment="最大可兑换次数")
    used_count: Mapped[int] = mapped_column(Integer, default=0, comment="已兑换次数")
    status: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="状态：1启用 0停用")
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True, comment="兑换码过期时间")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="更新时间",
    )


# 定义ZdaCreditRedeemRecord。
class ZdaCreditRedeemRecord(Base):
    """Credit 兑换记录表，限制同一用户不能重复兑换同一个码。"""

    __tablename__ = "zda_credit_redeem_record"
    __table_args__ = (
        UniqueConstraint("redeem_code_id", "user_id", name="uk_credit_redeem_user_once"),
        Index("idx_credit_redeem_record_user", "user_id", "created_at"),
        {"comment": "知搭 Credit 兑换记录表。"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="兑换记录自增主键"
    )
    redeem_code_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="兑换码 ID")
    user_id: Mapped[str] = mapped_column(String(64), index=True, comment="用户业务 ID")
    code: Mapped[str] = mapped_column(String(64), comment="兑换码明文快照")
    credit_total: Mapped[Decimal] = mapped_column(Numeric(30, 0), default=Decimal("0"), comment="本次兑换 Credit")
    ledger_id: Mapped[int] = mapped_column(BigInteger, default=0, index=True, comment="发放的 Credit 流水 ID")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="兑换时间",
    )


# 定义ZdaCreditUsageLog。
class ZdaCreditUsageLog(Base):
    """Credit 使用记录表，保存每次扣减对应的业务来源和扣减明细。"""

    __tablename__ = "zda_credit_usage_log"
    __table_args__ = (
        UniqueConstraint("user_id", "usage_type", "request_id", "ledger_id", name="uk_credit_usage_once"),
        Index("idx_credit_usage_user_created", "user_id", "created_at"),
        Index("idx_credit_usage_request", "usage_type", "request_id"),
        {"comment": "知搭 Credit 使用记录表。"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="使用记录自增主键"
    )
    user_id: Mapped[str] = mapped_column(String(64), index=True, comment="用户业务 ID")
    ledger_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="被扣减的 Credit 流水 ID")
    usage_type: Mapped[str] = mapped_column(String(32), comment="使用类型：dynamic_view/chat")
    request_id: Mapped[str] = mapped_column(String(128), comment="业务请求 requestId")
    model_level: Mapped[str] = mapped_column(String(32), default="", comment="模型等级")
    credit_amount: Mapped[Decimal] = mapped_column(Numeric(30, 0), default=Decimal("0"), comment="本次扣减 Credit")
    balance_before: Mapped[Decimal] = mapped_column(Numeric(30, 0), default=Decimal("0"), comment="扣减前流水余额")
    balance_after: Mapped[Decimal] = mapped_column(Numeric(30, 0), default=Decimal("0"), comment="扣减后流水余额")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="使用时间",
    )


# 定义DynamicViewClueArchive。
class DynamicViewClueArchive(Base):
    """动态视图游戏线索表，保存每条可点亮线索的展示内容。"""

    __tablename__ = "dynamic_view_clue"
    __table_args__ = {"comment": "动态视图游戏线索表。"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="线索表自增主键"
    )
    game_archive_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
        comment="关联的游戏动态视图 ID",
    )
    clue_key: Mapped[str] = mapped_column(String(64), comment="线索唯一键")
    clue_title: Mapped[str] = mapped_column(String(255), default="", comment="线索标题")
    clue_content: Mapped[str] = mapped_column(String(1000), comment="线索内容")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="线索创建时间",
    )


# 定义DynamicViewProgressArchive。
class DynamicViewProgressArchive(Base):
    """动态视图线索点亮进度表，按 user_id 记录单个用户在单条视图里的已解锁线索。"""

    __tablename__ = "dynamic_view_progress"
    __table_args__ = {"comment": "动态视图线索点亮进度表。"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="线索进度表自增主键"
    )
    game_archive_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
        comment="关联的游戏动态视图 ID",
    )
    user_id: Mapped[str] = mapped_column(
        String(64),
        index=True,
        comment="用户业务 ID",
    )
    clue_key: Mapped[str] = mapped_column(String(64), comment="线索唯一键")
    is_unlocked: Mapped[int] = mapped_column(Integer, default=1, comment="是否已点亮")
    matched_message_id: Mapped[str] = mapped_column(
        String(64),
        default="",
        comment="命中该线索的用户消息 ID",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="首次点亮时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="最近更新时间",
    )


# 定义DynamicViewTaskArchive。
class DynamicViewTaskArchive(Base):
    """动态视图后台任务表，只保存任务状态和最终视图关联。"""

    __tablename__ = "dynamic_view_task"
    __table_args__ = {"comment": "动态视图后台任务表，保存任务状态和最终视图关联。"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="任务表自增主键"
    )
    task_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, comment="动态视图后台任务 ID"
    )
    request_id: Mapped[str] = mapped_column(
        String(64), default="", index=True, comment="生成错误排查请求 ID"
    )
    author_id: Mapped[str] = mapped_column(
        String(64), default="system", index=True, comment="任务所属用户业务 ID"
    )
    topic: Mapped[str] = mapped_column(String(255), default="", comment="创建主题")
    scene_count_min: Mapped[int] = mapped_column(Integer, default=0, comment="最小分镜数量")
    view_type: Mapped[str] = mapped_column(
        String(32),
        default="knowledge",
        index=True,
        comment="任务生成的视图类型：game/knowledge",
    )
    template_type: Mapped[str] = mapped_column(
        String(32),
        default="landscape_16_9",
        comment="任务模板类型",
    )
    model_level: Mapped[str] = mapped_column(String(32), default="basic", comment="模型等级")
    payload_status: Mapped[str] = mapped_column(
        String(32),
        default="processing",
        comment="任务对应载荷状态",
    )
    generation_status: Mapped[str] = mapped_column(
        String(64),
        default="queued",
        index=True,
        comment="生成进度状态",
    )
    game_archive_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
        comment="任务生成的游戏视图 ID",
    )
    knowledge_archive_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
        comment="任务生成的知识视图 ID",
    )
    stage: Mapped[str] = mapped_column(String(64), default="queued", index=True, comment="任务阶段")
    message: Mapped[str] = mapped_column(String(1000), default="", comment="任务状态文案")
    node_title: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="当前节点标题")
    node_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="当前节点状态")
    stream_char_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="流式字符数")
    progress: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="任务进度值")
    is_final: Mapped[int] = mapped_column(Integer, default=0, comment="是否最终块")
    is_terminal: Mapped[int] = mapped_column(Integer, default=0, index=True, comment="是否终态")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="任务创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="任务更新时间",
    )


# 定义DynamicViewModelProfile。
class DynamicViewModelProfile(Base):
    """动态视图模型配置表，按模型等级保存实时调用参数。"""

    __tablename__ = "dynamic_view_model_profile"
    __table_args__ = {"comment": "动态视图模型配置表，按模型等级和调用节点保存实时调用参数。"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="配置表自增主键"
    )
    model_level: Mapped[str] = mapped_column(
        String(32), index=True, comment="模型等级"
    )
    node_key: Mapped[str] = mapped_column(
        String(64), index=True, comment="动态视图调用节点"
    )
    credit_cost: Mapped[int] = mapped_column(Integer, default=0, comment="对应的生成 Credit")
    router_type: Mapped[str] = mapped_column(String(32), default="openai", comment="请求路由类型：openai/gemini")
    base_url: Mapped[str] = mapped_column(String(500), comment="模型网关 base_url")
    model_name: Mapped[str] = mapped_column(String(255), comment="模型名称")
    api_key: Mapped[str] = mapped_column(String(2000), comment="模型 API Key，支持逗号分隔多个 Key")
    stream: Mapped[int] = mapped_column(Integer, default=0, comment="是否启用流式调用")
    temperature: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), default=Decimal("1.00"), comment="模型 temperature"
    )
    top_p: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), default=Decimal("0.35"), comment="模型 top_p"
    )
    enable_deepthinking: Mapped[int] = mapped_column(
        Integer, default=0, comment="是否启用深度思考"
    )
    reasoning_effort: Mapped[str] = mapped_column(
        String(32), default="high", comment="推理强度"
    )
    max_tokens: Mapped[int] = mapped_column(Integer, default=65536, comment="最大输出 token")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=250, comment="首 token 超时秒数")
    total_timeout_seconds: Mapped[int] = mapped_column(Integer, default=250, comment="请求总超时秒数")
    enabled: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="最近更新时间",
    )


# 定义DynamicViewGenerationErrorLog。
class DynamicViewGenerationErrorLog(Base):
    """动态视图生成错误日志表，保存仅管理员可看的堆栈和排查上下文。"""

    __tablename__ = "dynamic_view_generation_error_log"
    __table_args__ = (
        Index("idx_dynamic_error_task", "task_id"),
        Index("idx_dynamic_error_request", "request_id"),
        Index("idx_dynamic_error_user_created", "user_id", "created_at"),
        {"comment": "动态视图生成错误日志表。"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="错误日志自增主键"
    )
    task_id: Mapped[str] = mapped_column(String(64), default="", comment="动态视图任务 ID")
    request_id: Mapped[str] = mapped_column(String(64), default="", comment="排查请求 ID")
    user_id: Mapped[str] = mapped_column(String(64), default="", comment="用户业务 ID")
    topic: Mapped[str] = mapped_column(String(255), default="", comment="生成主题")
    stage: Mapped[str] = mapped_column(String(64), default="", comment="失败阶段")
    node_key: Mapped[str] = mapped_column(String(128), default="", comment="失败节点键")
    node_title: Mapped[str] = mapped_column(String(128), default="", comment="失败节点标题")
    error_type: Mapped[str] = mapped_column(String(128), default="", comment="异常类型")
    error_message: Mapped[str] = mapped_column(Text, comment="异常消息")
    stack_trace: Mapped[str] = mapped_column(LONGTEXT, comment="异常堆栈")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="错误创建时间",
    )


class DynamicViewCharacterArchive(Base):
    """动态视图角色存档表，保存可供后续拟人化问答的关键实体。"""

    __tablename__ = "dynamic_view_character"
    __table_args__ = {"comment": "动态视图角色表，保存角色名称、题材、图标与人设字段。"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="角色表自增主键"
    )
    owner_type: Mapped[str] = mapped_column(
        String(32),
        default="game",
        comment="视图归属类型：game/knowledge",
    )
    # 这里只保留视图归属 ID，严禁下沉为数据库外键，关联校验统一放在业务层处理。
    owner_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
        comment="关联的视图归属 ID",
    )
    role_name: Mapped[str] = mapped_column(String(128), comment="角色名称")
    category_name: Mapped[str] = mapped_column(String(64), default="", comment="角色所属题材")
    icon: Mapped[str] = mapped_column(String(32), default="", comment="角色图标 Emoji")
    persona_prompt: Mapped[str] = mapped_column(LONGTEXT, comment="角色后续问答使用的人设 prompt")
    personality: Mapped[str] = mapped_column(LONGTEXT, default="", comment="角色行为图谱")
    scenario: Mapped[str] = mapped_column(LONGTEXT, default="", comment="角色场景设定")
    nsfw_setting: Mapped[str] = mapped_column(LONGTEXT, default="", comment="角色亲密与羁绊设定")
    author: Mapped[str] = mapped_column(String(64), default="system", comment="角色作者")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="角色创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="角色更新时间",
    )


# 定义AppUser。
class AppUser(Base):
    """应用用户表，统一保存评论与内容关联场景使用的基础用户资料。"""

    __tablename__ = "app_user"
    __table_args__ = {"comment": "应用用户表，保存昵称、头像、IP 与状态等标准信息。"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="用户表自增主键"
    )
    user_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, comment="业务用户标识"
    )
    nickname: Mapped[str] = mapped_column(String(64), comment="用户昵称")
    avatar: Mapped[str] = mapped_column(String(255), default="", comment="头像地址")
    ip_address: Mapped[str] = mapped_column(String(64), default="", comment="最近IP地址")
    ip_location: Mapped[str] = mapped_column(String(128), default="", comment="IP归属地")
    bio: Mapped[str] = mapped_column(String(255), default="", comment="用户简介")
    status: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="用户状态")
    deleted: Mapped[int] = mapped_column(Integer, default=0, index=True, comment="是否删除")
    last_active_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
        comment="最近活跃时间",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="用户创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="用户更新时间",
    )


# 定义EmailAuthAccount。
class EmailAuthAccount(Base):
    """邮箱登录账号表，邮箱明文只用于业务展示，验证与索引用 HMAC 摘要。"""

    __tablename__ = "email_auth_account"
    __table_args__ = (
        Index("idx_email_auth_account_user", "user_key", "status"),
        {"comment": "邮箱验证码登录账号表。"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="邮箱账号表自增主键"
    )
    email: Mapped[str] = mapped_column(String(255), comment="登录邮箱")
    email_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, comment="邮箱 HMAC-SHA256 摘要"
    )
    user_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, comment="用户业务 ID")
    status: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="账号状态")
    last_login_ip: Mapped[str] = mapped_column(String(64), default="", comment="最近登录 IP")
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True, comment="最近登录时间")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="账号创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="账号更新时间",
    )


# 定义EmailVerificationCode。
class EmailVerificationCode(Base):
    """邮箱验证码表，只保存验证码哈希、过期时间和尝试次数。"""

    __tablename__ = "email_verification_code"
    __table_args__ = (
        Index("idx_email_verify_hash_created", "email_hash", "created_at"),
        Index("idx_email_verify_ip_created", "request_ip", "created_at"),
        {"comment": "邮箱验证码表。"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="验证码表自增主键"
    )
    email: Mapped[str] = mapped_column(String(255), comment="目标邮箱")
    email_hash: Mapped[str] = mapped_column(String(64), index=True, comment="邮箱 HMAC-SHA256 摘要")
    code_hash: Mapped[str] = mapped_column(String(64), comment="验证码 HMAC-SHA256 摘要")
    purpose: Mapped[str] = mapped_column(String(32), default="login", index=True, comment="验证码用途")
    request_ip: Mapped[str] = mapped_column(String(64), default="", index=True, comment="请求 IP")
    browser_fingerprint: Mapped[str] = mapped_column(String(128), default="", index=True, comment="浏览器指纹")
    consumed: Mapped[int] = mapped_column(Integer, default=0, index=True, comment="是否已使用")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, comment="验证尝试次数")
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP, index=True, comment="验证码过期时间")
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True, comment="使用时间")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="验证码创建时间",
    )


class EmailAuthSession(Base):
    """邮箱登录会话表，保存已签发 token 的哈希以便后续失效与审计。"""

    __tablename__ = "email_auth_session"
    __table_args__ = (
        Index("idx_email_auth_session_user_status", "user_key", "status"),
        {"comment": "邮箱登录会话表。"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="邮箱会话表自增主键"
    )
    user_key: Mapped[str] = mapped_column(String(64), index=True, comment="用户业务 ID")
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, comment="会话 token SHA256 摘要"
    )
    request_ip: Mapped[str] = mapped_column(String(64), default="", comment="登录 IP")
    browser_fingerprint: Mapped[str] = mapped_column(String(128), default="", comment="浏览器指纹")
    status: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="会话状态")
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP, index=True, comment="会话过期时间")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="会话创建时间",
    )


# 定义DynamicViewComment。
class DynamicViewComment(Base):
    """动态视图评论表，保存视图评论正文与评论用户快照关联。"""

    __tablename__ = "dynamic_view_comment"
    __table_args__ = {"comment": "动态视图评论表，保存动态视图与用户关联评论。"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="评论表自增主键"
    )
    # 评论和动态视图之间只保留业务关联字段，禁止使用数据库外键，避免后续跨服务约束僵化。
    archive_id: Mapped[int] = mapped_column(
        "game_archive_id",
        BigInteger,
        index=True,
        comment="关联的动态视图ID",
    )
    view_type: Mapped[str] = mapped_column(
        String(32),
        default="game",
        index=True,
        comment="关联的动态视图类型：game/knowledge",
    )
    # 评论用户关联同样只在 Python 或 Spring 业务层校验，不在 MySQL 层建立外键约束。
    app_user_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
        comment="关联的用户表ID",
    )
    pid: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        index=True,
        comment="父评论ID，0表示顶级评论",
    )
    content: Mapped[str] = mapped_column(LONGTEXT, comment="评论正文")
    like_count: Mapped[int] = mapped_column(Integer, default=0, comment="点赞数量")
    reply_count: Mapped[int] = mapped_column(Integer, default=0, comment="回复数量")
    status: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="评论状态")
    is_pinned: Mapped[int] = mapped_column(
        Integer, default=0, index=True, comment="是否置顶"
    )
    ip_address: Mapped[str] = mapped_column(String(64), default="", comment="评论IP地址")
    ip_location: Mapped[str] = mapped_column(String(128), default="", comment="评论归属地")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="评论创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="评论更新时间",
    )


# 定义WebsiteContent。
class WebsiteContent(Base):
    """官网页面配置表，保存静态官网需要展示的真实配置数据。"""

    __tablename__ = "website_content"
    __table_args__ = {"comment": "官网页面配置表。"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="官网配置表自增主键"
    )
    content_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, comment="配置唯一键"
    )
    content_json: Mapped[str] = mapped_column(LONGTEXT, comment="官网配置 JSON")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP"),
        comment="配置创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="配置更新时间",
    )


# 定义WebsiteTopicBatch。
class WebsiteTopicBatch(Base):
    """官网主题 chip 批次表，每天 00:00 由 analyze 节点生成一批候选主题。"""

    __tablename__ = "website_topic_batch"
    __table_args__ = (
        UniqueConstraint("batch_date", "batch_index", name="uk_website_topic_batch_date_index"),
        Index("idx_website_topic_batch_date", "batch_date"),
        {"comment": "官网主题 chip 批次表。"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主题批次自增主键"
    )
    batch_date: Mapped[date] = mapped_column(
        Date, nullable=False, comment="批次日期"
    )
    batch_index: Mapped[int] = mapped_column(Integer, nullable=False, comment="批次序号")
    topics_json: Mapped[str] = mapped_column(LONGTEXT, comment="主题批次 JSON")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        # 执行text相关逻辑。
        server_default=text("CURRENT_TIMESTAMP"),
        comment="批次创建时间",
    )
