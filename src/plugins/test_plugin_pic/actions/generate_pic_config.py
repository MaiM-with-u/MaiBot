import os

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

# 更多插件特定配置可以在此添加...
# custom_parameter = "some_value"
"""


def generate_config():
    # 获取当前脚本所在的目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    config_file_path = os.path.join(current_dir, "pic_action_config.toml")

    if not os.path.exists(config_file_path):
        try:
            with open(config_file_path, "w", encoding="utf-8") as f:
                f.write(CONFIG_CONTENT)
            print(f"配置文件已生成: {config_file_path}")
            print("请记得编辑该文件，填入您的火山引擎API 密钥。")
        except IOError as e:
            print(f"错误：无法写入配置文件 {config_file_path}。原因: {e}")
    else:
        print(f"配置文件已存在: {config_file_path}")
        print("未进行任何更改。如果您想重新生成，请先删除或重命名现有文件。")


if __name__ == "__main__":
    generate_config()
