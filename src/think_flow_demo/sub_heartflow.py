from .outer_world import outer_world
import asyncio
from src.plugins.moods.moods import MoodManager
from src.plugins.models.utils_model import LLM_request
from src.plugins.config.config import global_config, BotConfig
import re
import time
from src.plugins.schedule.schedule_generator import bot_schedule
from src.plugins.memory_system.Hippocampus import HippocampusManager
from src.common.logger import get_module_logger, LogConfig, SUB_HEARTFLOW_STYLE_CONFIG # noqa: E402

subheartflow_config = LogConfig(
    # 使用海马体专用样式
    console_format=SUB_HEARTFLOW_STYLE_CONFIG["console_format"],
    file_format=SUB_HEARTFLOW_STYLE_CONFIG["file_format"],
)   
logger = get_module_logger("subheartflow", config=subheartflow_config)


class CuttentState:
    def __init__(self):
        self.willing = 0
        self.current_state_info = ""
        
        self.mood_manager = MoodManager()
        self.mood = self.mood_manager.get_prompt()
    
    def update_current_state_info(self):
        self.current_state_info = self.mood_manager.get_current_mood()


class SubHeartflow:
    def __init__(self):
        self.current_mind = ""
        self.past_mind = []
        self.current_state : CuttentState = CuttentState()
        self.llm_model = LLM_request(
            model=global_config.llm_sub_heartflow, temperature=0.7, max_tokens=600, request_type="sub_heart_flow")
        self.outer_world = None
        
        self.main_heartflow_info = ""
        
        self.observe_chat_id = None
        
        self.last_reply_time = time.time()
        
        if not self.current_mind:
            self.current_mind = "你什么也没想"
            
        self.personality_info = " ".join(global_config.PROMPT_PERSONALITY)
        
        self.is_active = False
    
    def assign_observe(self,stream_id):
        self.outer_world = outer_world.get_world_by_stream_id(stream_id)
        self.observe_chat_id = stream_id

    async def subheartflow_start_working(self):
        while True:
            current_time = time.time()
            if current_time - self.last_reply_time > 180:  # 3分钟 = 180秒
                # print(f"{self.observe_chat_id}麦麦已经3分钟没有回复了，暂时停止思考")
                self.is_active = False
                await asyncio.sleep(60)  # 每30秒检查一次
            else:
                self.is_active = True
                await self.do_a_thinking()
                await self.judge_willing()
                await asyncio.sleep(60)
    
    async def do_a_thinking(self):
        self.current_state.update_current_state_info()
        
        current_thinking_info = self.current_mind
        mood_info = self.current_state.mood
        
        message_stream_info = self.outer_world.talking_summary
        print(f"message_stream_info：{message_stream_info}")
        
        related_memory = await HippocampusManager.get_instance().get_memory_from_text(
            text=message_stream_info,
            max_memory_num=2,
            max_memory_length=2,
            max_depth=3,
            fast_retrieval=False
        )
        # print(f"相关记忆：{related_memory}")
        if related_memory:
            related_memory_info = ""
            for memory in related_memory:
                related_memory_info += memory[1]
        else:
            related_memory_info = ''
            
        # print(f"相关记忆：{related_memory_info}")
        
        schedule_info = bot_schedule.get_current_num_task(num = 1,time_info = False)
        
        prompt = ""
        prompt += f"你刚刚在做的事情是：{schedule_info}\n"
        # prompt += f"麦麦的总体想法是：{self.main_heartflow_info}\n\n"
        prompt += f"你{self.personality_info}\n"
        if related_memory_info:
            prompt += f"你想起来你之前见过的回忆：{related_memory_info}。\n以上是你的回忆，不一定是目前聊天里的人说的，也不一定是现在发生的事情，请记住。\n"
        prompt += f"刚刚你的想法是{current_thinking_info}。\n"
        prompt += "-----------------------------------\n"
        if message_stream_info:
            prompt += f"现在你正在上网，和qq群里的网友们聊天，群里正在聊的话题是：{message_stream_info}\n"
        prompt += f"你现在{mood_info}。\n"
        prompt += "现在你接下去继续思考，产生新的想法，不要分点输出，输出连贯的内心独白，不要太长，"
        prompt += "但是记得结合上述的消息，要记得维持住你的人设，关注聊天和新内容，不要思考太多:"
        reponse, reasoning_content = await self.llm_model.generate_response_async(prompt)
        
        self.update_current_mind(reponse)
        
        self.current_mind = reponse
        logger.info(f"prompt:\n{prompt}\n")
        logger.info(f"麦麦的脑内状态：{self.current_mind}")
    
    async def do_after_reply(self,reply_content,chat_talking_prompt):
        # print("麦麦脑袋转起来了")
        self.current_state.update_current_state_info()
        
        current_thinking_info = self.current_mind
        mood_info = self.current_state.mood
        # related_memory_info = 'memory'
        message_stream_info = self.outer_world.talking_summary
        message_new_info = chat_talking_prompt
        reply_info = reply_content
        schedule_info = bot_schedule.get_current_num_task(num = 1,time_info = False)
       
        
        prompt = ""
        prompt += f"你刚刚在做的事情是：{schedule_info}\n"
        prompt += f"你{self.personality_info}\n"
        
        prompt += f"现在你正在上网，和qq群里的网友们聊天，群里正在聊的话题是：{message_stream_info}\n"
        # if related_memory_info:
            # prompt += f"你想起来{related_memory_info}。"
        prompt += f"刚刚你的想法是{current_thinking_info}。"
        prompt += f"你现在看到了网友们发的新消息:{message_new_info}\n"
        prompt += f"你刚刚回复了群友们:{reply_info}"
        prompt += f"你现在{mood_info}。"
        prompt += "现在你接下去继续思考，产生新的想法，记得保留你刚刚的想法，不要分点输出，输出连贯的内心独白"
        prompt += "不要太长，但是记得结合上述的消息，要记得你的人设，关注聊天和新内容，关注你回复的内容，不要思考太多:"
        
        reponse, reasoning_content = await self.llm_model.generate_response_async(prompt)
        
        self.update_current_mind(reponse)
        
        self.current_mind = reponse
        logger.info(f"麦麦回复后的脑内状态：{self.current_mind}")
        
        self.last_reply_time = time.time()
        
    async def judge_willing(self):
        # print("麦麦闹情绪了1")
        current_thinking_info = self.current_mind
        mood_info = self.current_state.mood
        # print("麦麦闹情绪了2")
        prompt = ""
        prompt += f"{self.personality_info}\n"
        prompt += "现在你正在上网，和qq群里的网友们聊天"
        prompt += f"你现在的想法是{current_thinking_info}。"
        prompt += f"你现在{mood_info}。"
        prompt += "现在请你思考，你想不想发言或者回复，请你输出一个数字，1-10，1表示非常不想，10表示非常想。"
        prompt += "请你用<>包裹你的回复意愿，输出<1>表示不想回复，输出<10>表示非常想回复。请你考虑，你完全可以不回复"
        
        response, reasoning_content = await self.llm_model.generate_response_async(prompt)
        # 解析willing值
        willing_match = re.search(r'<(\d+)>', response)
        if willing_match:
            self.current_state.willing = int(willing_match.group(1))
        else:
            self.current_state.willing = 0
            
        logger.info(f"{self.observe_chat_id}麦麦的回复意愿：{self.current_state.willing}")
            
        return self.current_state.willing

    def build_outer_world_info(self):
        outer_world_info = outer_world.outer_world_info
        return outer_world_info

    def update_current_mind(self,reponse):
        self.past_mind.append(self.current_mind)
        self.current_mind = reponse


# subheartflow = SubHeartflow()

