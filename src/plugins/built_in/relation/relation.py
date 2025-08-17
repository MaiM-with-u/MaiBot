import random
from typing import Tuple

# 导入新插件系统
from src.plugin_system import BaseAction, ActionActivationType, ChatMode

# 导入依赖的系统组件
from src.common.logger import get_logger

# 导入API模块 - 标准Python包方式
from src.plugin_system.apis import emoji_api, llm_api, message_api
# NoReplyAction已集成到heartFC_chat.py中，不再需要导入
from src.config.config import global_config
from src.person_info.person_info import Person, get_memory_content_from_memory, get_weight_from_memory
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
import json
from json_repair import repair_json


logger = get_logger("relation")


def init_prompt():
    Prompt(
        """
以下是一些记忆条目的分类：
----------------------
{category_list}
----------------------
每一个分类条目类型代表了你对用户："{person_name}"的印象的一个类别

现在，你有一条对 {person_name} 的新记忆内容：
{memory_point}

请判断该记忆内容是否属于上述分类，请给出分类的名称。
如果不属于上述分类，请输出一个合适的分类名称，对新记忆内容进行概括。要求分类名具有概括性。
注意分类数一般不超过5个
请严格用json格式输出，不要输出任何其他内容：
{{
    "category": "分类名称"
}} """,
        "relation_category"
    )
    
    
    Prompt(
        """
以下是有关{category}的现有记忆：
----------------------
{memory_list}
----------------------

现在，你有一条对 {person_name} 的新记忆内容：
{memory_point}

请判断该新记忆内容是否已经存在于现有记忆中，你可以对现有进行进行以下修改：
注意，一般来说记忆内容不超过5个，且记忆文本不应太长

1.新增：当记忆内容不存在于现有记忆，且不存在矛盾，请用json格式输出：
{{
    "new_memory": "需要新增的记忆内容"
}}
2.加深印象：如果这个新记忆已经存在于现有记忆中，在内容上与现有记忆类似，请用json格式输出：
{{
    "memory_id": 1, #请输出你认为需要加深印象的，与新记忆内容类似的，已经存在的记忆的序号
    "integrate_memory": "加深后的记忆内容，合并内容类似的新记忆和旧记忆"
}}
3.整合：如果这个新记忆与现有记忆产生矛盾，请你结合其他记忆进行整合，用json格式输出：
{{
    "memory_id": 1, #请输出你认为需要整合的，与新记忆存在矛盾的，已经存在的记忆的序号
    "integrate_memory": "整合后的记忆内容，合并内容矛盾的新记忆和旧记忆"
}}

现在，请你根据情况选出合适的修改方式，并输出json，不要输出其他内容：
""",
        "relation_category_update"
    )


class BuildRelationAction(BaseAction):
    """关系动作 - 构建关系"""

    activation_type = ActionActivationType.LLM_JUDGE
    parallel_action = True

    # 动作基本信息
    action_name = "build_relation"
    action_description = "了解对于某人的记忆，并添加到你对对方的印象中"

    # LLM判断提示词
    llm_judge_prompt = """
    判定是否需要使用关系动作，添加对于某人的记忆：
    1. 对方与你的交互让你对其有新记忆
    2. 对方有提到其个人信息，包括喜好，身份，等等
    3. 对方希望你记住对方的信息
    
    请回答"是"或"否"。
    """

    # 动作参数定义
    action_parameters = {
        "person_name":"需要了解或记忆的人的名称",
        "impression":"需要了解的对某人的记忆或印象"
    }

    # 动作使用场景
    action_require = [
        "了解对于某人的记忆，并添加到你对对方的印象中",
        "对方与有明确提到有关其自身的事件",
        "对方有提到其个人信息，包括喜好，身份，等等",
        "对方希望你记住对方的信息"
    ]

    # 关联类型
    associated_types = ["text"]

    async def execute(self) -> Tuple[bool, str]:
        # sourcery skip: assign-if-exp, introduce-default-else, swap-if-else-branches, use-named-expression
        """执行关系动作"""
        logger.info(f"{self.log_prefix} 决定添加记忆")

        try:
            # 1. 获取构建关系的原因
            impression = self.action_data.get("impression", "")
            logger.info(f"{self.log_prefix} 添加记忆原因: {self.reasoning}")
            person_name = self.action_data.get("person_name", "")
            # 2. 获取目标用户信息
            person = Person(person_name=person_name)
            if not person.is_known:
                logger.warning(f"{self.log_prefix} 用户 {person_name} 不存在，跳过添加记忆")
                return False, f"用户 {person_name} 不存在，跳过添加记忆"
            

            
            category_list = person.get_all_category()
            if not category_list:
                category_list_str = "无分类"
            else:
                category_list_str = "\n".join(category_list)

            prompt = await global_prompt_manager.format_prompt(
                "relation_category",
                category_list=category_list_str,
                memory_point=impression,
                person_name=person.person_name
            )
            

            if global_config.debug.show_prompt:
                logger.info(f"{self.log_prefix} 生成的LLM Prompt: {prompt}")
            else:
                logger.debug(f"{self.log_prefix} 生成的LLM Prompt: {prompt}")

            # 5. 调用LLM
            models = llm_api.get_available_models()
            chat_model_config = models.get("utils_small")  # 使用字典访问方式
            if not chat_model_config:
                logger.error(f"{self.log_prefix} 未找到'utils_small'模型配置，无法调用LLM")
                return False, "未找到'utils_small'模型配置"

            success, category, _, _ = await llm_api.generate_with_model(
                prompt, model_config=chat_model_config, request_type="relation.category"
            )
            
            

            category_data = json.loads(repair_json(category))
            category = category_data.get("category", "")
            if not category:
                logger.warning(f"{self.log_prefix} LLM未给出分类，跳过添加记忆")
                return False, "LLM未给出分类，跳过添加记忆"
            
            
            # 第二部分：更新记忆
            
            memory_list = person.get_memory_list_by_category(category)
            if not memory_list:
                logger.info(f"{self.log_prefix} {person.person_name} 的  {category}  的记忆为空，进行创建")
                person.memory_points.append(f"{category}:{impression}:1.0")
                person.sync_to_database()
                
                return True, f"未找到分类为{category}的记忆点，进行添加"
            
            memory_list_str = ""
            memory_list_id = {}
            id = 1
            for memory in memory_list:
                memory_content = get_memory_content_from_memory(memory)
                memory_list_str += f"{id}. {memory_content}\n"
                memory_list_id[id] = memory
                id += 1
            
            prompt = await global_prompt_manager.format_prompt(
                "relation_category_update",
                category=category,
                memory_list=memory_list_str,
                memory_point=impression,
                person_name=person.person_name
            )
            
            if global_config.debug.show_prompt:
                logger.info(f"{self.log_prefix} 生成的LLM Prompt: {prompt}")
            else:
                logger.debug(f"{self.log_prefix} 生成的LLM Prompt: {prompt}")

            chat_model_config = models.get("utils")            
            success, update_memory, _, _ = await llm_api.generate_with_model(
                prompt, model_config=chat_model_config, request_type="relation.category.update"
            )
            
            update_memory_data = json.loads(repair_json(update_memory))
            new_memory = update_memory_data.get("new_memory", "")
            memory_id = update_memory_data.get("memory_id", "")
            integrate_memory = update_memory_data.get("integrate_memory", "")
            
            if new_memory:
                # 新记忆
                person.memory_points.append(f"{category}:{new_memory}:1.0")
                person.sync_to_database()
                
                return True, f"为{person.person_name}新增记忆点: {new_memory}"
            elif memory_id and integrate_memory:
                # 现存或冲突记忆
                memory = memory_list_id[memory_id]
                memory_content = get_memory_content_from_memory(memory)
                del_count = person.del_memory(category,memory_content)
                
                if del_count > 0:
                    logger.info(f"{self.log_prefix} 删除记忆点: {memory_content}")

                    memory_weight = get_weight_from_memory(memory)
                    person.memory_points.append(f"{category}:{integrate_memory}:{memory_weight + 1.0}")
                    person.sync_to_database()
                    
                    return True, f"更新{person.person_name}的记忆点: {memory_content} -> {integrate_memory}"
                    
                else:
                    logger.warning(f"{self.log_prefix} 删除记忆点失败: {memory_content}")
                    return False, f"删除{person.person_name}的记忆点失败: {memory_content}"
                
                

            return True, "关系动作执行成功"

        except Exception as e:
            logger.error(f"{self.log_prefix} 关系构建动作执行失败: {e}", exc_info=True)
            return False, f"关系动作执行失败: {str(e)}"


# 还缺一个关系的太多遗忘和对应的提取
init_prompt()