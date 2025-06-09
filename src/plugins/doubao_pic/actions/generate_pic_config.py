import os
import toml
from src.common.logger_manager import get_logger

logger = get_logger("pic_config")

CONFIG_CONTENT = """\
# 火山方舟 API 的基础 URL
base_url = "https://ark.cn-beijing.volces.com/api/v3"
# 用于图片生成的API密钥
volcano_generate_api_key = "YOUR_VOLCANO_GENERATE_API_KEY_HERE"
# 默认图片生成模型
default_model = "doubao-seedream-3-0-t2i-250415"
# 默认图片尺寸
default_size = "1024x1024"


# 是否默认开启水印
default_watermark = true
# 默认引导强度
default_guidance_scale = 2.5
# 默认随机种子
default_seed = 42

# 缓存设置
cache_enabled = true
cache_max_size = 10

# 更多插件特定配置可以在此添加...
# custom_parameter = "some_value"
"""

# 默认配置字典，用于验证和修复
DEFAULT_CONFIG = {
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "volcano_generate_api_key": "YOUR_VOLCANO_GENERATE_API_KEY_HERE",
    "default_model": "doubao-seedream-3-0-t2i-250415",
    "default_size": "1024x1024",
    "default_watermark": True,
    "default_guidance_scale": 2.5,
    "default_seed": 42,
    "cache_enabled": True,
    "cache_max_size": 10
}


def validate_and_fix_config(config_path: str) -> bool:
    """验证并修复配置文件"""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = toml.load(f)
        
        # 检查缺失的配置项
        missing_keys = []
        fixed = False
        
        for key, default_value in DEFAULT_CONFIG.items():
            if key not in config:
                missing_keys.append(key)
                config[key] = default_value
                fixed = True
                logger.info(f"添加缺失的配置项: {key} = {default_value}")
        
        # 验证配置值的类型和范围
        if isinstance(config.get("default_guidance_scale"), (int, float)):
            if not 0.1 <= config["default_guidance_scale"] <= 20.0:
                config["default_guidance_scale"] = 2.5
                fixed = True
                logger.info("修复无效的 default_guidance_scale 值")
        
        if isinstance(config.get("default_seed"), (int, float)):
            config["default_seed"] = int(config["default_seed"])
        else:
            config["default_seed"] = 42
            fixed = True
            logger.info("修复无效的 default_seed 值")
        
        if config.get("cache_max_size") and not isinstance(config["cache_max_size"], int):
            config["cache_max_size"] = 10
            fixed = True
            logger.info("修复无效的 cache_max_size 值")
        
        # 如果有修复，写回文件
        if fixed:
            # 创建备份
            backup_path = config_path + ".backup"
            if os.path.exists(config_path):
                os.rename(config_path, backup_path)
                logger.info(f"已创建配置备份: {backup_path}")
            
            # 写入修复后的配置
            with open(config_path, "w", encoding="utf-8") as f:
                toml.dump(config, f)
            logger.info(f"配置文件已修复: {config_path}")
        
        return True
        
    except Exception as e:
        logger.error(f"验证配置文件时出错: {e}")
        return False


def generate_config():
    # 获取当前脚本所在的目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    config_file_path = os.path.join(current_dir, "pic_action_config.toml")

    if not os.path.exists(config_file_path):
        try:
            with open(config_file_path, "w", encoding="utf-8") as f:
                f.write(CONFIG_CONTENT)
            logger.info(f"配置文件已生成: {config_file_path}")
            logger.info("请记得编辑该文件，填入您的火山引擎API 密钥。")
        except IOError as e:
            logger.error(f"错误：无法写入配置文件 {config_file_path}。原因: {e}")
    else:
        # 验证并修复现有配置
        validate_and_fix_config(config_file_path)


if __name__ == "__main__":
    generate_config()
