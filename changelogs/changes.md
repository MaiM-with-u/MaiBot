# 插件API与规范修改

1. 现在`plugin_system`的`__init__.py`文件中包含了所有插件API的导入，用户可以直接使用`from src.plugin_system import *`来导入所有API。

2. register_plugin函数现在转移到了`plugin_system.apis.plugin_register_api`模块中，用户可以通过`from src.plugin_system.apis.plugin_register_api import register_plugin`来导入。
  - 顺便一提，按照1中说法，你可以这么用：
    ```python
    from src.plugin_system import register_plugin
    ```

3. 现在强制要求的property如下，即你必须覆盖的属性有：
  - `plugin_name`: 插件名称，必须是唯一的。（与文件夹相同）
  - `enable_plugin`: 是否启用插件，默认为`True`。
  - `dependencies`: 插件依赖的其他插件列表，默认为空。**现在并不检查（也许）**
  - `python_dependencies`: 插件依赖的Python包列表，默认为空。**现在并不检查**
  - `config_file_name`: 插件配置文件名，默认为`config.toml`。
  - `config_schema`: 插件配置文件的schema，用于自动生成配置文件。
4. 部分API的参数类型和返回值进行了调整
  - `chat_api.py`中获取流的参数中可以使用一个特殊的枚举类型来获得所有平台的 ChatStream 了。
  - `config_api.py`中的`get_global_config`和`get_plugin_config`方法现在支持嵌套访问的配置键名。
  - `database_api.py`中的`db_query`方法调整了参数顺序以增强参数限制的同时，保证了typing正确；`db_get`方法增加了`single_result`参数，与`db_query`保持一致。
5. 增加了`logging_api`，可以用`get_logger`来获取日志记录器。
6. 增加了插件和组件管理的API。
7. `BaseCommand`的`execute`方法现在返回一个三元组，包含是否执行成功、可选的回复消息和是否拦截消息。
  - 这意味着你终于可以动态控制是否继续后续消息的处理了。
8. 移除了dependency_manager，但是依然保留了`python_dependencies`属性，等待后续重构。
  - 一并移除了文档有关manager的内容。
9. 增加了工具的有关api

# 插件系统修改
1. 现在所有的匹配模式不再是关键字了，而是枚举类。**（可能有遗漏）**
2. 修复了一下显示插件信息不显示的问题。同时精简了一下显示内容
3. 修复了插件系统混用了`plugin_name`和`display_name`的问题。现在所有的插件信息都使用`display_name`来显示，而内部标识仍然使用`plugin_name`。
4. 现在增加了参数类型检查，完善了对应注释
5. 现在插件抽象出了总基类 `PluginBase`
  - <del>基于`Action`和`Command`的插件基类现在为`BasePlugin`。</del>
  - <del>基于`Event`的插件基类现在为`BaseEventPlugin`。</del>
  - 基于`Action`，`Command`和`Event`的插件基类现在为`BasePlugin`，所有插件都应该继承此基类。
  - `BasePlugin`继承自`PluginBase`。
  - 所有的插件类都由`register_plugin`装饰器注册。
6. 现在我们终于可以让插件有自定义的名字了！
  - 真正实现了插件的`plugin_name`**不受文件夹名称限制**的功能。（吐槽：可乐你的某个小小细节导致我搞了好久……）
  - 通过在插件类中定义`plugin_name`属性来指定插件内部标识符。
  - 由于此更改一个文件中现在可以有多个插件类，但每个插件类必须有**唯一的**`plugin_name`。
  - 在某些插件加载失败时，现在会显示包名而不是插件内部标识符。
    - 例如：`MaiMBot.plugins.example_plugin`而不是`example_plugin`。
    - 仅在插件 import 失败时会如此，正常注册过程中失败的插件不会显示包名，而是显示插件内部标识符。（这是特性，但是基本上不可能出现这个情况）
7. 现在不支持单文件插件了，加载方式已经完全删除。
8. 把`BaseEventPlugin`合并到了`BasePlugin`中，所有插件都应该继承自`BasePlugin`。
9. `BaseEventHandler`现在有了`get_config`方法了。
10. 修正了`main.py`中的错误输出。
11. 修正了`command`所编译的`Pattern`注册时的错误输出。
12. `events_manager`有了task相关逻辑了。
13. 现在有了插件卸载和重载功能了，也就是热插拔。
14. 实现了组件的全局启用和禁用功能。
  - 通过`enable_component`和`disable_component`方法来启用或禁用组件。
  - 不过这个操作不会保存到配置文件~
15. 实现了组件的局部禁用，也就是针对某一个聊天禁用的功能。
  - 通过`disable_specific_chat_action`，`enable_specific_chat_action`，`disable_specific_chat_command`，`enable_specific_chat_command`，`disable_specific_chat_event_handler`，`enable_specific_chat_event_handler`来操作
  - 同样不保存到配置文件~
16. 把`BaseTool`一并合并进入了插件系统

# 官方插件修改
1. `HelloWorld`插件现在有一个样例的`EventHandler`。
2. 内置插件增加了一个通过`Command`来管理插件的功能。具体是使用`/pm`命令唤起。（需要自行启用）
3. `HelloWorld`插件现在有一个样例的`CompareNumbersTool`。

### 执笔BGM
塞壬唱片！