import os
import shutil
import tomlkit
from pathlib import Path


def update_config():
    # 获取根目录路径
    root_dir = Path(__file__).parent.parent
    template_dir = root_dir / "template"
    config_dir = root_dir / "config"

    # 定义文件路径
    template_path = template_dir / "bot_config_template.toml"
    old_config_path = config_dir / "bot_config.toml"
    new_config_path = config_dir / "bot_config.toml"
    action_template_path = template_dir / "actions_template.py"
    action_config_path = config_dir / "actions.py"

    # 如果config里没有actions.py就复制一份过来
    if not action_config_path.exists():
        shutil.copy(action_template_path, action_config_path)
    Path('config/__init__.py').touch()

    # 读取旧配置文件
    old_config = {}
    if old_config_path.exists():
        with open(old_config_path, "r", encoding="utf-8") as f:
            old_config = tomlkit.load(f)

    # 删除旧的配置文件
    if old_config_path.exists():
        os.remove(old_config_path)

    # 复制模板文件到配置目录
    shutil.copy2(template_path, new_config_path)

    # 读取新配置文件
    with open(new_config_path, "r", encoding="utf-8") as f:
        new_config = tomlkit.load(f)

    # 递归更新配置
    def update_dict(target, source):
        for key, value in source.items():
            # 跳过version字段的更新
            if key == "version":
                continue
            if key in target:
                if isinstance(value, dict) and isinstance(target[key], (dict, tomlkit.items.Table)):
                    update_dict(target[key], value)
                else:
                    try:
                        # 对数组类型进行特殊处理
                        if isinstance(value, list):
                            # 如果是空数组，确保它保持为空数组
                            if not value:
                                target[key] = tomlkit.array()
                            else:
                                target[key] = tomlkit.array(value)
                        else:
                            # 其他类型使用item方法创建新值
                            target[key] = tomlkit.item(value)
                    except (TypeError, ValueError):
                        # 如果转换失败，直接赋值
                        target[key] = value

    # 将旧配置的值更新到新配置中
    update_dict(new_config, old_config)

    # 保存更新后的配置（保留注释和格式）
    with open(new_config_path, "w", encoding="utf-8") as f:
        f.write(tomlkit.dumps(new_config))


if __name__ == "__main__":
    update_config()
