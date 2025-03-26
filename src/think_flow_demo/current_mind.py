from .outer_world import outer_world
import asyncio
from src.plugins.moods.moods import MoodManager
from src.plugins.models.utils_model import LLM_request
from src.plugins.chat.config import global_config
import re
import time
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
    
    def assign_observe(self,stream_id):
        self.outer_world = outer_world.get_world_by_stream_id(stream_id)
        self.observe_chat_id = stream_id

    async def subheartflow_start_working(self):
        while True:
            current_time = time.time()
            if current_time - self.last_reply_time > 180:  # 3分钟 = 180秒
                # print(f"{self.observe_chat_id}麦麦已经3分钟没有回复了，暂时停止思考")
                await asyncio.sleep(25)  # 每30秒检查一次
            else:
                await self.do_a_thinking()
                await self.judge_willing()
                await asyncio.sleep(25)
    
    async def do_a_thinking(self):
        print("麦麦小脑袋转起来了")
        self.current_state.update_current_state_info()
        
        personality_info = open("src/think_flow_demo/personality_info.txt", "r", encoding="utf-8").read()
        current_thinking_info = self.current_mind
        mood_info = self.current_state.mood
        related_memory_info = 'memory'
        message_stream_info = self.outer_world.talking_summary
        
        prompt = ""
        # prompt += f"麦麦的总体想法是：{self.main_heartflow_info}\n\n"
        prompt += f"{personality_info}\n"
        prompt += f"现在你正在上网，和qq群里的网友们聊天，群里正在聊的话题是：{message_stream_info}\n"
        prompt += f"你想起来{related_memory_info}。"
        prompt += f"刚刚你的想法是{current_thinking_info}。"
        prompt += f"你现在{mood_info}。"
        prompt += "现在你接下去继续思考，产生新的想法，不要分点输出，输出连贯的内心独白，不要太长，"
        prompt += "但是记得结合上述的消息，要记得维持住你的人设，关注聊天和新内容，不要思考太多:"
        reponse, reasoning_content = await self.llm_model.generate_response_async(prompt)
        
        self.update_current_mind(reponse)
        
        self.current_mind = reponse
        print(f"麦麦的脑内状态：{self.current_mind}")
    
    async def do_after_reply(self,reply_content,chat_talking_prompt):
        # print("麦麦脑袋转起来了")
        self.current_state.update_current_state_info()
        
        personality_info = open("src/think_flow_demo/personality_info.txt", "r", encoding="utf-8").read()
        current_thinking_info = self.current_mind
        mood_info = self.current_state.mood
        related_memory_info = 'memory'
        message_stream_info = self.outer_world.talking_summary
        message_new_info = chat_talking_prompt
        reply_info = reply_content
        
        prompt = ""
        prompt += f"{personality_info}\n"
        prompt += f"现在你正在上网，和qq群里的网友们聊天，群里正在聊的话题是：{message_stream_info}\n"
        prompt += f"你想起来{related_memory_info}。"
        prompt += f"刚刚你的想法是{current_thinking_info}。"
        prompt += f"你现在看到了网友们发的新消息:{message_new_info}\n"
        prompt += f"你刚刚回复了群友们:{reply_info}"
        prompt += f"你现在{mood_info}。"
        prompt += "现在你接下去继续思考，产生新的想法，记得保留你刚刚的想法，不要分点输出，输出连贯的内心独白"
        prompt += "不要太长，但是记得结合上述的消息，要记得你的人设，关注聊天和新内容，以及你回复的内容，不要思考太多:"
        
        reponse, reasoning_content = await self.llm_model.generate_response_async(prompt)
        
        self.update_current_mind(reponse)
        
        self.current_mind = reponse
        print(f"{self.observe_chat_id}麦麦的脑内状态：{self.current_mind}")
        
        self.last_reply_time = time.time()
        
    async def judge_willing(self):
        # print("麦麦闹情绪了1")
        personality_info = open("src/think_flow_demo/personality_info.txt", "r", encoding="utf-8").read()
        current_thinking_info = self.current_mind
        mood_info = self.current_state.mood
        # print("麦麦闹情绪了2")
        prompt = ""
        prompt += f"{personality_info}\n"
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
            
        print(f"{self.observe_chat_id}麦麦的回复意愿：{self.current_state.willing}")
            
        return self.current_state.willing

    def build_outer_world_info(self):
        outer_world_info = outer_world.outer_world_info
        return outer_world_info

    def update_current_mind(self,reponse):
        self.past_mind.append(self.current_mind)
        self.current_mind = reponse


# subheartflow = SubHeartflow()

