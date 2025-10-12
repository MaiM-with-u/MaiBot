import json
import time
import random
import hashlib

from typing import List, Dict, Optional, Any, Tuple
from json_repair import repair_json

from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config, model_config
from src.common.logger import get_logger
from src.common.database.database_model import Expression
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.express.style_learner import style_learner_manager

logger = get_logger("expression_selector")


def init_prompt():
    expression_evaluation_prompt = """
以下是正在进行的聊天内容：
{chat_observe_info}

你的名字是{bot_name}{target_message}

以下是可选的表达情境：
{all_situations}

请你分析聊天内容的语境、情绪、话题类型，从上述情境中选择最适合当前聊天情境的，最多{max_num}个情境。
考虑因素包括：
1. 聊天的情绪氛围（轻松、严肃、幽默等）
2. 话题类型（日常、技术、游戏、情感等）
3. 情境与当前语境的匹配度
{target_message_extra_block}

请以JSON格式输出，只需要输出选中的情境编号：
例如：
{{
    "selected_situations": [2, 3, 5, 7, 19]
}}

请严格按照JSON格式输出，不要包含其他内容：
"""
    Prompt(expression_evaluation_prompt, "expression_evaluation_prompt")


def weighted_sample(population: List[Dict], k: int) -> List[Dict]:
    """随机抽样"""
    if not population or k <= 0:
        return []

    if len(population) <= k:
        return population.copy()

    # 使用随机抽样
    selected = []
    population_copy = population.copy()

    for _ in range(k):
        if not population_copy:
            break

        # 随机选择一个元素
        chosen_idx = random.randint(0, len(population_copy) - 1)
        selected.append(population_copy.pop(chosen_idx))

    return selected


class ExpressionSelector:
    def __init__(self):
        self.llm_model = LLMRequest(
            model_set=model_config.model_task_config.utils_small, request_type="expression.selector"
        )

    def can_use_expression_for_chat(self, chat_id: str) -> bool:
        """
        检查指定聊天流是否允许使用表达

        Args:
            chat_id: 聊天流ID

        Returns:
            bool: 是否允许使用表达
        """
        try:
            use_expression, _, _ = global_config.expression.get_expression_config_for_chat(chat_id)
            return use_expression
        except Exception as e:
            logger.error(f"检查表达使用权限失败: {e}")
            return False

    @staticmethod
    def _parse_stream_config_to_chat_id(stream_config_str: str) -> Optional[str]:
        """解析'platform:id:type'为chat_id（与get_stream_id一致）"""
        try:
            parts = stream_config_str.split(":")
            if len(parts) != 3:
                return None
            platform = parts[0]
            id_str = parts[1]
            stream_type = parts[2]
            is_group = stream_type == "group"
            if is_group:
                components = [platform, str(id_str)]
            else:
                components = [platform, str(id_str), "private"]
            key = "_".join(components)
            return hashlib.md5(key.encode()).hexdigest()
        except Exception:
            return None

    def get_related_chat_ids(self, chat_id: str) -> List[str]:
        """根据expression_groups配置，获取与当前chat_id相关的所有chat_id（包括自身）"""
        groups = global_config.expression.expression_groups

        # 检查是否存在全局共享组（包含"*"的组）
        global_group_exists = any("*" in group for group in groups)

        if global_group_exists:
            # 如果存在全局共享组，则返回所有可用的chat_id
            all_chat_ids = set()
            for group in groups:
                for stream_config_str in group:
                    if chat_id_candidate := self._parse_stream_config_to_chat_id(stream_config_str):
                        all_chat_ids.add(chat_id_candidate)
            return list(all_chat_ids) if all_chat_ids else [chat_id]

        # 否则使用现有的组逻辑
        for group in groups:
            group_chat_ids = []
            for stream_config_str in group:
                if chat_id_candidate := self._parse_stream_config_to_chat_id(stream_config_str):
                    group_chat_ids.append(chat_id_candidate)
            if chat_id in group_chat_ids:
                return group_chat_ids
        return [chat_id]

    def get_model_predicted_expressions(self, chat_id: str, target_message: str, total_num: int = 10) -> List[Dict[str, Any]]:
        """
        使用 style_learner 模型预测最合适的表达方式
        
        Args:
            chat_id: 聊天室ID
            target_message: 目标消息内容
            total_num: 需要预测的数量
            
        Returns:
            List[Dict[str, Any]]: 预测的表达方式列表
        """
        try:
            # 支持多chat_id合并预测
            related_chat_ids = self.get_related_chat_ids(chat_id)
            

            predicted_expressions = []
            
            # 为每个相关的chat_id进行预测
            for related_chat_id in related_chat_ids:
                try:
                    # 使用 style_learner 预测最合适的风格
                    best_style, scores = style_learner_manager.predict_style(
                        related_chat_id, target_message, top_k=total_num
                    )
                    
                    if best_style and scores:
                        # 获取预测风格的完整信息
                        learner = style_learner_manager.get_learner(related_chat_id)
                        style_id, situation = learner.get_style_info(best_style)
                        
                        if style_id and situation:
                            # 从数据库查找对应的表达记录
                            expr_query = Expression.select().where(
                                (Expression.chat_id == related_chat_id) &
                                (Expression.situation == situation) &
                                (Expression.style == best_style)
                            )
                            
                            if expr_query.exists():
                                expr = expr_query.get()
                                predicted_expressions.append({
                                    "id": expr.id,
                                    "situation": expr.situation,
                                    "style": expr.style,
                                    "last_active_time": expr.last_active_time,
                                    "source_id": expr.chat_id,
                                    "create_date": expr.create_date if expr.create_date is not None else expr.last_active_time,
                                    "prediction_score": scores.get(best_style, 0.0),
                                    "prediction_input": target_message
                                })
                            else:
                                logger.warning(f"为聊天室 {related_chat_id} 预测表达方式失败: {best_style} 没有找到对应的表达方式")
                                
                except Exception as e:
                    logger.warning(f"为聊天室 {related_chat_id} 预测表达方式失败: {e}")
                    continue
            
            # 按预测分数排序，取前 total_num 个
            predicted_expressions.sort(key=lambda x: x.get("prediction_score", 0.0), reverse=True)
            selected_expressions = predicted_expressions[:total_num]
            
            logger.info(f"为聊天室 {chat_id} 预测到 {len(selected_expressions)} 个表达方式")
            return selected_expressions
            
        except Exception as e:
            logger.error(f"模型预测表达方式失败: {e}")
            # 如果预测失败，回退到随机选择
            return self._random_expressions(chat_id, total_num)
    
    def _random_expressions(self, chat_id: str, total_num: int) -> List[Dict[str, Any]]:
        """
        随机选择表达方式
        
        Args:
            chat_id: 聊天室ID
            total_num: 需要选择的数量
            
        Returns:
            List[Dict[str, Any]]: 随机选择的表达方式列表
        """
        try:
            # 支持多chat_id合并抽选
            related_chat_ids = self.get_related_chat_ids(chat_id)

            # 优化：一次性查询所有相关chat_id的表达方式
            style_query = Expression.select().where(
                (Expression.chat_id.in_(related_chat_ids))
            )

            style_exprs = [
                {
                    "id": expr.id,
                    "situation": expr.situation,
                    "style": expr.style,
                    "last_active_time": expr.last_active_time,
                    "source_id": expr.chat_id,
                    "create_date": expr.create_date if expr.create_date is not None else expr.last_active_time,
                }
                for expr in style_query
            ]

            # 随机抽样
            if style_exprs:
                selected_style = weighted_sample(style_exprs, total_num)
            else:
                selected_style = []
            
            logger.info(f"随机选择，为聊天室 {chat_id} 选择了 {len(selected_style)} 个表达方式")
            return selected_style
            
        except Exception as e:
            logger.error(f"随机选择表达方式失败: {e}")
            return []


    async def select_suitable_expressions(
        self,
        chat_id: str,
        chat_info: str,
        max_num: int = 10,
        target_message: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[int]]:
        """
        根据配置模式选择适合的表达方式
        
        Args:
            chat_id: 聊天流ID
            chat_info: 聊天内容信息
            max_num: 最大选择数量
            target_message: 目标消息内容
            
        Returns:
            Tuple[List[Dict[str, Any]], List[int]]: 选中的表达方式列表和ID列表
        """
        # 检查是否允许在此聊天流中使用表达
        if not self.can_use_expression_for_chat(chat_id):
            logger.debug(f"聊天流 {chat_id} 不允许使用表达，返回空列表")
            return [], []

        # 获取配置模式
        expression_mode = global_config.expression.mode
        
        if expression_mode == "exp_model":
            # exp_model模式：直接使用模型预测，不经过LLM
            logger.debug(f"使用exp_model模式为聊天流 {chat_id} 选择表达方式")
            return await self._select_expressions_model_only(chat_id, target_message, max_num)
        elif expression_mode == "classic":
            # classic模式：随机选择+LLM选择
            logger.debug(f"使用classic模式为聊天流 {chat_id} 选择表达方式")
            return await self._select_expressions_classic(chat_id, chat_info, max_num, target_message)
        else:
            logger.warning(f"未知的表达模式: {expression_mode}，回退到classic模式")
            return await self._select_expressions_classic(chat_id, chat_info, max_num, target_message)

    async def _select_expressions_model_only(
        self,
        chat_id: str,
        target_message: str,
        max_num: int = 10,
    ) -> Tuple[List[Dict[str, Any]], List[int]]:
        """
        exp_model模式：直接使用模型预测，不经过LLM
        
        Args:
            chat_id: 聊天流ID
            target_message: 目标消息内容
            max_num: 最大选择数量
            
        Returns:
            Tuple[List[Dict[str, Any]], List[int]]: 选中的表达方式列表和ID列表
        """
        try:
            # 使用模型预测最合适的表达方式
            selected_expressions = self.get_model_predicted_expressions(chat_id, target_message, max_num)
            selected_ids = [expr["id"] for expr in selected_expressions]
            
            # 更新last_active_time
            if selected_expressions:
                self.update_expressions_last_active_time(selected_expressions)
            
            logger.info(f"exp_model模式为聊天流 {chat_id} 选择了 {len(selected_expressions)} 个表达方式")
            return selected_expressions, selected_ids
            
        except Exception as e:
            logger.error(f"exp_model模式选择表达方式失败: {e}")
            return [], []

    async def _select_expressions_classic(
        self,
        chat_id: str,
        chat_info: str,
        max_num: int = 10,
        target_message: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[int]]:
        """
        classic模式：随机选择+LLM选择
        
        Args:
            chat_id: 聊天流ID
            chat_info: 聊天内容信息
            max_num: 最大选择数量
            target_message: 目标消息内容
            
        Returns:
            Tuple[List[Dict[str, Any]], List[int]]: 选中的表达方式列表和ID列表
        """
        try:
            # 1. 使用随机抽样选择表达方式
            style_exprs = self._random_expressions(chat_id, 20)

            if len(style_exprs) < 10:
                logger.info(f"聊天流 {chat_id} 表达方式正在积累中")
                return [], []

            # 2. 构建所有表达方式的索引和情境列表
            all_expressions: List[Dict[str, Any]] = []
            all_situations: List[str] = []

            # 添加style表达方式
            for expr in style_exprs:
                expr = expr.copy()
                all_expressions.append(expr)
                all_situations.append(f"{len(all_expressions)}.当 {expr['situation']} 时，使用 {expr['style']}")

            if not all_expressions:
                logger.warning("没有找到可用的表达方式")
                return [], []

            all_situations_str = "\n".join(all_situations)

            if target_message:
                target_message_str = f"，现在你想要回复消息：{target_message}"
                target_message_extra_block = "4.考虑你要回复的目标消息"
            else:
                target_message_str = ""
                target_message_extra_block = ""

            # 3. 构建prompt（只包含情境，不包含完整的表达方式）
            prompt = (await global_prompt_manager.get_prompt_async("expression_evaluation_prompt")).format(
                bot_name=global_config.bot.nickname,
                chat_observe_info=chat_info,
                all_situations=all_situations_str,
                max_num=max_num,
                target_message=target_message_str,
                target_message_extra_block=target_message_extra_block,
            )

            # 4. 调用LLM
            content, (reasoning_content, model_name, _) = await self.llm_model.generate_response_async(prompt=prompt)

            if not content:
                logger.warning("LLM返回空结果")
                return [], []

            # 5. 解析结果
            result = repair_json(content)
            if isinstance(result, str):
                result = json.loads(result)

            if not isinstance(result, dict) or "selected_situations" not in result:
                logger.error("LLM返回格式错误")
                logger.info(f"LLM返回结果: \n{content}")
                return [], []

            selected_indices = result["selected_situations"]

            # 根据索引获取完整的表达方式
            valid_expressions: List[Dict[str, Any]] = []
            selected_ids = []
            for idx in selected_indices:
                if isinstance(idx, int) and 1 <= idx <= len(all_expressions):
                    expression = all_expressions[idx - 1]  # 索引从1开始
                    selected_ids.append(expression["id"])
                    valid_expressions.append(expression)

            # 对选中的所有表达方式，更新last_active_time
            if valid_expressions:
                self.update_expressions_last_active_time(valid_expressions)

            logger.info(f"classic模式从{len(all_expressions)}个情境中选择了{len(valid_expressions)}个")
            return valid_expressions, selected_ids

        except Exception as e:
            logger.error(f"classic模式处理表达方式选择时出错: {e}")
            return [], []

    def update_expressions_last_active_time(self, expressions_to_update: List[Dict[str, Any]]):
        """对一批表达方式更新last_active_time"""
        if not expressions_to_update:
            return
        updates_by_key = {}
        for expr in expressions_to_update:
            source_id: str = expr.get("source_id")  # type: ignore
            situation: str = expr.get("situation")  # type: ignore
            style: str = expr.get("style")  # type: ignore
            if not source_id or not situation or not style:
                logger.warning(f"表达方式缺少必要字段，无法更新: {expr}")
                continue
            key = (source_id, situation, style)
            if key not in updates_by_key:
                updates_by_key[key] = expr
        for chat_id, situation, style in updates_by_key:
            query = Expression.select().where(
                (Expression.chat_id == chat_id)
                & (Expression.situation == situation)
                & (Expression.style == style)
            )
            if query.exists():
                expr_obj = query.get()
                expr_obj.last_active_time = time.time()
                expr_obj.save()
                logger.debug(
                    "表达方式激活: 更新last_active_time in db"
                )


init_prompt()

try:
    expression_selector = ExpressionSelector()
except Exception as e:
    logger.error(f"ExpressionSelector初始化失败: {e}")
