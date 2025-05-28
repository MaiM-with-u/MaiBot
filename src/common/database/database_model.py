from peewee import Model, DoubleField, IntegerField, BooleanField, TextField, FloatField, DateTimeField
from .database import db
import datetime
from ..logger_manager import get_logger

logger = get_logger("database_model")
# 请在此处定义您的数据库实例。
# 您需要取消注释并配置适合您的数据库的部分。
# 例如，对于 SQLite:
# db = SqliteDatabase('MaiBot.db')
#
# 对于 PostgreSQL:
# db = PostgresqlDatabase('your_db_name', user='your_user', password='your_password',
#                         host='localhost', port=5432)
#
# 对于 MySQL:
# db = MySQLDatabase('your_db_name', user='your_user', password='your_password',
#                    host='localhost', port=3306)


# 定义一个基础模型是一个好习惯，所有其他模型都应继承自它。
# 这允许您在一个地方为所有模型指定数据库。
class BaseModel(Model):
    class Meta:
        # 将下面的 'db' 替换为您实际的数据库实例变量名。
        database = db  # 例如: database = my_actual_db_instance
        pass  # 在用户定义数据库实例之前，此处为占位符


class ChatStreams(BaseModel):
    """
    用于存储流式记录数据的模型，类似于提供的 MongoDB 结构。
    """

    # stream_id: "a544edeb1a9b73e3e1d77dff36e41264"
    # 假设 stream_id 是唯一的，并为其创建索引以提高查询性能。
    stream_id = TextField(unique=True, index=True)

    # create_time: 1746096761.4490178 (时间戳，精确到小数点后7位)
    # DoubleField 用于存储浮点数，适合此类时间戳。
    create_time = DoubleField()

    # group_info 字段:
    #   platform: "qq"
    #   group_id: "941657197"
    #   group_name: "测试"
    group_platform = TextField(null=True)  # 群聊信息可能不存在
    group_id = TextField(null=True)
    group_name = TextField(null=True)

    # last_active_time: 1746623771.4825106 (时间戳，精确到小数点后7位)
    last_active_time = DoubleField()

    # platform: "qq" (顶层平台字段)
    platform = TextField()

    # user_info 字段:
    #   platform: "qq"
    #   user_id: "1787882683"
    #   user_nickname: "墨梓柒(IceSakurary)"
    #   user_cardname: ""
    user_platform = TextField()
    user_id = TextField()
    user_nickname = TextField()
    # user_cardname 可能为空字符串或不存在，设置 null=True 更具灵活性。
    user_cardname = TextField(null=True)

    class Meta:
        # 如果 BaseModel.Meta.database 已设置，则此模型将继承该数据库配置。
        # 如果不使用带有数据库实例的 BaseModel，或者想覆盖它，
        # 请取消注释并在下面设置数据库实例：
        # database = db
        table_name = "chat_streams"  # 可选：明确指定数据库中的表名


class LLMUsage(BaseModel):
    """
    用于存储 API 使用日志数据的模型。
    """

    model_name = TextField(index=True)  # 添加索引
    user_id = TextField(index=True)  # 添加索引
    request_type = TextField(index=True)  # 添加索引
    endpoint = TextField()
    prompt_tokens = IntegerField()
    completion_tokens = IntegerField()
    total_tokens = IntegerField()
    cost = DoubleField()
    status = TextField()
    timestamp = DateTimeField(index=True)  # 更改为 DateTimeField 并添加索引

    class Meta:
        # 如果 BaseModel.Meta.database 已设置，则此模型将继承该数据库配置。
        # database = db
        table_name = "llm_usage"


class Emoji(BaseModel):
    """表情包"""

    full_path = TextField(unique=True, index=True)  # 文件的完整路径 (包括文件名)
    format = TextField()  # 图片格式
    emoji_hash = TextField(index=True)  # 表情包的哈希值
    description = TextField()  # 表情包的描述
    query_count = IntegerField(default=0)  # 查询次数（用于统计表情包被查询描述的次数）
    is_registered = BooleanField(default=False)  # 是否已注册
    is_banned = BooleanField(default=False)  # 是否被禁止注册
    # emotion: list[str]  # 表情包的情感标签 - 存储为文本，应用层处理序列化/反序列化
    emotion = TextField(null=True)
    record_time = FloatField()  # 记录时间（被创建的时间）
    register_time = FloatField(null=True)  # 注册时间（被注册为可用表情包的时间）
    usage_count = IntegerField(default=0)  # 使用次数（被使用的次数）
    last_used_time = FloatField(null=True)  # 上次使用时间

    class Meta:
        # database = db # 继承自 BaseModel
        table_name = "emoji"


class Messages(BaseModel):
    """
    用于存储消息数据的模型。
    """

    message_id = TextField(index=True)  # 消息 ID (更改自 IntegerField)
    time = DoubleField()  # 消息时间戳

    chat_id = TextField(index=True)  # 对应的 ChatStreams stream_id

    # 从 chat_info 扁平化而来的字段
    chat_info_stream_id = TextField()
    chat_info_platform = TextField()
    chat_info_user_platform = TextField()
    chat_info_user_id = TextField()
    chat_info_user_nickname = TextField()
    chat_info_user_cardname = TextField(null=True)
    chat_info_group_platform = TextField(null=True)  # 群聊信息可能不存在
    chat_info_group_id = TextField(null=True)
    chat_info_group_name = TextField(null=True)
    chat_info_create_time = DoubleField()
    chat_info_last_active_time = DoubleField()

    # 从顶层 user_info 扁平化而来的字段 (消息发送者信息)
    user_platform = TextField()
    user_id = TextField()
    user_nickname = TextField()
    user_cardname = TextField(null=True)

    processed_plain_text = TextField(null=True)  # 处理后的纯文本消息
    display_message = TextField(null=True)  # 显示的消息
    detailed_plain_text = TextField(null=True)  # 详细的纯文本消息
    memorized_times = IntegerField(default=0)  # 被记忆的次数

    class Meta:
        # database = db # 继承自 BaseModel
        table_name = "messages"


class Images(BaseModel):
    """
    用于存储图像信息的模型。
    """

    emoji_hash = TextField(index=True)  # 图像的哈希值
    description = TextField(null=True)  # 图像的描述
    path = TextField(unique=True)  # 图像文件的路径
    timestamp = FloatField()  # 时间戳
    type = TextField()  # 图像类型，例如 "emoji"

    class Meta:
        # database = db # 继承自 BaseModel
        table_name = "images"


class ImageDescriptions(BaseModel):
    """
    用于存储图像描述信息的模型。
    """

    type = TextField()  # 类型，例如 "emoji"
    image_description_hash = TextField(index=True)  # 图像的哈希值
    description = TextField()  # 图像的描述
    timestamp = FloatField()  # 时间戳

    class Meta:
        # database = db # 继承自 BaseModel
        table_name = "image_descriptions"


class OnlineTime(BaseModel):
    """
    用于存储在线时长记录的模型。
    """

    # timestamp: "$date": "2025-05-01T18:52:18.191Z" (存储为字符串)
    timestamp = TextField(default=datetime.datetime.now)  # 时间戳
    duration = IntegerField()  # 时长，单位分钟
    start_timestamp = DateTimeField(default=datetime.datetime.now)
    end_timestamp = DateTimeField(index=True)

    class Meta:
        # database = db # 继承自 BaseModel
        table_name = "online_time"


class PersonInfo(BaseModel):
    """
    用于存储个人信息数据的模型。
    """

    person_id = TextField(unique=True, index=True)  # 个人唯一ID
    person_name = TextField(null=True)  # 个人名称 (允许为空)
    name_reason = TextField(null=True)  # 名称设定的原因
    platform = TextField()  # 平台
    user_id = TextField(index=True)  # 用户ID
    nickname = TextField()  # 用户昵称
    relationship_value = IntegerField(default=0)  # 关系值
    know_time = FloatField()  # 认识时间 (时间戳)
    msg_interval = IntegerField()  # 消息间隔
    # msg_interval_list: 存储为 JSON 字符串的列表
    msg_interval_list = TextField(null=True)

    class Meta:
        # database = db # 继承自 BaseModel
        table_name = "person_info"


class Knowledges(BaseModel):
    """
    用于存储知识库条目的模型。
    """

    content = TextField()  # 知识内容的文本
    embedding = TextField()  # 知识内容的嵌入向量，存储为 JSON 字符串的浮点数列表
    # 可以添加其他元数据字段，如 source, create_time 等

    class Meta:
        # database = db # 继承自 BaseModel
        table_name = "knowledges"


class ThinkingLog(BaseModel):
    chat_id = TextField(index=True)
    trigger_text = TextField(null=True)
    response_text = TextField(null=True)

    # Store complex dicts/lists as JSON strings
    trigger_info_json = TextField(null=True)
    response_info_json = TextField(null=True)
    timing_results_json = TextField(null=True)
    chat_history_json = TextField(null=True)
    chat_history_in_thinking_json = TextField(null=True)
    chat_history_after_response_json = TextField(null=True)
    heartflow_data_json = TextField(null=True)
    reasoning_data_json = TextField(null=True)

    # Add a timestamp for the log entry itself
    # Ensure you have: from peewee import DateTimeField
    # And: import datetime
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        table_name = "thinking_logs"


class RecalledMessages(BaseModel):
    """
    用于存储撤回消息记录的模型。
    """

    message_id = TextField(index=True)  # 被撤回的消息 ID
    time = DoubleField()  # 撤回操作发生的时间戳
    stream_id = TextField()  # 对应的 ChatStreams stream_id

    class Meta:
        table_name = "recalled_messages"


class GraphNodes(BaseModel):
    """
    用于存储记忆图节点的模型
    """

    concept = TextField(unique=True, index=True)  # 节点概念
    memory_items = TextField()  # JSON格式存储的记忆列表
    hash = TextField()  # 节点哈希值
    created_time = FloatField()  # 创建时间戳
    last_modified = FloatField()  # 最后修改时间戳

    class Meta:
        table_name = "graph_nodes"


class GraphEdges(BaseModel):
    """
    用于存储记忆图边的模型
    """

    source = TextField(index=True)  # 源节点
    target = TextField(index=True)  # 目标节点
    strength = IntegerField()  # 连接强度
    hash = TextField()  # 边哈希值
    created_time = FloatField()  # 创建时间戳
    last_modified = FloatField()  # 最后修改时间戳

    class Meta:
        table_name = "graph_edges"


def create_tables():
    """
    创建所有在模型中定义的数据库表。
    """
    with db:
        db.create_tables(
            [
                ChatStreams,
                LLMUsage,
                Emoji,
                Messages,
                Images,
                ImageDescriptions,
                OnlineTime,
                PersonInfo,
                Knowledges,
                ThinkingLog,
                RecalledMessages,  # 添加新模型
                GraphNodes,  # 添加图节点表
                GraphEdges,  # 添加图边表
            ]
        )


def initialize_database():
    """
    检查所有定义的表是否存在，如果不存在则创建它们。
    检查所有表的所有字段是否存在，如果缺失则警告用户并退出程序。
    """
    import sys

    models = [
        ChatStreams,
        LLMUsage,
        Emoji,
        Messages,
        Images,
        ImageDescriptions,
        OnlineTime,
        PersonInfo,
        Knowledges,
        ThinkingLog,
        RecalledMessages,
        GraphNodes,  # 添加图节点表
        GraphEdges,  # 添加图边表
    ]

    needs_creation = False
    try:
        with db:  # 管理 table_exists 检查的连接
            for model in models:
                table_name = model._meta.table_name
                if not db.table_exists(model):
                    logger.warning(f"表 '{table_name}' 未找到。")
                    needs_creation = True
                    break  # 一个表丢失，无需进一步检查。
            if not needs_creation:
                # 检查字段
                for model in models:
                    table_name = model._meta.table_name
                    cursor = db.execute_sql(f"PRAGMA table_info('{table_name}')")
                    existing_columns = {row[1] for row in cursor.fetchall()}
                    model_fields = model._meta.fields
                    for field_name in model_fields:
                        if field_name not in existing_columns:
                            logger.error(f"表 '{table_name}' 缺失字段 '{field_name}'，请手动迁移数据库结构后重启程序。")
                            sys.exit(1)
    except Exception as e:
        logger.exception(f"检查表或字段是否存在时出错: {e}")
        # 如果检查失败（例如数据库不可用），则退出
        return

    if needs_creation:
        logger.info("正在初始化数据库：一个或多个表丢失。正在尝试创建所有定义的表...")
        try:
            create_tables()  # 此函数有其自己的 'with db:' 上下文管理。
            logger.info("数据库表创建过程完成。")
        except Exception as e:
            logger.exception(f"创建表期间出错: {e}")
    else:
        logger.info("所有数据库表及字段均已存在。")


# 模块加载时调用初始化函数
initialize_database()
