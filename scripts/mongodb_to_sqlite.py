import os
import json
import sys  # 新增系统模块导入

# import time
import pickle
from pathlib import Path

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from typing import Dict, Any, List, Optional, Type
from dataclasses import dataclass, field
from datetime import datetime
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from peewee import Model, Field, IntegrityError

# Rich 进度条和显示组件
from rich.console import Console
from rich.progress import (
    Progress,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
    TimeElapsedColumn,
    SpinnerColumn,
)
from rich.table import Table
from rich.panel import Panel
# from rich.text import Text

from src.common.database.database import db
from src.common.database.database_model import (
    ChatStreams,
    LLMUsage,
    Emoji,
    Messages,
    Images,
    ImageDescriptions,
    PersonInfo,
    Knowledges,
    ThinkingLog,
    GraphNodes,
    GraphEdges,
)
from src.common.logger_manager import get_logger

logger = get_logger("mongodb_to_sqlite")

ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@dataclass
class MigrationConfig:
    """迁移配置类"""

    mongo_collection: str
    target_model: Type[Model]
    field_mapping: Dict[str, str]
    batch_size: int = 500
    enable_validation: bool = True
    skip_duplicates: bool = True
    unique_fields: List[str] = field(default_factory=list)  # 用于重复检查的字段


# 数据验证相关类已移除 - 用户要求不要数据验证


@dataclass
class MigrationCheckpoint:
    """迁移断点数据"""

    collection_name: str
    processed_count: int
    last_processed_id: Any
    timestamp: datetime
    batch_errors: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class MigrationStats:
    """迁移统计信息"""

    total_documents: int = 0
    processed_count: int = 0
    success_count: int = 0
    error_count: int = 0
    skipped_count: int = 0
    duplicate_count: int = 0
    validation_errors: int = 0
    batch_insert_count: int = 0
    errors: List[Dict[str, Any]] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    def add_error(self, doc_id: Any, error: str, doc_data: Optional[Dict] = None):
        """添加错误记录"""
        self.errors.append(
            {"doc_id": str(doc_id), "error": error, "timestamp": datetime.now().isoformat(), "doc_data": doc_data}
        )
        self.error_count += 1

    def add_validation_error(self, doc_id: Any, field: str, error: str):
        """添加验证错误"""
        self.add_error(doc_id, f"验证失败 - {field}: {error}")
        self.validation_errors += 1


class MongoToSQLiteMigrator:
    """MongoDB到SQLite数据迁移器 - 使用Peewee ORM"""

    def __init__(self, mongo_uri: Optional[str] = None, database_name: Optional[str] = None):
        self.database_name = database_name or os.getenv("DATABASE_NAME", "MegBot")
        self.mongo_uri = mongo_uri or self._build_mongo_uri()
        self.mongo_client: Optional[MongoClient] = None
        self.mongo_db = None

        # 迁移配置
        self.migration_configs = self._initialize_migration_configs()

        # 进度条控制台
        self.console = Console()
        # 检查点目录
        self.checkpoint_dir = Path(os.path.join(ROOT_PATH, "data", "checkpoints"))
        self.checkpoint_dir.mkdir(exist_ok=True)

        # 验证规则已禁用
        self.validation_rules = self._initialize_validation_rules()

    def _build_mongo_uri(self) -> str:
        """构建MongoDB连接URI"""
        if mongo_uri := os.getenv("MONGODB_URI"):
            return mongo_uri

        user = os.getenv("MONGODB_USER")
        password = os.getenv("MONGODB_PASS")
        host = os.getenv("MONGODB_HOST", "localhost")
        port = os.getenv("MONGODB_PORT", "27017")
        auth_source = os.getenv("MONGODB_AUTH_SOURCE", "admin")

        if user and password:
            return f"mongodb://{user}:{password}@{host}:{port}/{self.database_name}?authSource={auth_source}"
        else:
            return f"mongodb://{host}:{port}/{self.database_name}"

    def _initialize_migration_configs(self) -> List[MigrationConfig]:
        """初始化迁移配置"""
        return [  # 表情包迁移配置
            MigrationConfig(
                mongo_collection="emoji",
                target_model=Emoji,
                field_mapping={
                    "full_path": "full_path",
                    "format": "format",
                    "hash": "emoji_hash",
                    "description": "description",
                    "emotion": "emotion",
                    "usage_count": "usage_count",
                    "last_used_time": "last_used_time",
                    # record_time字段将在转换时自动设置为当前时间
                },
                enable_validation=False,  # 禁用数据验证
                unique_fields=["full_path", "emoji_hash"],
            ),
            # 聊天流迁移配置
            MigrationConfig(
                mongo_collection="chat_streams",
                target_model=ChatStreams,
                field_mapping={
                    "stream_id": "stream_id",
                    "create_time": "create_time",
                    "group_info.platform": "group_platform",  # 由于Mongodb处理私聊时会让group_info值为null，而新的数据库不允许为null，所以私聊聊天流是没法迁移的，等更新吧。
                    "group_info.group_id": "group_id",  # 同上
                    "group_info.group_name": "group_name",  # 同上
                    "last_active_time": "last_active_time",
                    "platform": "platform",
                    "user_info.platform": "user_platform",
                    "user_info.user_id": "user_id",
                    "user_info.user_nickname": "user_nickname",
                    "user_info.user_cardname": "user_cardname",
                },
                enable_validation=False,  # 禁用数据验证
                unique_fields=["stream_id"],
            ),
            # LLM使用记录迁移配置
            MigrationConfig(
                mongo_collection="llm_usage",
                target_model=LLMUsage,
                field_mapping={
                    "model_name": "model_name",
                    "user_id": "user_id",
                    "request_type": "request_type",
                    "endpoint": "endpoint",
                    "prompt_tokens": "prompt_tokens",
                    "completion_tokens": "completion_tokens",
                    "total_tokens": "total_tokens",
                    "cost": "cost",
                    "status": "status",
                    "timestamp": "timestamp",
                },
                enable_validation=True,  # 禁用数据验证"
                unique_fields=["user_id", "prompt_tokens", "completion_tokens", "total_tokens", "cost"],  # 组合唯一性
            ),
            # 消息迁移配置
            MigrationConfig(
                mongo_collection="messages",
                target_model=Messages,
                field_mapping={
                    "message_id": "message_id",
                    "time": "time",
                    "chat_id": "chat_id",
                    "chat_info.stream_id": "chat_info_stream_id",
                    "chat_info.platform": "chat_info_platform",
                    "chat_info.user_info.platform": "chat_info_user_platform",
                    "chat_info.user_info.user_id": "chat_info_user_id",
                    "chat_info.user_info.user_nickname": "chat_info_user_nickname",
                    "chat_info.user_info.user_cardname": "chat_info_user_cardname",
                    "chat_info.group_info.platform": "chat_info_group_platform",
                    "chat_info.group_info.group_id": "chat_info_group_id",
                    "chat_info.group_info.group_name": "chat_info_group_name",
                    "chat_info.create_time": "chat_info_create_time",
                    "chat_info.last_active_time": "chat_info_last_active_time",
                    "user_info.platform": "user_platform",
                    "user_info.user_id": "user_id",
                    "user_info.user_nickname": "user_nickname",
                    "user_info.user_cardname": "user_cardname",
                    "processed_plain_text": "processed_plain_text",
                    "detailed_plain_text": "detailed_plain_text",
                    "memorized_times": "memorized_times",
                },
                enable_validation=False,  # 禁用数据验证
                unique_fields=["message_id"],
            ),
            # 图片迁移配置
            MigrationConfig(
                mongo_collection="images",
                target_model=Images,
                field_mapping={
                    "hash": "emoji_hash",
                    "description": "description",
                    "path": "path",
                    "timestamp": "timestamp",
                    "type": "type",
                },
                unique_fields=["path"],
            ),
            # 图片描述迁移配置
            MigrationConfig(
                mongo_collection="image_descriptions",
                target_model=ImageDescriptions,
                field_mapping={
                    "type": "type",
                    "hash": "image_description_hash",
                    "description": "description",
                    "timestamp": "timestamp",
                },
                unique_fields=["image_description_hash", "type"],
            ),
            # 个人信息迁移配置
            MigrationConfig(
                mongo_collection="person_info",
                target_model=PersonInfo,
                field_mapping={
                    "person_id": "person_id",
                    "person_name": "person_name",
                    "name_reason": "name_reason",
                    "platform": "platform",
                    "user_id": "user_id",
                    "nickname": "nickname",
                    "relationship_value": "relationship_value",
                    "konw_time": "know_time",
                    "msg_interval": "msg_interval",
                    "msg_interval_list": "msg_interval_list",
                },
                unique_fields=["person_id"],
            ),
            # 知识库迁移配置
            MigrationConfig(
                mongo_collection="knowledges",
                target_model=Knowledges,
                field_mapping={"content": "content", "embedding": "embedding"},
                unique_fields=["content"],  # 假设内容唯一
            ),
            # 思考日志迁移配置
            MigrationConfig(
                mongo_collection="thinking_log",
                target_model=ThinkingLog,
                field_mapping={
                    "chat_id": "chat_id",
                    "trigger_text": "trigger_text",
                    "response_text": "response_text",
                    "trigger_info": "trigger_info_json",
                    "response_info": "response_info_json",
                    "timing_results": "timing_results_json",
                    "chat_history": "chat_history_json",
                    "chat_history_in_thinking": "chat_history_in_thinking_json",
                    "chat_history_after_response": "chat_history_after_response_json",
                    "heartflow_data": "heartflow_data_json",
                    "reasoning_data": "reasoning_data_json",
                },
                unique_fields=["chat_id", "trigger_text"],
            ),
            # 图节点迁移配置
            MigrationConfig(
                mongo_collection="graph_data.nodes",
                target_model=GraphNodes,
                field_mapping={
                    "concept": "concept",
                    "memory_items": "memory_items",
                    "hash": "hash",
                    "created_time": "created_time",
                    "last_modified": "last_modified",
                },
                unique_fields=["concept"],
            ),
            # 图边迁移配置
            MigrationConfig(
                mongo_collection="graph_data.edges",
                target_model=GraphEdges,
                field_mapping={
                    "source": "source",
                    "target": "target",
                    "strength": "strength",
                    "hash": "hash",
                    "created_time": "created_time",
                    "last_modified": "last_modified",
                },
                unique_fields=["source", "target"],  # 组合唯一性
            ),
        ]

    def _initialize_validation_rules(self) -> Dict[str, Any]:
        """数据验证已禁用 - 返回空字典"""
        return {}

    def connect_mongodb(self) -> bool:
        """连接到MongoDB"""
        try:
            self.mongo_client = MongoClient(
                self.mongo_uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=10000, maxPoolSize=10
            )

            # 测试连接
            self.mongo_client.admin.command("ping")
            self.mongo_db = self.mongo_client[self.database_name]

            logger.info(f"成功连接到MongoDB: {self.database_name}")
            return True

        except ConnectionFailure as e:
            logger.error(f"MongoDB连接失败: {e}")
            return False
        except Exception as e:
            logger.error(f"MongoDB连接异常: {e}")
            return False

    def disconnect_mongodb(self):
        """断开MongoDB连接"""
        if self.mongo_client:
            self.mongo_client.close()
            logger.info("MongoDB连接已关闭")

    def _get_nested_value(self, document: Dict[str, Any], field_path: str) -> Any:
        """获取嵌套字段的值"""
        if "." not in field_path:
            return document.get(field_path)

        parts = field_path.split(".")
        value = document

        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None

            if value is None:
                break

        return value

    def _convert_field_value(self, value: Any, target_field: Field) -> Any:
        """根据目标字段类型转换值"""
        if value is None:
            return None

        field_type = target_field.__class__.__name__

        try:
            if target_field.name == "record_time" and field_type == "DateTimeField":
                return datetime.now()

            if field_type in ["CharField", "TextField"]:
                if isinstance(value, (list, dict)):
                    return json.dumps(value, ensure_ascii=False)
                return str(value) if value is not None else ""

            elif field_type == "IntegerField":
                if isinstance(value, str):
                    # 处理字符串数字
                    clean_value = value.strip()
                    if clean_value.replace(".", "").replace("-", "").isdigit():
                        return int(float(clean_value))
                    return 0
                return int(value) if value is not None else 0

            elif field_type in ["FloatField", "DoubleField"]:
                return float(value) if value is not None else 0.0

            elif field_type == "BooleanField":
                if isinstance(value, str):
                    return value.lower() in ("true", "1", "yes", "on")
                return bool(value)

            elif field_type == "DateTimeField":
                if isinstance(value, (int, float)):
                    return datetime.fromtimestamp(value)
                elif isinstance(value, str):
                    try:
                        # 尝试解析ISO格式日期
                        return datetime.fromisoformat(value.replace("Z", "+00:00"))
                    except ValueError:
                        try:
                            # 尝试解析时间戳字符串
                            return datetime.fromtimestamp(float(value))
                        except ValueError:
                            return datetime.now()
                return datetime.now()

            return value

        except (ValueError, TypeError) as e:
            logger.warning(f"字段值转换失败 ({field_type}): {value} -> {e}")
            return self._get_default_value_for_field(target_field)

    def _get_default_value_for_field(self, field: Field) -> Any:
        """获取字段的默认值"""
        field_type = field.__class__.__name__

        if hasattr(field, "default") and field.default is not None:
            return field.default

        if field.null:
            return None

        # 根据字段类型返回默认值
        if field_type in ["CharField", "TextField"]:
            return ""
        elif field_type == "IntegerField":
            return 0
        elif field_type in ["FloatField", "DoubleField"]:
            return 0.0
        elif field_type == "BooleanField":
            return False
        elif field_type == "DateTimeField":
            return datetime.now()

        return None

    def _validate_data(self, collection_name: str, data: Dict[str, Any], doc_id: Any, stats: MigrationStats) -> bool:
        """数据验证已禁用 - 始终返回True"""
        return True

    def _save_checkpoint(self, collection_name: str, processed_count: int, last_id: Any):
        """保存迁移断点"""
        checkpoint = MigrationCheckpoint(
            collection_name=collection_name,
            processed_count=processed_count,
            last_processed_id=last_id,
            timestamp=datetime.now(),
        )

        checkpoint_file = self.checkpoint_dir / f"{collection_name}_checkpoint.pkl"
        try:
            with open(checkpoint_file, "wb") as f:
                pickle.dump(checkpoint, f)
        except Exception as e:
            logger.warning(f"保存断点失败: {e}")

    def _load_checkpoint(self, collection_name: str) -> Optional[MigrationCheckpoint]:
        """加载迁移断点"""
        checkpoint_file = self.checkpoint_dir / f"{collection_name}_checkpoint.pkl"
        if not checkpoint_file.exists():
            return None

        try:
            with open(checkpoint_file, "rb") as f:
                return pickle.load(f)
        except Exception as e:
            logger.warning(f"加载断点失败: {e}")
            return None

    def _batch_insert(self, model: Type[Model], data_list: List[Dict[str, Any]]) -> int:
        """批量插入数据"""
        if not data_list:
            return 0

        success_count = 0
        try:
            with db.atomic():
                # 分批插入，避免SQL语句过长
                batch_size = 100
                for i in range(0, len(data_list), batch_size):
                    batch = data_list[i : i + batch_size]
                    model.insert_many(batch).execute()
                    success_count += len(batch)
        except Exception as e:
            logger.error(f"批量插入失败: {e}")
            # 如果批量插入失败，尝试逐个插入
            for data in data_list:
                try:
                    model.create(**data)
                    success_count += 1
                except Exception:
                    pass  # 忽略单个插入失败

        return success_count

    def _check_duplicate_by_unique_fields(
        self, model: Type[Model], data: Dict[str, Any], unique_fields: List[str]
    ) -> bool:
        """根据唯一字段检查重复"""
        if not unique_fields:
            return False

        try:
            query = model.select()
            for field_name in unique_fields:
                if field_name in data and data[field_name] is not None:
                    field_obj = getattr(model, field_name)
                    query = query.where(field_obj == data[field_name])

            return query.exists()
        except Exception as e:
            logger.debug(f"重复检查失败: {e}")
            return False

    def _create_model_instance(self, model: Type[Model], data: Dict[str, Any]) -> Optional[Model]:
        """使用ORM创建模型实例"""
        try:
            # 过滤掉不存在的字段
            valid_data = {}
            for field_name, value in data.items():
                if hasattr(model, field_name):
                    valid_data[field_name] = value
                else:
                    logger.debug(f"跳过未知字段: {field_name}")

            # 创建实例
            instance = model.create(**valid_data)
            return instance

        except IntegrityError as e:
            # 处理唯一约束冲突等完整性错误
            logger.debug(f"完整性约束冲突: {e}")
            return None
        except Exception as e:
            logger.error(f"创建模型实例失败: {e}")
            return None

    def migrate_collection(self, config: MigrationConfig) -> MigrationStats:
        """迁移单个集合 - 使用优化的批量插入和进度条"""
        stats = MigrationStats()
        stats.start_time = datetime.now()

        # 检查是否有断点
        checkpoint = self._load_checkpoint(config.mongo_collection)
        start_from_id = checkpoint.last_processed_id if checkpoint else None
        if checkpoint:
            stats.processed_count = checkpoint.processed_count
            logger.info(f"从断点恢复: 已处理 {checkpoint.processed_count} 条记录")

        logger.info(f"开始迁移: {config.mongo_collection} -> {config.target_model._meta.table_name}")

        try:
            # 获取MongoDB集合
            mongo_collection = self.mongo_db[config.mongo_collection]

            # 构建查询条件（用于断点恢复）
            query = {}
            if start_from_id:
                query = {"_id": {"$gt": start_from_id}}

            stats.total_documents = mongo_collection.count_documents(query)

            if stats.total_documents == 0:
                logger.warning(f"集合 {config.mongo_collection} 为空，跳过迁移")
                return stats

            logger.info(f"待迁移文档数量: {stats.total_documents}")

            # 创建Rich进度条
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=self.console,
                refresh_per_second=10,
            ) as progress:
                task = progress.add_task(f"迁移 {config.mongo_collection}", total=stats.total_documents)
                # 批量处理数据
                batch_data = []
                batch_count = 0
                last_processed_id = None

                for mongo_doc in mongo_collection.find(query).batch_size(config.batch_size):
                    try:
                        doc_id = mongo_doc.get("_id", "unknown")
                        last_processed_id = doc_id

                        # 构建目标数据
                        target_data = {}
                        for mongo_field, sqlite_field in config.field_mapping.items():
                            value = self._get_nested_value(mongo_doc, mongo_field)

                            # 获取目标字段对象并转换类型
                            if hasattr(config.target_model, sqlite_field):
                                field_obj = getattr(config.target_model, sqlite_field)
                                converted_value = self._convert_field_value(value, field_obj)
                                target_data[sqlite_field] = converted_value

                        # 数据验证已禁用
                        # if config.enable_validation:
                        #     if not self._validate_data(config.mongo_collection, target_data, doc_id, stats):
                        #         stats.skipped_count += 1
                        #         continue

                        # 重复检查
                        if config.skip_duplicates and self._check_duplicate_by_unique_fields(
                            config.target_model, target_data, config.unique_fields
                        ):
                            stats.duplicate_count += 1
                            stats.skipped_count += 1
                            logger.debug(f"跳过重复记录: {doc_id}")
                            continue

                        # 添加到批量数据
                        batch_data.append(target_data)
                        stats.processed_count += 1

                        # 执行批量插入
                        if len(batch_data) >= config.batch_size:
                            success_count = self._batch_insert(config.target_model, batch_data)
                            stats.success_count += success_count
                            stats.batch_insert_count += 1

                            # 保存断点
                            self._save_checkpoint(config.mongo_collection, stats.processed_count, last_processed_id)

                            batch_data.clear()
                            batch_count += 1

                            # 更新进度条
                            progress.update(task, advance=config.batch_size)

                    except Exception as e:
                        doc_id = mongo_doc.get("_id", "unknown")
                        stats.add_error(doc_id, f"处理文档异常: {e}", mongo_doc)
                        logger.error(f"处理文档失败 (ID: {doc_id}): {e}")

                # 处理剩余的批量数据
                if batch_data:
                    success_count = self._batch_insert(config.target_model, batch_data)
                    stats.success_count += success_count
                    stats.batch_insert_count += 1
                    progress.update(task, advance=len(batch_data))

                # 完成进度条
                progress.update(task, completed=stats.total_documents)

            stats.end_time = datetime.now()
            duration = stats.end_time - stats.start_time

            logger.info(
                f"迁移完成: {config.mongo_collection} -> {config.target_model._meta.table_name}\n"
                f"总计: {stats.total_documents}, 成功: {stats.success_count}, "
                f"错误: {stats.error_count}, 跳过: {stats.skipped_count}, 重复: {stats.duplicate_count}\n"
                f"耗时: {duration.total_seconds():.2f}秒, 批量插入次数: {stats.batch_insert_count}"
            )

            # 清理断点文件
            checkpoint_file = self.checkpoint_dir / f"{config.mongo_collection}_checkpoint.pkl"
            if checkpoint_file.exists():
                checkpoint_file.unlink()

        except Exception as e:
            logger.error(f"迁移集合 {config.mongo_collection} 时发生异常: {e}")
            stats.add_error("collection_error", str(e))

        return stats

    def migrate_all(self) -> Dict[str, MigrationStats]:
        """执行所有迁移任务"""
        logger.info("开始执行数据库迁移...")

        if not self.connect_mongodb():
            logger.error("无法连接到MongoDB，迁移终止")
            return {}

        all_stats = {}

        try:
            # 创建总体进度表格
            total_collections = len(self.migration_configs)
            self.console.print(
                Panel(
                    f"[bold blue]MongoDB 到 SQLite 数据迁移[/bold blue]\n"
                    f"[yellow]总集合数: {total_collections}[/yellow]",
                    title="迁移开始",
                    expand=False,
                )
            )
            for idx, config in enumerate(self.migration_configs, 1):
                self.console.print(
                    f"\n[bold green]正在处理集合 {idx}/{total_collections}: {config.mongo_collection}[/bold green]"
                )
                stats = self.migrate_collection(config)
                all_stats[config.mongo_collection] = stats

                # 显示单个集合的快速统计
                if stats.processed_count > 0:
                    success_rate = stats.success_count / stats.processed_count * 100
                    if success_rate >= 95:
                        status_emoji = "✅"
                        status_color = "bright_green"
                    elif success_rate >= 80:
                        status_emoji = "⚠️"
                        status_color = "yellow"
                    else:
                        status_emoji = "❌"
                        status_color = "red"

                    self.console.print(
                        f"   {status_emoji} [{status_color}]完成: {stats.success_count}/{stats.processed_count} "
                        f"({success_rate:.1f}%) 错误: {stats.error_count}[/{status_color}]"
                    )

                # 错误率检查
                if stats.processed_count > 0:
                    error_rate = stats.error_count / stats.processed_count
                    if error_rate > 0.1:  # 错误率超过10%
                        self.console.print(
                            f"   [red]⚠️  警告: 错误率较高 {error_rate:.1%} "
                            f"({stats.error_count}/{stats.processed_count})[/red]"
                        )

        finally:
            self.disconnect_mongodb()

        self._print_migration_summary(all_stats)
        return all_stats

    def _print_migration_summary(self, all_stats: Dict[str, MigrationStats]):
        """使用Rich打印美观的迁移汇总信息"""
        # 计算总体统计
        total_processed = sum(stats.processed_count for stats in all_stats.values())
        total_success = sum(stats.success_count for stats in all_stats.values())
        total_errors = sum(stats.error_count for stats in all_stats.values())
        total_skipped = sum(stats.skipped_count for stats in all_stats.values())
        total_duplicates = sum(stats.duplicate_count for stats in all_stats.values())
        total_validation_errors = sum(stats.validation_errors for stats in all_stats.values())
        total_batch_inserts = sum(stats.batch_insert_count for stats in all_stats.values())

        # 计算总耗时
        total_duration_seconds = 0
        for stats in all_stats.values():
            if stats.start_time and stats.end_time:
                duration = stats.end_time - stats.start_time
                total_duration_seconds += duration.total_seconds()

        # 创建详细统计表格
        table = Table(title="[bold blue]数据迁移汇总报告[/bold blue]", show_header=True, header_style="bold magenta")
        table.add_column("集合名称", style="cyan", width=20)
        table.add_column("文档总数", justify="right", style="blue")
        table.add_column("处理数量", justify="right", style="green")
        table.add_column("成功数量", justify="right", style="green")
        table.add_column("错误数量", justify="right", style="red")
        table.add_column("跳过数量", justify="right", style="yellow")
        table.add_column("重复数量", justify="right", style="bright_yellow")
        table.add_column("验证错误", justify="right", style="red")
        table.add_column("批次数", justify="right", style="purple")
        table.add_column("成功率", justify="right", style="bright_green")
        table.add_column("耗时(秒)", justify="right", style="blue")

        for collection_name, stats in all_stats.items():
            success_rate = (stats.success_count / stats.processed_count * 100) if stats.processed_count > 0 else 0
            duration = 0
            if stats.start_time and stats.end_time:
                duration = (stats.end_time - stats.start_time).total_seconds()

            # 根据成功率设置颜色
            if success_rate >= 95:
                success_rate_style = "[bright_green]"
            elif success_rate >= 80:
                success_rate_style = "[yellow]"
            else:
                success_rate_style = "[red]"

            table.add_row(
                collection_name,
                str(stats.total_documents),
                str(stats.processed_count),
                str(stats.success_count),
                f"[red]{stats.error_count}[/red]" if stats.error_count > 0 else "0",
                f"[yellow]{stats.skipped_count}[/yellow]" if stats.skipped_count > 0 else "0",
                f"[bright_yellow]{stats.duplicate_count}[/bright_yellow]" if stats.duplicate_count > 0 else "0",
                f"[red]{stats.validation_errors}[/red]" if stats.validation_errors > 0 else "0",
                str(stats.batch_insert_count),
                f"{success_rate_style}{success_rate:.1f}%[/{success_rate_style[1:]}",
                f"{duration:.2f}",
            )

        # 添加总计行
        total_success_rate = (total_success / total_processed * 100) if total_processed > 0 else 0
        if total_success_rate >= 95:
            total_rate_style = "[bright_green]"
        elif total_success_rate >= 80:
            total_rate_style = "[yellow]"
        else:
            total_rate_style = "[red]"

        table.add_section()
        table.add_row(
            "[bold]总计[/bold]",
            f"[bold]{sum(stats.total_documents for stats in all_stats.values())}[/bold]",
            f"[bold]{total_processed}[/bold]",
            f"[bold]{total_success}[/bold]",
            f"[bold red]{total_errors}[/bold red]" if total_errors > 0 else "[bold]0[/bold]",
            f"[bold yellow]{total_skipped}[/bold yellow]" if total_skipped > 0 else "[bold]0[/bold]",
            f"[bold bright_yellow]{total_duplicates}[/bold bright_yellow]"
            if total_duplicates > 0
            else "[bold]0[/bold]",
            f"[bold red]{total_validation_errors}[/bold red]" if total_validation_errors > 0 else "[bold]0[/bold]",
            f"[bold]{total_batch_inserts}[/bold]",
            f"[bold]{total_rate_style}{total_success_rate:.1f}%[/{total_rate_style[1:]}[/bold]",
            f"[bold]{total_duration_seconds:.2f}[/bold]",
        )

        self.console.print(table)

        # 创建状态面板
        status_items = []
        if total_errors > 0:
            status_items.append(f"[red]⚠️  发现 {total_errors} 个错误，请检查日志详情[/red]")

        if total_validation_errors > 0:
            status_items.append(f"[red]🔍 数据验证失败: {total_validation_errors} 条记录[/red]")

        if total_duplicates > 0:
            status_items.append(f"[yellow]📋 跳过重复记录: {total_duplicates} 条[/yellow]")

        if total_success_rate >= 95:
            status_items.append(f"[bright_green]✅ 迁移成功率优秀: {total_success_rate:.1f}%[/bright_green]")
        elif total_success_rate >= 80:
            status_items.append(f"[yellow]⚡ 迁移成功率良好: {total_success_rate:.1f}%[/yellow]")
        else:
            status_items.append(f"[red]❌ 迁移成功率较低: {total_success_rate:.1f}%，需要检查[/red]")

        if status_items:
            status_panel = Panel(
                "\n".join(status_items), title="[bold yellow]迁移状态总结[/bold yellow]", border_style="yellow"
            )
            self.console.print(status_panel)

        # 性能统计面板
        avg_speed = total_processed / total_duration_seconds if total_duration_seconds > 0 else 0
        performance_info = (
            f"[cyan]总处理时间:[/cyan] {total_duration_seconds:.2f} 秒\n"
            f"[cyan]平均处理速度:[/cyan] {avg_speed:.1f} 条记录/秒\n"
            f"[cyan]批量插入优化:[/cyan] 执行了 {total_batch_inserts} 次批量操作"
        )

        performance_panel = Panel(performance_info, title="[bold green]性能统计[/bold green]", border_style="green")
        self.console.print(performance_panel)

    def add_migration_config(self, config: MigrationConfig):
        """添加新的迁移配置"""
        self.migration_configs.append(config)

    def migrate_single_collection(self, collection_name: str) -> Optional[MigrationStats]:
        """迁移单个指定的集合"""
        config = next((c for c in self.migration_configs if c.mongo_collection == collection_name), None)
        if not config:
            logger.error(f"未找到集合 {collection_name} 的迁移配置")
            return None

        if not self.connect_mongodb():
            logger.error("无法连接到MongoDB")
            return None

        try:
            stats = self.migrate_collection(config)
            self._print_migration_summary({collection_name: stats})
            return stats
        finally:
            self.disconnect_mongodb()

    def export_error_report(self, all_stats: Dict[str, MigrationStats], filepath: str):
        """导出错误报告"""
        error_report = {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                collection: {
                    "total": stats.total_documents,
                    "processed": stats.processed_count,
                    "success": stats.success_count,
                    "errors": stats.error_count,
                    "skipped": stats.skipped_count,
                    "duplicates": stats.duplicate_count,
                }
                for collection, stats in all_stats.items()
            },
            "errors": {collection: stats.errors for collection, stats in all_stats.items() if stats.errors},
        }

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(error_report, f, ensure_ascii=False, indent=2)
            logger.info(f"错误报告已导出到: {filepath}")
        except Exception as e:
            logger.error(f"导出错误报告失败: {e}")


def main():
    """主程序入口"""
    migrator = MongoToSQLiteMigrator()

    # 执行迁移
    migration_results = migrator.migrate_all()

    # 导出错误报告（如果有错误）
    if any(stats.error_count > 0 for stats in migration_results.values()):
        error_report_path = f"migration_errors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        migrator.export_error_report(migration_results, error_report_path)

    logger.info("数据迁移完成！")


if __name__ == "__main__":
    main()
