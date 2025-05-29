import random
from src.common.logger_manager import get_logger
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from typing import List, Tuple
import os
import json

logger = get_logger("expressor")


def init_prompt() -> None:
    personality_expression_prompt = """
{personality}

请从以上人设中总结出这个角色可能的语言风格
思考回复的特殊内容和情感
思考有没有特殊的梗，一并总结成语言风格
总结成如下格式的规律，总结的内容要详细，但具有概括性：
当"xxx"时，可以"xxx", xxx不超过10个字

例如：
当"表示十分惊叹"时，使用"我嘞个xxxx"
当"表示讽刺的赞同，不想讲道理"时，使用"对对对"
当"想说明某个观点，但懒得明说"，使用"懂的都懂"

现在请你概括
"""
    Prompt(personality_expression_prompt, "personality_expression_prompt")


class PersonalityExpression:
    def __init__(self):
        self.express_learn_model: LLMRequest = LLMRequest(
            model=global_config.model.focus_expressor,
            temperature=0.1,
            max_tokens=256,
            request_type="expressor.learner",
        )
        self.meta_file_path = os.path.join("data", "expression", "personality", "expression_style_meta.json")
        self.expressions_file_path = os.path.join("data", "expression", "personality", "expressions.json")
        self.max_calculations = 5

    def _read_meta_data(self):
        if os.path.exists(self.meta_file_path):
            try:
                with open(self.meta_file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"无法解析 {self.meta_file_path} 中的JSON数据，将重新开始。")
                return {"last_style_text": None, "count": 0}
        return {"last_style_text": None, "count": 0}

    def _write_meta_data(self, data):
        os.makedirs(os.path.dirname(self.meta_file_path), exist_ok=True)
        with open(self.meta_file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    async def extract_and_store_personality_expressions(self):
        """
        检查data/expression/personality目录，不存在则创建。
        用peronality变量作为chat_str，调用LLM生成表达风格，解析后count=100，存储到expressions.json。
        如果expression_style发生变化，则删除旧的expressions.json并重置计数。
        对于相同的expression_style，最多计算self.max_calculations次。
        """
        os.makedirs(os.path.dirname(self.expressions_file_path), exist_ok=True)

        current_style_text = global_config.expression.expression_style
        meta_data = self._read_meta_data()

        last_style_text = meta_data.get("last_style_text")
        count = meta_data.get("count", 0)

        if current_style_text != last_style_text:
            logger.info(f"表达风格已从 '{last_style_text}' 变为 '{current_style_text}'。重置计数。")
            count = 0
            if os.path.exists(self.expressions_file_path):
                try:
                    os.remove(self.expressions_file_path)
                    logger.info(f"已删除旧的表达文件: {self.expressions_file_path}")
                except OSError as e:
                    logger.error(f"删除旧的表达文件 {self.expressions_file_path} 失败: {e}")

        if count >= self.max_calculations:
            logger.debug(f"对于风格 '{current_style_text}' 已达到最大计算次数 ({self.max_calculations})。跳过提取。")
            # 即使跳过，也更新元数据以反映当前风格已被识别且计数已满
            self._write_meta_data({"last_style_text": current_style_text, "count": count})
            return

        # 构建prompt
        prompt = await global_prompt_manager.format_prompt(
            "personality_expression_prompt",
            personality=current_style_text,
        )
        # logger.info(f"个性表达方式提取prompt: {prompt}")

        try:
            response, _ = await self.express_learn_model.generate_response_async(prompt)
        except Exception as e:
            logger.error(f"个性表达方式提取失败: {e}")
            # 如果提取失败，保存当前的风格和未增加的计数
            self._write_meta_data({"last_style_text": current_style_text, "count": count})
            return

        logger.info(f"个性表达方式提取response: {response}")
        # chat_id用personality
        expressions = self.parse_expression_response(response, "personality")
        # 转为dict并count=100
        result = []
        for _, situation, style in expressions:
            result.append({"situation": situation, "style": style, "count": 100})
        # 超过50条时随机删除多余的，只保留50条
        if len(result) > 50:
            remove_count = len(result) - 50
            remove_indices = set(random.sample(range(len(result)), remove_count))
            result = [item for idx, item in enumerate(result) if idx not in remove_indices]

        with open(self.expressions_file_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"已写入{len(result)}条表达到{self.expressions_file_path}")

        # 成功提取后更新元数据
        count += 1
        self._write_meta_data({"last_style_text": current_style_text, "count": count})
        logger.info(f"成功处理。风格 '{current_style_text}' 的计数现在是 {count}。")

    def parse_expression_response(self, response: str, chat_id: str) -> List[Tuple[str, str, str]]:
        """
        解析LLM返回的表达风格总结，每一行提取"当"和"使用"之间的内容，存储为(situation, style)元组
        """
        expressions: List[Tuple[str, str, str]] = []
        for line in response.splitlines():
            line = line.strip()
            if not line:
                continue
            # 查找"当"和下一个引号
            idx_when = line.find('当"')
            if idx_when == -1:
                continue
            idx_quote1 = idx_when + 1
            idx_quote2 = line.find('"', idx_quote1 + 1)
            if idx_quote2 == -1:
                continue
            situation = line[idx_quote1 + 1 : idx_quote2]
            # 查找"使用"
            idx_use = line.find('使用"', idx_quote2)
            if idx_use == -1:
                continue
            idx_quote3 = idx_use + 2
            idx_quote4 = line.find('"', idx_quote3 + 1)
            if idx_quote4 == -1:
                continue
            style = line[idx_quote3 + 1 : idx_quote4]
            expressions.append((chat_id, situation, style))
        return expressions


init_prompt()
