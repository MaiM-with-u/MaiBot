import traceback
import time
from typing import Dict, List, Any, Union, Type
from src.common.logger_manager import get_logger
from src.common.database.database_model import ActionRecords
from src.common.database.database import db
from peewee import Model, DoesNotExist

logger = get_logger("database_api")

class DatabaseAPI:
    """数据库API模块
    
    提供了数据库操作相关的功能
    """
    
    async def store_action_info(self, action_build_into_prompt: bool = False, action_prompt_display: str = "", action_done: bool = True) -> None:
        """存储action执行信息到数据库

        Args:
            action_build_into_prompt: 是否构建到提示中
            action_prompt_display: 动作显示内容
            action_done: 动作是否已完成
        """
        try:
            chat_stream = self._services.get("chat_stream")
            if not chat_stream:
                logger.error(f"{self.log_prefix} 无法存储action信息：缺少chat_stream服务")
                return

            action_time = time.time()
            action_id = f"{action_time}_{self.thinking_id}"

            ActionRecords.create(
                action_id=action_id,
                time=action_time,
                action_name=self.__class__.__name__,
                action_data=str(self.action_data),
                action_done=action_done,
                action_build_into_prompt=action_build_into_prompt,
                action_prompt_display=action_prompt_display,
                chat_id=chat_stream.stream_id,
                chat_info_stream_id=chat_stream.stream_id,
                chat_info_platform=chat_stream.platform,
                user_id=chat_stream.user_info.user_id if chat_stream.user_info else "",
                user_nickname=chat_stream.user_info.user_nickname if chat_stream.user_info else "",
                user_cardname=chat_stream.user_info.user_cardname if chat_stream.user_info else ""
            )
            logger.debug(f"{self.log_prefix} 已存储action信息: {action_prompt_display}")
        except Exception as e:
            logger.error(f"{self.log_prefix} 存储action信息时出错: {e}")
            traceback.print_exc()
            
    async def db_query(
        self,
        model_class: Type[Model],
        query_type: str = "get",
        filters: Dict[str, Any] = None,
        data: Dict[str, Any] = None,
        limit: int = None,
        order_by: List[str] = None,
        single_result: bool = False
    ) -> Union[List[Dict[str, Any]], Dict[str, Any], None]:
        """执行数据库查询操作
        
        这个方法提供了一个通用接口来执行数据库操作，包括查询、创建、更新和删除记录。
        
        Args:
            model_class: Peewee 模型类，例如 ActionRecords, Messages 等
            query_type: 查询类型，可选值: "get", "create", "update", "delete", "count"
            filters: 过滤条件字典，键为字段名，值为要匹配的值
            data: 用于创建或更新的数据字典
            limit: 限制结果数量
            order_by: 排序字段列表，使用字段名，前缀'-'表示降序
            single_result: 是否只返回单个结果
            
        Returns:
            根据查询类型返回不同的结果:
            - "get": 返回查询结果列表或单个结果（如果 single_result=True）
            - "create": 返回创建的记录
            - "update": 返回受影响的行数
            - "delete": 返回受影响的行数
            - "count": 返回记录数量
            
        示例:
            # 查询最近10条消息
            messages = await self.db_query(
                Messages, 
                query_type="get",
                filters={"chat_id": chat_stream.stream_id},
                limit=10,
                order_by=["-time"]
            )
            
            # 创建一条记录
            new_record = await self.db_query(
                ActionRecords,
                query_type="create",
                data={"action_id": "123", "time": time.time(), "action_name": "TestAction"}
            )
            
            # 更新记录
            updated_count = await self.db_query(
                ActionRecords,
                query_type="update",
                filters={"action_id": "123"},
                data={"action_done": True}
            )
            
            # 删除记录
            deleted_count = await self.db_query(
                ActionRecords,
                query_type="delete",
                filters={"action_id": "123"}
            )
            
            # 计数
            count = await self.db_query(
                Messages,
                query_type="count",
                filters={"chat_id": chat_stream.stream_id}
            )
        """
        try:
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
                return model_class.select().where(model_class.id == record.id).dicts().get()
                
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
            if query_type == "get" and single_result:
                return None
            return []
            
        except Exception as e:
            logger.error(f"{self.log_prefix} 数据库操作出错: {e}")
            traceback.print_exc()
            
            # 根据查询类型返回合适的默认值
            if query_type == "get":
                return None if single_result else []
            elif query_type in ["create", "update", "delete", "count"]:
                return None
                
    async def db_raw_query(
        self, 
        sql: str, 
        params: List[Any] = None,
        fetch_results: bool = True
    ) -> Union[List[Dict[str, Any]], int, None]:
        """执行原始SQL查询
        
        警告: 使用此方法需要小心，确保SQL语句已正确构造以避免SQL注入风险。
        
        Args:
            sql: 原始SQL查询字符串
            params: 查询参数列表，用于替换SQL中的占位符
            fetch_results: 是否获取查询结果，对于SELECT查询设为True，对于
                          UPDATE/INSERT/DELETE等操作设为False
                          
        Returns:
            如果fetch_results为True，返回查询结果列表；
            如果fetch_results为False，返回受影响的行数；
            如果出错，返回None
        """
        try:
            cursor = db.execute_sql(sql, params or [])
            
            if fetch_results:
                # 获取列名
                columns = [col[0] for col in cursor.description]
                
                # 构建结果字典列表
                results = []
                for row in cursor.fetchall():
                    results.append(dict(zip(columns, row)))
                
                return results
            else:
                # 返回受影响的行数
                return cursor.rowcount
                
        except Exception as e:
            logger.error(f"{self.log_prefix} 执行原始SQL查询出错: {e}")
            traceback.print_exc()
            return None
            
    async def db_save(
        self, 
        model_class: Type[Model], 
        data: Dict[str, Any], 
        key_field: str = None, 
        key_value: Any = None
    ) -> Union[Dict[str, Any], None]:
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
            record = await self.db_save(
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
                # 查找现有记录
                existing_records = list(model_class.select().where(
                    getattr(model_class, key_field) == key_value
                ).limit(1))
                
                if existing_records:
                    # 更新现有记录
                    existing_record = existing_records[0]
                    for field, value in data.items():
                        setattr(existing_record, field, value)
                    existing_record.save()
                    
                    # 返回更新后的记录
                    updated_record = model_class.select().where(
                        model_class.id == existing_record.id
                    ).dicts().get()
                    return updated_record
            
            # 如果没有找到现有记录或未提供key_field和key_value，创建新记录
            new_record = model_class.create(**data)
            
            # 返回创建的记录
            created_record = model_class.select().where(
                model_class.id == new_record.id
            ).dicts().get()
            return created_record
            
        except Exception as e:
            logger.error(f"{self.log_prefix} 保存数据库记录出错: {e}")
            traceback.print_exc()
            return None
            
    async def db_get(
        self,
        model_class: Type[Model],
        filters: Dict[str, Any] = None,
        order_by: str = None,
        limit: int = None
    ) -> Union[List[Dict[str, Any]], Dict[str, Any], None]:
        """从数据库获取记录
        
        这是db_query方法的简化版本，专注于数据检索操作。
        
        Args:
            model_class: Peewee模型类
            filters: 过滤条件，字段名和值的字典
            order_by: 排序字段，前缀'-'表示降序，例如'-time'表示按时间降序
            limit: 结果数量限制，如果为1则返回单个记录而不是列表
            
        Returns:
            如果limit=1，返回单个记录字典或None；
            否则返回记录字典列表或空列表。
            
        示例:
            # 获取单个记录
            record = await self.db_get(
                ActionRecords,
                filters={"action_id": "123"},
                limit=1
            )
            
            # 获取最近10条记录
            records = await self.db_get(
                Messages,
                filters={"chat_id": chat_stream.stream_id},
                order_by="-time",
                limit=10
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
            if limit == 1:
                return results[0] if results else None
            return results
            
        except Exception as e:
            logger.error(f"{self.log_prefix} 获取数据库记录出错: {e}")
            traceback.print_exc()
            return None if limit == 1 else [] 