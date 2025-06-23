from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple, List
import asyncio
import concurrent.futures
import json
import os
import glob


from src.common.logger import get_logger
from src.manager.async_task_manager import AsyncTask

from ...common.database.database import db  # This db is the Peewee database instance
from ...common.database.database_model import OnlineTime, LLMUsage, Messages  # Import the Peewee model
from src.manager.local_store_manager import local_storage

logger = get_logger("maibot_statistic")

# 统计数据的键
TOTAL_REQ_CNT = "total_requests"
TOTAL_COST = "total_cost"
REQ_CNT_BY_TYPE = "requests_by_type"
REQ_CNT_BY_USER = "requests_by_user"
REQ_CNT_BY_MODEL = "requests_by_model"
REQ_CNT_BY_MODULE = "requests_by_module"
IN_TOK_BY_TYPE = "in_tokens_by_type"
IN_TOK_BY_USER = "in_tokens_by_user"
IN_TOK_BY_MODEL = "in_tokens_by_model"
IN_TOK_BY_MODULE = "in_tokens_by_module"
OUT_TOK_BY_TYPE = "out_tokens_by_type"
OUT_TOK_BY_USER = "out_tokens_by_user"
OUT_TOK_BY_MODEL = "out_tokens_by_model"
OUT_TOK_BY_MODULE = "out_tokens_by_module"
TOTAL_TOK_BY_TYPE = "tokens_by_type"
TOTAL_TOK_BY_USER = "tokens_by_user"
TOTAL_TOK_BY_MODEL = "tokens_by_model"
TOTAL_TOK_BY_MODULE = "tokens_by_module"
COST_BY_TYPE = "costs_by_type"
COST_BY_USER = "costs_by_user"
COST_BY_MODEL = "costs_by_model"
COST_BY_MODULE = "costs_by_module"
ONLINE_TIME = "online_time"
TOTAL_MSG_CNT = "total_messages"
MSG_CNT_BY_CHAT = "messages_by_chat"

# Focus统计数据的键
FOCUS_TOTAL_CYCLES = "focus_total_cycles"
FOCUS_AVG_TIMES_BY_STAGE = "focus_avg_times_by_stage"
FOCUS_ACTION_RATIOS = "focus_action_ratios"
FOCUS_CYCLE_CNT_BY_CHAT = "focus_cycle_count_by_chat"
FOCUS_CYCLE_CNT_BY_ACTION = "focus_cycle_count_by_action"
FOCUS_AVG_TIMES_BY_CHAT_ACTION = "focus_avg_times_by_chat_action"
FOCUS_AVG_TIMES_BY_ACTION = "focus_avg_times_by_action"
FOCUS_TOTAL_TIME_BY_CHAT = "focus_total_time_by_chat"
FOCUS_TOTAL_TIME_BY_ACTION = "focus_total_time_by_action"
FOCUS_CYCLE_CNT_BY_VERSION = "focus_cycle_count_by_version"
FOCUS_ACTION_RATIOS_BY_VERSION = "focus_action_ratios_by_version"
FOCUS_AVG_TIMES_BY_VERSION = "focus_avg_times_by_version"

# 新增: 后处理器统计数据的键
FOCUS_POST_PROCESSOR_TIMES = "focus_post_processor_times"
FOCUS_POST_PROCESSOR_COUNT = "focus_post_processor_count"
FOCUS_POST_PROCESSOR_SUCCESS_RATE = "focus_post_processor_success_rate"
FOCUS_PROCESSOR_TIMES = "focus_processor_times"  # 前处理器统计


class OnlineTimeRecordTask(AsyncTask):
    """在线时间记录任务"""

    def __init__(self):
        super().__init__(task_name="Online Time Record Task", run_interval=60)

        self.record_id: int | None = None  # Changed to int for Peewee's default ID
        """记录ID"""

        self._init_database()  # 初始化数据库

    @staticmethod
    def _init_database():
        """初始化数据库"""
        with db.atomic():  # Use atomic operations for schema changes
            OnlineTime.create_table(safe=True)  # Creates table if it doesn't exist, Peewee handles indexes from model

    async def run(self):
        try:
            current_time = datetime.now()
            extended_end_time = current_time + timedelta(minutes=1)

            if self.record_id:
                # 如果有记录，则更新结束时间
                query = OnlineTime.update(end_timestamp=extended_end_time).where(OnlineTime.id == self.record_id)
                updated_rows = query.execute()
                if updated_rows == 0:
                    # Record might have been deleted or ID is stale, try to find/create
                    self.record_id = None  # Reset record_id to trigger find/create logic below

            if not self.record_id:  # Check again if record_id was reset or initially None
                # 如果没有记录，检查一分钟以内是否已有记录
                # Look for a record whose end_timestamp is recent enough to be considered ongoing
                recent_record = (
                    OnlineTime.select()
                    .where(OnlineTime.end_timestamp >= (current_time - timedelta(minutes=1)))
                    .order_by(OnlineTime.end_timestamp.desc())
                    .first()
                )

                if recent_record:
                    # 如果有记录，则更新结束时间
                    self.record_id = recent_record.id
                    recent_record.end_timestamp = extended_end_time
                    recent_record.save()
                else:
                    # 若没有记录，则插入新的在线时间记录
                    new_record = OnlineTime.create(
                        timestamp=current_time.timestamp(),  # 添加此行
                        start_timestamp=current_time,
                        end_timestamp=extended_end_time,
                        duration=5,  # 初始时长为5分钟
                    )
                    self.record_id = new_record.id
        except Exception as e:
            logger.error(f"在线时间记录失败，错误信息：{e}")


def _format_online_time(online_seconds: int) -> str:
    """
    格式化在线时间
    :param online_seconds: 在线时间（秒）
    :return: 格式化后的在线时间字符串
    """
    total_oneline_time = timedelta(seconds=online_seconds)

    days = total_oneline_time.days
    hours = total_oneline_time.seconds // 3600
    minutes = (total_oneline_time.seconds // 60) % 60
    seconds = total_oneline_time.seconds % 60
    if days > 0:
        # 如果在线时间超过1天，则格式化为"X天X小时X分钟"
        return f"{total_oneline_time.days}天{hours}小时{minutes}分钟{seconds}秒"
    elif hours > 0:
        # 如果在线时间超过1小时，则格式化为"X小时X分钟X秒"
        return f"{hours}小时{minutes}分钟{seconds}秒"
    else:
        # 其他情况格式化为"X分钟X秒"
        return f"{minutes}分钟{seconds}秒"


class StatisticOutputTask(AsyncTask):
    """统计输出任务"""

    SEP_LINE = "-" * 84

    def __init__(self, record_file_path: str = "maibot_statistics.html"):
        # 延迟300秒启动，运行间隔300秒
        super().__init__(task_name="Statistics Data Output Task", wait_before_start=0, run_interval=300)

        self.name_mapping: Dict[str, Tuple[str, float]] = {}
        """
            联系人/群聊名称映射 {聊天ID: (联系人/群聊名称, 记录时间（timestamp）)}
            注：设计记录时间的目的是方便更新名称，使联系人/群聊名称保持最新
        """

        self.record_file_path: str = record_file_path
        """
        记录文件路径
        """

        now = datetime.now()
        if "deploy_time" in local_storage:
            # 如果存在部署时间，则使用该时间作为全量统计的起始时间
            deploy_time = datetime.fromtimestamp(local_storage["deploy_time"])
        else:
            # 否则，使用最大时间范围，并记录部署时间为当前时间
            deploy_time = datetime(2000, 1, 1)
            local_storage["deploy_time"] = now.timestamp()

        self.stat_period: List[Tuple[str, timedelta, str]] = [
            ("all_time", now - deploy_time, "自部署以来"),  # 必须保留"all_time"
            ("last_7_days", timedelta(days=7), "最近7天"),
            ("last_24_hours", timedelta(days=1), "最近24小时"),
            ("last_3_hours", timedelta(hours=3), "最近3小时"),
            ("last_hour", timedelta(hours=1), "最近1小时"),
        ]
        """
        统计时间段 [(统计名称, 统计时间段, 统计描述), ...]
        """

    def _statistic_console_output(self, stats: Dict[str, Any], now: datetime):
        """
        输出统计数据到控制台
        :param stats: 统计数据
        :param now: 基准当前时间
        """
        # 输出最近一小时的统计数据

        output = [
            self.SEP_LINE,
            f"  最近1小时的统计数据  (自{now.strftime('%Y-%m-%d %H:%M:%S')}开始，详细信息见文件：{self.record_file_path})",
            self.SEP_LINE,
            self._format_total_stat(stats["last_hour"]),
            "",
            self._format_model_classified_stat(stats["last_hour"]),
            "",
            self._format_chat_stat(stats["last_hour"]),
            "",
            self._format_focus_stat(stats["last_hour"]),
            self.SEP_LINE,
            "",
        ]

        logger.info("\n" + "\n".join(output))

    async def run(self):
        try:
            now = datetime.now()

            # 使用线程池并行执行耗时操作
            loop = asyncio.get_event_loop()

            # 在线程池中并行执行数据收集和之前的HTML生成（如果存在）
            with concurrent.futures.ThreadPoolExecutor() as executor:
                logger.info("正在收集统计数据...")

                # 数据收集任务
                collect_task = loop.run_in_executor(executor, self._collect_all_statistics, now)

                # 等待数据收集完成
                stats = await collect_task
                logger.info("统计数据收集完成")

                # 并行执行控制台输出和HTML报告生成
                console_task = loop.run_in_executor(executor, self._statistic_console_output, stats, now)
                html_task = loop.run_in_executor(executor, self._generate_html_report, stats, now)

                # 等待两个输出任务完成
                await asyncio.gather(console_task, html_task)

            logger.info("统计数据输出完成")
        except Exception as e:
            logger.exception(f"输出统计数据过程中发生异常，错误信息：{e}")

    async def run_async_background(self):
        """
        备选方案：完全异步后台运行统计输出
        使用此方法可以让统计任务完全非阻塞
        """

        async def _async_collect_and_output():
            try:
                import concurrent.futures

                now = datetime.now()
                loop = asyncio.get_event_loop()

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    logger.info("正在后台收集统计数据...")

                    # 创建后台任务，不等待完成
                    collect_task = asyncio.create_task(
                        loop.run_in_executor(executor, self._collect_all_statistics, now)
                    )

                    stats = await collect_task
                    logger.info("统计数据收集完成")

                    # 创建并发的输出任务
                    output_tasks = [
                        asyncio.create_task(loop.run_in_executor(executor, self._statistic_console_output, stats, now)),
                        asyncio.create_task(loop.run_in_executor(executor, self._generate_html_report, stats, now)),
                    ]

                    # 等待所有输出任务完成
                    await asyncio.gather(*output_tasks)

                logger.info("统计数据后台输出完成")
            except Exception as e:
                logger.exception(f"后台统计数据输出过程中发生异常：{e}")

        # 创建后台任务，立即返回
        asyncio.create_task(_async_collect_and_output())

    # -- 以下为统计数据收集方法 --

    @staticmethod
    def _collect_model_request_for_period(collect_period: List[Tuple[str, datetime]]) -> Dict[str, Any]:
        """
        收集指定时间段的LLM请求统计数据

        :param collect_period: 统计时间段
        """
        if not collect_period:
            return {}

        # 排序-按照时间段开始时间降序排列（最晚的时间段在前）
        collect_period.sort(key=lambda x: x[1], reverse=True)

        stats = {
            period_key: {
                TOTAL_REQ_CNT: 0,
                REQ_CNT_BY_TYPE: defaultdict(int),
                REQ_CNT_BY_USER: defaultdict(int),
                REQ_CNT_BY_MODEL: defaultdict(int),
                REQ_CNT_BY_MODULE: defaultdict(int),
                IN_TOK_BY_TYPE: defaultdict(int),
                IN_TOK_BY_USER: defaultdict(int),
                IN_TOK_BY_MODEL: defaultdict(int),
                IN_TOK_BY_MODULE: defaultdict(int),
                OUT_TOK_BY_TYPE: defaultdict(int),
                OUT_TOK_BY_USER: defaultdict(int),
                OUT_TOK_BY_MODEL: defaultdict(int),
                OUT_TOK_BY_MODULE: defaultdict(int),
                TOTAL_TOK_BY_TYPE: defaultdict(int),
                TOTAL_TOK_BY_USER: defaultdict(int),
                TOTAL_TOK_BY_MODEL: defaultdict(int),
                TOTAL_TOK_BY_MODULE: defaultdict(int),
                TOTAL_COST: 0.0,
                COST_BY_TYPE: defaultdict(float),
                COST_BY_USER: defaultdict(float),
                COST_BY_MODEL: defaultdict(float),
                COST_BY_MODULE: defaultdict(float),
            }
            for period_key, _ in collect_period
        }

        # 以最早的时间戳为起始时间获取记录
        # Assuming LLMUsage.timestamp is a DateTimeField
        query_start_time = collect_period[-1][1]
        for record in LLMUsage.select().where(LLMUsage.timestamp >= query_start_time):
            record_timestamp = record.timestamp  # This is already a datetime object
            for idx, (_, period_start) in enumerate(collect_period):
                if record_timestamp >= period_start:
                    for period_key, _ in collect_period[idx:]:
                        stats[period_key][TOTAL_REQ_CNT] += 1

                        request_type = record.request_type or "unknown"
                        user_id = record.user_id or "unknown"  # user_id is TextField, already string
                        model_name = record.model_name or "unknown"

                        # 提取模块名：如果请求类型包含"."，取第一个"."之前的部分
                        module_name = request_type.split(".")[0] if "." in request_type else request_type

                        stats[period_key][REQ_CNT_BY_TYPE][request_type] += 1
                        stats[period_key][REQ_CNT_BY_USER][user_id] += 1
                        stats[period_key][REQ_CNT_BY_MODEL][model_name] += 1
                        stats[period_key][REQ_CNT_BY_MODULE][module_name] += 1

                        prompt_tokens = record.prompt_tokens or 0
                        completion_tokens = record.completion_tokens or 0
                        total_tokens = prompt_tokens + completion_tokens

                        stats[period_key][IN_TOK_BY_TYPE][request_type] += prompt_tokens
                        stats[period_key][IN_TOK_BY_USER][user_id] += prompt_tokens
                        stats[period_key][IN_TOK_BY_MODEL][model_name] += prompt_tokens
                        stats[period_key][IN_TOK_BY_MODULE][module_name] += prompt_tokens

                        stats[period_key][OUT_TOK_BY_TYPE][request_type] += completion_tokens
                        stats[period_key][OUT_TOK_BY_USER][user_id] += completion_tokens
                        stats[period_key][OUT_TOK_BY_MODEL][model_name] += completion_tokens
                        stats[period_key][OUT_TOK_BY_MODULE][module_name] += completion_tokens

                        stats[period_key][TOTAL_TOK_BY_TYPE][request_type] += total_tokens
                        stats[period_key][TOTAL_TOK_BY_USER][user_id] += total_tokens
                        stats[period_key][TOTAL_TOK_BY_MODEL][model_name] += total_tokens
                        stats[period_key][TOTAL_TOK_BY_MODULE][module_name] += total_tokens

                        cost = record.cost or 0.0
                        stats[period_key][TOTAL_COST] += cost
                        stats[period_key][COST_BY_TYPE][request_type] += cost
                        stats[period_key][COST_BY_USER][user_id] += cost
                        stats[period_key][COST_BY_MODEL][model_name] += cost
                        stats[period_key][COST_BY_MODULE][module_name] += cost
                    break
        return stats

    @staticmethod
    def _collect_online_time_for_period(collect_period: List[Tuple[str, datetime]], now: datetime) -> Dict[str, Any]:
        """
        收集指定时间段的在线时间统计数据

        :param collect_period: 统计时间段
        """
        if not collect_period:
            return {}

        collect_period.sort(key=lambda x: x[1], reverse=True)

        stats = {
            period_key: {
                ONLINE_TIME: 0.0,
            }
            for period_key, _ in collect_period
        }

        query_start_time = collect_period[-1][1]
        # Assuming OnlineTime.end_timestamp is a DateTimeField
        for record in OnlineTime.select().where(OnlineTime.end_timestamp >= query_start_time):
            # record.end_timestamp and record.start_timestamp are datetime objects
            record_end_timestamp = record.end_timestamp
            record_start_timestamp = record.start_timestamp

            for idx, (_, period_boundary_start) in enumerate(collect_period):
                if record_end_timestamp >= period_boundary_start:
                    # Calculate effective end time for this record in relation to 'now'
                    effective_end_time = min(record_end_timestamp, now)

                    for period_key, current_period_start_time in collect_period[idx:]:
                        # Determine the portion of the record that falls within this specific statistical period
                        overlap_start = max(record_start_timestamp, current_period_start_time)
                        overlap_end = effective_end_time  # Already capped by 'now' and record's own end

                        if overlap_end > overlap_start:
                            stats[period_key][ONLINE_TIME] += (overlap_end - overlap_start).total_seconds()
                    break
        return stats

    def _collect_message_count_for_period(self, collect_period: List[Tuple[str, datetime]]) -> Dict[str, Any]:
        """
        收集指定时间段的消息统计数据

        :param collect_period: 统计时间段
        """
        if not collect_period:
            return {}

        collect_period.sort(key=lambda x: x[1], reverse=True)

        stats = {
            period_key: {
                TOTAL_MSG_CNT: 0,
                MSG_CNT_BY_CHAT: defaultdict(int),
            }
            for period_key, _ in collect_period
        }

        query_start_timestamp = collect_period[-1][1].timestamp()  # Messages.time is a DoubleField (timestamp)
        for message in Messages.select().where(Messages.time >= query_start_timestamp):
            message_time_ts = message.time  # This is a float timestamp

            chat_id = None
            chat_name = None

            # Logic based on Peewee model structure, aiming to replicate original intent
            if message.chat_info_group_id:
                chat_id = f"g{message.chat_info_group_id}"
                chat_name = message.chat_info_group_name or f"群{message.chat_info_group_id}"
            elif message.user_id:  # Fallback to sender's info for chat_id if not a group_info based chat
                # This uses the message SENDER's ID as per original logic's fallback
                chat_id = f"u{message.user_id}"  # SENDER's user_id
                chat_name = message.user_nickname  # SENDER's nickname
            else:
                # If neither group_id nor sender_id is available for chat identification
                logger.warning(
                    f"Message (PK: {message.id if hasattr(message, 'id') else 'N/A'}) lacks group_id and user_id for chat stats."
                )
                continue

            if not chat_id:  # Should not happen if above logic is correct
                continue

            # Update name_mapping
            if chat_id in self.name_mapping:
                if chat_name != self.name_mapping[chat_id][0] and message_time_ts > self.name_mapping[chat_id][1]:
                    self.name_mapping[chat_id] = (chat_name, message_time_ts)
            else:
                self.name_mapping[chat_id] = (chat_name, message_time_ts)

            for idx, (_, period_start_dt) in enumerate(collect_period):
                if message_time_ts >= period_start_dt.timestamp():
                    for period_key, _ in collect_period[idx:]:
                        stats[period_key][TOTAL_MSG_CNT] += 1
                        stats[period_key][MSG_CNT_BY_CHAT][chat_id] += 1
                    break
        return stats

    def _collect_focus_statistics_for_period(self, collect_period: List[Tuple[str, datetime]]) -> Dict[str, Any]:
        """
        收集指定时间段的Focus统计数据

        :param collect_period: 统计时间段
        """
        if not collect_period:
            return {}

        collect_period.sort(key=lambda x: x[1], reverse=True)

        stats = {
            period_key: {
                FOCUS_TOTAL_CYCLES: 0,
                FOCUS_AVG_TIMES_BY_STAGE: defaultdict(list),
                FOCUS_ACTION_RATIOS: defaultdict(int),
                FOCUS_CYCLE_CNT_BY_CHAT: defaultdict(int),
                FOCUS_CYCLE_CNT_BY_ACTION: defaultdict(int),
                FOCUS_AVG_TIMES_BY_CHAT_ACTION: defaultdict(lambda: defaultdict(list)),
                FOCUS_AVG_TIMES_BY_ACTION: defaultdict(lambda: defaultdict(list)),
                "focus_exec_times_by_chat_action": defaultdict(lambda: defaultdict(list)),
                FOCUS_TOTAL_TIME_BY_CHAT: defaultdict(float),
                FOCUS_TOTAL_TIME_BY_ACTION: defaultdict(float),
                FOCUS_CYCLE_CNT_BY_VERSION: defaultdict(int),
                FOCUS_ACTION_RATIOS_BY_VERSION: defaultdict(lambda: defaultdict(int)),
                FOCUS_AVG_TIMES_BY_VERSION: defaultdict(lambda: defaultdict(list)),
                "focus_exec_times_by_version_action": defaultdict(lambda: defaultdict(list)),
                "focus_action_ratios_by_chat": defaultdict(lambda: defaultdict(int)),
                # 新增：前处理器和后处理器统计字段
                FOCUS_PROCESSOR_TIMES: defaultdict(list),  # 前处理器时间
                FOCUS_POST_PROCESSOR_TIMES: defaultdict(list),  # 后处理器时间
                FOCUS_POST_PROCESSOR_COUNT: defaultdict(int),  # 后处理器执行次数
            }
            for period_key, _ in collect_period
        }

        # 获取 log/hfc_loop 目录下的所有 json 文件
        log_dir = "log/hfc_loop"
        if not os.path.exists(log_dir):
            logger.warning(f"Focus log directory {log_dir} does not exist")
            return stats

        json_files = glob.glob(os.path.join(log_dir, "*.json"))
        query_start_time = collect_period[-1][1]

        for json_file in json_files:
            try:
                # 从文件名解析时间戳 (格式: hash_version_date_time.json)
                filename = os.path.basename(json_file)
                name_parts = filename.replace(".json", "").split("_")
                if len(name_parts) >= 4:
                    date_str = name_parts[-2]  # YYYYMMDD
                    time_str = name_parts[-1]  # HHMMSS
                    file_time_str = f"{date_str}_{time_str}"
                    file_time = datetime.strptime(file_time_str, "%Y%m%d_%H%M%S")

                    # 如果文件时间在查询范围内，则处理该文件
                    if file_time >= query_start_time:
                        with open(json_file, "r", encoding="utf-8") as f:
                            cycles_data = json.load(f)
                            self._process_focus_file_data(cycles_data, stats, collect_period, file_time)
            except Exception as e:
                logger.warning(f"Failed to process focus file {json_file}: {e}")
                continue

        # 计算平均值
        self._calculate_focus_averages(stats)
        return stats

    def _process_focus_file_data(
        self,
        cycles_data: List[Dict],
        stats: Dict[str, Any],
        collect_period: List[Tuple[str, datetime]],
        file_time: datetime,
    ):
        """
        处理单个focus文件的数据
        """
        for cycle_data in cycles_data:
            try:
                # 解析时间戳
                timestamp_str = cycle_data.get("timestamp", "")
                if timestamp_str:
                    cycle_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                else:
                    cycle_time = file_time  # 使用文件时间作为后备

                chat_id = cycle_data.get("chat_id", "unknown")
                action_type = cycle_data.get("action_type", "unknown")
                total_time = cycle_data.get("total_time", 0.0)
                step_times = cycle_data.get("step_times", {})
                version = cycle_data.get("version", "unknown")

                # 新增：获取前处理器和后处理器时间
                processor_time_costs = cycle_data.get("processor_time_costs", {})
                post_processor_time_costs = cycle_data.get("post_processor_time_costs", {})

                # 更新聊天ID名称映射
                if chat_id not in self.name_mapping:
                    # 尝试获取实际的聊天名称
                    display_name = self._get_chat_display_name_from_id(chat_id)
                    self.name_mapping[chat_id] = (display_name, cycle_time.timestamp())

                # 对每个时间段进行统计
                for idx, (_, period_start) in enumerate(collect_period):
                    if cycle_time >= period_start:
                        for period_key, _ in collect_period[idx:]:
                            stat = stats[period_key]

                            # 基础统计
                            stat[FOCUS_TOTAL_CYCLES] += 1
                            stat[FOCUS_ACTION_RATIOS][action_type] += 1
                            stat[FOCUS_CYCLE_CNT_BY_CHAT][chat_id] += 1
                            stat[FOCUS_CYCLE_CNT_BY_ACTION][action_type] += 1
                            stat["focus_action_ratios_by_chat"][chat_id][action_type] += 1
                            stat[FOCUS_TOTAL_TIME_BY_CHAT][chat_id] += total_time
                            stat[FOCUS_TOTAL_TIME_BY_ACTION][action_type] += total_time

                            # 版本统计
                            stat[FOCUS_CYCLE_CNT_BY_VERSION][version] += 1
                            stat[FOCUS_ACTION_RATIOS_BY_VERSION][version][action_type] += 1

                            # 阶段时间统计
                            for stage, time_val in step_times.items():
                                stat[FOCUS_AVG_TIMES_BY_STAGE][stage].append(time_val)
                                stat[FOCUS_AVG_TIMES_BY_CHAT_ACTION][chat_id][stage].append(time_val)
                                stat[FOCUS_AVG_TIMES_BY_ACTION][action_type][stage].append(time_val)
                                stat[FOCUS_AVG_TIMES_BY_VERSION][version][stage].append(time_val)

                                # 专门收集执行动作阶段的时间，按聊天流和action类型分组
                                if stage == "执行动作":
                                    stat["focus_exec_times_by_chat_action"][chat_id][action_type].append(time_val)
                                    # 按版本和action类型收集执行时间
                                    stat["focus_exec_times_by_version_action"][version][action_type].append(time_val)

                            # 新增：前处理器时间统计
                            for processor_name, time_val in processor_time_costs.items():
                                stat[FOCUS_PROCESSOR_TIMES][processor_name].append(time_val)

                            # 新增：后处理器时间统计
                            for processor_name, time_val in post_processor_time_costs.items():
                                stat[FOCUS_POST_PROCESSOR_TIMES][processor_name].append(time_val)
                                stat[FOCUS_POST_PROCESSOR_COUNT][processor_name] += 1
                        break
            except Exception as e:
                logger.warning(f"Failed to process cycle data: {e}")
                continue

    def _calculate_focus_averages(self, stats: Dict[str, Any]):
        """
        计算Focus统计的平均值
        """
        for _period_key, stat in stats.items():
            # 计算全局阶段平均时间
            for stage, times in stat[FOCUS_AVG_TIMES_BY_STAGE].items():
                if times:
                    stat[FOCUS_AVG_TIMES_BY_STAGE][stage] = sum(times) / len(times)
                else:
                    stat[FOCUS_AVG_TIMES_BY_STAGE][stage] = 0.0

            # 计算按chat_id和action_type的阶段平均时间
            for chat_id, stage_times in stat[FOCUS_AVG_TIMES_BY_CHAT_ACTION].items():
                for stage, times in stage_times.items():
                    if times:
                        stat[FOCUS_AVG_TIMES_BY_CHAT_ACTION][chat_id][stage] = sum(times) / len(times)
                    else:
                        stat[FOCUS_AVG_TIMES_BY_CHAT_ACTION][chat_id][stage] = 0.0

            # 计算按action_type的阶段平均时间
            for action_type, stage_times in stat[FOCUS_AVG_TIMES_BY_ACTION].items():
                for stage, times in stage_times.items():
                    if times:
                        stat[FOCUS_AVG_TIMES_BY_ACTION][action_type][stage] = sum(times) / len(times)
                    else:
                        stat[FOCUS_AVG_TIMES_BY_ACTION][action_type][stage] = 0.0

            # 计算按聊天流和action类型的执行时间平均值
            for chat_id, action_times in stat["focus_exec_times_by_chat_action"].items():
                for action_type, times in action_times.items():
                    if times:
                        stat["focus_exec_times_by_chat_action"][chat_id][action_type] = sum(times) / len(times)
                    else:
                        stat["focus_exec_times_by_chat_action"][chat_id][action_type] = 0.0

            # 计算按版本的阶段平均时间
            for version, stage_times in stat[FOCUS_AVG_TIMES_BY_VERSION].items():
                for stage, times in stage_times.items():
                    if times:
                        stat[FOCUS_AVG_TIMES_BY_VERSION][version][stage] = sum(times) / len(times)
                    else:
                        stat[FOCUS_AVG_TIMES_BY_VERSION][version][stage] = 0.0

            # 计算按版本和action类型的执行时间平均值
            for version, action_times in stat["focus_exec_times_by_version_action"].items():
                for action_type, times in action_times.items():
                    if times:
                        stat["focus_exec_times_by_version_action"][version][action_type] = sum(times) / len(times)
                    else:
                        stat["focus_exec_times_by_version_action"][version][action_type] = 0.0

            # 新增：计算前处理器平均时间
            for processor_name, times in stat[FOCUS_PROCESSOR_TIMES].items():
                if times:
                    stat[FOCUS_PROCESSOR_TIMES][processor_name] = sum(times) / len(times)
                else:
                    stat[FOCUS_PROCESSOR_TIMES][processor_name] = 0.0

            # 新增：计算后处理器平均时间
            for processor_name, times in stat[FOCUS_POST_PROCESSOR_TIMES].items():
                if times:
                    stat[FOCUS_POST_PROCESSOR_TIMES][processor_name] = sum(times) / len(times)
                else:
                    stat[FOCUS_POST_PROCESSOR_TIMES][processor_name] = 0.0

    def _collect_all_statistics(self, now: datetime) -> Dict[str, Dict[str, Any]]:
        """
        收集各时间段的统计数据
        :param now: 基准当前时间
        """

        last_all_time_stat = None

        if "last_full_statistics" in local_storage:
            # 如果存在上次完整统计数据，则使用该数据进行增量统计
            last_stat = local_storage["last_full_statistics"]  # 上次完整统计数据

            self.name_mapping = last_stat["name_mapping"]  # 上次完整统计数据的名称映射
            last_all_time_stat = last_stat["stat_data"]  # 上次完整统计的统计数据
            last_stat_timestamp = datetime.fromtimestamp(last_stat["timestamp"])  # 上次完整统计数据的时间戳
            self.stat_period = [item for item in self.stat_period if item[0] != "all_time"]  # 删除"所有时间"的统计时段
            self.stat_period.append(("all_time", now - last_stat_timestamp, "自部署以来的"))

        stat_start_timestamp = [(period[0], now - period[1]) for period in self.stat_period]

        stat = {item[0]: {} for item in self.stat_period}

        model_req_stat = self._collect_model_request_for_period(stat_start_timestamp)
        online_time_stat = self._collect_online_time_for_period(stat_start_timestamp, now)
        message_count_stat = self._collect_message_count_for_period(stat_start_timestamp)
        focus_stat = self._collect_focus_statistics_for_period(stat_start_timestamp)

        # 统计数据合并
        # 合并四类统计数据
        for period_key, _ in stat_start_timestamp:
            stat[period_key].update(model_req_stat[period_key])
            stat[period_key].update(online_time_stat[period_key])
            stat[period_key].update(message_count_stat[period_key])
            stat[period_key].update(focus_stat[period_key])

        if last_all_time_stat:
            # 若存在上次完整统计数据，则将其与当前统计数据合并
            for key, val in last_all_time_stat.items():
                # 确保当前统计数据中存在该key
                if key not in stat["all_time"]:
                    continue

                if isinstance(val, dict):
                    # 是字典类型，则进行合并
                    for sub_key, sub_val in val.items():
                        # 普通的数值或字典合并
                        if sub_key in stat["all_time"][key]:
                            # 检查是否为嵌套的字典类型（如版本统计）
                            if isinstance(sub_val, dict) and isinstance(stat["all_time"][key][sub_key], dict):
                                # 合并嵌套字典
                                for nested_key, nested_val in sub_val.items():
                                    if nested_key in stat["all_time"][key][sub_key]:
                                        stat["all_time"][key][sub_key][nested_key] += nested_val
                                    else:
                                        stat["all_time"][key][sub_key][nested_key] = nested_val
                            else:
                                # 普通数值累加
                                stat["all_time"][key][sub_key] += sub_val
                        else:
                            stat["all_time"][key][sub_key] = sub_val
                else:
                    # 直接合并
                    stat["all_time"][key] += val

        # 更新上次完整统计数据的时间戳
        # 将所有defaultdict转换为普通dict以避免类型冲突
        clean_stat_data = self._convert_defaultdict_to_dict(stat["all_time"])
        local_storage["last_full_statistics"] = {
            "name_mapping": self.name_mapping,
            "stat_data": clean_stat_data,
            "timestamp": now.timestamp(),
        }

        return stat

    def _convert_defaultdict_to_dict(self, data):
        """递归转换defaultdict为普通dict"""
        if isinstance(data, defaultdict):
            # 转换defaultdict为普通dict
            result = {}
            for key, value in data.items():
                result[key] = self._convert_defaultdict_to_dict(value)
            return result
        elif isinstance(data, dict):
            # 递归处理普通dict
            result = {}
            for key, value in data.items():
                result[key] = self._convert_defaultdict_to_dict(value)
            return result
        else:
            # 其他类型直接返回
            return data

    # -- 以下为统计数据格式化方法 --

    @staticmethod
    def _format_total_stat(stats: Dict[str, Any]) -> str:
        """
        格式化总统计数据
        """

        output = [
            f"总在线时间: {_format_online_time(stats[ONLINE_TIME])}",
            f"总消息数: {stats[TOTAL_MSG_CNT]}",
            f"总请求数: {stats[TOTAL_REQ_CNT]}",
            f"总花费: {stats[TOTAL_COST]:.4f}¥",
            "",
        ]

        return "\n".join(output)

    @staticmethod
    def _format_model_classified_stat(stats: Dict[str, Any]) -> str:
        """
        格式化按模型分类的统计数据
        """
        if stats[TOTAL_REQ_CNT] <= 0:
            return ""
        data_fmt = "{:<32}  {:>10}  {:>12}  {:>12}  {:>12}  {:>9.4f}¥"

        output = [
            "按模型分类统计:",
            " 模型名称                          调用次数    输入Token     输出Token     Token总量     累计花费",
        ]
        for model_name, count in sorted(stats[REQ_CNT_BY_MODEL].items()):
            name = f"{model_name[:29]}..." if len(model_name) > 32 else model_name
            in_tokens = stats[IN_TOK_BY_MODEL][model_name]
            out_tokens = stats[OUT_TOK_BY_MODEL][model_name]
            tokens = stats[TOTAL_TOK_BY_MODEL][model_name]
            cost = stats[COST_BY_MODEL][model_name]
            output.append(data_fmt.format(name, count, in_tokens, out_tokens, tokens, cost))

        output.append("")
        return "\n".join(output)

    def _format_chat_stat(self, stats: Dict[str, Any]) -> str:
        """
        格式化聊天统计数据
        """
        if stats[TOTAL_MSG_CNT] <= 0:
            return ""
        output = ["聊天消息统计:", " 联系人/群组名称                  消息数量"]
        output.extend(
            f"{self.name_mapping[chat_id][0][:32]:<32}  {count:>10}"
            for chat_id, count in sorted(stats[MSG_CNT_BY_CHAT].items())
        )
        output.append("")
        return "\n".join(output)

    def _format_focus_stat(self, stats: Dict[str, Any]) -> str:
        """
        格式化Focus统计数据
        """
        if stats[FOCUS_TOTAL_CYCLES] <= 0:
            return ""

        output = ["Focus系统统计:", f"总循环数: {stats[FOCUS_TOTAL_CYCLES]}", ""]

        # 全局阶段平均时间
        if stats[FOCUS_AVG_TIMES_BY_STAGE]:
            output.append("全局阶段平均时间:")
            for stage, avg_time in stats[FOCUS_AVG_TIMES_BY_STAGE].items():
                output.append(f"  {stage}: {avg_time:.3f}秒")
            output.append("")

        # Action类型比例
        if stats[FOCUS_ACTION_RATIOS]:
            total_actions = sum(stats[FOCUS_ACTION_RATIOS].values())
            output.append("Action类型分布:")
            for action_type, count in sorted(stats[FOCUS_ACTION_RATIOS].items()):
                ratio = (count / total_actions) * 100 if total_actions > 0 else 0
                output.append(f"  {action_type}: {count} ({ratio:.1f}%)")
            output.append("")

        # 按Chat统计（仅显示前10个）
        if stats[FOCUS_CYCLE_CNT_BY_CHAT]:
            output.append("按聊天流统计 (前10):")
            sorted_chats = sorted(stats[FOCUS_CYCLE_CNT_BY_CHAT].items(), key=lambda x: x[1], reverse=True)[:10]
            for chat_id, count in sorted_chats:
                chat_name = self.name_mapping.get(chat_id, (chat_id, 0))[0]
                output.append(f"  {chat_name[:30]}: {count} 循环")
            output.append("")

        return "\n".join(output)

    def _get_chat_display_name_from_id(self, chat_id: str) -> str:
        """从chat_id获取显示名称"""
        try:
            # 首先尝试从chat_stream获取真实群组名称
            from src.chat.message_receive.chat_stream import get_chat_manager

            chat_manager = get_chat_manager()

            if chat_id in chat_manager.streams:
                stream = chat_manager.streams[chat_id]
                if stream.group_info and hasattr(stream.group_info, "group_name"):
                    group_name = stream.group_info.group_name
                    if group_name and group_name.strip():
                        return group_name.strip()
                elif stream.user_info and hasattr(stream.user_info, "user_nickname"):
                    user_name = stream.user_info.user_nickname
                    if user_name and user_name.strip():
                        return user_name.strip()

            # 如果从chat_stream获取失败，尝试解析chat_id格式
            if chat_id.startswith("g"):
                return f"群聊{chat_id[1:]}"
            elif chat_id.startswith("u"):
                return f"用户{chat_id[1:]}"
            else:
                return chat_id
        except Exception as e:
            logger.warning(f"获取聊天显示名称失败: {e}")
            return chat_id

    def _generate_html_report(self, stat: dict[str, Any], now: datetime):
        """
        生成HTML格式的统计报告
        :param stat: 统计数据
        :param now: 基准当前时间
        :return: HTML格式的统计报告
        """

        tab_list = [
            f'<button class="tab-link" onclick="showTab(event, \'{period[0]}\')">{period[2]}</button>'
            for period in self.stat_period
        ]
        # 添加Focus统计、版本对比和图表选项卡
        tab_list.append('<button class="tab-link" onclick="showTab(event, \'focus\')">Focus统计</button>')
        tab_list.append('<button class="tab-link" onclick="showTab(event, \'versions\')">版本对比</button>')
        tab_list.append('<button class="tab-link" onclick="showTab(event, \'charts\')">数据图表</button>')

        def _format_stat_data(stat_data: dict[str, Any], div_id: str, start_time: datetime) -> str:
            """
            格式化一个时间段的统计数据到html div块
            :param stat_data: 统计数据
            :param div_id: div的ID
            :param start_time: 统计时间段开始时间
            """
            # format总在线时间

            # 按模型分类统计
            "\n".join(
                [
                    f"<tr>"
                    f"<td>{model_name}</td>"
                    f"<td>{count}</td>"
                    f"<td>{stat_data[IN_TOK_BY_MODEL][model_name]}</td>"
                    f"<td>{stat_data[OUT_TOK_BY_MODEL][model_name]}</td>"
                    f"<td>{stat_data[TOTAL_TOK_BY_MODEL][model_name]}</td>"
                    f"<td>{stat_data[COST_BY_MODEL][model_name]:.4f} ¥</td>"
                    f"</tr>"
                    for model_name, count in sorted(stat_data[REQ_CNT_BY_MODEL].items())
                ]
            )
            # 按请求类型分类统计
            "\n".join(
                [
                    f"<tr>"
                    f"<td>{req_type}</td>"
                    f"<td>{count}</td>"
                    f"<td>{stat_data[IN_TOK_BY_TYPE][req_type]}</td>"
                    f"<td>{stat_data[OUT_TOK_BY_TYPE][req_type]}</td>"
                    f"<td>{stat_data[TOTAL_TOK_BY_TYPE][req_type]}</td>"
                    f"<td>{stat_data[COST_BY_TYPE][req_type]:.4f} ¥</td>"
                    f"</tr>"
                    for req_type, count in sorted(stat_data[REQ_CNT_BY_TYPE].items())
                ]
            )
            # 按模块分类统计
            "\n".join(
                [
                    f"<tr>"
                    f"<td>{module_name}</td>"
                    f"<td>{count}</td>"
                    f"<td>{stat_data[IN_TOK_BY_MODULE][module_name]}</td>"
                    f"<td>{stat_data[OUT_TOK_BY_MODULE][module_name]}</td>"
                    f"<td>{stat_data[TOTAL_TOK_BY_MODULE][module_name]}</td>"
                    f"<td>{stat_data[COST_BY_MODULE][module_name]:.4f} ¥</td>"
                    f"</tr>"
                    for module_name, count in sorted(stat_data[REQ_CNT_BY_MODULE].items())
                ]
            )

            # 聊天消息统计
            "\n".join(
                [
                    f"<tr><td>{self.name_mapping[chat_id][0]}</td><td>{count}</td></tr>"
                    for chat_id, count in sorted(stat_data[MSG_CNT_BY_CHAT].items())
                ]
            )

            # Focus统计数据
            # focus_action_rows = ""
            # focus_chat_rows = ""
            # focus_stage_rows = ""
            # focus_action_stage_rows = ""

            if stat_data.get(FOCUS_TOTAL_CYCLES, 0) > 0:
                # Action类型统计
                total_actions = sum(stat_data[FOCUS_ACTION_RATIOS].values()) if stat_data[FOCUS_ACTION_RATIOS] else 0
                _focus_action_rows = "\n".join(
                    [
                        f"<tr><td>{action_type}</td><td>{count}</td><td>{(count / total_actions * 100):.1f}%</td></tr>"
                        for action_type, count in sorted(stat_data[FOCUS_ACTION_RATIOS].items())
                    ]
                )

        # 为每个时间段准备Focus数据
        focus_sections = []

        for period_name, period_delta, period_desc in self.stat_period:
            stat_data = stat.get(period_name, {})

            if stat_data.get(FOCUS_TOTAL_CYCLES, 0) <= 0:
                continue

            # 生成Focus统计数据行
            focus_action_rows = ""
            focus_chat_rows = ""
            focus_stage_rows = ""
            focus_action_stage_rows = ""

            # Action类型统计
            total_actions = sum(stat_data[FOCUS_ACTION_RATIOS].values()) if stat_data[FOCUS_ACTION_RATIOS] else 0
            if total_actions > 0:
                focus_action_rows = "\n".join(
                    [
                        f"<tr><td>{action_type}</td><td>{count}</td><td>{(count / total_actions * 100):.1f}%</td></tr>"
                        for action_type, count in sorted(stat_data[FOCUS_ACTION_RATIOS].items())
                    ]
                )

            # 按聊天流统计（横向表格，显示各阶段时间差异和不同action的平均时间）
            focus_chat_rows = ""
            if stat_data[FOCUS_AVG_TIMES_BY_CHAT_ACTION]:
                # 获取所有阶段（包括后处理器）
                basic_stages = ["观察", "并行调整动作、处理", "规划器", "后期处理器", "动作执行"]
                existing_basic_stages = []
                for stage in basic_stages:
                    # 检查是否有任何聊天流在这个阶段有数据
                    stage_exists = False
                    for _chat_id, stage_times in stat_data[FOCUS_AVG_TIMES_BY_CHAT_ACTION].items():
                        if stage in stage_times:
                            stage_exists = True
                            break
                    if stage_exists:
                        existing_basic_stages.append(stage)

                # 获取所有action类型（按出现频率排序）
                all_action_types = sorted(
                    stat_data[FOCUS_ACTION_RATIOS].keys(), key=lambda x: stat_data[FOCUS_ACTION_RATIOS][x], reverse=True
                )

                # 为每个聊天流生成一行
                chat_rows = []
                for chat_id in sorted(
                    stat_data[FOCUS_CYCLE_CNT_BY_CHAT].keys(),
                    key=lambda x: stat_data[FOCUS_CYCLE_CNT_BY_CHAT][x],
                    reverse=True,
                ):
                    chat_name = self.name_mapping.get(chat_id, (chat_id, 0))[0]
                    cycle_count = stat_data[FOCUS_CYCLE_CNT_BY_CHAT][chat_id]

                    # 获取该聊天流的各阶段平均时间
                    stage_times = stat_data[FOCUS_AVG_TIMES_BY_CHAT_ACTION].get(chat_id, {})

                    row_cells = [f"<td><strong>{chat_name}</strong><br><small>({cycle_count}次循环)</small></td>"]

                    # 添加基础阶段时间
                    for stage in existing_basic_stages:
                        time_val = stage_times.get(stage, 0.0)
                        row_cells.append(f"<td>{time_val:.3f}秒</td>")

                    # 添加每个action类型的平均执行时间
                    for action_type in all_action_types:
                        # 使用真实的按聊天流+action类型分组的执行时间数据
                        exec_times_by_chat_action = stat_data.get("focus_exec_times_by_chat_action", {})
                        chat_action_times = exec_times_by_chat_action.get(chat_id, {})
                        avg_exec_time = chat_action_times.get(action_type, 0.0)

                        if avg_exec_time > 0:
                            row_cells.append(f"<td>{avg_exec_time:.3f}秒</td>")
                        else:
                            row_cells.append("<td>-</td>")

                    chat_rows.append(f"<tr>{''.join(row_cells)}</tr>")

                # 生成表头
                stage_headers = "".join([f"<th>{stage}</th>" for stage in existing_basic_stages])
                action_headers = "".join(
                    [f"<th>{action_type}<br><small>(执行)</small></th>" for action_type in all_action_types]
                )
                focus_chat_table_header = f"<tr><th>聊天流</th>{stage_headers}{action_headers}</tr>"
                focus_chat_rows = focus_chat_table_header + "\n" + "\n".join(chat_rows)

            # 全局阶段时间统计
            focus_stage_rows = "\n".join(
                [
                    f"<tr><td>{stage}</td><td>{avg_time:.3f}秒</td></tr>"
                    for stage, avg_time in sorted(stat_data[FOCUS_AVG_TIMES_BY_STAGE].items())
                ]
            )

            # 聊天流Action选择比例对比表（横向表格）
            focus_chat_action_ratios_rows = ""
            if stat_data.get("focus_action_ratios_by_chat"):
                # 获取所有action类型（按全局频率排序）
                all_action_types_for_ratio = sorted(
                    stat_data[FOCUS_ACTION_RATIOS].keys(), key=lambda x: stat_data[FOCUS_ACTION_RATIOS][x], reverse=True
                )

                if all_action_types_for_ratio:
                    # 为每个聊天流生成数据行（按循环数排序）
                    chat_ratio_rows = []
                    for chat_id in sorted(
                        stat_data[FOCUS_CYCLE_CNT_BY_CHAT].keys(),
                        key=lambda x: stat_data[FOCUS_CYCLE_CNT_BY_CHAT][x],
                        reverse=True,
                    ):
                        chat_name = self.name_mapping.get(chat_id, (chat_id, 0))[0]
                        total_cycles = stat_data[FOCUS_CYCLE_CNT_BY_CHAT][chat_id]
                        chat_action_counts = stat_data["focus_action_ratios_by_chat"].get(chat_id, {})

                        row_cells = [f"<td><strong>{chat_name}</strong><br><small>({total_cycles}次循环)</small></td>"]

                        # 添加每个action类型的数量和百分比
                        for action_type in all_action_types_for_ratio:
                            count = chat_action_counts.get(action_type, 0)
                            ratio = (count / total_cycles * 100) if total_cycles > 0 else 0
                            if count > 0:
                                row_cells.append(f"<td>{count}<br><small>({ratio:.1f}%)</small></td>")
                            else:
                                row_cells.append("<td>-<br><small>(0%)</small></td>")

                        chat_ratio_rows.append(f"<tr>{''.join(row_cells)}</tr>")

                    # 生成表头
                    action_headers = "".join([f"<th>{action_type}</th>" for action_type in all_action_types_for_ratio])
                    chat_action_ratio_table_header = f"<tr><th>聊天流</th>{action_headers}</tr>"
                    focus_chat_action_ratios_rows = chat_action_ratio_table_header + "\n" + "\n".join(chat_ratio_rows)

                # 获取所有action类型（按出现频率排序）
                all_action_types = sorted(
                    stat_data[FOCUS_ACTION_RATIOS].keys(), key=lambda x: stat_data[FOCUS_ACTION_RATIOS][x], reverse=True
                )

                # 为每个聊天流生成一行
                chat_rows = []
                for chat_id in sorted(
                    stat_data[FOCUS_CYCLE_CNT_BY_CHAT].keys(),
                    key=lambda x: stat_data[FOCUS_CYCLE_CNT_BY_CHAT][x],
                    reverse=True,
                ):
                    chat_name = self.name_mapping.get(chat_id, (chat_id, 0))[0]
                    cycle_count = stat_data[FOCUS_CYCLE_CNT_BY_CHAT][chat_id]

                    # 获取该聊天流的各阶段平均时间
                    stage_times = stat_data[FOCUS_AVG_TIMES_BY_CHAT_ACTION].get(chat_id, {})

                    row_cells = [f"<td><strong>{chat_name}</strong><br><small>({cycle_count}次循环)</small></td>"]

                    # 添加基础阶段时间
                    for stage in existing_basic_stages:
                        time_val = stage_times.get(stage, 0.0)
                        row_cells.append(f"<td>{time_val:.3f}秒</td>")

                    # 添加每个action类型的平均执行时间
                    for action_type in all_action_types:
                        # 使用真实的按聊天流+action类型分组的执行时间数据
                        exec_times_by_chat_action = stat_data.get("focus_exec_times_by_chat_action", {})
                        chat_action_times = exec_times_by_chat_action.get(chat_id, {})
                        avg_exec_time = chat_action_times.get(action_type, 0.0)

                        if avg_exec_time > 0:
                            row_cells.append(f"<td>{avg_exec_time:.3f}秒</td>")
                        else:
                            row_cells.append("<td>-</td>")

                    chat_rows.append(f"<tr>{''.join(row_cells)}</tr>")

                # 生成表头
                stage_headers = "".join([f"<th>{stage}</th>" for stage in existing_basic_stages])
                action_headers = "".join(
                    [f"<th>{action_type}<br><small>(执行)</small></th>" for action_type in all_action_types]
                )
                focus_chat_table_header = f"<tr><th>聊天流</th>{stage_headers}{action_headers}</tr>"
                focus_chat_rows = focus_chat_table_header + "\n" + "\n".join(chat_rows)

            # 全局阶段时间统计
            focus_stage_rows = "\n".join(
                [
                    f"<tr><td>{stage}</td><td>{avg_time:.3f}秒</td></tr>"
                    for stage, avg_time in sorted(stat_data[FOCUS_AVG_TIMES_BY_STAGE].items())
                ]
            )

            # 聊天流Action选择比例对比表（横向表格）
            focus_chat_action_ratios_rows = ""
            if stat_data.get("focus_action_ratios_by_chat"):
                # 获取所有action类型（按全局频率排序）
                all_action_types_for_ratio = sorted(
                    stat_data[FOCUS_ACTION_RATIOS].keys(), key=lambda x: stat_data[FOCUS_ACTION_RATIOS][x], reverse=True
                )

                if all_action_types_for_ratio:
                    # 为每个聊天流生成数据行（按循环数排序）
                    chat_ratio_rows = []
                    for chat_id in sorted(
                        stat_data[FOCUS_CYCLE_CNT_BY_CHAT].keys(),
                        key=lambda x: stat_data[FOCUS_CYCLE_CNT_BY_CHAT][x],
                        reverse=True,
                    ):
                        chat_name = self.name_mapping.get(chat_id, (chat_id, 0))[0]
                        total_cycles = stat_data[FOCUS_CYCLE_CNT_BY_CHAT][chat_id]
                        chat_action_counts = stat_data["focus_action_ratios_by_chat"].get(chat_id, {})

                        row_cells = [f"<td><strong>{chat_name}</strong><br><small>({total_cycles}次循环)</small></td>"]

                        # 添加每个action类型的数量和百分比
                        for action_type in all_action_types_for_ratio:
                            count = chat_action_counts.get(action_type, 0)
                            ratio = (count / total_cycles * 100) if total_cycles > 0 else 0
                            if count > 0:
                                row_cells.append(f"<td>{count}<br><small>({ratio:.1f}%)</small></td>")
                            else:
                                row_cells.append("<td>-<br><small>(0%)</small></td>")

                        chat_ratio_rows.append(f"<tr>{''.join(row_cells)}</tr>")

                    # 生成表头
                    action_headers = "".join([f"<th>{action_type}</th>" for action_type in all_action_types_for_ratio])
                    chat_action_ratio_table_header = f"<tr><th>聊天流</th>{action_headers}</tr>"
                    focus_chat_action_ratios_rows = chat_action_ratio_table_header + "\n" + "\n".join(chat_ratio_rows)

            # 按Action类型的阶段时间统计（横向表格）
            focus_action_stage_rows = ""
            if stat_data[FOCUS_AVG_TIMES_BY_ACTION]:
                # 获取所有阶段（按固定顺序，确保与实际Timer名称一致）
                stage_order = ["观察", "并行调整动作、处理", "规划器", "后期处理器", "动作执行"]
                all_stages = []
                for stage in stage_order:
                    if any(stage in stage_times for stage_times in stat_data[FOCUS_AVG_TIMES_BY_ACTION].values()):
                        all_stages.append(stage)

                # 为每个Action类型生成一行
                action_rows = []
                for action_type in sorted(stat_data[FOCUS_AVG_TIMES_BY_ACTION].keys()):
                    stage_times = stat_data[FOCUS_AVG_TIMES_BY_ACTION][action_type]
                    row_cells = [f"<td><strong>{action_type}</strong></td>"]

                    for stage in all_stages:
                        time_val = stage_times.get(stage, 0.0)
                        row_cells.append(f"<td>{time_val:.3f}秒</td>")

                    action_rows.append(f"<tr>{''.join(row_cells)}</tr>")

                # 生成表头
                stage_headers = "".join([f"<th>{stage}</th>" for stage in all_stages])
                focus_action_stage_table_header = f"<tr><th>Action类型</th>{stage_headers}</tr>"
                focus_action_stage_rows = focus_action_stage_table_header + "\n" + "\n".join(action_rows)

            # 新增：前处理器统计表格
            focus_processor_rows = ""
            if stat_data.get(FOCUS_PROCESSOR_TIMES):
                processor_rows = []
                for processor_name in sorted(stat_data[FOCUS_PROCESSOR_TIMES].keys()):
                    avg_time = stat_data[FOCUS_PROCESSOR_TIMES][processor_name]
                    processor_rows.append(f"<tr><td>{processor_name}</td><td>{avg_time:.3f}秒</td></tr>")
                focus_processor_rows = "\n".join(processor_rows)

            # 新增：前处理器统计表格
            focus_processor_rows = ""
            if stat_data.get(FOCUS_PROCESSOR_TIMES):
                processor_rows = []
                for processor_name in sorted(stat_data[FOCUS_PROCESSOR_TIMES].keys()):
                    avg_time = stat_data[FOCUS_PROCESSOR_TIMES][processor_name]
                    processor_rows.append(f"<tr><td>{processor_name}</td><td>{avg_time:.3f}秒</td></tr>")
                focus_processor_rows = "\n".join(processor_rows)

            # 新增：后处理器统计表格
            focus_post_processor_rows = ""
            if stat_data.get(FOCUS_POST_PROCESSOR_TIMES):
                post_processor_rows = []
                for processor_name in sorted(stat_data[FOCUS_POST_PROCESSOR_TIMES].keys()):
                    avg_time = stat_data[FOCUS_POST_PROCESSOR_TIMES][processor_name]
                    count = stat_data[FOCUS_POST_PROCESSOR_COUNT].get(processor_name, 0)
                    post_processor_rows.append(
                        f"<tr><td>{processor_name}</td><td>{avg_time:.3f}秒</td><td>{count}</td></tr>"
                    )
                focus_post_processor_rows = "\n".join(post_processor_rows)

            # 计算时间范围
            if period_name == "all_time":
                from src.manager.local_store_manager import local_storage

                start_time = datetime.fromtimestamp(local_storage["deploy_time"])
                time_range = (
                    f"{start_time.strftime('%Y-%m-%d %H:%M:%S')} ~ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
            else:
                start_time = datetime.now() - period_delta
                time_range = (
                    f"{start_time.strftime('%Y-%m-%d %H:%M:%S')} ~ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )

            # 生成该时间段的Focus统计HTML
            section_html = f"""
            <div class="focus-period-section">
                <h2>{period_desc}Focus统计</h2>
                <p class="info-item"><strong>统计时段:</strong> {time_range}</p>
                <p class="info-item"><strong>总循环数:</strong> {stat_data.get(FOCUS_TOTAL_CYCLES, 0)}</p>
                
                <div class="focus-stats-grid">
                    <div class="focus-stat-item">
                        <h3>全局阶段平均时间</h3>
                        <table>
                            <thead><tr><th>阶段</th><th>平均时间</th></tr></thead>
                            <tbody>{focus_stage_rows}</tbody>
                        </table>
                    </div>
                    
                    <div class="focus-stat-item">
                        <h3>Action类型分布</h3>
                        <table>
                            <thead><tr><th>Action类型</th><th>次数</th><th>占比</th></tr></thead>
                            <tbody>{focus_action_rows}</tbody>
                        </table>
                    </div>
                </div>
                
                <div class="focus-stat-item">
                    <h3>按聊天流各阶段时间统计</h3>
                    <table class="chat-stage-table">
                        <thead></thead>
                        <tbody>{focus_chat_rows}</tbody>
                    </table>
                </div>
                
                <div class="focus-stat-item">
                    <h3>聊天流Action选择比例对比</h3>
                    <table class="chat-action-ratio-table">
                        <thead></thead>
                        <tbody>{focus_chat_action_ratios_rows}</tbody>
                    </table>
                </div>
                
                <div class="focus-stat-item">
                    <h3>Action类型阶段时间详情</h3>
                    <table class="action-stage-table">
                        <thead></thead>
                        <tbody>{focus_action_stage_rows}</tbody>
                    </table>
                </div>
                
                <div class="focus-stats-grid">
                    <div class="focus-stat-item">
                        <h3>前处理器平均时间</h3>
                        <table>
                            <thead><tr><th>处理器名称</th><th>平均耗时</th></tr></thead>
                            <tbody>{focus_processor_rows}</tbody>
                        </table>
                    </div>
                    
                    <div class="focus-stat-item">
                        <h3>后处理器统计</h3>
                        <table>
                            <thead><tr><th>处理器名称</th><th>平均耗时</th><th>执行次数</th></tr></thead>
                            <tbody>{focus_post_processor_rows}</tbody>
                        </table>
                    </div>
                </div>
            </div>
            """

            focus_sections.append(section_html)

        # 如果没有任何Focus数据
        if not focus_sections:
            focus_sections.append("""
            <div class="focus-period-section">
                <h2>暂无Focus统计数据</h2>
                <p class="info-item">在指定时间段内未找到任何Focus循环数据。</p>
                <p class="info-item">请确保 <code>log/hfc_loop/</code> 目录下存在相应的JSON文件。</p>
            </div>
            """)

        return f"""
        <div id="focus" class="tab-content">
            <h1>Focus系统详细统计</h1>
            <p class="info-item">
                <strong>数据来源:</strong> log/hfc_loop/ 目录下的JSON文件<br>
                <strong>统计内容:</strong> 各时间段的Focus循环性能分析
            </p>
            
            {"".join(focus_sections)}
            
            <style>
                .focus-period-section {{
                    margin-bottom: 40px;
                    padding: 20px;
                    border: 1px solid #ddd;
                    border-radius: 8px;
                    background-color: #fafafa;
                }}
                
                .focus-stats-grid {{
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 20px;
                    margin: 20px 0;
                }}
                
                .focus-stat-item {{
                    background-color: white;
                    padding: 15px;
                    border-radius: 6px;
                    border: 1px solid #eee;
                }}
                
                .focus-stat-item h3 {{
                    margin-top: 0;
                    color: #2c3e50;
                    border-bottom: 1px solid #3498db;
                    padding-bottom: 5px;
                }}
                
                @media (max-width: 768px) {{
                    .focus-stats-grid {{
                        grid-template-columns: 1fr;
                    }}
                }}
                
                /* 为横向表格添加特殊样式 */
                .focus-stat-item table.action-stage-table,
                .focus-stat-item table.chat-stage-table,
                .focus-stat-item table.chat-action-ratio-table {{
                    width: 100%;
                    overflow-x: auto;
                    display: block;
                    white-space: nowrap;
                }}
                
                .focus-stat-item table.action-stage-table tbody,
                .focus-stat-item table.chat-stage-table tbody,
                .focus-stat-item table.chat-action-ratio-table tbody {{
                    display: table;
                    width: 100%;
                }}
                
                .focus-stat-item table.action-stage-table tr:first-child,
                .focus-stat-item table.chat-stage-table tr:first-child,
                .focus-stat-item table.chat-action-ratio-table tr:first-child {{
                    background-color: #3498db;
                    color: white;
                    font-weight: bold;
                }}
                
                .focus-stat-item table.action-stage-table tr:first-child td,
                .focus-stat-item table.chat-stage-table tr:first-child td,
                .focus-stat-item table.chat-action-ratio-table tr:first-child td {{
                    background-color: #3498db !important;
                    color: white !important;
                    font-weight: bold;
                }}
                
                /* 为聊天流表格添加额外样式 */
                .focus-stat-item table.chat-stage-table td:first-child,
                .focus-stat-item table.chat-action-ratio-table td:first-child {{
                    min-width: 150px;
                    max-width: 200px;
                    word-wrap: break-word;
                    white-space: normal;
                }}
                
                .focus-stat-item table.chat-stage-table small,
                .focus-stat-item table.chat-action-ratio-table small {{
                    color: #7f8c8d;
                    font-size: 0.8em;
                }}
                
                /* 聊天流Action比例表格的特殊样式 */
                .focus-stat-item table.chat-action-ratio-table {{
                    border-spacing: 0;
                }}
                
                .focus-stat-item table.chat-action-ratio-table td {{
                    text-align: center;
                    padding: 8px;
                }}
                
                .focus-stat-item table.chat-action-ratio-table td:first-child {{
                    text-align: left;
                    font-weight: bold;
                }}
            </style>
        </div>
        """

    def _generate_versions_tab(self, stat: dict[str, Any]) -> str:
        """生成版本对比独立分页的HTML内容"""

        # 为每个时间段准备版本对比数据
        version_sections = []

        for period_name, period_delta, period_desc in self.stat_period:
            stat_data = stat.get(period_name, {})

            if not stat_data.get(FOCUS_CYCLE_CNT_BY_VERSION):
                continue

            # 获取所有版本（按循环数排序）
            all_versions = sorted(
                stat_data[FOCUS_CYCLE_CNT_BY_VERSION].keys(),
                key=lambda x: stat_data[FOCUS_CYCLE_CNT_BY_VERSION][x],
                reverse=True,
            )

            # 生成版本Action分布表
            focus_version_action_rows = ""
            if stat_data[FOCUS_ACTION_RATIOS_BY_VERSION]:
                # 获取所有action类型
                all_action_types_for_version = set()
                for version_actions in stat_data[FOCUS_ACTION_RATIOS_BY_VERSION].values():
                    all_action_types_for_version.update(version_actions.keys())
                all_action_types_for_version = sorted(all_action_types_for_version)

                if all_action_types_for_version:
                    version_action_rows = []
                    for version in all_versions:
                        version_actions = stat_data[FOCUS_ACTION_RATIOS_BY_VERSION].get(version, {})
                        total_cycles = stat_data[FOCUS_CYCLE_CNT_BY_VERSION][version]

                        row_cells = [f"<td><strong>{version}</strong><br><small>({total_cycles}次循环)</small></td>"]

                        for action_type in all_action_types_for_version:
                            count = version_actions.get(action_type, 0)
                            ratio = (count / total_cycles * 100) if total_cycles > 0 else 0
                            row_cells.append(f"<td>{count}<br><small>({ratio:.1f}%)</small></td>")

                        version_action_rows.append(f"<tr>{''.join(row_cells)}</tr>")

                    # 生成表头
                    action_headers = "".join(
                        [f"<th>{action_type}</th>" for action_type in all_action_types_for_version]
                    )
                    version_action_table_header = f"<tr><th>版本</th>{action_headers}</tr>"
                    focus_version_action_rows = version_action_table_header + "\n" + "\n".join(version_action_rows)

            # 生成版本阶段时间表（按action类型分解执行时间）
            focus_version_stage_rows = ""
            if stat_data[FOCUS_AVG_TIMES_BY_VERSION]:
                # 基础三个阶段
                basic_stages = ["观察", "并行调整动作、处理", "规划器"]

                # 获取所有action类型用于执行时间列
                all_action_types_for_exec = set()
                if stat_data.get("focus_exec_times_by_version_action"):
                    for version_actions in stat_data["focus_exec_times_by_version_action"].values():
                        all_action_types_for_exec.update(version_actions.keys())
                all_action_types_for_exec = sorted(all_action_types_for_exec)

                # 检查哪些基础阶段存在数据
                existing_basic_stages = []
                for stage in basic_stages:
                    stage_exists = False
                    for version_stages in stat_data[FOCUS_AVG_TIMES_BY_VERSION].values():
                        if stage in version_stages:
                            stage_exists = True
                            break
                    if stage_exists:
                        existing_basic_stages.append(stage)

                # 构建表格
                if existing_basic_stages or all_action_types_for_exec:
                    version_stage_rows = []

                    # 为每个版本生成数据行
                    for version in all_versions:
                        version_stages = stat_data[FOCUS_AVG_TIMES_BY_VERSION].get(version, {})
                        total_cycles = stat_data[FOCUS_CYCLE_CNT_BY_VERSION][version]

                        row_cells = [f"<td><strong>{version}</strong><br><small>({total_cycles}次循环)</small></td>"]

                        # 添加基础阶段时间
                        for stage in existing_basic_stages:
                            time_val = version_stages.get(stage, 0.0)
                            row_cells.append(f"<td>{time_val:.3f}秒</td>")

                        # 添加不同action类型的执行时间
                        for action_type in all_action_types_for_exec:
                            # 获取该版本该action类型的平均执行时间
                            version_exec_times = stat_data.get("focus_exec_times_by_version_action", {})
                            if version in version_exec_times and action_type in version_exec_times[version]:
                                exec_time = version_exec_times[version][action_type]
                                row_cells.append(f"<td>{exec_time:.3f}秒</td>")
                            else:
                                row_cells.append("<td>-</td>")

                        version_stage_rows.append(f"<tr>{''.join(row_cells)}</tr>")

                    # 生成表头
                    basic_headers = "".join([f"<th>{stage}</th>" for stage in existing_basic_stages])
                    action_headers = "".join(
                        [
                            f"<th>执行时间<br><small>[{action_type}]</small></th>"
                            for action_type in all_action_types_for_exec
                        ]
                    )
                    version_stage_table_header = f"<tr><th>版本</th>{basic_headers}{action_headers}</tr>"
                    focus_version_stage_rows = version_stage_table_header + "\n" + "\n".join(version_stage_rows)

            # 计算时间范围
            if period_name == "all_time":
                from src.manager.local_store_manager import local_storage

                start_time = datetime.fromtimestamp(local_storage["deploy_time"])
                time_range = (
                    f"{start_time.strftime('%Y-%m-%d %H:%M:%S')} ~ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
            else:
                start_time = datetime.now() - period_delta
                time_range = (
                    f"{start_time.strftime('%Y-%m-%d %H:%M:%S')} ~ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )

            # 生成该时间段的版本对比HTML
            section_html = f"""
            <div class="version-period-section">
                <h2>{period_desc}版本对比</h2>
                <p class="info-item"><strong>统计时段:</strong> {time_range}</p>
                <p class="info-item"><strong>包含版本:</strong> {len(all_versions)} 个版本</p>
                
                <div class="version-stats-grid">
                    <div class="version-stat-item">
                        <h3>版本Action类型分布对比</h3>
                        <table class="version-comparison-table">
                            <thead></thead>
                            <tbody>{focus_version_action_rows}</tbody>
                        </table>
                    </div>
                    
                    <div class="version-stat-item">
                        <h3>版本阶段时间对比</h3>
                        <table class="version-comparison-table">
                            <thead></thead>
                            <tbody>{focus_version_stage_rows}</tbody>
                        </table>
                    </div>
                </div>
            </div>
            """

            version_sections.append(section_html)

        # 如果没有任何版本数据
        if not version_sections:
            version_sections.append("""
            <div class="version-period-section">
                <h2>暂无版本对比数据</h2>
                <p class="info-item">在指定时间段内未找到任何版本信息。</p>
                <p class="info-item">请确保 <code>log/hfc_loop/</code> 目录下的JSON文件包含版本信息。</p>
            </div>
            """)

        return f"""
        <div id="versions" class="tab-content">
            <h1>Focus HFC版本对比分析</h1>
            <p class="info-item">
                <strong>对比内容:</strong> 不同版本的Action类型分布和各阶段性能表现<br>
                <strong>数据来源:</strong> log/hfc_loop/ 目录下JSON文件中的version字段
            </p>
            
            {"".join(version_sections)}
            
            <style>
                .version-period-section {{
                    margin-bottom: 40px;
                    padding: 20px;
                    border: 1px solid #e74c3c;
                    border-radius: 8px;
                    background-color: #fdf2f2;
                }}
                
                .version-stats-grid {{
                    display: grid;
                    grid-template-columns: 1fr;
                    gap: 30px;
                    margin: 20px 0;
                }}
                
                .version-stat-item {{
                    background-color: white;
                    padding: 15px;
                    border-radius: 6px;
                    border: 1px solid #eee;
                }}
                
                .version-stat-item h3 {{
                    margin-top: 0;
                    color: #c0392b;
                    border-bottom: 1px solid #e74c3c;
                    padding-bottom: 5px;
                }}
                
                @media (max-width: 768px) {{
                    .version-stats-grid {{
                        grid-template-columns: 1fr;
                        gap: 20px;
                    }}
                }}
                
                /* 版本对比表格样式 */
                .version-stat-item table.version-comparison-table {{
                    width: 100%;
                    overflow-x: auto;
                    display: block;
                    white-space: nowrap;
                }}
                
                .version-stat-item table.version-comparison-table tbody {{
                    display: table;
                    width: 100%;
                }}
                
                .version-stat-item table.version-comparison-table tr:first-child {{
                    background-color: #e74c3c;
                    color: white;
                    font-weight: bold;
                }}
                
                .version-stat-item table.version-comparison-table tr:first-child td {{
                    background-color: #e74c3c !important;
                    color: white !important;
                    font-weight: bold;
                }}
                
                .version-stat-item table.version-comparison-table td:first-child {{
                    min-width: 120px;
                    font-weight: bold;
                }}
                
                .version-stat-item table.version-comparison-table small {{
                    color: #7f8c8d;
                    font-size: 0.8em;
                }}
                
                /* 版本差异高亮 */
                .version-stat-item table.version-comparison-table tr:nth-child(even) {{
                    background-color: #f9f9f9;
                }}
                
                .version-stat-item table.version-comparison-table tr:hover {{
                    background-color: #f5f5f5;
                }}
            </style>
        </div>
        """

    def _generate_chart_data(self, stat: dict[str, Any]) -> dict:
        """生成图表数据"""
        now = datetime.now()
        chart_data = {}

        # 支持多个时间范围
        time_ranges = [
            ("6h", 6, 10),  # 6小时，10分钟间隔
            ("12h", 12, 15),  # 12小时，15分钟间隔
            ("24h", 24, 15),  # 24小时，15分钟间隔
            ("48h", 48, 30),  # 48小时，30分钟间隔
        ]

        for range_key, hours, interval_minutes in time_ranges:
            range_data = self._collect_interval_data(now, hours, interval_minutes)
            chart_data[range_key] = range_data

        return chart_data

    def _collect_interval_data(self, now: datetime, hours: int, interval_minutes: int) -> dict:
        """收集指定时间范围内每个间隔的数据"""
        # 生成时间点
        start_time = now - timedelta(hours=hours)
        time_points = []
        current_time = start_time

        while current_time <= now:
            time_points.append(current_time)
            current_time += timedelta(minutes=interval_minutes)

        # 初始化数据结构
        total_cost_data = [0] * len(time_points)
        cost_by_model = {}
        cost_by_module = {}
        message_by_chat = {}
        time_labels = [t.strftime("%H:%M") for t in time_points]

        interval_seconds = interval_minutes * 60

        # 查询LLM使用记录
        query_start_time = start_time
        for record in LLMUsage.select().where(LLMUsage.timestamp >= query_start_time):
            record_time = record.timestamp

            # 找到对应的时间间隔索引
            time_diff = (record_time - start_time).total_seconds()
            interval_index = int(time_diff // interval_seconds)

            if 0 <= interval_index < len(time_points):
                # 累加总花费数据
                cost = record.cost or 0.0
                total_cost_data[interval_index] += cost

                # 累加按模型分类的花费
                model_name = record.model_name or "unknown"
                if model_name not in cost_by_model:
                    cost_by_model[model_name] = [0] * len(time_points)
                cost_by_model[model_name][interval_index] += cost

                # 累加按模块分类的花费
                request_type = record.request_type or "unknown"
                module_name = request_type.split(".")[0] if "." in request_type else request_type
                if module_name not in cost_by_module:
                    cost_by_module[module_name] = [0] * len(time_points)
                cost_by_module[module_name][interval_index] += cost

        # 查询消息记录
        query_start_timestamp = start_time.timestamp()
        for message in Messages.select().where(Messages.time >= query_start_timestamp):
            message_time_ts = message.time

            # 找到对应的时间间隔索引
            time_diff = message_time_ts - query_start_timestamp
            interval_index = int(time_diff // interval_seconds)

            if 0 <= interval_index < len(time_points):
                # 确定聊天流名称
                chat_name = None
                if message.chat_info_group_id:
                    chat_name = message.chat_info_group_name or f"群{message.chat_info_group_id}"
                elif message.user_id:
                    chat_name = message.user_nickname or f"用户{message.user_id}"
                else:
                    continue

                if not chat_name:
                    continue

                # 累加消息数
                if chat_name not in message_by_chat:
                    message_by_chat[chat_name] = [0] * len(time_points)
                message_by_chat[chat_name][interval_index] += 1

        # 查询Focus循环记录
        focus_cycles_by_action = {}
        focus_time_by_stage = {}

        log_dir = "log/hfc_loop"
        if os.path.exists(log_dir):
            json_files = glob.glob(os.path.join(log_dir, "*.json"))
            for json_file in json_files:
                try:
                    # 解析文件时间
                    filename = os.path.basename(json_file)
                    name_parts = filename.replace(".json", "").split("_")
                    if len(name_parts) >= 4:
                        date_str = name_parts[-2]
                        time_str = name_parts[-1]
                        file_time_str = f"{date_str}_{time_str}"
                        file_time = datetime.strptime(file_time_str, "%Y%m%d_%H%M%S")

                        if file_time >= start_time:
                            with open(json_file, "r", encoding="utf-8") as f:
                                cycles_data = json.load(f)

                                for cycle in cycles_data:
                                    try:
                                        timestamp_str = cycle.get("timestamp", "")
                                        if timestamp_str:
                                            cycle_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                                        else:
                                            cycle_time = file_time

                                        if cycle_time >= start_time:
                                            # 计算时间间隔索引
                                            time_diff = (cycle_time - start_time).total_seconds()
                                            interval_index = int(time_diff // interval_seconds)

                                            if 0 <= interval_index < len(time_points):
                                                action_type = cycle.get("action_type", "unknown")
                                                step_times = cycle.get("step_times", {})

                                                # 累计action类型数据
                                                if action_type not in focus_cycles_by_action:
                                                    focus_cycles_by_action[action_type] = [0] * len(time_points)
                                                focus_cycles_by_action[action_type][interval_index] += 1

                                                # 累计阶段时间数据
                                                for stage, time_val in step_times.items():
                                                    if stage not in focus_time_by_stage:
                                                        focus_time_by_stage[stage] = [0] * len(time_points)
                                                    focus_time_by_stage[stage][interval_index] += time_val
                                    except Exception:
                                        continue
                except Exception:
                    continue

        return {
            "time_labels": time_labels,
            "total_cost_data": total_cost_data,
            "cost_by_model": cost_by_model,
            "cost_by_module": cost_by_module,
            "message_by_chat": message_by_chat,
            "focus_cycles_by_action": focus_cycles_by_action,
            "focus_time_by_stage": focus_time_by_stage,
        }

    def _generate_chart_tab(self, chart_data: dict) -> str:
        """生成图表选项卡HTML内容"""

        # 生成不同颜色的调色板
        colors = [
            "#3498db",
            "#e74c3c",
            "#2ecc71",
            "#f39c12",
            "#9b59b6",
            "#1abc9c",
            "#34495e",
            "#e67e22",
            "#95a5a6",
            "#f1c40f",
        ]

        # 默认使用24小时数据生成数据集
        default_data = chart_data["24h"]

        # 为每个模型生成数据集
        model_datasets = []
        for i, (model_name, cost_data) in enumerate(default_data["cost_by_model"].items()):
            color = colors[i % len(colors)]
            model_datasets.append(f"""{{
                label: '{model_name}',
                data: {cost_data},
                borderColor: '{color}',
                backgroundColor: '{color}20',
                tension: 0.4,
                fill: false
            }}""")

        ",\n                    ".join(model_datasets)

        # 为每个模块生成数据集
        module_datasets = []
        for i, (module_name, cost_data) in enumerate(default_data["cost_by_module"].items()):
            color = colors[i % len(colors)]
            module_datasets.append(f"""{{
                label: '{module_name}',
                data: {cost_data},
                borderColor: '{color}',
                backgroundColor: '{color}20',
                tension: 0.4,
                fill: false
            }}""")

        ",\n                    ".join(module_datasets)

        # 为每个聊天流生成消息数据集
        message_datasets = []
        for i, (chat_name, message_data) in enumerate(default_data["message_by_chat"].items()):
            color = colors[i % len(colors)]
            message_datasets.append(f"""{{
                label: '{chat_name}',
                data: {message_data},
                borderColor: '{color}',
                backgroundColor: '{color}20',
                tension: 0.4,
                fill: false
            }}""")

        ",\n                    ".join(message_datasets)

        return f"""
        <div id="charts" class="tab-content">
            <h2>数据图表</h2>
            
            <!-- 时间范围选择按钮 -->
            <div style="margin: 20px 0; text-align: center;">
                <label style="margin-right: 10px; font-weight: bold;">时间范围:</label>
                <button class="time-range-btn" onclick="switchTimeRange('6h')">6小时</button>
                <button class="time-range-btn" onclick="switchTimeRange('12h')">12小时</button>
                <button class="time-range-btn active" onclick="switchTimeRange('24h')">24小时</button>
                <button class="time-range-btn" onclick="switchTimeRange('48h')">48小时</button>
            </div>
            
            <div style="margin-top: 20px;">
                <div style="margin-bottom: 40px;">
                    <canvas id="totalCostChart" width="800" height="400"></canvas>
                </div>
                <div style="margin-bottom: 40px;">
                    <canvas id="costByModuleChart" width="800" height="400"></canvas>
                </div>
                <div style="margin-bottom: 40px;">
                    <canvas id="costByModelChart" width="800" height="400"></canvas>
                </div>
                <div style="margin-bottom: 40px;">
                    <canvas id="messageByChatChart" width="800" height="400"></canvas>
                </div>
                <div style="margin-bottom: 40px;">
                    <canvas id="focusCyclesByActionChart" width="800" height="400"></canvas>
                </div>
                <div>
                    <canvas id="focusTimeByStageChart" width="800" height="400"></canvas>
                </div>
            </div>
            
            <style>
                .time-range-btn {{
                    background-color: #ecf0f1;
                    border: 1px solid #bdc3c7;
                    color: #2c3e50;
                    padding: 8px 16px;
                    margin: 0 5px;
                    border-radius: 4px;
                    cursor: pointer;
                    font-size: 14px;
                    transition: all 0.3s ease;
                }}
                
                .time-range-btn:hover {{
                    background-color: #d5dbdb;
                }}
                
                .time-range-btn.active {{
                    background-color: #3498db;
                    color: white;
                    border-color: #2980b9;
                }}
            </style>
            
            <script>
                const allChartData = {chart_data};
                let currentCharts = {{}};
                
                // 图表配置模板
                const chartConfigs = {{
                    totalCost: {{
                        id: 'totalCostChart',
                        title: '总花费',
                        yAxisLabel: '花费 (¥)',
                        dataKey: 'total_cost_data',
                        fill: true
                    }},
                    costByModule: {{
                        id: 'costByModuleChart', 
                        title: '各模块花费',
                        yAxisLabel: '花费 (¥)',
                        dataKey: 'cost_by_module',
                        fill: false
                    }},
                    costByModel: {{
                        id: 'costByModelChart',
                        title: '各模型花费', 
                        yAxisLabel: '花费 (¥)',
                        dataKey: 'cost_by_model',
                        fill: false
                    }},
                    messageByChat: {{
                        id: 'messageByChatChart',
                        title: '各聊天流消息数',
                        yAxisLabel: '消息数',
                        dataKey: 'message_by_chat',
                        fill: false
                    }},
                    focusCyclesByAction: {{
                        id: 'focusCyclesByActionChart',
                        title: 'Focus循环按Action类型',
                        yAxisLabel: '循环数',
                        dataKey: 'focus_cycles_by_action',
                        fill: false
                    }},
                    focusTimeByStage: {{
                        id: 'focusTimeByStageChart',
                        title: 'Focus各阶段累计时间',
                        yAxisLabel: '时间 (秒)',
                        dataKey: 'focus_time_by_stage',
                        fill: false
                    }}
                }};
                
                function switchTimeRange(timeRange) {{
                    // 更新按钮状态
                    document.querySelectorAll('.time-range-btn').forEach(btn => {{
                        btn.classList.remove('active');
                    }});
                    event.target.classList.add('active');
                    
                    // 更新图表数据
                    const data = allChartData[timeRange];
                    updateAllCharts(data, timeRange);
                }}
                
                function updateAllCharts(data, timeRange) {{
                    // 销毁现有图表
                    Object.values(currentCharts).forEach(chart => {{
                        if (chart) chart.destroy();
                    }});
                    
                    currentCharts = {{}};
                    
                    // 重新创建图表
                    createChart('totalCost', data, timeRange);
                    createChart('costByModule', data, timeRange);
                    createChart('costByModel', data, timeRange);
                    createChart('messageByChat', data, timeRange);
                    createChart('focusCyclesByAction', data, timeRange);
                    createChart('focusTimeByStage', data, timeRange);
                }}
                
                function createChart(chartType, data, timeRange) {{
                    const config = chartConfigs[chartType];
                    const colors = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c', '#34495e', '#e67e22', '#95a5a6', '#f1c40f'];
                    
                    let datasets = [];
                    
                    if (chartType === 'totalCost') {{
                        datasets = [{{
                            label: config.title,
                            data: data[config.dataKey],
                            borderColor: colors[0],
                            backgroundColor: 'rgba(52, 152, 219, 0.1)',
                            tension: 0.4,
                            fill: config.fill
                        }}];
                    }} else {{
                        let i = 0;
                        Object.entries(data[config.dataKey]).forEach(([name, chartData]) => {{
                            datasets.push({{
                                label: name,
                                data: chartData,
                                borderColor: colors[i % colors.length],
                                backgroundColor: colors[i % colors.length] + '20',
                                tension: 0.4,
                                fill: config.fill
                            }});
                            i++;
                        }});
                    }}
                    
                    currentCharts[chartType] = new Chart(document.getElementById(config.id), {{
                        type: 'line',
                        data: {{
                            labels: data.time_labels,
                            datasets: datasets
                        }},
                        options: {{
                            responsive: true,
                            plugins: {{
                                title: {{
                                    display: true,
                                    text: timeRange + '内' + config.title + '趋势',
                                    font: {{ size: 16 }}
                                }},
                                legend: {{
                                    display: chartType !== 'totalCost',
                                    position: 'top'
                                }}
                            }},
                            scales: {{
                                x: {{
                                    title: {{
                                        display: true,
                                        text: '时间'
                                    }},
                                    ticks: {{
                                        maxTicksLimit: 12
                                    }}
                                }},
                                y: {{
                                    title: {{
                                        display: true,
                                        text: config.yAxisLabel
                                    }},
                                    beginAtZero: true
                                }}
                            }},
                            interaction: {{
                                intersect: false,
                                mode: 'index'
                            }}
                        }}
                    }});
                }}
                
                // 初始化图表（默认24小时）
                document.addEventListener('DOMContentLoaded', function() {{
                    updateAllCharts(allChartData['24h'], '24h');
                }});
            </script>
        </div>
        """


class AsyncStatisticOutputTask(AsyncTask):
    """完全异步的统计输出任务 - 更高性能版本"""

    def __init__(self, record_file_path: str = "maibot_statistics.html"):
        # 延迟0秒启动，运行间隔300秒
        super().__init__(task_name="Async Statistics Data Output Task", wait_before_start=0, run_interval=300)

        # 直接复用 StatisticOutputTask 的初始化逻辑
        temp_stat_task = StatisticOutputTask(record_file_path)
        self.name_mapping = temp_stat_task.name_mapping
        self.record_file_path = temp_stat_task.record_file_path
        self.stat_period = temp_stat_task.stat_period

    async def run(self):
        """完全异步执行统计任务"""

        async def _async_collect_and_output():
            try:
                now = datetime.now()
                loop = asyncio.get_event_loop()

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    logger.info("正在后台收集统计数据...")

                    # 数据收集任务
                    collect_task = asyncio.create_task(
                        loop.run_in_executor(executor, self._collect_all_statistics, now)
                    )

                    stats = await collect_task
                    logger.info("统计数据收集完成")

                    # 创建并发的输出任务
                    output_tasks = [
                        asyncio.create_task(loop.run_in_executor(executor, self._statistic_console_output, stats, now)),
                        asyncio.create_task(loop.run_in_executor(executor, self._generate_html_report, stats, now)),
                    ]

                    # 等待所有输出任务完成
                    await asyncio.gather(*output_tasks)

                logger.info("统计数据后台输出完成")
            except Exception as e:
                logger.exception(f"后台统计数据输出过程中发生异常：{e}")

        # 创建后台任务，立即返回
        asyncio.create_task(_async_collect_and_output())

    # 复用 StatisticOutputTask 的所有方法
    def _collect_all_statistics(self, now: datetime):
        return StatisticOutputTask._collect_all_statistics(self, now)

    def _statistic_console_output(self, stats: Dict[str, Any], now: datetime):
        return StatisticOutputTask._statistic_console_output(self, stats, now)

    def _generate_html_report(self, stats: dict[str, Any], now: datetime):
        return StatisticOutputTask._generate_html_report(self, stats, now)

    # 其他需要的方法也可以类似复用...
    @staticmethod
    def _collect_model_request_for_period(collect_period: List[Tuple[str, datetime]]) -> Dict[str, Any]:
        return StatisticOutputTask._collect_model_request_for_period(collect_period)

    @staticmethod
    def _collect_online_time_for_period(collect_period: List[Tuple[str, datetime]], now: datetime) -> Dict[str, Any]:
        return StatisticOutputTask._collect_online_time_for_period(collect_period, now)

    def _collect_message_count_for_period(self, collect_period: List[Tuple[str, datetime]]) -> Dict[str, Any]:
        return StatisticOutputTask._collect_message_count_for_period(self, collect_period)

    def _collect_focus_statistics_for_period(self, collect_period: List[Tuple[str, datetime]]) -> Dict[str, Any]:
        return StatisticOutputTask._collect_focus_statistics_for_period(self, collect_period)

    def _process_focus_file_data(
        self,
        cycles_data: List[Dict],
        stats: Dict[str, Any],
        collect_period: List[Tuple[str, datetime]],
        file_time: datetime,
    ):
        return StatisticOutputTask._process_focus_file_data(self, cycles_data, stats, collect_period, file_time)

    def _calculate_focus_averages(self, stats: Dict[str, Any]):
        return StatisticOutputTask._calculate_focus_averages(self, stats)

    @staticmethod
    def _format_total_stat(stats: Dict[str, Any]) -> str:
        return StatisticOutputTask._format_total_stat(stats)

    @staticmethod
    def _format_model_classified_stat(stats: Dict[str, Any]) -> str:
        return StatisticOutputTask._format_model_classified_stat(stats)

    def _format_chat_stat(self, stats: Dict[str, Any]) -> str:
        return StatisticOutputTask._format_chat_stat(self, stats)

    def _format_focus_stat(self, stats: Dict[str, Any]) -> str:
        return StatisticOutputTask._format_focus_stat(self, stats)

    def _generate_chart_data(self, stat: dict[str, Any]) -> dict:
        return StatisticOutputTask._generate_chart_data(self, stat)

    def _collect_interval_data(self, now: datetime, hours: int, interval_minutes: int) -> dict:
        return StatisticOutputTask._collect_interval_data(self, now, hours, interval_minutes)

    def _generate_chart_tab(self, chart_data: dict) -> str:
        return StatisticOutputTask._generate_chart_tab(self, chart_data)

    def _get_chat_display_name_from_id(self, chat_id: str) -> str:
        return StatisticOutputTask._get_chat_display_name_from_id(self, chat_id)

    def _generate_focus_tab(self, stat: dict[str, Any]) -> str:
        return StatisticOutputTask._generate_focus_tab(self, stat)

    def _generate_versions_tab(self, stat: dict[str, Any]) -> str:
        return StatisticOutputTask._generate_versions_tab(self, stat)

    def _convert_defaultdict_to_dict(self, data):
        return StatisticOutputTask._convert_defaultdict_to_dict(self, data)
