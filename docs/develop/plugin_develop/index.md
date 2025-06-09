# 如何编写MaiBot插件

## 前言

插件系统目前为v1.0版本，支持Focus和Normal两种聊天模式下的动作扩展。

### 🆕 v1.0 新特性
- **双激活类型系统**：Focus模式智能化，Normal模式高性能
- **并行动作支持**：支持与回复同时执行的动作
- **四种激活类型**：ALWAYS、RANDOM、LLM_JUDGE、KEYWORD
- **智能缓存机制**：提升LLM判定性能
- **模式启用控制**：精确控制插件在不同模式下的行为

插件以**动作(Action)**的形式扩展MaiBot功能。原有的focus模式包含reply和no_reply两种基础动作，通过插件系统可以添加更多自定义动作如mute_action、pic_action等。

**⚠️ 重要变更**：旧的`action_activation_type`属性已被移除，必须使用新的双激活类型系统。详见[迁移指南](#迁移指南)。

## 动作激活系统 🚀

### 双激活类型架构

MaiBot采用**双激活类型架构**，为Focus模式和Normal模式分别提供最优的激活策略：

**Focus模式**：智能优先
- 支持复杂的LLM判定
- 提供精确的上下文理解
- 适合需要深度分析的场景

**Normal模式**：性能优先  
- 使用快速的关键词匹配
- 采用简单的随机触发
- 确保快速响应用户

### 四种激活类型

#### 1. ALWAYS - 总是激活
```python
focus_activation_type = ActionActivationType.ALWAYS
normal_activation_type = ActionActivationType.ALWAYS
```
**用途**：基础必需动作，如`reply_action`、`no_reply_action`

#### 2. KEYWORD - 关键词触发
```python
focus_activation_type = ActionActivationType.KEYWORD
normal_activation_type = ActionActivationType.KEYWORD
activation_keywords = ["画", "画图", "生成图片", "draw"]
keyword_case_sensitive = False
```
**用途**：精确命令式触发，如图片生成、搜索等

#### 3. LLM_JUDGE - 智能判定
```python
focus_activation_type = ActionActivationType.LLM_JUDGE
normal_activation_type = ActionActivationType.KEYWORD  # 推荐Normal模式使用KEYWORD
```
**用途**：需要上下文理解的复杂判定，如情感分析、意图识别

**优化特性**：
- 🚀 并行执行：多个LLM判定同时进行
- 💾 智能缓存：相同上下文复用结果（30秒有效期）
- ⚡ 直接判定：减少复杂度，提升性能

#### 4. RANDOM - 随机激活
```python
focus_activation_type = ActionActivationType.RANDOM
normal_activation_type = ActionActivationType.RANDOM
random_activation_probability = 0.1  # 10%概率
```
**用途**：增加不可预测性和趣味性，如随机表情

### 并行动作系统 🆕

支持动作与回复生成同时执行：

```python
# 并行动作：与回复生成同时执行
parallel_action = True   # 提升用户体验，适用于辅助性动作

# 串行动作：替代回复生成（传统行为）
parallel_action = False  # 默认值，适用于主要内容生成
```

**适用场景**：
- **并行动作**：情感表达、状态变更、TTS播报
- **串行动作**：图片生成、搜索查询、内容创作

### 模式启用控制

```python
from src.chat.chat_mode import ChatMode

mode_enable = ChatMode.ALL      # 在所有模式下启用（默认）
mode_enable = ChatMode.FOCUS    # 仅在Focus模式启用
mode_enable = ChatMode.NORMAL   # 仅在Normal模式启用
```

## 基本步骤

1. 在`src/plugins/你的插件名/actions/`目录下创建插件文件
2. 继承`PluginAction`基类
3. 配置双激活类型和相关属性
4. 实现`process`方法
5. 在`src/plugins/你的插件名/__init__.py`中导入你的插件类

```python
# src/plugins/你的插件名/__init__.py
from .actions.your_action import YourAction

__all__ = ["YourAction"]
```

## 插件结构示例

### 智能自适应插件（推荐）

```python
from src.common.logger_manager import get_logger
from src.chat.focus_chat.planners.actions.plugin_action import PluginAction, register_action, ActionActivationType
from src.chat.chat_mode import ChatMode
from typing import Tuple

logger = get_logger("your_action_name")

@register_action
class YourAction(PluginAction):
    """你的动作描述"""

    action_name = "your_action_name"
    action_description = "这个动作的详细描述，会展示给用户"
    
    # 🆕 双激活类型配置（智能自适应模式）
    focus_activation_type = ActionActivationType.LLM_JUDGE      # Focus模式使用智能判定
    normal_activation_type = ActionActivationType.KEYWORD       # Normal模式使用关键词
    activation_keywords = ["关键词1", "关键词2", "keyword"]
    keyword_case_sensitive = False
    
    # 🆕 模式和并行控制
    mode_enable = ChatMode.ALL      # 支持所有模式
    parallel_action = False         # 根据需要调整
    enable_plugin = True            # 是否启用插件
    
    # 传统配置
    action_parameters = {
        "param1": "参数1的说明（可选）",
        "param2": "参数2的说明（可选）"
    }
    action_require = [
        "使用场景1",
        "使用场景2"
    ]
    default = False

    associated_types = ["text", "command"]

    async def process(self) -> Tuple[bool, str]:
        """插件核心逻辑"""
        # 你的代码逻辑...
        return True, "执行结果"
```

### 关键词触发插件

```python
@register_action
class SearchAction(PluginAction):
    action_name = "search_action"
    action_description = "智能搜索功能"
    
    # 两个模式都使用关键词触发
    focus_activation_type = ActionActivationType.KEYWORD
    normal_activation_type = ActionActivationType.KEYWORD
    activation_keywords = ["搜索", "查找", "什么是", "search", "find"]
    keyword_case_sensitive = False
    
    mode_enable = ChatMode.ALL
    parallel_action = False
    enable_plugin = True
    
    async def process(self) -> Tuple[bool, str]:
        # 搜索逻辑
        return True, "搜索完成"
```

### 并行辅助动作

```python
@register_action
class EmotionAction(PluginAction):
    action_name = "emotion_action"
    action_description = "情感表达动作"
    
    focus_activation_type = ActionActivationType.LLM_JUDGE
    normal_activation_type = ActionActivationType.RANDOM
    random_activation_probability = 0.05  # 5%概率
    
    mode_enable = ChatMode.ALL
    parallel_action = True  # 🆕 与回复并行执行
    enable_plugin = True
    
    async def process(self) -> Tuple[bool, str]:
        # 情感表达逻辑
        return True, ""  # 并行动作通常不返回文本
```

### Focus专享高级功能

```python
@register_action
class AdvancedAnalysisAction(PluginAction):
    action_name = "advanced_analysis"
    action_description = "高级分析功能"
    
    focus_activation_type = ActionActivationType.LLM_JUDGE
    normal_activation_type = ActionActivationType.ALWAYS  # 不会生效
    
    mode_enable = ChatMode.FOCUS  # 🆕 仅在Focus模式启用
    parallel_action = False
    enable_plugin = True
```

## 推荐配置模式

### 模式1：智能自适应（推荐）
```python
# Focus模式智能判定，Normal模式快速触发
focus_activation_type = ActionActivationType.LLM_JUDGE
normal_activation_type = ActionActivationType.KEYWORD
activation_keywords = ["相关", "关键词"]
mode_enable = ChatMode.ALL
parallel_action = False  # 根据具体需求调整
```

### 模式2：统一关键词
```python
# 两个模式都使用关键词，确保行为一致
focus_activation_type = ActionActivationType.KEYWORD
normal_activation_type = ActionActivationType.KEYWORD
activation_keywords = ["画", "图片", "生成"]
mode_enable = ChatMode.ALL
```

### 模式3：Focus专享功能
```python
# 仅在Focus模式启用的高级功能
focus_activation_type = ActionActivationType.LLM_JUDGE
mode_enable = ChatMode.FOCUS
parallel_action = False
```

### 模式4：随机娱乐功能
```python
# 增加趣味性的随机功能
focus_activation_type = ActionActivationType.RANDOM
normal_activation_type = ActionActivationType.RANDOM
random_activation_probability = 0.08  # 8%概率
mode_enable = ChatMode.ALL
parallel_action = True  # 通常与回复并行
```

## 可用的API方法

插件可以使用`PluginAction`基类提供的以下API：

### 1. 直接发送消息

```python
#发送文本
await self.send_message(type="text", data="你好")

#发送图片
await self.send_message(type="image", data=base64_image_string)

#发送命令（需要adapter支持）
await self.send_message(
    type="command",
    data={"name": "GROUP_BAN", "args": {"qq_id": str(user_id), "duration": duration_str}},
    display_message=f"我 禁言了 {target} {duration_str}秒",
)
```

### 2. 使用表达器发送消息

```python
await self.send_message_by_expressor("你好")
await self.send_message_by_expressor(f"禁言{target} {duration}秒，因为{reason}")
```

### 3. 获取聊天类型

```python
chat_type = self.get_chat_type()  # 返回 "group" 或 "private" 或 "unknown"
```

### 4. 获取最近消息

```python
messages = self.get_recent_messages(count=5)  # 获取最近5条消息
# 返回格式: [{"sender": "发送者", "content": "内容", "timestamp": 时间戳}, ...]
```

### 5. 获取动作参数

```python
param_value = self.action_data.get("param_name", "默认值")
```

### 6. 获取可用模型

```python
models = self.get_available_models()  # 返回所有可用的模型配置
# 返回格式: {"model_name": {"config": "value", ...}, ...}
```

### 7. 使用模型生成内容

```python
success, response, reasoning, model_name = await self.generate_with_model(
    prompt="你的提示词",
    model_config=models["model_name"],  # 从get_available_models获取的模型配置
    max_tokens=2000,  # 可选，最大生成token数
    request_type="plugin.generate",  # 可选，请求类型标识
    temperature=0.7,  # 可选，温度参数
    # 其他模型特定参数...
)
```

### 8. 获取用户ID

```python
platform, user_id = await self.get_user_id_by_person_name("用户名")
```

### 日志记录

```python
logger.info(f"{self.log_prefix} 你的日志信息")
logger.warning("警告信息")
logger.error("错误信息")
```

## 返回值说明

`process`方法必须返回一个元组，包含两个元素：

- 第一个元素(bool): 表示动作是否执行成功
- 第二个元素(str): 执行结果的文本描述（可以为空""）

```python
return True, "执行成功的消息"
# 或
return False, "执行失败的原因"
```

## 性能优化建议

### 1. 激活类型选择
- **ALWAYS**：仅用于基础必需动作
- **KEYWORD**：明确的命令式动作，性能最佳
- **LLM_JUDGE**：复杂判断，建议仅在Focus模式使用
- **RANDOM**：娱乐功能，低概率触发

### 2. 双模式配置
- **智能自适应**：Focus用LLM_JUDGE，Normal用KEYWORD（推荐）
- **性能优先**：两个模式都用KEYWORD或RANDOM
- **功能分离**：高级功能仅在Focus模式启用

### 3. 并行动作使用
- **parallel_action = True**：辅助性、非内容生成类动作
- **parallel_action = False**：主要内容生成、需要完整注意力的动作

### 4. LLM判定优化
- 编写清晰的激活条件描述
- 避免过于复杂的逻辑判断
- 利用智能缓存机制（自动）
- Normal模式避免使用LLM_JUDGE

### 5. 关键词设计
- 包含同义词和英文对应词
- 考虑用户的不同表达习惯
- 避免过于宽泛的关键词
- 根据实际使用调整覆盖率

## 迁移指南 ⚠️

### 重大变更说明
**旧的 `action_activation_type` 属性已被移除**，必须更新为新的双激活类型系统。

### 快速迁移步骤

#### 第一步：更新基本属性
```python
# 旧的配置（已废弃）❌
class OldAction(BaseAction):
    action_activation_type = ActionActivationType.LLM_JUDGE

# 新的配置（必须使用）✅
class NewAction(BaseAction):
    focus_activation_type = ActionActivationType.LLM_JUDGE
    normal_activation_type = ActionActivationType.KEYWORD
    activation_keywords = ["相关", "关键词"]
    mode_enable = ChatMode.ALL
    parallel_action = False
    enable_plugin = True
```

#### 第二步：根据原类型选择对应策略
```python
# 原来是 ALWAYS
focus_activation_type = ActionActivationType.ALWAYS
normal_activation_type = ActionActivationType.ALWAYS

# 原来是 LLM_JUDGE  
focus_activation_type = ActionActivationType.LLM_JUDGE
normal_activation_type = ActionActivationType.KEYWORD  # 添加关键词
activation_keywords = ["需要", "添加", "关键词"]

# 原来是 KEYWORD
focus_activation_type = ActionActivationType.KEYWORD
normal_activation_type = ActionActivationType.KEYWORD
# 保持原有的 activation_keywords

# 原来是 RANDOM
focus_activation_type = ActionActivationType.RANDOM
normal_activation_type = ActionActivationType.RANDOM
# 保持原有的 random_activation_probability
```

#### 第三步：配置新功能
```python
# 添加模式控制
mode_enable = ChatMode.ALL  # 或 ChatMode.FOCUS / ChatMode.NORMAL

# 添加并行控制  
parallel_action = False  # 根据动作特性选择True/False

# 添加插件控制
enable_plugin = True  # 是否启用此插件
```

## 最佳实践

### 1. 代码组织
- 使用清晰的`action_description`描述功能
- 使用`action_parameters`定义所需参数
- 使用`action_require`描述使用场景
- 使用`logger`记录重要信息，方便调试

### 2. 性能考虑
- 优先使用KEYWORD触发，性能最佳
- Normal模式避免使用LLM_JUDGE
- 合理设置随机概率（0.05-0.3）
- 利用智能缓存机制（自动优化）

### 3. 用户体验
- 并行动作提升响应速度
- 关键词覆盖用户常用表达
- 错误处理和友好提示
- 避免操作底层系统

### 4. 兼容性
- 支持中英文关键词
- 考虑不同聊天模式的用户需求
- 提供合理的默认配置
- 向后兼容旧版本用户习惯

## 注册与加载

插件会在系统启动时自动加载，只要：
1. 放在正确的目录结构中
2. 添加了`@register_action`装饰器
3. 在`__init__.py`中正确导入

若设置`default = True`，插件会自动添加到默认动作集并启用，否则默认只加载不启用。

## 调试和测试

### 性能监控
系统会自动记录以下性能指标：
```python
logger.debug(f"激活判定：{before_count} -> {after_count} actions")
logger.debug(f"并行LLM判定完成，耗时: {duration:.2f}s")  
logger.debug(f"使用缓存结果 {action_name}: {'激活' if result else '未激活'}")
```

### 测试验证
使用测试脚本验证配置：
```bash
python test_action_activation.py
```

该脚本会显示：
- 所有注册动作的双激活类型配置
- 模拟不同模式下的激活结果
- 并行动作系统的工作状态
- 帮助验证配置是否正确

## 系统优势

### 1. 高性能
- **并行判定**：多个LLM判定同时进行
- **智能缓存**：避免重复计算
- **双模式优化**：Focus智能化，Normal快速化
- **预期性能提升**：3-5x

### 2. 智能化
- **上下文感知**：基于聊天内容智能激活
- **动态配置**：从动作配置中收集关键词
- **冲突避免**：防止重复激活
- **模式自适应**：根据聊天模式选择最优策略

### 3. 可扩展性
- **插件式**：新的激活类型易于添加
- **配置驱动**：通过配置控制行为
- **模块化**：各组件独立可测试
- **双模式支持**：灵活适应不同使用场景

### 4. 用户体验
- **响应速度**：显著提升机器人反应速度
- **智能决策**：精确理解用户意图
- **交互流畅**：并行动作减少等待时间
- **适应性强**：不同模式满足不同需求

这个升级后的插件系统为MaiBot提供了强大而灵活的扩展能力，既保证了性能，又提供了智能化的用户体验。
