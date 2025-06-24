# 🚀 快速开始指南

本指南将带你用5分钟时间，从零开始创建一个功能完整的MaiCore插件。

## 📖 概述

这个指南将带你快速创建你的第一个MaiCore插件。我们将创建一个简单的问候插件，展示插件系统的基本概念。无需阅读其他文档，跟着本指南就能完成！

## 🎯 学习目标

- 理解插件的基本结构
- 从最简单的插件开始，循序渐进
- 学会创建Action组件（智能动作）
- 学会创建Command组件（命令响应）
- 掌握配置Schema定义和配置文件自动生成（可选）

## 📂 准备工作

确保你已经：

1. 克隆了MaiCore项目
2. 安装了Python依赖
3. 了解基本的Python语法

## 🏗️ 创建插件

### 1. 创建插件目录

在项目根目录的 `plugins/` 文件夹下创建你的插件目录，目录名与插件名保持一致：

可以用以下命令快速创建：

```bash
mkdir plugins/hello_world_plugin
cd plugins/hello_world_plugin
```

### 2. 创建最简单的插件

让我们从最基础的开始！创建 `plugin.py` 文件：

```python
from typing import List, Tuple, Type
from src.plugin_system import BasePlugin, register_plugin, ComponentInfo

# ===== 插件注册 =====

@register_plugin
class HelloWorldPlugin(BasePlugin):
    """Hello World插件 - 你的第一个MaiCore插件"""

    # 插件基本信息（必须填写）
    plugin_name = "hello_world_plugin"
    plugin_description = "我的第一个MaiCore插件"
    plugin_version = "1.0.0"
    plugin_author = "你的名字"
    enable_plugin = True  # 启用插件

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表（目前是空的）"""
        return []
```

🎉 **恭喜！你刚刚创建了一个最简单但完整的MaiCore插件！**

**解释一下这些代码：**

- 首先，我们在plugin.py中定义了一个HelloWorldPulgin插件类，继承自 `BasePlugin` ，提供基本功能。
- 通过给类加上，`@register_plugin` 装饰器，我们告诉系统"这是一个插件"
- `plugin_name` 等是插件的基本信息，必须填写，**此部分必须与目录名称相同，否则插件无法使用**
- `get_plugin_components()` 返回插件的功能组件，现在我们没有定义任何action（动作）或者command(指令)，是空的

### 3. 测试基础插件

现在就可以测试这个插件了！启动MaiCore：

直接通过启动器运行MaiCore或者 `python bot.py`

在日志中你应该能看到插件被加载的信息。虽然插件还没有任何功能，但它已经成功运行了！

![1750326700269](image/quick-start/1750326700269.png)

### 4. 添加第一个功能：问候Action

现在我们要给插件加入一个有用的功能，我们从最好玩的Action做起

Action是一类可以让MaiCore根据自身意愿选择使用的“动作”，在MaiCore中，不论是“回复”还是“不回复”，或者“发送表情”以及“禁言”等等，都是通过Action实现的。

你可以通过编写动作，来拓展MaiCore的能力，包括发送语音，截图，甚至操作文件，编写代码......

现在让我们给插件添加第一个简单的功能。这个Action可以对用户发送一句问候语。

在 `plugin.py` 文件中添加Action组件，完整代码如下：

```python
from typing import List, Tuple, Type
from src.plugin_system import (
    BasePlugin, register_plugin, BaseAction, 
    ComponentInfo, ActionActivationType, ChatMode
)

# ===== Action组件 =====

class HelloAction(BaseAction):
    """问候Action - 简单的问候动作"""

    # === 基本信息（必须填写）===
    action_name = "hello_greeting"
    action_description = "向用户发送问候消息"

    # === 功能描述（必须填写）===
    action_parameters = {
        "greeting_message": "要发送的问候消息"
    }
    action_require = [
        "需要发送友好问候时使用",
        "当有人向你问好时使用",
        "当你遇见没有见过的人时使用"
        ]
    associated_types = ["text"]

    async def execute(self) -> Tuple[bool, str]:
        """执行问候动作 - 这是核心功能"""
        # 发送问候消息
        greeting_message = self.action_data.get("greeting_message","")
  
        message = "嗨！很开心见到你！😊" + greeting_message
        await self.send_text(message)

        return True, "发送了问候消息"

# ===== 插件注册 =====

@register_plugin
class HelloWorldPlugin(BasePlugin):
    """Hello World插件 - 你的第一个MaiCore插件"""

    # 插件基本信息
    plugin_name = "hello_world_plugin"
    plugin_description = "我的第一个MaiCore插件，包含问候功能"
    plugin_version = "1.0.0"
    plugin_author = "你的名字"
    enable_plugin = True

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表"""
        return [
            # 添加我们的问候Action
            (HelloAction.get_action_info(), HelloAction),
        ]
```

**新增内容解释：**

- `HelloAction` 是一个Action组件，MaiCore可能会选择使用它
- `execute()` 函数是Action的核心，定义了当Action被MaiCore选择后，具体要做什么
- `self.send_text()` 是发送文本消息的便捷方法

### 5. 测试问候功能

重启MaiCore，然后在聊天中发送任意消息，比如：

```
你好
```

MaiCore可能会选择使用你的问候Action，发送回复：

```
嗨！很开心见到你！😊
```

![1750332508760](image/quick-start/1750332508760.png)

> **💡 小提示**：MaiCore会智能地决定什么时候使用它。如果没有立即看到效果，多试几次不同的消息。

🎉 **太棒了！你的插件已经有实际功能了！**

### 5.5. 了解激活系统（重要概念）

Action固然好用简单，但是现在有个问题，当用户加载了非常多的插件，添加了很多自定义Action，LLM需要选择的Action也会变多

而不断增多的Action会加大LLM的消耗和负担，降低Action使用的精准度。而且我们并不需要LLM在所有时候都考虑所有Action

例如，当群友只是在进行正常的聊天，就没有必要每次都考虑是否要选择“禁言”动作，这不仅影响决策速度，还会增加消耗。

那有什么办法，能够让Action有选择的加入MaiCore的决策池呢？

**什么是激活系统？**
激活系统决定了什么时候你的Action会被MaiCore"考虑"使用：

- **`ActionActivationType.ALWAYS`** - 总是可用（默认值）
- **`ActionActivationType.KEYWORD`** - 只有消息包含特定关键词时才可用
- **`ActionActivationType.PROBABILITY`** - 根据概率随机可用
- **`ActionActivationType.NEVER`** - 永不可用（用于调试）

> **💡 使用提示**：
>
> - 推荐使用枚举类型（如 `ActionActivationType.ALWAYS`），有代码提示和类型检查
> - 也可以直接使用字符串（如 `"always"`），系统都支持

### 5.6. 进阶：尝试关键词激活（可选）

现在让我们尝试一个更精确的激活方式！添加一个只在用户说特定关键词时才激活的Action：

```python
# 在HelloAction后面添加这个新Action
class ByeAction(BaseAction):
    """告别Action - 只在用户说再见时激活"""
  
    action_name = "bye_greeting"
    action_description = "向用户发送告别消息"
  
    # 使用关键词激活
    focus_activation_type = ActionActivationType.KEYWORD
    normal_activation_type = ActionActivationType.KEYWORD
  
    # 关键词设置
    activation_keywords = ["再见", "bye", "88", "拜拜"]
    keyword_case_sensitive = False
  
    action_parameters = {"bye_message": "要发送的告别消息"}
    action_require = [
        "用户要告别时使用",
        "当有人要离开时使用",
        "当有人和你说再见时使用",
        ]
    associated_types = ["text"]
  
    async def execute(self) -> Tuple[bool, str]:
        bye_message = self.action_data.get("bye_message","")
  
        message = "再见！期待下次聊天！👋" + bye_message
        await self.send_text(message)
        return True, "发送了告别消息"
```

然后在插件注册中添加这个Action：

```python
def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
    return [
        (HelloAction.get_action_info(), HelloAction),
        (ByeAction.get_action_info(), ByeAction),  # 添加告别Action
    ]
```

现在测试：发送"再见"，应该会触发告别Action！

**关键词激活的特点：**

- 更精确：只在包含特定关键词时才会被考虑
- 更可预测：用户知道说什么会触发什么功能
- 更适合：特定场景或命令式的功能

### 6. 添加第二个功能：时间查询Command

现在让我们添加一个Command组件。Command和Action不同，它是直接响应用户命令的：

Command是最简单，最直接的相应，不由LLM判断选择使用

```python
# 在现有代码基础上，添加Command组件

# ===== Command组件 =====

from src.plugin_system import BaseCommand
#导入Command基类

class TimeCommand(BaseCommand):
    """时间查询Command - 响应/time命令"""

    command_name = "time"
    command_description = "查询当前时间"

    # === 命令设置（必须填写）===
    command_pattern = r"^/time$"  # 精确匹配 "/time" 命令
    command_help = "查询当前时间"
    command_examples = ["/time"]
    intercept_message = True  # 拦截消息，不让其他组件处理

    async def execute(self) -> Tuple[bool, str]:
        """执行时间查询"""
        import datetime
  
        # 获取当前时间
        time_format = self.get_config("time.format", "%Y-%m-%d %H:%M:%S")
        now = datetime.datetime.now()
        time_str = now.strftime(time_format)
  
        # 发送时间信息
        message = f"⏰ 当前时间：{time_str}"
        await self.send_text(message)
  
        return True, f"显示了当前时间: {time_str}"

# ===== 插件注册 =====

@register_plugin
class HelloWorldPlugin(BasePlugin):
    """Hello World插件 - 你的第一个MaiCore插件"""

    plugin_name = "hello_world_plugin"
    plugin_description = "我的第一个MaiCore插件，包含问候和时间查询功能"
    plugin_version = "1.0.0"
    plugin_author = "你的名字"
    enable_plugin = True

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        return [
            (HelloAction.get_action_info(), HelloAction),
            (ByeAction.get_action_info(), ByeAction), 
            (TimeCommand.get_command_info(), TimeCommand),
        ]
```

**Command组件解释：**

- Command是直接响应用户命令的组件
- `command_pattern` 使用正则表达式匹配用户输入
- `^/time$` 表示精确匹配 "/time"
- `intercept_message = True` 表示处理完命令后不再让其他组件处理

### 7. 测试时间查询功能

重启MaiCore，发送命令：

```
/time
```

你应该会收到回复：

```
⏰ 当前时间：2024-01-01 12:30:45
```

🎉 **太棒了！现在你的插件有3个功能了！**

### 8. 添加配置文件（可选进阶）

如果你想让插件更加灵活，可以添加配置支持。

> **🚨 重要：不要手动创建config.toml文件！**
>
> 我们需要在插件代码中定义配置Schema，让系统自动生成配置文件。

#### 📄 配置架构说明

在新的插件系统中，我们采用了**职责分离**的设计：

- **`_manifest.json`** - 插件元数据（名称、版本、描述、作者等）
- **`config.toml`** - 运行时配置（启用状态、功能参数等）

这样避免了信息重复，提高了维护性。

首先，在插件类中定义配置Schema：

```python
from src.plugin_system.base.config_types import ConfigField

@register_plugin
class HelloWorldPlugin(BasePlugin):
    """Hello World插件 - 你的第一个MaiCore插件"""

    plugin_name = "hello_world_plugin"
    plugin_description = "我的第一个MaiCore插件，包含问候和时间查询功能"
    plugin_version = "1.0.0"
    plugin_author = "你的名字"
    enable_plugin = True
    config_file_name = "config.toml"  # 配置文件名

    # 配置节描述
    config_section_descriptions = {
        "plugin": "插件启用配置",
        "greeting": "问候功能配置",
        "time": "时间查询配置"
    }

    # 配置Schema定义
    config_schema = {
        "plugin": {
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件")
        },
        "greeting": {
            "message": ConfigField(
                type=str, 
                default="嗨！很开心见到你！😊", 
                description="默认问候消息"
            ),
            "enable_emoji": ConfigField(type=bool, default=True, description="是否启用表情符号")
        },
        "time": {
            "format": ConfigField(
                type=str, 
                default="%Y-%m-%d %H:%M:%S", 
                description="时间显示格式"
            )
        }
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        return [
            (HelloAction.get_action_info(), HelloAction),
            (ByeAction.get_action_info(), ByeAction), 
            (TimeCommand.get_command_info(), TimeCommand),
        ]
```

然后修改Action和Command代码，让它们读取配置：

```python
# 在HelloAction的execute方法中：
async def execute(self) -> Tuple[bool, str]:
    # 从配置文件读取问候消息
    greeting_message = self.action_data.get("greeting_message", "")
    base_message = self.get_config("greeting.message", "嗨！很开心见到你！😊")
  
    message = base_message + greeting_message
    await self.send_text(message)
    return True, "发送了问候消息"

# 在TimeCommand的execute方法中：
async def execute(self) -> Tuple[bool, str]:
    import datetime
  
    # 从配置文件读取时间格式
    time_format = self.get_config("time.format", "%Y-%m-%d %H:%M:%S")
    now = datetime.datetime.now()
    time_str = now.strftime(time_format)
  
    message = f"⏰ 当前时间：{time_str}"
    await self.send_text(message)
    return True, f"显示了当前时间: {time_str}"
```

**配置系统工作流程：**

1. **定义Schema**: 在插件代码中定义配置结构
2. **自动生成**: 启动插件时，系统会自动生成 `config.toml` 文件
3. **用户修改**: 用户可以修改生成的配置文件
4. **代码读取**: 使用 `self.get_config()` 读取配置值

**配置功能解释：**

- `self.get_config()` 可以读取配置文件中的值
- 第一个参数是配置路径（用点分隔），第二个参数是默认值
- 配置文件会包含详细的注释和说明，用户可以轻松理解和修改
- **绝不要手动创建配置文件**，让系统自动生成

### 9. 创建说明文档（可选）

创建 `README.md` 文件来说明你的插件：

```markdown
# Hello World 插件

## 概述
我的第一个MaiCore插件，包含问候和时间查询功能。

## 功能
- **问候功能**: 当用户说"你好"、"hello"、"hi"时自动回复
- **时间查询**: 发送 `/time` 命令查询当前时间

## 使用方法
### 问候功能
发送包含以下关键词的消息：
- "你好"
- "hello" 
- "hi"

### 时间查询
发送命令：`/time`

## 配置文件
插件会自动生成 `config.toml` 配置文件，用户可以修改：
- 问候消息内容
- 时间显示格式
- 插件启用状态

注意：配置文件是自动生成的，不要手动创建！
```


```

```
