"""数据库API模块

提供数据库操作相关功能，采用标准Python包设计模式
使用方式：
    from src.plugin_system.apis import database_api
    records = await database_api.db_query(ActionRecords, query_type="get")
    record = await database_api.db_save(ActionRecords, data={"action_id": "123"})
"""

import traceback
import time
import json
from typing import Dict, List, Any, Union, Type, Optional
from src.common.logger import get_logger
from peewee import Model, DoesNotExist

logger = get_logger("database_api")

# =============================================================================
# 通用数据库查询API函数
# =============================================================================


async def db_query(
    model_class: Type[Model],
    data: Optional[Dict[str, Any]] = None,
    query_type: Optional[str] = "get",
    filters: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
    order_by: Optional[List[str]] = None,
    single_result: Optional[bool] = False,
) -> Union[List[Dict[str, Any]], Dict[str, Any], None]:
    """执行数据库查询操作

    这个方法提供了一个通用接口来执行数据库操作，包括查询、创建、更新和删除记录。

    Args:
        model_class: Peewee 模型类，例如 ActionRecords, Messages 等
        data: 用于创建或更新的数据字典
        query_type: 查询类型，可选值: "get", "create", "update", "delete", "count"
        filters: 过滤条件字典，键为字段名，值为要匹配的值
        limit: 限制结果数量
        order_by: 排序字段，前缀'-'表示降序，例如'-time'表示按时间字段（即time字段）降序
        single_result: 是否只返回单个结果

    Returns:
        根据查询类型返回不同的结果:
        - "get": 返回查询结果列表或单个结果（如果 single_result=True）
        - "create": 返回创建的记录
        - "update": 返回受影响的行数
        - "delete": 返回受影响的行数
        - "count": 返回记录数量
    """
    """
    示例:
        # 查询最近10条消息
        messages = await database_api.db_query(
            Messages,
            query_type="get",
            filters={"chat_id": chat_stream.stream_id},
            limit=10,
            order_by=["-time"]
        )

        # 创建一条记录
        new_record = await database_api.db_query(
            ActionRecords,
            data={"action_id": "123", "time": time.time(), "action_name": "TestAction"},
            query_type="create",
        )

        # 更新记录
        updated_count = await database_api.db_query(
            ActionRecords,
            data={"action_done": True},
            query_type="update",
            filters={"action_id": "123"},
        )

        # 删除记录
        deleted_count = await database_api.db_query(
            ActionRecords,
            query_type="delete",
            filters={"action_id": "123"}
        )

        # 计数
        count = await database_api.db_query(
            Messages,
            query_type="count",
            filters={"chat_id": chat_stream.stream_id}
        )
    """
    try:
        if query_type not in ["get", "create", "update", "delete", "count"]:
            raise ValueError("query_type must be 'get' or 'create' or 'update' or 'delete' or 'count'")
        # 构建基本查询
        if query_type in ["get", "update", "delete", "count"]:
            query = model_class.select()

            # 应用过滤条件
            if filters:
                for field, value in filters.items():
                    query = query.where(getattr(model_class, field) == value)

        # 执行查询
        if query_type == "get":
            # 应用排序
            if order_by:
                for field in order_by:
                    if field.startswith("-"):
                        query = query.order_by(getattr(model_class, field[1:]).desc())
                    else:
                        query = query.order_by(getattr(model_class, field))

            # 应用限制
            if limit:
                query = query.limit(limit)

            # 执行查询
            results = list(query.dicts())

            # 返回结果
            if single_result:
                return results[0] if results else None
            return results

        elif query_type == "create":
            if not data:
                raise ValueError("创建记录需要提供data参数")

            # 创建记录
            record = model_class.create(**data)
            # 返回创建的记录
            return model_class.select().where(model_class.id == record.id).dicts().get()  # type: ignore

        elif query_type == "update":
            if not data:
                raise ValueError("更新记录需要提供data参数")

            # 更新记录
            return query.update(**data).execute()

        elif query_type == "delete":
            # 删除记录
            return query.delete().execute()

        elif query_type == "count":
            # 计数
            return query.count()

        else:
            raise ValueError(f"不支持的查询类型: {query_type}")

    except DoesNotExist:
        # 记录不存在
        return None if query_type == "get" and single_result else []
    except Exception as e:
        logger.error(f"[DatabaseAPI] 数据库操作出错: {e}")
        traceback.print_exc()

        # 根据查询类型返回合适的默认值
        if query_type == "get":
            return None if single_result else []
        elif query_type in ["create", "update", "delete", "count"]:
            return None
        return None


async def db_save(
    model_class: Type[Model], data: Dict[str, Any], key_field: Optional[str] = None, key_value: Optional[Any] = None
) -> Optional[Dict[str, Any]]:
    # sourcery skip: inline-immediately-returned-variable
    """保存数据到数据库（创建或更新）

    如果提供了key_field和key_value，会先尝试查找匹配的记录进行更新；
    如果没有找到匹配记录，或未提供key_field和key_value，则创建新记录。

    Args:
        model_class: Peewee模型类，如ActionRecords, Messages等
        data: 要保存的数据字典
        key_field: 用于查找现有记录的字段名，例如"action_id"
        key_value: 用于查找现有记录的字段值

    Returns:
        Dict[str, Any]: 保存后的记录数据
        None: 如果操作失败

    示例:
        # 创建或更新一条记录
        record = await database_api.db_save(
            ActionRecords,
            {
                "action_id": "123",
                "time": time.time(),
                "action_name": "TestAction",
                "action_done": True
            },
            key_field="action_id",
            key_value="123"
        )
    """
    try:
        # 如果提供了key_field和key_value，尝试更新现有记录
        if key_field and key_value is not None:
            if existing_records := list(
                model_class.select().where(getattr(model_class, key_field) == key_value).limit(1)
            ):
                # 更新现有记录
                existing_record = existing_records[0]
                for field, value in data.items():
                    setattr(existing_record, field, value)
                existing_record.save()

                # 返回更新后的记录
                updated_record = model_class.select().where(model_class.id == existing_record.id).dicts().get()  # type: ignore
                return updated_record

        # 如果没有找到现有记录或未提供key_field和key_value，创建新记录
        new_record = model_class.create(**data)

        # 返回创建的记录
        created_record = model_class.select().where(model_class.id == new_record.id).dicts().get()  # type: ignore
        return created_record

    except Exception as e:
        logger.error(f"[DatabaseAPI] 保存数据库记录出错: {e}")
        traceback.print_exc()
        return None


async def db_get(
    model_class: Type[Model],
    filters: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
    order_by: Optional[str] = None,
    single_result: Optional[bool] = False,
) -> Union[List[Dict[str, Any]], Dict[str, Any], None]:
    """从数据库获取记录

    这是db_query方法的简化版本，专注于数据检索操作。

    Args:
        model_class: Peewee模型类
        filters: 过滤条件，字段名和值的字典
        limit: 结果数量限制
        order_by: 排序字段，前缀'-'表示降序，例如'-time'表示按时间字段（即time字段）降序
        single_result: 是否只返回单个结果，如果为True，则返回单个记录字典或None；否则返回记录字典列表或空列表

    Returns:
        如果single_result为True，返回单个记录字典或None；
        否则返回记录字典列表或空列表。

    示例:
        # 获取单个记录
        record = await database_api.db_get(
            ActionRecords,
            filters={"action_id": "123"},
            limit=1
        )

        # 获取最近10条记录
        records = await database_api.db_get(
            Messages,
            filters={"chat_id": chat_stream.stream_id},
            limit=10,
            order_by="-time",
        )
    """
    try:
        # 构建查询
        query = model_class.select()

        # 应用过滤条件
        if filters:
            for field, value in filters.items():
                query = query.where(getattr(model_class, field) == value)

        # 应用排序
        if order_by:
            if order_by.startswith("-"):
                query = query.order_by(getattr(model_class, order_by[1:]).desc())
            else:
                query = query.order_by(getattr(model_class, order_by))

        # 应用限制
        if limit:
            query = query.limit(limit)

        # 执行查询
        results = list(query.dicts())

        # 返回结果
        if single_result:
            return results[0] if results else None
        return results

    except Exception as e:
        logger.error(f"[DatabaseAPI] 获取数据库记录出错: {e}")
        traceback.print_exc()
        return None if single_result else []


async def store_action_info(
    chat_stream=None,
    action_build_into_prompt: bool = False,
    action_prompt_display: str = "",
    action_done: bool = True,
    thinking_id: str = "",
    action_data: Optional[dict] = None,
    action_name: str = "",
) -> Optional[Dict[str, Any]]:
    """存储动作信息到数据库

    将Action执行的相关信息保存到ActionRecords表中，用于后续的记忆和上下文构建。

    Args:
        chat_stream: 聊天流对象，包含聊天相关信息
        action_build_into_prompt: 是否将此动作构建到提示中
        action_prompt_display: 动作的提示显示文本
        action_done: 动作是否完成
        thinking_id: 关联的思考ID
        action_data: 动作数据字典
        action_name: 动作名称

    Returns:
        Dict[str, Any]: 保存的记录数据
        None: 如果保存失败

    示例:
        record = await database_api.store_action_info(
            chat_stream=chat_stream,
            action_build_into_prompt=True,
            action_prompt_display="执行了回复动作",
            action_done=True,
            thinking_id="thinking_123",
            action_data={"content": "Hello"},
            action_name="reply_action"
        )
    """
    try:
        from src.common.database.database_model import ActionRecords

        # 构建动作记录数据
        record_data = {
            "action_id": thinking_id or str(int(time.time() * 1000000)),  # 使用thinking_id或生成唯一ID
            "time": time.time(),
            "action_name": action_name,
            "action_data": json.dumps(action_data or {}, ensure_ascii=False),
            "action_done": action_done,
            "action_build_into_prompt": action_build_into_prompt,
            "action_prompt_display": action_prompt_display,
        }

        # 从chat_stream获取聊天信息
        if chat_stream:
            record_data.update(
                {
                    "chat_id": getattr(chat_stream, "stream_id", ""),
                    "chat_info_stream_id": getattr(chat_stream, "stream_id", ""),
                    "chat_info_platform": getattr(chat_stream, "platform", ""),
                }
            )
        else:
            # 如果没有chat_stream，设置默认值
            record_data.update(
                {
                    "chat_id": "",
                    "chat_info_stream_id": "",
                    "chat_info_platform": "",
                }
            )

        # 使用已有的db_save函数保存记录
        saved_record = await db_save(
            ActionRecords, data=record_data, key_field="action_id", key_value=record_data["action_id"]
        )

        if saved_record:
            logger.debug(f"[DatabaseAPI] 成功存储动作信息: {action_name} (ID: {record_data['action_id']})")
        else:
            logger.error(f"[DatabaseAPI] 存储动作信息失败: {action_name}")

        return saved_record

    except Exception as e:
        logger.error(f"[DatabaseAPI] 存储动作信息时发生错误: {e}")
        traceback.print_exc()
        return None
