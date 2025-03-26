import tomli
import sys
import re
from pathlib import Path
from typing import Dict, Any, List, Set, Tuple

def load_toml_file(file_path: str) -> Dict[str, Any]:
    """加载TOML文件"""
    try:
        with open(file_path, "rb") as f:
            return tomli.load(f)
    except Exception as e:
        print(f"错误: 无法加载配置文件 {file_path}: {str(e)} 请检查文件是否存在或者他妈的有没有东西没写值")
        sys.exit(1)

def load_env_file(file_path: str) -> Dict[str, str]:
    """加载.env文件中的环境变量"""
    env_vars = {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # 处理注释
                    if '#' in value:
                        value = value.split('#', 1)[0].strip()
                    
                    # 处理引号
                    if (value.startswith('"') and value.endswith('"')) or \
                       (value.startswith("'") and value.endswith("'")):
                        value = value[1:-1]
                    
                    env_vars[key] = value
        return env_vars
    except Exception as e:
        print(f"警告: 无法加载.env文件 {file_path}: {str(e)}")
        return {}

def check_required_sections(config: Dict[str, Any]) -> List[str]:
    """检查必要的配置段是否存在"""
    required_sections = [
        "inner", "bot", "personality", "message", "emoji", 
        "cq_code", "response", "willing", "memory", "mood",
        "groups", "model"
    ]
    missing_sections = []
    
    for section in required_sections:
        if section not in config:
            missing_sections.append(section)
    
    return missing_sections

def check_probability_sum(config: Dict[str, Any]) -> List[Tuple[str, float]]:
    """检查概率总和是否为1"""
    errors = []
    
    # 检查人格概率
    if "personality" in config:
        personality = config["personality"]
        prob_sum = sum([
            personality.get("personality_1_probability", 0),
            personality.get("personality_2_probability", 0),
            personality.get("personality_3_probability", 0)
        ])
        if abs(prob_sum - 1.0) > 0.001:  # 允许有小数点精度误差
            errors.append(("人格概率总和", prob_sum))
    
    # 检查响应模型概率
    if "response" in config:
        response = config["response"]
        model_prob_sum = sum([
            response.get("model_r1_probability", 0),
            response.get("model_v3_probability", 0),
            response.get("model_r1_distill_probability", 0)
        ])
        if abs(model_prob_sum - 1.0) > 0.001:
            errors.append(("响应模型概率总和", model_prob_sum))
    
    return errors

def check_probability_range(config: Dict[str, Any]) -> List[Tuple[str, float]]:
    """检查概率值是否在0-1范围内"""
    errors = []
    
    # 收集所有概率值
    prob_fields = []
    
    # 人格概率
    if "personality" in config:
        personality = config["personality"]
        prob_fields.extend([
            ("personality.personality_1_probability", personality.get("personality_1_probability")),
            ("personality.personality_2_probability", personality.get("personality_2_probability")),
            ("personality.personality_3_probability", personality.get("personality_3_probability"))
        ])
    
    # 消息概率
    if "message" in config:
        message = config["message"]
        prob_fields.append(("message.emoji_chance", message.get("emoji_chance")))
    
    # 响应模型概率
    if "response" in config:
        response = config["response"]
        prob_fields.extend([
            ("response.model_r1_probability", response.get("model_r1_probability")),
            ("response.model_v3_probability", response.get("model_v3_probability")),
            ("response.model_r1_distill_probability", response.get("model_r1_distill_probability"))
        ])
    
    # 情绪衰减率
    if "mood" in config:
        mood = config["mood"]
        prob_fields.append(("mood.mood_decay_rate", mood.get("mood_decay_rate")))
    
    # 中文错别字概率
    if "chinese_typo" in config and config["chinese_typo"].get("enable", False):
        typo = config["chinese_typo"]
        prob_fields.extend([
            ("chinese_typo.error_rate", typo.get("error_rate")),
            ("chinese_typo.tone_error_rate", typo.get("tone_error_rate")),
            ("chinese_typo.word_replace_rate", typo.get("word_replace_rate"))
        ])
    
    # 检查所有概率值是否在0-1范围内
    for field_name, value in prob_fields:
        if value is not None and (value < 0 or value > 1):
            errors.append((field_name, value))
    
    return errors

def check_model_configurations(config: Dict[str, Any], env_vars: Dict[str, str]) -> List[str]:
    """检查模型配置是否完整，并验证provider是否正确"""
    errors = []
    
    if "model" not in config:
        return ["缺少[model]部分"]
    
    required_models = [
        "llm_reasoning", "llm_reasoning_minor", "llm_normal", 
        "llm_normal_minor", "llm_emotion_judge", "llm_topic_judge",
        "llm_summary_by_topic", "vlm", "embedding"
    ]
    
    # 从环境变量中提取有效的API提供商
    valid_providers = set()
    for key in env_vars:
        if key.endswith('_BASE_URL'):
            provider_name = key.replace('_BASE_URL', '')
            valid_providers.add(provider_name)
    
    # 将provider名称标准化以便比较
    provider_mapping = {
        "SILICONFLOW": ["SILICONFLOW", "SILICON_FLOW", "SILICON-FLOW"],
        "CHAT_ANY_WHERE": ["CHAT_ANY_WHERE", "CHAT-ANY-WHERE", "CHATANYWHERE"],
        "DEEP_SEEK": ["DEEP_SEEK", "DEEP-SEEK", "DEEPSEEK"]
    }
    
    # 创建反向映射表，用于检查错误拼写
    reverse_mapping = {}
    for standard, variants in provider_mapping.items():
        for variant in variants:
            reverse_mapping[variant.upper()] = standard
    
    for model_name in required_models:
        # 检查model下是否有对应子部分
        if model_name not in config["model"]:
            errors.append(f"缺少[model.{model_name}]配置")
        else:
            model_config = config["model"][model_name]
            if "name" not in model_config:
                errors.append(f"[model.{model_name}]缺少name属性")
            
            if "provider" not in model_config:
                errors.append(f"[model.{model_name}]缺少provider属性")
            else:
                provider = model_config["provider"].upper()
                
                # 检查拼写错误
                for known_provider, correct_provider in reverse_mapping.items():
                    # 使用模糊匹配检测拼写错误
                    if provider != known_provider and _similar_strings(provider, known_provider) and provider not in reverse_mapping:
                        errors.append(f"[model.{model_name}]的provider '{model_config['provider']}' 可能拼写错误，应为 '{known_provider}'")
                        break
    
    return errors

def _similar_strings(s1: str, s2: str) -> bool:
    """简单检查两个字符串是否相似（用于检测拼写错误）"""
    # 如果两个字符串长度相差过大，则认为不相似
    if abs(len(s1) - len(s2)) > 2:
        return False
    
    # 计算相同字符的数量
    common_chars = sum(1 for c1, c2 in zip(s1, s2) if c1 == c2)
    # 如果相同字符比例超过80%，则认为相似
    return common_chars / max(len(s1), len(s2)) > 0.8

def check_api_providers(config: Dict[str, Any], env_vars: Dict[str, str]) -> List[str]:
    """检查配置文件中的API提供商是否与环境变量中的一致"""
    errors = []
    
    if "model" not in config:
        return ["缺少[model]部分"]
    
    # 从环境变量中提取有效的API提供商
    valid_providers = {}
    for key in env_vars:
        if key.endswith('_BASE_URL'):
            provider_name = key.replace('_BASE_URL', '')
            base_url = env_vars[key]
            valid_providers[provider_name] = {
                "base_url": base_url,
                "key": env_vars.get(f"{provider_name}_KEY", "")
            }
    
    # 检查配置文件中使用的所有提供商
    used_providers = set()
    for model_category, model_config in config["model"].items():
        if "provider" in model_config:
            provider = model_config["provider"]
            used_providers.add(provider)
            
            # 检查此提供商是否在环境变量中定义
            normalized_provider = provider.replace(" ", "_").upper()
            found = False
            for env_provider in valid_providers:
                if normalized_provider == env_provider:
                    found = True
                    break
                # 尝试更宽松的匹配（例如SILICONFLOW可能匹配SILICON_FLOW）
                elif normalized_provider.replace("_", "") == env_provider.replace("_", ""):
                    found = True
                    errors.append(f"提供商 '{provider}' 在环境变量中的名称是 '{env_provider}', 建议统一命名")
                    break
            
            if not found:
                errors.append(f"提供商 '{provider}' 在环境变量中未定义")
    
    # 特别检查常见的拼写错误
    for provider in used_providers:
        if provider.upper() == "SILICONFOLW":
            errors.append(f"提供商 'SILICONFOLW' 存在拼写错误，应为 'SILICONFLOW'")
    
    return errors

def check_groups_configuration(config: Dict[str, Any]) -> List[str]:
    """检查群组配置"""
    errors = []
    
    if "groups" not in config:
        return ["缺少[groups]部分"]
    
    groups = config["groups"]
    
    # 检查talk_allowed是否为列表
    if "talk_allowed" not in groups:
        errors.append("缺少groups.talk_allowed配置")
    elif not isinstance(groups["talk_allowed"], list):
        errors.append("groups.talk_allowed应该是一个列表")
    else:
        # 检查talk_allowed是否包含默认示例值123
        if 123 in groups["talk_allowed"]:
            errors.append({
                "main": "groups.talk_allowed中存在默认示例值'123'，请修改为真实的群号",
                "details": [
                    f"  当前值: {groups['talk_allowed']}",
                    f"  '123'为示例值，需要替换为真实群号"
                ]
            })
        
        # 检查是否存在重复的群号
        talk_allowed = groups["talk_allowed"]
        duplicates = []
        seen = set()
        for gid in talk_allowed:
            if gid in seen and gid not in duplicates:
                duplicates.append(gid)
            seen.add(gid)
        
        if duplicates:
            errors.append({
                "main": "groups.talk_allowed中存在重复的群号",
                "details": [f"  重复的群号: {duplicates}"]
            })
    
    # 检查其他群组配置
    if "talk_frequency_down" in groups and not isinstance(groups["talk_frequency_down"], list):
        errors.append("groups.talk_frequency_down应该是一个列表")
    
    if "ban_user_id" in groups and not isinstance(groups["ban_user_id"], list):
        errors.append("groups.ban_user_id应该是一个列表")
    
    return errors

def check_keywords_reaction(config: Dict[str, Any]) -> List[str]:
    """检查关键词反应配置"""
    errors = []
    
    if "keywords_reaction" not in config:
        return ["缺少[keywords_reaction]部分"]
    
    kr = config["keywords_reaction"]
    
    # 检查enable字段
    if "enable" not in kr:
        errors.append("缺少keywords_reaction.enable配置")
    
    # 检查规则配置
    if "rules" not in kr:
        errors.append("缺少keywords_reaction.rules配置")
    elif not isinstance(kr["rules"], list):
        errors.append("keywords_reaction.rules应该是一个列表")
    else:
        for i, rule in enumerate(kr["rules"]):
            if "enable" not in rule:
                errors.append(f"关键词规则 #{i+1} 缺少enable字段")
            if "keywords" not in rule:
                errors.append(f"关键词规则 #{i+1} 缺少keywords字段")
            elif not isinstance(rule["keywords"], list):
                errors.append(f"关键词规则 #{i+1} 的keywords应该是一个列表")
            if "reaction" not in rule:
                errors.append(f"关键词规则 #{i+1} 缺少reaction字段")
    
    return errors

def check_willing_mode(config: Dict[str, Any]) -> List[str]:
    """检查回复意愿模式配置"""
    errors = []
    
    if "willing" not in config:
        return ["缺少[willing]部分"]
    
    willing = config["willing"]
    
    if "willing_mode" not in willing:
        errors.append("缺少willing.willing_mode配置")
    elif willing["willing_mode"] not in ["classical", "dynamic", "custom"]:
        errors.append(f"willing.willing_mode值无效: {willing['willing_mode']}, 应为classical/dynamic/custom")
    
    return errors

def check_memory_config(config: Dict[str, Any]) -> List[str]:
    """检查记忆系统配置"""
    errors = []
    
    if "memory" not in config:
        return ["缺少[memory]部分"]
    
    memory = config["memory"]
    
    # 检查必要的参数
    required_fields = [
        "build_memory_interval", "memory_compress_rate", 
        "forget_memory_interval", "memory_forget_time", 
        "memory_forget_percentage"
    ]
    
    for field in required_fields:
        if field not in memory:
            errors.append(f"缺少memory.{field}配置")
    
    # 检查参数值的有效性
    if "memory_compress_rate" in memory and (memory["memory_compress_rate"] <= 0 or memory["memory_compress_rate"] > 1):
        errors.append(f"memory.memory_compress_rate值无效: {memory['memory_compress_rate']}, 应在0-1之间")
    
    if "memory_forget_percentage" in memory and (memory["memory_forget_percentage"] <= 0 or memory["memory_forget_percentage"] > 1):
        errors.append(f"memory.memory_forget_percentage值无效: {memory['memory_forget_percentage']}, 应在0-1之间")
    
    return errors

def check_personality_config(config: Dict[str, Any]) -> List[str]:
    """检查人格配置"""
    errors = []
    
    if "personality" not in config:
        return ["缺少[personality]部分"]
    
    personality = config["personality"]
    
    # 检查prompt_personality是否存在且为数组
    if "prompt_personality" not in personality:
        errors.append("缺少personality.prompt_personality配置")
    elif not isinstance(personality["prompt_personality"], list):
        errors.append("personality.prompt_personality应该是一个数组")
    else:
        # 检查数组长度
        if len(personality["prompt_personality"]) < 1:
            errors.append(f"personality.prompt_personality数组长度不足，当前长度: {len(personality['prompt_personality'])}, 需要至少1项")
        else:
            # 模板默认值
            template_values = [
                "用一句话或几句话描述性格特点和其他特征",
                "用一句话或几句话描述性格特点和其他特征",
                "例如，是一个热爱国家热爱党的新时代好青年"
            ]
            
            # 检查是否仍然使用默认模板值
            error_details = []
            for i, (current, template) in enumerate(zip(personality["prompt_personality"][:3], template_values)):
                if current == template:
                    error_details.append({
                        "main": f"personality.prompt_personality第{i+1}项仍使用默认模板值，请自定义",
                        "details": [
                            f"  当前值: '{current}'",
                            f"  请不要使用模板值: '{template}'"
                        ]
                    })
            
            # 将错误添加到errors列表
            for error in error_details:
                errors.append(error)
    
    return errors

def check_bot_config(config: Dict[str, Any]) -> List[str]:
    """检查机器人基础配置"""
    errors = []
    infos = []
    
    if "bot" not in config:
        return ["缺少[bot]部分"]
    
    bot = config["bot"]
    
    # 检查QQ号是否为默认值或测试值
    if "qq" not in bot:
        errors.append("缺少bot.qq配置")
    elif bot["qq"] == 1 or bot["qq"] == 123:
        errors.append(f"QQ号 '{bot['qq']}' 似乎是默认值或测试值，请设置为真实的QQ号")
    else:
        infos.append(f"当前QQ号: {bot['qq']}")
    
    # 检查昵称是否设置
    if "nickname" not in bot or not bot["nickname"]:
        errors.append("缺少bot.nickname配置或昵称为空")
    elif bot["nickname"]:
        infos.append(f"当前昵称: {bot['nickname']}")
    
    # 检查别名是否为列表
    if "alias_names" in bot and not isinstance(bot["alias_names"], list):
        errors.append("bot.alias_names应该是一个列表")
    
    return errors, infos

def format_results(all_errors):
    """格式化检查结果"""
    sections_errors, prob_sum_errors, prob_range_errors, model_errors, api_errors, groups_errors, kr_errors, willing_errors, memory_errors, personality_errors, bot_results = all_errors
    bot_errors, bot_infos = bot_results
    
    if not any([sections_errors, prob_sum_errors, prob_range_errors, model_errors, api_errors, groups_errors, kr_errors, willing_errors, memory_errors, personality_errors, bot_errors]):
        result = "✅ 配置文件检查通过，未发现问题。"
        
        # 添加机器人信息
        if bot_infos:
            result += "\n\n【机器人信息】"
            for info in bot_infos:
                result += f"\n  - {info}"
        
        return result
    
    output = []
    output.append("❌ 配置文件检查发现以下问题:")
    
    if sections_errors:
        output.append("\n【缺失的配置段】")
        for section in sections_errors:
            output.append(f"  - {section}")
    
    if prob_sum_errors:
        output.append("\n【概率总和错误】(应为1.0)")
        for name, value in prob_sum_errors:
            output.append(f"  - {name}: {value:.4f}")
    
    if prob_range_errors:
        output.append("\n【概率值范围错误】(应在0-1之间)")
        for name, value in prob_range_errors:
            output.append(f"  - {name}: {value}")
    
    if model_errors:
        output.append("\n【模型配置错误】")
        for error in model_errors:
            output.append(f"  - {error}")
    
    if api_errors:
        output.append("\n【API提供商错误】")
        for error in api_errors:
            output.append(f"  - {error}")
    
    if groups_errors:
        output.append("\n【群组配置错误】")
        for error in groups_errors:
            if isinstance(error, dict):
                output.append(f"  - {error['main']}")
                for detail in error['details']:
                    output.append(f"{detail}")
            else:
                output.append(f"  - {error}")
    
    if kr_errors:
        output.append("\n【关键词反应配置错误】")
        for error in kr_errors:
            output.append(f"  - {error}")
    
    if willing_errors:
        output.append("\n【回复意愿配置错误】")
        for error in willing_errors:
            output.append(f"  - {error}")
    
    if memory_errors:
        output.append("\n【记忆系统配置错误】")
        for error in memory_errors:
            output.append(f"  - {error}")
    
    if personality_errors:
        output.append("\n【人格配置错误】")
        for error in personality_errors:
            if isinstance(error, dict):
                output.append(f"  - {error['main']}")
                for detail in error['details']:
                    output.append(f"{detail}")
            else:
                output.append(f"  - {error}")
    
    if bot_errors:
        output.append("\n【机器人基础配置错误】")
        for error in bot_errors:
            output.append(f"  - {error}")
    
    # 添加机器人信息，即使有错误
    if bot_infos:
        output.append("\n【机器人信息】")
        for info in bot_infos:
            output.append(f"  - {info}")
    
    return "\n".join(output)

def main():
    # 获取配置文件路径
    config_path = Path("config/bot_config.toml")
    env_path = Path(".env.prod")
    
    if not config_path.exists():
        print(f"错误: 找不到配置文件 {config_path}")
        return
    
    if not env_path.exists():
        print(f"警告: 找不到环境变量文件 {env_path}, 将跳过API提供商检查")
        env_vars = {}
    else:
        env_vars = load_env_file(env_path)
    
    # 加载配置文件
    config = load_toml_file(config_path)
    
    # 运行各种检查
    sections_errors = check_required_sections(config)
    prob_sum_errors = check_probability_sum(config)
    prob_range_errors = check_probability_range(config)
    model_errors = check_model_configurations(config, env_vars)
    api_errors = check_api_providers(config, env_vars)
    groups_errors = check_groups_configuration(config)
    kr_errors = check_keywords_reaction(config)
    willing_errors = check_willing_mode(config)
    memory_errors = check_memory_config(config)
    personality_errors = check_personality_config(config)
    bot_results = check_bot_config(config)
    
    # 格式化并打印结果
    all_errors = (sections_errors, prob_sum_errors, prob_range_errors, model_errors, api_errors, groups_errors, kr_errors, willing_errors, memory_errors, personality_errors, bot_results)
    result = format_results(all_errors)
    print("📋 机器人配置检查结果:")
    print(result)
    
    # 综合评估
    total_errors = 0
    
    # 解包bot_results
    bot_errors, _ = bot_results
    
    # 计算普通错误列表的长度
    for errors in [sections_errors, model_errors, api_errors, groups_errors, kr_errors, willing_errors, memory_errors, bot_errors]:
        total_errors += len(errors)
    
    # 计算元组列表的长度（概率相关错误）
    total_errors += len(prob_sum_errors)
    total_errors += len(prob_range_errors)
    
    # 特殊处理personality_errors和groups_errors
    for errors_list in [personality_errors, groups_errors]:
        for error in errors_list:
            if isinstance(error, dict):
                # 每个字典表示一个错误，而不是每行都算一个
                total_errors += 1
            else:
                total_errors += 1
    
    if total_errors > 0:
        print(f"\n总计发现 {total_errors} 个配置问题。")
        print("\n建议：")
        print("1. 修复所有错误后再运行机器人")
        print("2. 特别注意拼写错误，例如不！要！写！错！别！字！！！！！")
        print("3. 确保所有API提供商名称与环境变量中一致")
        print("4. 检查概率值设置，确保总和为1")
    else:
        print("\n您的配置文件完全正确！机器人可以正常运行。")

if __name__ == "__main__":
    main() 
    input("\n按任意键退出...") 