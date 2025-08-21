"""工具执行历史记录模块"""
import functools
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union
import json
from pathlib import Path
import asyncio

from .logger import get_logger
from src.config.config import global_config

logger = get_logger("tool_history")

class ToolHistoryManager:
    """工具执行历史记录管理器"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
        
    def __init__(self):
        if not self._initialized:
            self._history: List[Dict[str, Any]] = []
            self._initialized = True
            self._data_dir = Path("data/tool_history")
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._history_file = self._data_dir / "tool_history.jsonl"
            self._load_history()

    def _save_history(self):
        """保存所有历史记录到文件"""
        try:
            with self._history_file.open("w", encoding="utf-8") as f:
                for record in self._history:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"保存工具调用记录失败: {e}")

    def _save_record(self, record: Dict[str, Any]):
        """保存单条记录到文件"""
        try:
            with self._history_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"保存工具调用记录失败: {e}")
            
    def _clean_expired_records(self):
        """清理已过期的记录"""
        original_count = len(self._history)
        self._history = [record for record in self._history if record.get("ttl_count", 0) < record.get("ttl", 5)]
        cleaned_count = original_count - len(self._history)
        
        if cleaned_count > 0:
            logger.info(f"清理了 {cleaned_count} 条过期的工具历史记录，剩余 {len(self._history)} 条")
            self._save_history()
        else:
            logger.debug("没有需要清理的过期工具历史记录")

    def record_tool_call(self, 
                        tool_name: str,
                        args: Dict[str, Any],
                        result: Any,
                        execution_time: float,
                        status: str,
                        chat_id: Optional[str] = None,
                        ttl: int = 5):
        """记录工具调用
        
        Args:
            tool_name: 工具名称
            args: 工具调用参数
            result: 工具返回结果
            execution_time: 执行时间（秒）
            status: 执行状态("completed"或"error")
            chat_id: 聊天ID，与ChatManager中的chat_id对应，用于标识群聊或私聊会话
            ttl: 该记录的生命周期值，插入提示词多少次后删除，默认为5
        """
        # 检查是否启用历史记录且ttl大于0
        if not global_config.tool.history.enable_history or ttl <= 0:
            return
            
        # 先清理过期记录
        self._clean_expired_records()
            
        try:
            # 创建记录
            record = {
                "tool_name": tool_name,
                "timestamp": datetime.now().isoformat(),
                "arguments": self._sanitize_args(args),
                "result": self._sanitize_result(result),
                "execution_time": execution_time,
                "status": status,
                "chat_id": chat_id,
                "ttl": ttl,
                "ttl_count": 0
            }
            
            # 添加到内存中的历史记录
            self._history.append(record)
            
            # 保存到文件
            self._save_record(record)
            
            if status == "completed":
                logger.info(f"工具 {tool_name} 调用完成，耗时：{execution_time:.2f}s")
            else:
                logger.error(f"工具 {tool_name} 调用失败：{result}")
                
        except Exception as e:
            logger.error(f"记录工具调用时发生错误: {e}")

    def find_cached_result(self, tool_name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """查找匹配的缓存记录
        
        Args:
            tool_name: 工具名称
            args: 工具调用参数
            
        Returns:
            Optional[Dict[str, Any]]: 如果找到匹配的缓存记录则返回结果，否则返回None
        """
        # 检查是否启用历史记录
        if not global_config.tool.history.enable_history:
            return None
            
        # 清理输入参数中的敏感信息以便比较
        sanitized_input_args = self._sanitize_args(args)
        
        # 按时间倒序遍历历史记录
        for record in reversed(self._history):
            if (record["tool_name"] == tool_name and 
                record["status"] == "completed" and
                record["ttl_count"] < record.get("ttl", 5)):
                # 比较参数是否匹配
                if self._sanitize_args(record["arguments"]) == sanitized_input_args:
                    logger.info(f"工具 {tool_name} 命中缓存记录")
                    return record["result"]
        return None

    def _sanitize_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """清理参数中的敏感信息"""
        sensitive_keys = ['api_key', 'token', 'password', 'secret']
        sanitized = args.copy()
        
        def _sanitize_value(value):
            if isinstance(value, dict):
                return {k: '***' if k.lower() in sensitive_keys else _sanitize_value(v)
                       for k, v in value.items()}
            return value
            
        return {k: '***' if k.lower() in sensitive_keys else _sanitize_value(v)
                for k, v in sanitized.items()}

    def _sanitize_result(self, result: Any) -> Any:
        """清理结果中的敏感信息"""
        if isinstance(result, dict):
            return self._sanitize_args(result)
        return result

    def _load_history(self):
        """加载历史记录文件"""
        try:
            if self._history_file.exists():
                self._history = []
                with self._history_file.open("r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            record = json.loads(line)
                            if record.get("ttl_count", 0) < record.get("ttl", 5):  # 只加载未过期的记录
                                self._history.append(record)
                        except json.JSONDecodeError:
                            continue
                logger.info(f"成功加载了 {len(self._history)} 条历史记录")
        except Exception as e:
            logger.error(f"加载历史记录失败: {e}")

    def query_history(self,
                     tool_names: Optional[List[str]] = None,
                     start_time: Optional[Union[datetime, str]] = None,
                     end_time: Optional[Union[datetime, str]] = None,
                     chat_id: Optional[str] = None,
                     limit: Optional[int] = None,
                     status: Optional[str] = None) -> List[Dict[str, Any]]:
        """查询工具调用历史
        
        Args:
            tool_names: 工具名称列表，为空则查询所有工具
            start_time: 开始时间，可以是datetime对象或ISO格式字符串
            end_time: 结束时间，可以是datetime对象或ISO格式字符串
            chat_id: 聊天ID，与ChatManager中的chat_id对应，用于查询特定群聊或私聊的历史记录
            limit: 返回记录数量限制
            status: 执行状态筛选("completed"或"error")
            
        Returns:
            符合条件的历史记录列表
        """
        # 先清理过期记录
        self._clean_expired_records()
        def _parse_time(time_str: Optional[Union[datetime, str]]) -> Optional[datetime]:
            if isinstance(time_str, datetime):
                return time_str
            elif isinstance(time_str, str):
                return datetime.fromisoformat(time_str)
            return None
            
        filtered_history = self._history
        
        # 按工具名筛选
        if tool_names:
            filtered_history = [
                record for record in filtered_history 
                if record["tool_name"] in tool_names
            ]
            
        # 按时间范围筛选
        start_dt = _parse_time(start_time)
        end_dt = _parse_time(end_time)
        
        if start_dt:
            filtered_history = [
                record for record in filtered_history
                if datetime.fromisoformat(record["timestamp"]) >= start_dt
            ]
            
        if end_dt:
            filtered_history = [
                record for record in filtered_history
                if datetime.fromisoformat(record["timestamp"]) <= end_dt
            ]
            
        # 按聊天ID筛选
        if chat_id:
            filtered_history = [
                record for record in filtered_history
                if record.get("chat_id") == chat_id
            ]
            
        # 按状态筛选
        if status:
            filtered_history = [
                record for record in filtered_history
                if record["status"] == status
            ]
            
        # 应用数量限制
        if limit:
            filtered_history = filtered_history[-limit:]
            
        return filtered_history

    def get_recent_history_prompt(self, 
                                limit: Optional[int] = None,
                                chat_id: Optional[str] = None) -> str:
        """
        获取最近工具调用历史的提示词
        
        Args:
            limit: 返回的历史记录数量,如果不提供则使用配置中的max_history
            chat_id: 会话ID，用于只获取当前会话的历史
            
        Returns:
            格式化的历史记录提示词
        """
        # 检查是否启用历史记录
        if not global_config.tool.history.enable_history:
            return ""
            
        # 使用配置中的最大历史记录数
        if limit is None:
            limit = global_config.tool.history.max_history
            
        recent_history = self.query_history(
            chat_id=chat_id,
            limit=limit
        )
        
        if not recent_history:
            return ""
            
        prompt = "\n工具执行历史:\n"
        needs_save = False
        updated_history = []
        
        for record in recent_history:
            # 增加ttl计数
            record["ttl_count"] = record.get("ttl_count", 0) + 1
            needs_save = True
            
            # 如果未超过ttl，则添加到提示词中
            if record["ttl_count"] < record.get("ttl", 5):
                # 提取结果中的name和content
                result = record['result']
                if isinstance(result, dict):
                    name = result.get('name', record['tool_name'])
                    content = result.get('content', str(result))
                else:
                    name = record['tool_name']
                    content = str(result)
                    
                # 格式化内容，去除多余空白和换行
                content = content.strip().replace('\n', ' ')
                
                # 如果内容太长则截断
                if len(content) > 200:
                    content = content[:200] + "..."
                    
                prompt += f"{name}: \n{content}\n\n"
                updated_history.append(record)
                
        # 更新历史记录并保存
        if needs_save:
            self._history = updated_history
            self._save_history()
            
        return prompt
        
    def clear_history(self):
        """清除历史记录"""
        self._history.clear()
        self._save_history()
        logger.info("工具调用历史记录已清除")


def wrap_tool_executor():
    """
    包装工具执行器以添加历史记录功能
    这个函数应该在系统启动时被调用一次
    """
    from src.plugin_system.core.tool_use import ToolExecutor
    original_execute = ToolExecutor.execute_tool_call
    history_manager = ToolHistoryManager()
    
    async def wrapped_execute_tool_call(self, tool_call, tool_instance=None):
        start_time = time.time()

        # 首先检查缓存
        if cached_result := history_manager.find_cached_result(tool_call.func_name, tool_call.args):
            logger.info(f"{getattr(self, 'log_prefix', '')}使用缓存结果，跳过工具 {tool_call.func_name} 执行")
            return cached_result

        try:
            result = await original_execute(self, tool_call, tool_instance)
            execution_time = time.time() - start_time
            
            # 获取工具的ttl值
            ttl = getattr(tool_instance, 'history_ttl', 5) if tool_instance else 5
            
            # 记录成功的调用
            history_manager.record_tool_call(
                tool_name=tool_call.func_name,
                args=tool_call.args,
                result=result,
                execution_time=execution_time,
                status="completed",
                chat_id=getattr(self, 'chat_id', None),
                ttl=ttl
            )
            
            return result
            
        except Exception as e:
            execution_time = time.time() - start_time
            # 获取工具的ttl值
            ttl = getattr(tool_instance, 'history_ttl', 5) if tool_instance else 5
            
            # 记录失败的调用
            history_manager.record_tool_call(
                tool_name=tool_call.func_name,
                args=tool_call.args,
                result=str(e),
                execution_time=execution_time,
                status="error",
                chat_id=getattr(self, 'chat_id', None),
                ttl=ttl
            )
            raise
            
    # 替换原始方法
    ToolExecutor.execute_tool_call = wrapped_execute_tool_call
