# 修正后的动作激活架构

## 架构原则

### 正确的职责分工
- **主循环 (`modify_actions`)**: 负责完整的动作管理，包括传统观察处理和新的激活类型判定
- **规划器 (`Planner`)**: 专注于从最终确定的动作集中进行决策，不再处理动作筛选

### 关注点分离
- **动作管理** → 主循环处理
- **决策制定** → 规划器处理
- **配置解析** → ActionManager处理

## 修正后的调用流程

### 1. 主循环阶段 (heartFC_chat.py)

```python
# 在主循环中调用完整的动作管理流程
async def modify_actions_task():
    # 提取聊天上下文信息
    observed_messages_str = ""
    chat_context = ""
    
    for obs in self.observations:
        if hasattr(obs, 'get_talking_message_str_truncate'):
            observed_messages_str = obs.get_talking_message_str_truncate()
        elif hasattr(obs, 'get_chat_type'):
            chat_context = f"聊天类型: {obs.get_chat_type()}"
    
    # 调用完整的动作修改流程
    await self.action_modifier.modify_actions(
        observations=self.observations,
        observed_messages_str=observed_messages_str,
        chat_context=chat_context,
        extra_context=extra_context
    )
```

**处理内容:**
- 传统观察处理（循环历史分析、类型匹配等）
- 双激活类型判定（Focus模式和Normal模式分别处理）
- 并行LLM判定
- 智能缓存
- 动态关键词收集

### 2. 规划器阶段 (planner_simple.py)

```python
# 规划器直接获取最终的动作集
current_available_actions_dict = self.action_manager.get_using_actions()

# 获取完整的动作信息
all_registered_actions = self.action_manager.get_registered_actions()
current_available_actions = {}
for action_name in current_available_actions_dict.keys():
    if action_name in all_registered_actions:
        current_available_actions[action_name] = all_registered_actions[action_name]
```

**处理内容:**
- 仅获取经过完整处理的最终动作集
- 专注于从可用动作中进行决策
- 不再处理动作筛选逻辑

## 核心优化功能

### 1. 并行LLM判定
```python
# 同时判定多个LLM_JUDGE类型的动作
task_results = await asyncio.gather(*tasks, return_exceptions=True)
```

### 2. 智能缓存系统
```python
# 基于上下文哈希的缓存机制
cache_key = f"{action_name}_{context_hash}"
if cache_key in self._llm_judge_cache:
    return cached_result
```

### 3. 直接LLM判定
```python
# 直接对所有LLM_JUDGE类型的动作进行并行判定
llm_results = await self._process_llm_judge_actions_parallel(llm_judge_actions, ...)
```

### 4. 动态关键词收集
```python
# 从动作配置中动态收集关键词，避免硬编码
for action_name, action_info in llm_judge_actions.items():
    keywords = action_info.get("activation_keywords", [])
    if keywords:
        # 检查消息中的关键词匹配
```

## 双激活类型系统 🆕

### 系统设计理念
**Focus模式** 和 **Normal模式** 采用不同的激活策略：
- **Focus模式**: 智能化优先，支持复杂的LLM判定
- **Normal模式**: 性能优先，使用快速的关键词和随机触发

### 双激活类型配置
```python
class MyAction(BaseAction):
    action_name = "my_action"
    action_description = "我的动作"
    
    # Focus模式激活类型（支持LLM_JUDGE）
    focus_activation_type = ActionActivationType.LLM_JUDGE
    
    # Normal模式激活类型（建议使用KEYWORD/RANDOM/ALWAYS）
    normal_activation_type = ActionActivationType.KEYWORD
    activation_keywords = ["关键词1", "keyword"]
    
    # 模式启用控制
    mode_enable = ChatMode.ALL  # 在所有模式下启用
    
    # 并行执行控制
    parallel_action = False  # 是否与回复并行执行
```

### 模式启用类型 (ChatMode)
```python
from src.chat.chat_mode import ChatMode

# 可选值：
mode_enable = ChatMode.FOCUS  # 仅在Focus模式启用
mode_enable = ChatMode.NORMAL  # 仅在Normal模式启用
mode_enable = ChatMode.ALL     # 在所有模式启用（默认）
```

### 并行动作系统 🆕
```python
# 并行动作：可以与回复生成同时进行
parallel_action = True   # 不会阻止回复生成

# 串行动作：会替代回复生成
parallel_action = False  # 默认值，传统行为
```

**并行动作的优势：**
- 提升用户体验（同时获得回复和动作执行）
- 减少响应延迟
- 适用于情感表达、状态变更等辅助性动作

## 四种激活类型

### 1. ALWAYS - 始终激活
```python
focus_activation_type = ActionActivationType.ALWAYS
normal_activation_type = ActionActivationType.ALWAYS
# 基础动作，如 reply, no_reply
```

### 2. RANDOM - 随机激活
```python
focus_activation_type = ActionActivationType.RANDOM
normal_activation_type = ActionActivationType.RANDOM
random_probability = 0.3  # 激活概率
# 用于增加惊喜元素，如随机表情
```

### 3. LLM_JUDGE - 智能判定
```python
focus_activation_type = ActionActivationType.LLM_JUDGE
# 注意：Normal模式不建议使用LLM_JUDGE，会发出警告
normal_activation_type = ActionActivationType.KEYWORD
# 需要理解上下文的复杂动作，如情感表达
```

### 4. KEYWORD - 关键词触发
```python
focus_activation_type = ActionActivationType.KEYWORD
normal_activation_type = ActionActivationType.KEYWORD
activation_keywords = ["画", "图片", "生成"]
# 明确指令触发的动作，如图片生成
```

## 推荐配置模式

### 模式1：智能自适应
```python
# Focus模式使用智能判定，Normal模式使用关键词
focus_activation_type = ActionActivationType.LLM_JUDGE
normal_activation_type = ActionActivationType.KEYWORD
activation_keywords = ["相关", "关键词"]
```

### 模式2：统一关键词
```python
# 两个模式都使用关键词，确保一致性
focus_activation_type = ActionActivationType.KEYWORD
normal_activation_type = ActionActivationType.KEYWORD
activation_keywords = ["画", "图片", "生成"]
```

### 模式3：Focus专享
```python
# 仅在Focus模式启用的智能功能
focus_activation_type = ActionActivationType.LLM_JUDGE
normal_activation_type = ActionActivationType.ALWAYS  # 不会生效
mode_enable = ChatMode.FOCUS
```

## 性能提升

### 理论性能改进
- **并行LLM判定**: 1.5-2x 提升
- **智能缓存**: 20-30% 额外提升
- **双模式优化**: Normal模式额外1.5x提升
- **整体预期**: 3-5x 性能提升

### 缓存策略
- **缓存键**: `{action_name}_{context_hash}`
- **过期时间**: 30秒
- **哈希算法**: MD5 (消息内容+上下文)

## 向后兼容性

### ⚠️ 重大变更说明
**旧的 `action_activation_type` 属性已被移除**，必须更新为新的双激活类型系统：

#### 迁移指南
```python
# 旧的配置（已废弃）
class OldAction(BaseAction):
    action_activation_type = ActionActivationType.LLM_JUDGE  # ❌ 已移除

# 新的配置（必须使用）
class NewAction(BaseAction):
    focus_activation_type = ActionActivationType.LLM_JUDGE    # ✅ Focus模式
    normal_activation_type = ActionActivationType.KEYWORD     # ✅ Normal模式
    activation_keywords = ["相关", "关键词"]
    mode_enable = ChatMode.ALL
    parallel_action = False
```

#### 快速迁移脚本
对于简单的迁移，可以使用以下模式：
```python
# 如果原来是 ALWAYS
focus_activation_type = ActionActivationType.ALWAYS
normal_activation_type = ActionActivationType.ALWAYS

# 如果原来是 LLM_JUDGE
focus_activation_type = ActionActivationType.LLM_JUDGE
normal_activation_type = ActionActivationType.KEYWORD  # 需要添加关键词

# 如果原来是 KEYWORD
focus_activation_type = ActionActivationType.KEYWORD
normal_activation_type = ActionActivationType.KEYWORD

# 如果原来是 RANDOM
focus_activation_type = ActionActivationType.RANDOM
normal_activation_type = ActionActivationType.RANDOM
```

## 测试验证

### 运行测试
```bash
python test_corrected_architecture.py
```

### 测试内容
- 双激活类型系统验证
- 数据一致性检查
- 职责分离确认
- 性能测试
- 向后兼容性验证
- 并行动作功能验证

## 优势总结

### 1. 清晰的架构
- **单一职责**: 每个组件专注于自己的核心功能
- **关注点分离**: 动作管理与决策制定分离
- **可维护性**: 逻辑清晰，易于理解和修改

### 2. 高性能
- **并行处理**: 多个LLM判定同时进行
- **智能缓存**: 避免重复计算
- **双模式优化**: Focus智能化，Normal快速化

### 3. 智能化
- **动态配置**: 从动作配置中收集关键词
- **上下文感知**: 基于聊天内容智能激活
- **冲突避免**: 防止重复激活
- **模式自适应**: 根据聊天模式选择最优策略

### 4. 可扩展性
- **插件式**: 新的激活类型易于添加
- **配置驱动**: 通过配置控制行为
- **模块化**: 各组件独立可测试
- **双模式支持**: 灵活适应不同使用场景

这个修正后的架构实现了正确的职责分工，确保了主循环负责动作管理，规划器专注于决策，同时集成了双激活类型、并行判定和智能缓存等优化功能。 