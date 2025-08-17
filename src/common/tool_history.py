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
            self._current_file = None
            self._load_history()
            self._rotate_file()

    def _rotate_file(self):
        """轮换历史记录文件"""
        current_time = datetime.now()
        filename = f"tool_history_{current_time.strftime('%Y%m%d_%H%M%S')}.jsonl"
        self._current_file = self._data_dir / filename

    def _save_record(self, record: Dict[str, Any]):
        """保存单条记录到文件"""
        try:
            with self._current_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"保存工具调用记录失败: {e}")

    def record_tool_call(self, 
                        tool_name: str,
                        args: Dict[str, Any],
                        result: Any,
                        execution_time: float,
                        status: str,
                        chat_id: Optional[str] = None):
        """记录工具调用
        
        Args:
            tool_name: 工具名称
            args: 工具调用参数
            result: 工具返回结果
            execution_time: 执行时间（秒）
            status: 执行状态("completed"或"error")
            chat_id: 聊天ID，与ChatManager中的chat_id对应，用于标识群聊或私聊会话
        """
        # 检查是否启用历史记录
        if not global_config.tool.history.enable_history:
            return
            
        try:
            # 创建记录
            record = {
                "tool_name": tool_name,
                "timestamp": datetime.now().isoformat(),
                "arguments": self._sanitize_args(args),
                "result": self._sanitize_result(result),
                "execution_time": execution_time,
                "status": status,
                "chat_id": chat_id
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
            # 按文件修改时间排序,加载最近的文件
            history_files = sorted(
                self._data_dir.glob("tool_history_*.jsonl"), 
                key=lambda x: x.stat().st_mtime,
                reverse=True
            )
            
            # 最多加载最近3个文件的历史
            for file in history_files[:3]:
                try:
                    with file.open("r", encoding="utf-8") as f:
                        for line in f:
                            record = json.loads(line)
                            self._history.append(record)
                except Exception as e:
                    logger.error(f"加载历史记录文件 {file} 失败: {e}")
                    
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
        for record in recent_history:
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
            
        return prompt
        
    def clear_history(self):
        """清除历史记录"""
        self._history.clear()
        self._rotate_file()
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
        try:
            result = await original_execute(self, tool_call, tool_instance)
            execution_time = time.time() - start_time
            
            # 记录成功的调用
            history_manager.record_tool_call(
                tool_name=tool_call.func_name,
                args=tool_call.args,
                result=result,
                execution_time=execution_time,
                status="completed",
                chat_id=getattr(self, 'chat_id', None)
            )
            
            return result
            
        except Exception as e:
            execution_time = time.time() - start_time
            # 记录失败的调用
            history_manager.record_tool_call(
                tool_name=tool_call.func_name,
                args=tool_call.args,
                result=str(e),
                execution_time=execution_time,
                status="error",
                chat_id=getattr(self, 'chat_id', None)
            )
            raise
            
    # 替换原始方法
    ToolExecutor.execute_tool_call = wrapped_execute_tool_call
