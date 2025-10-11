# Mai NEXT 设计文档
Version 0.1.0 - 2025-10-08

## 配置文件设计
- [x] 使用 `toml` 作为配置文件格式
- [x] 合理使用注释说明当前配置作用
## 消息部分设计
解决原有的将消息类与数据库类存储不匹配的问题，现在存储所有消息类的所有属性
### 消息类设计
- [ ] 支持并使用maim_message新的`SenderInfo`和`ReceiverInfo`构建消息
- [ ] 适配器处理跟进该更新
- [ ] 修复适配器的类型检查问题
### 图片处理系统
- [ ] 规范化Emojis与Images的命名，统一保存
### 消息到Prompt的构建（决议）
- [ ] 类QQ的时间系统（即不是每条消息加时间戳，而是分大时间段加时间戳）
- [ ] 消息编号系统（已经有的）
- [ ] 思考打断，如何判定是否打断？
    - [ ] 如何判定消息是连贯的（MoFox: 一个反比例函数？？？太神秘了）

## 数据库部分设计
合并Emojis和Images到同一个表中
### 消息表设计
### Emojis与Images表设计
- [ ] 设计图片专有ID，并作为文件名
### Expressions表设计

## 数据模型部分设计
- [ ] <del>Message从数据库反序列化，不再使用额外的Message类</del>（放弃）

## 核心业务逻辑部分设计
### Prompt 设计
将Prompt内容彻底模块化设计
**具体实现**:
- [ ] 设计 Prompt 类
    - [ ] `__init__(self, template: list[str], *, **kwargs)` 维持现有的template设计，但不进行format，直到最后传入LLM时再进行render
        - [ ] `__init__`中允许传入任意的键值对，存储在`self.context`中
        - [ ] `self.prompt_name` 作为Prompt的名称
    - [ ] `render(self) -> str` 使用非递归渲染方式渲染Prompt内容
    - [ ] `add_block(self, prompt_block: "Prompt", block_name: str)` 将另一个Prompt的内容更新到当前Prompt中
        - [ ] 实现重名属性警告/错误
    - [ ] `remove_block(self, block_name: str)` 移除指定名称的Prompt块
- [ ] 设计 PromptManager 类
    - [ ] `__init__(self)` 初始化一个空的Prompt管理器
    - [ ] `add_prompt(self, name: str, prompt: Prompt)` 添加一个新的Prompt
        - [ ] 实现重名警告/错误
    - [ ] `get_prompt(self, name: str) -> Prompt` 根据名称获取Prompt
        - [ ] 实现不存在时的错误处理
    - [ ] `remove_prompt(self, name: str)` 移除指定名称的Prompt
        - [ ] 系统 Prompt 保护
    - [ ] `list_prompts(self) -> list[str]` 列出所有已添加的Prompt名称

#### Prompt 设计决议讨论
- MoFox 在我和@拾风的讨论中提出把 Prompt 类中传入构造函数以及构造函数所需要的内容

## 插件系统部分设计
### <del>设计一个插件沙盒系统</del>（放弃）
### 插件管理
- [ ] 插件管理器类 `PluginManager` 的更新
    - [ ] 细节待定
- [ ] 组件管理器类 `ComponentManager` 的更新
    - [ ] 细节待定
### 传递内容设计
对于传入的Prompt使用上文提到的Prompt类进行管理，方便内容修改避免正则匹配式查找
### MCP 接入（大饼）
- [ ] 设计 MCP 适配器类 `MCPAdapter`
    - [ ] MCP 调用构建说明Prompt
    - [ ] MCP 调用内容传递
    - [ ] MCP 调用结果处理

## 记忆系统部分设计
启用LPMM系统进行记忆构建，将记忆分类为短期记忆，长期记忆，以及知识
将所有内容放到同一张图上进行运算。
## API 设计（合并到插件系统部分，待定）
主体合并到插件系统部分
### API 设计细则
#### 配置文件
- [ ] 使用`tomlkit`作为配置文件解析方式
- [ ] 解析内容
    - [ ] 注释
    - [ ] 保持原有格式

## LLM UTILS设计
多轮对话设置
### FUNCTION CALLING设计（决议）
对于tools调用将其真正修正为function calling，即返回的结果不是加入prompt形式而是使用function calling的形式
- [ ] 使用 MessageBuilder 构建function call内容
    - [ ] （决议）是否维护使用同一个模型，即选择工具的和调用工具的LLM是否相同
        - [ ] `generate(**kwargs, model: Optional[str] = None)` 允许传入不同的模型
- [ ] 多轮对话中，Prompt不重复构建减少上下文

## 内建WebUI设计
⚠️ **注意**: 本webui设计仅为初步设计，方向为展示内建API的功能，后续应该分离到另外的子项目中完成
### 配置文件编辑
根据API内容完成
### 插件管理
### log viewer
通过特定方式获取日志内容（只读系统，无法将操作反向传递）
### 状态监控
1. Prompt 监控系统
2. 请求监控系统
    - [ ] 请求管理（待讨论）
    - [ ] 使用量
3. 记忆/知识图监控系统（待讨论）

---

# 内容讨论
- [ ] 适配器插件化: 省下序列化与反序列化，但是失去解耦性质
- [ ] 可能的内存泄露问题
    - [ ] 垃圾回收

---

# 依赖管理
已经完成，要点如下：
- 使用 pyproject.toml 和 requirements.txt 管理依赖
- 二者应保持同步修改，同时以 pyproject.toml 为主