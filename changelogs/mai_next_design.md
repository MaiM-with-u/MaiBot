# Mai NEXT 设计文档
Version 0.1.0 - 2025-10-08

## 配置文件设计
- [x] 使用 `toml` 作为配置文件格式
- [x] 合理使用注释说明当前项目
## 消息部分设计
解决原有的将消息类与数据库类存储不匹配的问题，现在存储所有消息类的所有属性
### 消息类设计
- [ ] 支持并使用maim_message新的`SenderInfo`和`ReceiverInfo`构建消息
- [ ] 适配器处理跟进该更新
- [ ] 修复适配器的类型检查问题

## 数据库部分设计
### 消息表设计
### Emoji 表设计
### Images 表设计

## 数据模型部分设计

## 核心业务逻辑部分设计
### Prompt 设计
将Prompt内容彻底模块化设计，同时使用一种专有继承自`TypedDict`的类进行管理
**具体实现**:
- [ ] 设计 Prompt 类，继承自 `TypedDict`
    - [ ] `__init__(self, template: list[str], *, **kwargs)` 维持现有的template设计，但不进行format，直到最后传入LLM时再进行render
    - [ ] `render(self) -> str` 使用非递归渲染方式渲染Prompt内容
    - [ ] `update_block(self, prompt_block: "Prompt")` 将另一个Prompt的内容更新到当前Prompt中
        - [ ] 实现重名属性警告/错误

## 插件系统部分设计
### 插件管理
- [ ] 插件管理器类 `PluginManager` 的更新
    - [ ] 细节待定
- [ ] 组件管理器类 `ComponentManager` 的更新
    - [ ] 细节待定
### 传递内容设计
对于传入的Prompt使用上文提到的Prompt类进行管理，方便内容修改避免正则匹配式查找

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

## 内建WebUI设计
⚠️ **注意**: 本webui设计仅为初步设计，方向为展示内建API的功能，后续应该分离到另外的子项目中完成
### 配置文件编辑
根据API内容完成
### 插件管理
### log viewer
### 状态监控

---

# 内容讨论
- [ ] 适配器插件化: 省下序列化与反序列化，但是失去解耦性质

---

# 依赖管理
已经完成，要点如下：
- 使用 pyproject.toml 和 requirements.txt 管理依赖
- 二者应保持同步修改，同时以 pyproject.toml 为主