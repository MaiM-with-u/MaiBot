from abc import abstractmethod

import asyncio
from asyncio import Task, Event, Lock
from typing import Callable, Dict

from src.common.logger_manager import get_logger

logger = get_logger("async_task_manager")


class AsyncTask:
    """异步任务基类"""

    def __init__(self, task_name: str | None = None, wait_before_start: int = 0, run_interval: int = 0):
        self.task_name: str = task_name or self.__class__.__name__
        """任务名称"""

        self.wait_before_start: int = wait_before_start
        """运行任务前是否进行等待（单位：秒，设为0则不等待）"""

        self.run_interval: int = run_interval
        """多次运行的时间间隔（单位：秒，设为0则仅运行一次）"""

    @abstractmethod
    async def run(self):
        """
        任务的执行过程
        """
        pass

    async def start_task(self, abort_flag: asyncio.Event):
        if self.wait_before_start > 0:
            # 等待指定时间后开始任务
            await asyncio.sleep(self.wait_before_start)

        while not abort_flag.is_set():
            await self.run()
            if self.run_interval > 0:
                await asyncio.sleep(self.run_interval)
            else:
                break


class AsyncTaskManager:
    """异步任务管理器"""

    def __init__(self):
        self.tasks: Dict[str, Task] = {}
        """任务列表"""

        self.abort_flag: Event = Event()
        """是否中止任务标志"""

        self._lock: Lock = Lock()
        """异步锁，当可能出现await时需要加锁"""

    def _remove_task_call_back(self, task: Task):
        """
        call_back: 任务完成后移除任务
        """
        task_name = task.get_name()
        if task_name in self.tasks:
            # 任务完成后移除任务
            del self.tasks[task_name]
            logger.debug(f"已移除任务 '{task_name}'")
        else:
            logger.warning(f"尝试移除不存在的任务 '{task_name}'")

    @staticmethod
    def _default_finish_call_back(task: Task):
        """
        call_back: 默认的任务完成回调函数
        """
        try:
            task.result()
            logger.debug(f"任务 '{task.get_name()}' 完成")
        except asyncio.CancelledError:
            logger.debug(f"任务 '{task.get_name()}' 被取消")
        except Exception as e:
            logger.error(f"任务 '{task.get_name()}' 执行时发生异常: {e}", exc_info=True)

    async def add_task(self, task: AsyncTask, call_back: Callable[[asyncio.Task], None] | None = None):
        """
        添加任务
        """
        if not issubclass(task.__class__, AsyncTask):
            raise TypeError(f"task '{task.__class__.__name__}' 必须是继承 AsyncTask 的子类")

        async with self._lock:  # 由于可能需要await等待任务完成，所以需要加异步锁
            if task.task_name in self.tasks:
                logger.warning(f"已存在名称为 '{task.task_name}' 的任务，正在尝试取消并替换")
                self.tasks[task.task_name].cancel()  # 取消已存在的任务
                await self.tasks[task.task_name]  # 等待任务完成
                logger.info(f"成功结束任务 '{task.task_name}'")

            # 创建新任务
            task_inst = asyncio.create_task(task.start_task(self.abort_flag))
            task_inst.set_name(task.task_name)
            task_inst.add_done_callback(self._remove_task_call_back)  # 添加完成回调函数-完成任务后自动移除任务
            task_inst.add_done_callback(
                call_back or self._default_finish_call_back
            )  # 添加完成回调函数-用户自定义，或默认的FallBack

            self.tasks[task.task_name] = task_inst  # 将任务添加到任务列表
            logger.debug(f"已启动任务 '{task.task_name}'")

    def get_tasks_status(self) -> Dict[str, Dict[str, str]]:
        """
        获取所有任务的状态
        """
        tasks_status = {}
        for task_name, task in self.tasks.items():
            tasks_status[task_name] = {
                "status": "running" if not task.done() else "done",
            }
        return tasks_status

    async def stop_and_wait_all_tasks(self):
        """
        终止所有任务并等待它们完成（该方法会阻塞其它尝试add_task()的操作）
        """
        async with self._lock:  # 由于可能需要await等待任务完成，所以需要加异步锁
            # 设置中止标志
            self.abort_flag.set()
            # 取消所有任务
            for name, inst in self.tasks.items():
                try:
                    inst.cancel()
                except asyncio.CancelledError:
                    logger.info(f"已取消任务 '{name}'")

            # 等待所有任务完成
            for task_name, task_inst in self.tasks.items():
                if not task_inst.done():
                    try:
                        await task_inst
                    except asyncio.CancelledError:  # 此处再次捕获取消异常，防止stop_all_tasks()时延迟抛出异常
                        logger.info(f"任务 {task_name} 已取消")
                    except Exception as e:
                        logger.error(f"任务 {task_name} 执行时发生异常: {e}", ext_info=True)

            # 清空任务列表
            self.tasks.clear()
            self.abort_flag.clear()
            logger.info("所有异步任务已停止")


async_task_manager = AsyncTaskManager()
"""全局异步任务管理器实例"""
