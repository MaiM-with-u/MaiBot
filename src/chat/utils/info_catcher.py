from src.config.config import global_config
from src.chat.message_receive.message import MessageRecv, MessageSending, Message
from src.common.database.database_model import Messages, ThinkingLog
import time
import traceback
from typing import List
import json


class InfoCatcher:
    def __init__(self):
        self.chat_history = []  # 聊天历史，长度为三倍使用的上下文喵~
        self.chat_history_in_thinking = []  # 思考期间的聊天内容喵~
        self.chat_history_after_response = []  # 回复后的聊天内容，长度为一倍上下文喵~

        self.chat_id = ""
        self.trigger_response_text = ""
        self.response_text = ""

        self.trigger_response_time = 0
        self.trigger_response_message = None

        self.response_time = 0
        self.response_messages = []

        # 使用字典来存储 heartflow 模式的数据
        self.heartflow_data = {
            "heart_flow_prompt": "",
            "sub_heartflow_before": "",
            "sub_heartflow_now": "",
            "sub_heartflow_after": "",
            "sub_heartflow_model": "",
            "prompt": "",
            "response": "",
            "model": "",
        }

        # 使用字典来存储 reasoning 模式的数据喵~
        self.reasoning_data = {"thinking_log": "", "prompt": "", "response": "", "model": ""}

        # 耗时喵~
        self.timing_results = {
            "interested_rate_time": 0,
            "sub_heartflow_observe_time": 0,
            "sub_heartflow_step_time": 0,
            "make_response_time": 0,
        }

    def catch_decide_to_response(self, message: MessageRecv):
        # 搜集决定回复时的信息
        self.trigger_response_message = message
        self.trigger_response_text = message.detailed_plain_text

        self.trigger_response_time = time.time()

        self.chat_id = message.chat_stream.stream_id

        self.chat_history = self.get_message_from_db_before_msg(message)

    def catch_after_observe(self, obs_duration: float):  # 这里可以有更多信息
        self.timing_results["sub_heartflow_observe_time"] = obs_duration

    def catch_afer_shf_step(self, step_duration: float, past_mind: str, current_mind: str):
        self.timing_results["sub_heartflow_step_time"] = step_duration
        if len(past_mind) > 1:
            self.heartflow_data["sub_heartflow_before"] = past_mind[-1]
            self.heartflow_data["sub_heartflow_now"] = current_mind
        else:
            self.heartflow_data["sub_heartflow_before"] = past_mind[-1]
            self.heartflow_data["sub_heartflow_now"] = current_mind

    def catch_after_llm_generated(self, prompt: str, response: str, reasoning_content: str = "", model_name: str = ""):
        self.reasoning_data["thinking_log"] = reasoning_content
        self.reasoning_data["prompt"] = prompt
        self.reasoning_data["response"] = response
        self.reasoning_data["model"] = model_name

        self.response_text = response

    def catch_after_generate_response(self, response_duration: float):
        self.timing_results["make_response_time"] = response_duration

    def catch_after_response(
        self, response_duration: float, response_message: List[str], first_bot_msg: MessageSending
    ):
        self.timing_results["make_response_time"] = response_duration
        self.response_time = time.time()
        self.response_messages = []
        for msg in response_message:
            self.response_messages.append(msg)

        self.chat_history_in_thinking = self.get_message_from_db_between_msgs(
            self.trigger_response_message, first_bot_msg
        )

    @staticmethod
    def get_message_from_db_between_msgs(message_start: Message, message_end: Message):
        try:
            time_start = message_start.message_info.time
            time_end = message_end.message_info.time
            chat_id = message_start.chat_stream.stream_id

            # print(f"查询参数: time_start={time_start}, time_end={time_end}, chat_id={chat_id}")

            messages_between_query = (
                Messages.select()
                .where((Messages.chat_id == chat_id) & (Messages.time > time_start) & (Messages.time < time_end))
                .order_by(Messages.time.desc())
            )

            result = list(messages_between_query)
            # print(f"查询结果数量: {len(result)}")
            # if result:
            # print(f"第一条消息时间: {result[0].time}")
            # print(f"最后一条消息时间: {result[-1].time}")
            return result
        except Exception as e:
            print(f"获取消息时出错: {str(e)}")
            print(traceback.format_exc())
            return []

    def get_message_from_db_before_msg(self, message: MessageRecv):
        message_id_val = message.message_info.message_id
        chat_id_val = message.chat_stream.stream_id

        messages_before_query = (
            Messages.select()
            .where((Messages.chat_id == chat_id_val) & (Messages.message_id < message_id_val))
            .order_by(Messages.time.desc())
            .limit(global_config.focus_chat.observation_context_size * 3)
        )

        return list(messages_before_query)

    def message_list_to_dict(self, message_list):
        result = []
        for msg_item in message_list:
            processed_msg_item = msg_item
            if not isinstance(msg_item, dict):
                processed_msg_item = self.message_to_dict(msg_item)

            if not processed_msg_item:
                continue

            lite_message = {
                "time": processed_msg_item.get("time"),
                "user_nickname": processed_msg_item.get("user_nickname"),
                "processed_plain_text": processed_msg_item.get("processed_plain_text"),
            }
            result.append(lite_message)
        return result

    @staticmethod
    def message_to_dict(msg_obj):
        if not msg_obj:
            return None
        if isinstance(msg_obj, dict):
            return msg_obj

        if isinstance(msg_obj, Messages):
            return {
                "time": msg_obj.time,
                "user_id": msg_obj.user_id,
                "user_nickname": msg_obj.user_nickname,
                "processed_plain_text": msg_obj.processed_plain_text,
            }

        if hasattr(msg_obj, "message_info") and hasattr(msg_obj.message_info, "user_info"):
            return {
                "time": msg_obj.message_info.time,
                "user_id": msg_obj.message_info.user_info.user_id,
                "user_nickname": msg_obj.message_info.user_info.user_nickname,
                "processed_plain_text": msg_obj.processed_plain_text,
            }

        print(f"Warning: message_to_dict received an unhandled type: {type(msg_obj)}")
        return {}

    def done_catch(self):
        """将收集到的信息存储到数据库的 thinking_log 表中喵~"""
        try:
            trigger_info_dict = self.message_to_dict(self.trigger_response_message)
            response_info_dict = {
                "time": self.response_time,
                "message": self.response_messages,
            }
            chat_history_list = self.message_list_to_dict(self.chat_history)
            chat_history_in_thinking_list = self.message_list_to_dict(self.chat_history_in_thinking)
            chat_history_after_response_list = self.message_list_to_dict(self.chat_history_after_response)

            log_entry = ThinkingLog(
                chat_id=self.chat_id,
                trigger_text=self.trigger_response_text,
                response_text=self.response_text,
                trigger_info_json=json.dumps(trigger_info_dict) if trigger_info_dict else None,
                response_info_json=json.dumps(response_info_dict),
                timing_results_json=json.dumps(self.timing_results),
                chat_history_json=json.dumps(chat_history_list),
                chat_history_in_thinking_json=json.dumps(chat_history_in_thinking_list),
                chat_history_after_response_json=json.dumps(chat_history_after_response_list),
                heartflow_data_json=json.dumps(self.heartflow_data),
                reasoning_data_json=json.dumps(self.reasoning_data),
            )
            log_entry.save()

            return True
        except Exception as e:
            print(f"存储思考日志时出错: {str(e)} 喵~")
            print(traceback.format_exc())
            return False


class InfoCatcherManager:
    def __init__(self):
        self.info_catchers = {}

    def get_info_catcher(self, thinking_id: str) -> InfoCatcher:
        if thinking_id not in self.info_catchers:
            self.info_catchers[thinking_id] = InfoCatcher()
        return self.info_catchers[thinking_id]


info_catcher_manager = InfoCatcherManager()
