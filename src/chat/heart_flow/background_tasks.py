import asyncio
import traceback
from typing import Optional, Coroutine, Callable, Any, List
from src.common.logger_manager import get_logger
from src.chat.heart_flow.subheartflow_manager import SubHeartflowManager
from src.config.config import global_config

logger = get_logger("background_tasks")


# 新增私聊激活检查间隔
PRIVATE_CHAT_ACTIVATION_CHECK_INTERVAL_SECONDS = 5  # 与兴趣评估类似，设为5秒

CLEANUP_INTERVAL_SECONDS = 1200


async def _run_periodic_loop(
    task_name: str, interval: int, task_func: Callable[..., Coroutine[Any, Any, None]], **kwargs
):
    """周期性任务主循环"""
    while True:
        start_time = asyncio.get_event_loop().time()
        # logger.debug(f"开始执行后台任务: {task_name}")

        try:
            await task_func(**kwargs)  # 执行实际任务
        except asyncio.CancelledError:
            logger.info(f"任务 {task_name} 已取消")
            break
        except Exception as e:
            logger.error(f"任务 {task_name} 执行出错: {e}")
            logger.error(traceback.format_exc())

        # 计算并执行间隔等待
        elapsed = asyncio.get_event_loop().time() - start_time
        sleep_time = max(0, interval - elapsed)
        # if sleep_time < 0.1:  # 任务超时处理, DEBUG 时可能干扰断点
        #     logger.warning(f"任务 {task_name} 超时执行 ({elapsed:.2f}s > {interval}s)")
        await asyncio.sleep(sleep_time)

    logger.debug(f"任务循环结束: {task_name}")  # 调整日志信息


class BackgroundTaskManager:
    """管理 Heartflow 的后台周期性任务。"""

    def __init__(
        self,
        subheartflow_manager: SubHeartflowManager,
    ):
        self.subheartflow_manager = subheartflow_manager

        # Task references
        self._cleanup_task: Optional[asyncio.Task] = None
        self._hf_judge_state_update_task: Optional[asyncio.Task] = None
        self._private_chat_activation_task: Optional[asyncio.Task] = None  # 新增私聊激活任务引用
        self._tasks: List[Optional[asyncio.Task]] = []  # Keep track of all tasks

    async def start_tasks(self):
        """启动所有后台任务

        功能说明:
        - 启动核心后台任务: 状态更新、清理、日志记录、兴趣评估和随机停用
        - 每个任务启动前检查是否已在运行
        - 将任务引用保存到任务列表
        """

        task_configs = []

        # 根据 chat_mode 条件添加其他任务
        if not (global_config.chat.chat_mode == "normal"):
            task_configs.extend(
                [
                    (
                        self._run_cleanup_cycle,
                        "info",
                        f"清理任务已启动 间隔:{CLEANUP_INTERVAL_SECONDS}s",
                        "_cleanup_task",
                    ),
                    # 新增私聊激活任务配置
                    (
                        # Use lambda to pass the interval to the runner function
                        lambda: self._run_private_chat_activation_cycle(PRIVATE_CHAT_ACTIVATION_CHECK_INTERVAL_SECONDS),
                        "debug",
                        f"私聊激活检查任务已启动 间隔:{PRIVATE_CHAT_ACTIVATION_CHECK_INTERVAL_SECONDS}s",
                        "_private_chat_activation_task",
                    ),
                ]
            )

        # 统一启动所有任务
        for task_func, log_level, log_msg, task_attr_name in task_configs:
            # 检查任务变量是否存在且未完成
            current_task_var = getattr(self, task_attr_name)
            if current_task_var is None or current_task_var.done():
                new_task = asyncio.create_task(task_func())
                setattr(self, task_attr_name, new_task)  # 更新任务变量
                if new_task not in self._tasks:  # 避免重复添加
                    self._tasks.append(new_task)

                # 根据配置记录不同级别的日志
                getattr(logger, log_level)(log_msg)
            else:
                logger.warning(f"{task_attr_name}任务已在运行")

    async def stop_tasks(self):
        """停止所有后台任务。

        该方法会:
        1. 遍历所有后台任务并取消未完成的任务
        2. 等待所有取消操作完成
        3. 清空任务列表
        """
        logger.info("正在停止所有后台任务...")
        cancelled_count = 0

        # 第一步：取消所有运行中的任务
        for task in self._tasks:
            if task and not task.done():
                task.cancel()  # 发送取消请求
                cancelled_count += 1

        # 第二步：处理取消结果
        if cancelled_count > 0:
            logger.debug(f"正在等待{cancelled_count}个任务完成取消...")
            # 使用gather等待所有取消操作完成，忽略异常
            await asyncio.gather(*[t for t in self._tasks if t and t.cancelled()], return_exceptions=True)
            logger.info(f"成功取消{cancelled_count}个后台任务")
        else:
            logger.info("没有需要取消的后台任务")

        # 第三步：清空任务列表
        self._tasks = []  # 重置任务列表

        # 状态转换处理

    async def _perform_cleanup_work(self):
        """执行子心流清理任务
        1. 获取需要清理的不活跃子心流列表
        2. 逐个停止这些子心流
        3. 记录清理结果
        """
        # 获取需要清理的子心流列表(包含ID和原因)
        flows_to_stop = self.subheartflow_manager.get_inactive_subheartflows()

        if not flows_to_stop:
            return  # 没有需要清理的子心流直接返回

        logger.info(f"准备删除 {len(flows_to_stop)} 个不活跃(1h)子心流")
        stopped_count = 0

        # 逐个停止子心流
        for flow_id in flows_to_stop:
            success = await self.subheartflow_manager.delete_subflow(flow_id)
            if success:
                stopped_count += 1
                logger.debug(f"[清理任务] 已停止子心流 {flow_id}")

        # 记录最终清理结果
        logger.info(f"[清理任务] 清理完成, 共停止 {stopped_count}/{len(flows_to_stop)} 个子心流")

    async def _run_cleanup_cycle(self):
        await _run_periodic_loop(
            task_name="Subflow Cleanup", interval=CLEANUP_INTERVAL_SECONDS, task_func=self._perform_cleanup_work
        )

    # 新增私聊激活任务运行器
    async def _run_private_chat_activation_cycle(self, interval: int):
        await _run_periodic_loop(
            task_name="Private Chat Activation Check",
            interval=interval,
            task_func=self.subheartflow_manager.sbhf_absent_private_into_focus,
        )
