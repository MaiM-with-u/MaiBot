import datetime
import os
import sys
from typing import Dict
import asyncio
from dateutil import tz

# 添加项目根目录到 Python 路径
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.append(root_path)

from src.common.database import db  # noqa: E402
from src.common.logger import get_module_logger, SCHEDULE_STYLE_CONFIG, LogConfig  # noqa: E402
from src.plugins.models.utils_model import LLM_request  # noqa: E402
from src.plugins.config.config import global_config  # noqa: E402

TIME_ZONE = tz.gettz(global_config.TIME_ZONE) # 设置时区


schedule_config = LogConfig(
    # 使用海马体专用样式
    console_format=SCHEDULE_STYLE_CONFIG["console_format"],
    file_format=SCHEDULE_STYLE_CONFIG["file_format"],
)
logger = get_module_logger("scheduler", config=schedule_config)


class ScheduleGenerator:
    # enable_output: bool = True

    def __init__(self):
        # 使用离线LLM模型
        self.llm_scheduler_all = LLM_request(
            model=global_config.llm_reasoning, temperature=global_config.SCHEDULE_TEMPERATURE, max_tokens=7000, request_type="schedule"
        )
        self.llm_scheduler_doing = LLM_request(
            model=global_config.llm_normal, temperature=global_config.SCHEDULE_TEMPERATURE, max_tokens=2048, request_type="schedule"
        )

        self.today_schedule_text = ""
        self.today_done_list = []

        self.yesterday_schedule_text = ""
        self.yesterday_done_list = []

        self.name = ""
        self.personality = ""
        self.behavior = ""

        self.start_time = datetime.datetime.now(TIME_ZONE)

        self.schedule_doing_update_interval = 300  # 最好大于60

    def initialize(
        self,
        name: str = "bot_name",
        personality: str = "你是一个爱国爱党的新时代青年",
        behavior: str = "你非常外向，喜欢尝试新事物和人交流",
        interval: int = 60,
    ):
        """初始化日程系统"""
        self.name = name
        self.behavior = behavior
        self.schedule_doing_update_interval = interval

        for pers in personality:
            self.personality += pers + "\n"

    async def mai_schedule_start(self):
        """启动日程系统，每5分钟执行一次move_doing，并在日期变化时重新检查日程"""
        try:
            logger.info(f"日程系统启动/刷新时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            # 初始化日程
            await self.check_and_create_today_schedule()
            self.print_schedule()

            while True:
                # print(self.get_current_num_task(1, True))

                current_time = datetime.datetime.now(TIME_ZONE)

                # 检查是否需要重新生成日程（日期变化）
                if current_time.date() != self.start_time.date():
                    logger.info("检测到日期变化，重新生成日程")
                    self.start_time = current_time
                    await self.check_and_create_today_schedule()
                    self.print_schedule()

                # 执行当前活动
                # mind_thinking = heartflow.current_state.current_mind

                await self.move_doing()

                await asyncio.sleep(self.schedule_doing_update_interval)

        except Exception as e:
            logger.error(f"日程系统运行时出错: {str(e)}")
            logger.exception("详细错误信息：")

    async def check_and_create_today_schedule(self):
        """检查昨天的日程，并确保今天有日程安排

        Returns:
            tuple: (today_schedule_text, today_schedule) 今天的日程文本和解析后的日程字典
        """
        today = datetime.datetime.now(TIME_ZONE)
        yesterday = today - datetime.timedelta(days=1)

        # 先检查昨天的日程
        self.yesterday_schedule_text, self.yesterday_done_list = self.load_schedule_from_db(yesterday)
        if self.yesterday_schedule_text:
            logger.debug(f"已加载{yesterday.strftime('%Y-%m-%d')}的日程")

        # 检查今天的日程
        self.today_schedule_text, self.today_done_list = self.load_schedule_from_db(today)
        if not self.today_done_list:
            self.today_done_list = []
        if not self.today_schedule_text:
            logger.info(f"{today.strftime('%Y-%m-%d')}的日程不存在，准备生成新的日程")
            self.today_schedule_text = await self.generate_daily_schedule(target_date=today)

        self.save_today_schedule_to_db()

    def construct_daytime_prompt(self, target_date: datetime.datetime):
        date_str = target_date.strftime("%Y-%m-%d")
        weekday = target_date.strftime("%A")

        prompt = f"你是{self.name}，{self.personality}，{self.behavior}"
        prompt += f"你昨天的日程是：{self.yesterday_schedule_text}\n"
        prompt += f"请为你生成{date_str}（{weekday}），也就是今天的日程安排，结合你的个人特点和行为习惯以及昨天的安排\n"
        prompt += "推测你的日程安排，包括你一天都在做什么，从起床到睡眠，有什么发现和思考，具体一些，详细一些，需要1500字以上，精确到每半个小时，记得写明时间\n"  # noqa: E501
        prompt += "直接返回你的日程，现实一点，不要浮夸，从起床到睡觉，不要输出其他内容："
        return prompt

    def construct_doing_prompt(self, time: datetime.datetime, mind_thinking: str = ""):
        now_time = time.strftime("%H:%M")
        previous_doings = self.get_current_num_task(5, True)

        prompt = f"你是{self.name}，{self.personality}，{self.behavior}"
        prompt += f"你今天的日程是：{self.today_schedule_text}\n"
        if previous_doings:
            prompt += f"你之前做了的事情是：{previous_doings}，从之前到现在已经过去了{self.schedule_doing_update_interval / 60}分钟了\n"  # noqa: E501
        if mind_thinking:
            prompt += f"你脑子里在想：{mind_thinking}\n"
        prompt += f"现在是{now_time}，结合你的个人特点和行为习惯,注意关注你今天的日程安排和想法安排你接下来做什么，现实一点，不要浮夸"
        prompt += "安排你接下来做什么，具体一些，详细一些\n"
        prompt += "直接返回你在做的事情，注意是当前时间，不要输出其他内容："
        return prompt

    async def generate_daily_schedule(
        self,
        target_date: datetime.datetime = None,
    ) -> Dict[str, str]:
        daytime_prompt = self.construct_daytime_prompt(target_date)
        daytime_response, _ = await self.llm_scheduler_all.generate_response_async(daytime_prompt)
        return daytime_response

    def print_schedule(self):
        """打印完整的日程安排"""
        if not self.today_schedule_text:
            logger.warning("今日日程有误，将在下次运行时重新生成")
            db.schedule.delete_one({"date": datetime.datetime.now(TIME_ZONE).strftime("%Y-%m-%d")})
        else:
            logger.info("=== 今日日程安排 ===")
            logger.info(self.today_schedule_text)
            logger.info("==================")
            self.enable_output = False

    async def update_today_done_list(self):
        # 更新数据库中的 today_done_list
        today_str = datetime.datetime.now(TIME_ZONE).strftime("%Y-%m-%d")
        existing_schedule = db.schedule.find_one({"date": today_str})

        if existing_schedule:
            # 更新数据库中的 today_done_list
            db.schedule.update_one({"date": today_str}, {"$set": {"today_done_list": self.today_done_list}})
            logger.debug(f"已更新{today_str}的已完成活动列表")
        else:
            logger.warning(f"未找到{today_str}的日程记录")

    async def move_doing(self, mind_thinking: str = ""):
        try:
            current_time = datetime.datetime.now(TIME_ZONE)
            if mind_thinking:
                doing_prompt = self.construct_doing_prompt(current_time, mind_thinking)
            else:
                doing_prompt = self.construct_doing_prompt(current_time)

            doing_response, _ = await self.llm_scheduler_doing.generate_response_async(doing_prompt)
            self.today_done_list.append((current_time, doing_response))

            await self.update_today_done_list()

            logger.info(f"当前活动: {doing_response}")

            return doing_response
        except GeneratorExit:
            logger.warning("日程生成被中断")
            return "日程生成被中断"
        except Exception as e:
            logger.error(f"生成日程时发生错误: {str(e)}")
            return "生成日程时发生错误"

    async def get_task_from_time_to_time(self, start_time: str, end_time: str):
        """获取指定时间范围内的任务列表

        Args:
            start_time (str): 开始时间，格式为"HH:MM"
            end_time (str): 结束时间，格式为"HH:MM"

        Returns:
            list: 时间范围内的任务列表
        """
        result = []
        for task in self.today_done_list:
            task_time = task[0]  # 获取任务的时间戳
            task_time_str = task_time.strftime("%H:%M")

            # 检查任务时间是否在指定范围内
            if self._time_diff(start_time, task_time_str) >= 0 and self._time_diff(task_time_str, end_time) >= 0:
                result.append(task)

        return result

    def get_current_num_task(self, num=1, time_info=False):
        """获取最新加入的指定数量的日程

        Args:
            num (int): 需要获取的日程数量，默认为1

        Returns:
            list: 最新加入的日程列表
        """
        if not self.today_done_list:
            return []

        # 确保num不超过列表长度
        num = min(num, len(self.today_done_list))
        pre_doings = ""
        for doing in self.today_done_list[-num:]:
            if time_info:
                time_str = doing[0].strftime("%H:%M")
                pre_doings += time_str + "时，" + doing[1] + "\n"
            else:
                pre_doings += doing[1] + "\n"

        # 返回最新的num条日程
        return pre_doings

    def save_today_schedule_to_db(self):
        """保存日程到数据库，同时初始化 today_done_list"""
        date_str = datetime.datetime.now(TIME_ZONE).strftime("%Y-%m-%d")
        schedule_data = {
            "date": date_str,
            "schedule": self.today_schedule_text,
            "today_done_list": self.today_done_list if hasattr(self, "today_done_list") else [],
        }
        # 使用 upsert 操作，如果存在则更新，不存在则插入
        db.schedule.update_one({"date": date_str}, {"$set": schedule_data}, upsert=True)
        logger.debug(f"已保存{date_str}的日程到数据库")

    def load_schedule_from_db(self, date: datetime.datetime):
        """从数据库加载日程，同时加载 today_done_list"""
        date_str = date.strftime("%Y-%m-%d")
        existing_schedule = db.schedule.find_one({"date": date_str})

        if existing_schedule:
            schedule_text = existing_schedule["schedule"]
            return schedule_text, existing_schedule.get("today_done_list", [])
        else:
            logger.debug(f"{date_str}的日程不存在")
            return None, None


async def main():
    # 使用示例
    scheduler = ScheduleGenerator()
    scheduler.initialize(
        name="麦麦",
        personality="你叫麦麦，你19岁，是一个大二的女大学生，你有一头黑色短发，你会刷贴吧，你现在在学习心理学",
        behavior="你比较内向，一般熬夜比较晚，然后第二天早上10点起床吃早午饭",
        interval=60,
    )
    await scheduler.mai_schedule_start()


# 当作为组件导入时使用的实例
bot_schedule = ScheduleGenerator()

if __name__ == "__main__":
    import asyncio

    # 当直接运行此文件时执行
    asyncio.run(main())
