import random
from src.common.logger_manager import get_logger
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from typing import List, Tuple
import os
import json
from datetime import datetime

logger = get_logger("expressor")


def init_prompt() -> None:
    personality_expression_prompt = """
{personality}

请从以上人设中总结出这个角色可能的语言风格，你必须严格根据人设引申，不要输出例子
思考回复的特殊内容和情感
思考有没有特殊的梗，一并总结成语言风格
总结成如下格式的规律，总结的内容要详细，但具有概括性：
当"xxx"时，可以"xxx", xxx不超过10个字

例如（不要输出例子）：
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
            max_tokens=512,
            request_type="expressor.learner",
        )
        self.meta_file_path = os.path.join("data", "expression", "personality", "expression_style_meta.json")
        self.expressions_file_path = os.path.join("data", "expression", "personality", "expressions.json")
        self.max_calculations = 20

    def _read_meta_data(self):
        if os.path.exists(self.meta_file_path):
            try:
                with open(self.meta_file_path, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
                    # 检查是否有last_update_time字段
                    if "last_update_time" not in meta_data:
                        logger.warning(f"{self.meta_file_path} 中缺少last_update_time字段，将重新开始。")
                        # 清空并重写元数据文件
                        self._write_meta_data({"last_style_text": None, "count": 0, "last_update_time": None})
                        # 清空并重写表达文件
                        if os.path.exists(self.expressions_file_path):
                            with open(self.expressions_file_path, "w", encoding="utf-8") as f:
                                json.dump([], f, ensure_ascii=False, indent=2)
                            logger.debug(f"已清空表达文件: {self.expressions_file_path}")
                        return {"last_style_text": None, "count": 0, "last_update_time": None}
                    return meta_data
            except json.JSONDecodeError:
                logger.warning(f"无法解析 {self.meta_file_path} 中的JSON数据，将重新开始。")
                # 清空并重写元数据文件
                self._write_meta_data({"last_style_text": None, "count": 0, "last_update_time": None})
                # 清空并重写表达文件
                if os.path.exists(self.expressions_file_path):
                    with open(self.expressions_file_path, "w", encoding="utf-8") as f:
                        json.dump([], f, ensure_ascii=False, indent=2)
                    logger.debug(f"已清空表达文件: {self.expressions_file_path}")
                return {"last_style_text": None, "count": 0, "last_update_time": None}
        return {"last_style_text": None, "count": 0, "last_update_time": None}

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
            self._write_meta_data(
                {
                    "last_style_text": current_style_text,
                    "count": count,
                    "last_update_time": meta_data.get("last_update_time"),
                }
            )
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
            self._write_meta_data(
                {
                    "last_style_text": current_style_text,
                    "count": count,
                    "last_update_time": meta_data.get("last_update_time"),
                }
            )
            return

        logger.info(f"个性表达方式提取response: {response}")
        # chat_id用personality

        # 转为dict并count=100
        if response != "":
            expressions = self.parse_expression_response(response, "personality")
            # 读取已有的表达方式
            existing_expressions = []
            if os.path.exists(self.expressions_file_path):
                try:
                    with open(self.expressions_file_path, "r", encoding="utf-8") as f:
                        existing_expressions = json.load(f)
                except (json.JSONDecodeError, FileNotFoundError):
                    logger.warning(f"无法读取或解析 {self.expressions_file_path}，将创建新的表达文件。")

            # 创建新的表达方式
            new_expressions = []
            for _, situation, style in expressions:
                new_expressions.append({"situation": situation, "style": style, "count": 1})

            # 合并表达方式，如果situation和style相同则累加count
            merged_expressions = existing_expressions.copy()
            for new_expr in new_expressions:
                found = False
                for existing_expr in merged_expressions:
                    if (
                        existing_expr["situation"] == new_expr["situation"]
                        and existing_expr["style"] == new_expr["style"]
                    ):
                        existing_expr["count"] += new_expr["count"]
                        found = True
                        break
                if not found:
                    merged_expressions.append(new_expr)

            # 超过50条时随机删除多余的，只保留50条
            if len(merged_expressions) > 50:
                remove_count = len(merged_expressions) - 50
                remove_indices = set(random.sample(range(len(merged_expressions)), remove_count))
                merged_expressions = [item for idx, item in enumerate(merged_expressions) if idx not in remove_indices]

            with open(self.expressions_file_path, "w", encoding="utf-8") as f:
                json.dump(merged_expressions, f, ensure_ascii=False, indent=2)
            logger.info(f"已写入{len(merged_expressions)}条表达到{self.expressions_file_path}")

            # 成功提取后更新元数据
            count += 1
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._write_meta_data(
                {"last_style_text": current_style_text, "count": count, "last_update_time": current_time}
            )
            logger.info(f"成功处理。风格 '{current_style_text}' 的计数现在是 {count}，最后更新时间：{current_time}。")
        else:
            logger.warning(f"个性表达方式提取失败，模型返回空内容: {response}")

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
