import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import tomli
import tomli_w
import os
from typing import Any, Dict, List
import threading
import time
import sys


class ConfigEditor:
    def __init__(self, root):
        self.root = root
        self.root.title("麦麦配置编辑器")

        # 加载编辑器配置
        self.load_editor_config()

        # 设置窗口大小
        self.root.geometry(f"{self.window_width}x{self.window_height}")

        # 加载配置
        self.load_config()

        # 加载环境变量
        self.load_env_vars()

        # 自动保存相关
        self.last_save_time = time.time()
        self.save_timer = None
        self.save_lock = threading.Lock()
        self.current_section = None  # 当前编辑的节
        self.pending_save = False  # 是否有待保存的更改

        # 存储控件的字典
        self.widgets = {}

        # 创建主框架
        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # 创建版本号显示
        self.create_version_label()

        # 创建左侧导航栏
        self.create_navbar()

        # 创建右侧编辑区
        self.create_editor()

        # 创建底部按钮
        self.create_buttons()

        # 配置网格权重
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.main_frame.columnconfigure(1, weight=1)
        self.main_frame.rowconfigure(1, weight=1)  # 修改为1，因为第0行是版本号

        # 默认选择快捷设置栏
        self.current_section = "quick_settings"
        self.create_quick_settings_widgets()
        # 选中导航树中的快捷设置项
        for item in self.tree.get_children():
            if self.tree.item(item)["values"][0] == "quick_settings":
                self.tree.selection_set(item)
                break

    def load_editor_config(self):
        """加载编辑器配置"""
        try:
            editor_config_path = os.path.join(os.path.dirname(__file__), "configexe.toml")
            with open(editor_config_path, "rb") as f:
                self.editor_config = tomli.load(f)  # 保存整个配置对象

            # 设置配置路径
            self.config_path = self.editor_config["config"]["bot_config_path"]
            # 如果路径是相对路径，转换为绝对路径
            if not os.path.isabs(self.config_path):
                self.config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), self.config_path)

            # 设置编辑器参数
            self.window_width = self.editor_config["editor"]["window_width"]
            self.window_height = self.editor_config["editor"]["window_height"]
            self.save_delay = self.editor_config["editor"]["save_delay"]

            # 加载翻译
            self.translations = self.editor_config.get("translations", {})

        except Exception as e:
            messagebox.showerror("错误", f"加载编辑器配置失败: {str(e)}")
            # 使用默认值
            self.editor_config = {}  # 初始化空配置
            self.config_path = "config/bot_config.toml"
            self.window_width = 1000
            self.window_height = 800
            self.save_delay = 1.0
            self.translations = {}

    def load_config(self):
        try:
            with open(self.config_path, "rb") as f:
                self.config = tomli.load(f)
        except Exception as e:
            messagebox.showerror("错误", f"加载配置文件失败: {str(e)}")
            self.config = {}
            # 自动打开配置路径窗口
            self.open_path_config()

    def load_env_vars(self):
        """加载并解析环境变量文件"""
        try:
            # 从配置中获取环境文件路径
            env_path = self.config.get("inner", {}).get("env_file", ".env")
            if not os.path.isabs(env_path):
                env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), env_path)

            if not os.path.exists(env_path):
                print(f"环境文件不存在: {env_path}")
                return

            # 读取环境文件
            with open(env_path, "r", encoding="utf-8") as f:
                env_content = f.read()

            # 解析环境变量
            env_vars = {}
            for line in env_content.split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()

                    # 检查是否是目标变量
                    if key.endswith("_BASE_URL") or key.endswith("_KEY"):
                        # 提取前缀（去掉_BASE_URL或_KEY）
                        prefix = key[:-9] if key.endswith("_BASE_URL") else key[:-4]
                        if prefix not in env_vars:
                            env_vars[prefix] = {}
                        env_vars[prefix][key] = value

            # 将解析的环境变量添加到配置中
            if "env_vars" not in self.config:
                self.config["env_vars"] = {}
            self.config["env_vars"].update(env_vars)

        except Exception as e:
            print(f"加载环境变量失败: {str(e)}")

    def create_version_label(self):
        """创建版本号显示标签"""
        version = self.config.get("inner", {}).get("version", "未知版本")
        version_frame = ttk.Frame(self.main_frame)
        version_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))

        # 添加配置按钮
        config_button = ttk.Button(version_frame, text="配置路径", command=self.open_path_config)
        config_button.pack(side=tk.LEFT, padx=5)

        version_label = ttk.Label(version_frame, text=f"麦麦版本：{version}", font=("微软雅黑", 10, "bold"))
        version_label.pack(side=tk.LEFT, padx=5)

    def create_navbar(self):
        # 创建左侧导航栏
        self.nav_frame = ttk.Frame(self.main_frame, padding="5")
        self.nav_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # 创建导航树
        self.tree = ttk.Treeview(self.nav_frame)
        self.tree.pack(fill=tk.BOTH, expand=True)

        # 添加快捷设置节
        self.tree.insert("", "end", text="快捷设置", values=("quick_settings",))

        # 添加env_vars节，显示为"配置你的模型APIKEY"
        self.tree.insert("", "end", text="配置你的模型APIKEY", values=("env_vars",))

        # 只显示bot_config.toml实际存在的section
        for section in self.config:
            if section not in (
                "inner",
                "env_vars",
                "telemetry",
                "experimental",
                "maim_message",
                "keyword_reaction",
                "message_receive",
                "relationship",
            ):
                section_trans = self.translations.get("sections", {}).get(section, {})
                section_name = section_trans.get("name", section)
                self.tree.insert("", "end", text=section_name, values=(section,))
        # 绑定选择事件
        self.tree.bind("<<TreeviewSelect>>", self.on_section_select)

    def create_editor(self):
        # 创建右侧编辑区
        self.editor_frame = ttk.Frame(self.main_frame, padding="5")
        self.editor_frame.grid(row=1, column=1, sticky=(tk.W, tk.E, tk.N, tk.S))

        # 创建编辑区标题
        # self.editor_title = ttk.Label(self.editor_frame, text="")
        # self.editor_title.pack(fill=tk.X)

        # 创建编辑区内容
        self.editor_content = ttk.Frame(self.editor_frame)
        self.editor_content.pack(fill=tk.BOTH, expand=True)

        # 创建滚动条
        self.scrollbar = ttk.Scrollbar(self.editor_content)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 创建画布和框架
        self.canvas = tk.Canvas(self.editor_content, yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.config(command=self.canvas.yview)

        # 创建内容框架
        self.content_frame = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.content_frame, anchor=tk.NW)

        # 绑定画布大小变化事件
        self.content_frame.bind("<Configure>", self.on_frame_configure)
        self.canvas.bind("<Configure>", self.on_canvas_configure)

    def on_frame_configure(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def on_canvas_configure(self, event):
        # 更新内容框架的宽度以适应画布
        self.canvas.itemconfig(self.canvas.find_withtag("all")[0], width=event.width)

    def create_buttons(self):
        # 创建底部按钮区
        self.button_frame = ttk.Frame(self.main_frame, padding="5")
        self.button_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E))

        # 刷新按钮
        # self.refresh_button = ttk.Button(self.button_frame, text="刷新", command=self.refresh_config)
        # self.refresh_button.pack(side=tk.RIGHT, padx=5)

        # 高级选项按钮（左下角）
        self.advanced_button = ttk.Button(self.button_frame, text="高级选项", command=self.open_advanced_options)
        self.advanced_button.pack(side=tk.LEFT, padx=5)

    def create_widget_for_value(self, parent: ttk.Frame, key: str, value: Any, path: List[str]) -> None:
        """为不同类型的值创建对应的编辑控件"""
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, padx=5, pady=2)

        # --- 修改开始: 改进翻译查找逻辑 ---
        full_config_path_key = ".".join(path + [key])  # 例如 "chinese_typo.enable"

        model_item_translations = {
            "name": ("模型名称", "模型的唯一标识或名称"),
            "provider": ("模型提供商", "模型API的提供商"),
            "pri_in": ("输入价格", "模型输入的价格/消耗"),
            "pri_out": ("输出价格", "模型输出的价格/消耗"),
            "temp": ("模型温度", "控制模型输出的多样性"),
        }

        item_name_to_display = key  # 默认显示原始键名
        item_desc_to_display = ""  # 默认无描述

        # 1. 尝试使用完整路径的特定翻译
        specific_translation = self.translations.get("items", {}).get(full_config_path_key)
        if specific_translation and specific_translation.get("name"):
            item_name_to_display = specific_translation.get("name")
            item_desc_to_display = specific_translation.get("description", "")
        else:
            # 2. 如果特定翻译未找到或没有name，尝试使用通用键名的翻译
            generic_translation = self.translations.get("items", {}).get(key)
            if generic_translation and generic_translation.get("name"):
                item_name_to_display = generic_translation.get("name")
                item_desc_to_display = generic_translation.get("description", "")
            elif key in model_item_translations:
                item_name_to_display, item_desc_to_display = model_item_translations[key]
        # --- 修改结束 ---

        # 配置名（大号字体）
        label = ttk.Label(frame, text=item_name_to_display, font=("微软雅黑", 16, "bold"))
        label.grid(row=0, column=0, sticky=tk.W, padx=5, pady=(0, 0))

        # 星星图标快捷设置（与配置名同一行）
        content_col_offset_for_star = 1  # 星标按钮占一列
        quick_settings = self.editor_config.get("editor", {}).get("quick_settings", {}).get("items", [])
        already_in_quick = any(item.get("path") == full_config_path_key for item in quick_settings)
        icon = "★" if already_in_quick else "☆"
        icon_fg = "#FFD600"  # 始终金色

        def on_star_click():
            self.toggle_quick_setting(
                full_config_path_key, widget_type, item_name_to_display, item_desc_to_display, already_in_quick
            )
            # 立即刷新本分组
            for widget in parent.winfo_children():
                widget.destroy()
            self.widgets.clear()
            # 判断parent是不是self.content_frame
            if parent == self.content_frame:
                # 主界面
                if (
                    hasattr(self, "current_section")
                    and self.current_section
                    and self.current_section != "quick_settings"
                ):
                    self.create_section_widgets(
                        parent, self.current_section, self.config[self.current_section], [self.current_section]
                    )
                elif hasattr(self, "current_section") and self.current_section == "quick_settings":
                    self.create_quick_settings_widgets()
            else:
                # 弹窗Tab
                # 重新渲染当前Tab的内容
                if path:
                    section = path[0]
                    self.create_section_widgets(parent, section, self.config[section], path)

        pin_btn = ttk.Button(frame, text=icon, width=2, command=on_star_click)
        pin_btn.grid(row=0, column=content_col_offset_for_star, sticky=tk.W, padx=5)
        try:
            pin_btn.configure(style="Pin.TButton")
            style = ttk.Style()
            style.configure("Pin.TButton", foreground=icon_fg)
        except Exception:
            pass

        # 配置项描述（第二行）
        desc_row = 1
        if item_desc_to_display:
            desc_label = ttk.Label(frame, text=item_desc_to_display, foreground="gray", font=("微软雅黑", 10))
            desc_label.grid(
                row=desc_row, column=0, columnspan=content_col_offset_for_star + 1, sticky=tk.W, padx=5, pady=(0, 4)
            )
            widget_row = desc_row + 1  # 内容控件在描述下方
        else:
            widget_row = desc_row  # 内容控件直接在第二行

        # 配置内容控件（第三行或第二行）
        if path[0] == "inner":
            value_label = ttk.Label(frame, text=str(value), font=("微软雅黑", 16))
            value_label.grid(row=widget_row, column=0, columnspan=content_col_offset_for_star + 1, sticky=tk.W, padx=5)
            return

        if isinstance(value, bool):
            # 布尔值使用复选框
            var = tk.BooleanVar(value=value)
            checkbox = ttk.Checkbutton(frame, variable=var, command=lambda: self.on_value_changed())
            checkbox.grid(row=widget_row, column=0, columnspan=content_col_offset_for_star + 1, sticky=tk.W, padx=5)
            self.widgets[tuple(path + [key])] = var
            widget_type = "bool"

        elif isinstance(value, (int, float)):
            # 数字使用数字输入框
            var = tk.StringVar(value=str(value))
            entry = ttk.Entry(frame, textvariable=var, font=("微软雅黑", 16))
            entry.grid(row=widget_row, column=0, columnspan=content_col_offset_for_star + 1, sticky=tk.W + tk.E, padx=5)
            var.trace_add("write", lambda *args: self.on_value_changed())
            self.widgets[tuple(path + [key])] = var
            widget_type = "number"

        elif isinstance(value, list):
            # 列表使用每行一个输入框的形式
            frame_list = ttk.Frame(frame)
            frame_list.grid(
                row=widget_row, column=0, columnspan=content_col_offset_for_star + 1, sticky=tk.W + tk.E, padx=5
            )

            # 创建添加和删除按钮
            button_frame = ttk.Frame(frame_list)
            button_frame.pack(side=tk.RIGHT, padx=5)

            add_button = ttk.Button(
                button_frame, text="+", width=3, command=lambda p=path + [key]: self.add_list_item(frame_list, p)
            )
            add_button.pack(side=tk.TOP, pady=2)

            # 创建列表项框架
            items_frame = ttk.Frame(frame_list)
            items_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

            # 存储所有输入框的变量
            entry_vars = []

            # 为每个列表项创建输入框
            for i, item in enumerate(value):
                self.create_list_item(items_frame, item, i, entry_vars, path + [key])

            # 存储控件引用
            self.widgets[tuple(path + [key])] = (items_frame, entry_vars)
            widget_type = "list"

        else:
            # 其他类型（字符串等）使用普通文本框
            var = tk.StringVar(value=str(value))

            # 特殊处理provider字段
            full_path = ".".join(path + [key])
            if key == "provider" and full_path.startswith("model."):
                # print(f"处理provider字段，完整路径: {full_path}")
                # print(f"当前config中的env_vars: {self.config.get('env_vars', {})}")
                # 获取所有可用的provider选项
                providers = []
                if "env_vars" in self.config:
                    # print(f"找到env_vars节，内容: {self.config['env_vars']}")
                    # 遍历env_vars中的所有配置对
                    for prefix, values in self.config["env_vars"].items():
                        # print(f"检查配置对 {prefix}: {values}")
                        # 检查是否同时有BASE_URL和KEY
                        if f"{prefix}_BASE_URL" in values and f"{prefix}_KEY" in values:
                            providers.append(prefix)
                            # print(f"添加provider: {prefix}")

                # print(f"最终providers列表: {providers}")
                if providers:
                    # 创建模型名称标签（大字体）
                    # model_name = var.get() if var.get() else providers[0]
                    # section_translations = {
                    #     "model.utils": "麦麦组件模型",
                    #     "model.utils_small": "小型麦麦组件模型",
                    #     "model.memory_summary": "记忆概括模型",
                    #     "model.vlm": "图像识别模型",
                    #     "model.embedding": "嵌入模型",
                    #     "model.normal_chat_1": "普通聊天：主要聊天模型",
                    #     "model.normal_chat_2": "普通聊天：次要聊天模型",
                    #     "model.focus_working_memory": "专注模式：工作记忆模型",
                    #     "model.focus_tool_use": "专注模式：工具调用模型",
                    #     "model.focus_planner": "专注模式：决策模型",
                    #     "model.focus_expressor": "专注模式：表达器模型",
                    # }
                    # 获取当前节的名称
                    # current_section = ".".join(path[:-1])  # 去掉最后一个key
                    # section_name = section_translations.get(current_section, current_section)

                    # 创建节名称标签（大字体）
                    # section_label = ttk.Label(frame, text="11", font=("微软雅黑", 24, "bold"))
                    # section_label.grid(row=widget_row, column=0, columnspan=content_col_offset_for_star +1, sticky=tk.W, padx=5, pady=(0, 5))

                    # 创建下拉菜单（小字体）
                    combo = ttk.Combobox(
                        frame, textvariable=var, values=providers, font=("微软雅黑", 12), state="readonly"
                    )
                    combo.grid(
                        row=widget_row + 1,
                        column=0,
                        columnspan=content_col_offset_for_star + 1,
                        sticky=tk.W + tk.E,
                        padx=5,
                    )
                    combo.bind("<<ComboboxSelected>>", lambda e: self.on_value_changed())
                    self.widgets[tuple(path + [key])] = var
                    widget_type = "provider"
                    # print(f"创建了下拉菜单，选项: {providers}")
                else:
                    # 如果没有可用的provider，使用普通文本框
                    # print(f"没有可用的provider，使用普通文本框")
                    entry = ttk.Entry(frame, textvariable=var, font=("微软雅黑", 16))
                    entry.grid(
                        row=widget_row, column=0, columnspan=content_col_offset_for_star + 1, sticky=tk.W + tk.E, padx=5
                    )
                    var.trace_add("write", lambda *args: self.on_value_changed())
                    self.widgets[tuple(path + [key])] = var
                    widget_type = "text"
            else:
                # 普通文本框
                entry = ttk.Entry(frame, textvariable=var, font=("微软雅黑", 16))
                entry.grid(
                    row=widget_row, column=0, columnspan=content_col_offset_for_star + 1, sticky=tk.W + tk.E, padx=5
                )
                var.trace_add("write", lambda *args: self.on_value_changed())
                self.widgets[tuple(path + [key])] = var
            widget_type = "text"

    def create_section_widgets(self, parent: ttk.Frame, section: str, data: Dict, path=None) -> None:
        """为配置节创建编辑控件"""
        if path is None:
            path = [section]
        # section完整路径
        full_section_path = ".".join(path)
        # 获取节的中文名称和描述
        section_translations = {
            "model.utils": "工具模型",
            "model.utils_small": "小型工具模型",
            "model.memory_summary": "记忆概括模型",
            "model.vlm": "图像识别模型",
            "model.embedding": "嵌入模型",
            "model.normal_chat_1": "主要聊天模型",
            "model.normal_chat_2": "次要聊天模型",
            "model.focus_working_memory": "工作记忆模型",
            "model.focus_tool_use": "工具调用模型",
            "model.focus_planner": "决策模型",
            "model.focus_expressor": "表达器模型",
        }
        section_trans = self.translations.get("sections", {}).get(full_section_path, {})
        section_name = section_trans.get("name") or section_translations.get(full_section_path) or section
        section_desc = section_trans.get("description", "")

        # 创建节的标签框架
        section_frame = ttk.Frame(parent)
        section_frame.pack(fill=tk.X, padx=5, pady=10)

        # 创建节的名称标签
        section_label = ttk.Label(section_frame, text=f"[{section_name}]", font=("微软雅黑", 18, "bold"))
        section_label.pack(side=tk.LEFT, padx=5)

        # 创建节的描述标签
        if isinstance(section_trans.get("description"), dict):
            # 如果是多语言描述，优先取en，否则取第一个
            desc_en = section_trans["description"].get("en") or next(iter(section_trans["description"].values()), "")
            desc_label = ttk.Label(section_frame, text=desc_en, foreground="gray", font=("微软雅黑", 10))
        else:
            desc_label = ttk.Label(section_frame, text=section_desc, foreground="gray", font=("微软雅黑", 10))
        desc_label.pack(side=tk.LEFT, padx=5)

        # 为每个配置项创建对应的控件
        for key, value in data.items():
            if isinstance(value, dict):
                self.create_section_widgets(parent, key, value, path + [key])
            else:
                self.create_widget_for_value(parent, key, value, path)

    def on_value_changed(self):
        """当值改变时触发自动保存"""
        self.pending_save = True
        current_time = time.time()
        if current_time - self.last_save_time > self.save_delay:
            if self.save_timer:
                self.root.after_cancel(self.save_timer)
            self.save_timer = self.root.after(int(self.save_delay * 1000), self.save_config)

    def on_section_select(self, event):
        # 如果有待保存的更改，先保存
        if self.pending_save:
            self.save_config()

        selection = self.tree.selection()
        if not selection:
            return

        section = self.tree.item(selection[0])["values"][0]  # 使用values中的原始节名
        self.current_section = section

        # 清空编辑器
        for widget in self.content_frame.winfo_children():
            widget.destroy()

        # 清空控件字典
        self.widgets.clear()

        # 创建编辑控件
        if section == "quick_settings":
            self.create_quick_settings_widgets()
        elif section == "env_vars":
            self.create_env_vars_section(self.content_frame)
        elif section in self.config:
            self.create_section_widgets(self.content_frame, section, self.config[section])

    def create_quick_settings_widgets(self):
        """创建快捷设置编辑界面"""
        # 获取快捷设置配置
        quick_settings = self.editor_config.get("editor", {}).get("quick_settings", {}).get("items", [])

        # 创建快捷设置控件
        for setting in quick_settings:
            frame = ttk.Frame(self.content_frame)
            frame.pack(fill=tk.X, padx=5, pady=2)

            # 获取当前值
            path = setting["path"].split(".")
            current = self.config
            for key in path[:-1]:  # 除了最后一个键
                current = current.get(key, {})
            value = current.get(path[-1])  # 获取最后一个键的值

            # 创建名称标签（加粗）
            name_label = ttk.Label(frame, text=setting["name"], font=("微软雅黑", 16, "bold"))
            name_label.pack(fill=tk.X, padx=5, pady=(2, 0))

            # 创建描述标签
            if setting.get("description"):
                desc_label = ttk.Label(frame, text=setting["description"], foreground="gray", font=("微软雅黑", 10))
                desc_label.pack(fill=tk.X, padx=5, pady=(0, 2))

            # 根据类型创建不同的控件
            setting_type = setting.get("type", "bool")

            if setting_type == "bool":
                value = bool(value) if value is not None else False
                var = tk.BooleanVar(value=value)
                checkbox = ttk.Checkbutton(
                    frame, text="", variable=var, command=lambda p=path, v=var: self.on_quick_setting_changed(p, v)
                )
                checkbox.pack(anchor=tk.W, padx=5, pady=(0, 5))

            elif setting_type == "text":
                value = str(value) if value is not None else ""
                var = tk.StringVar(value=value)
                entry = ttk.Entry(frame, textvariable=var, width=40, font=("微软雅黑", 12))
                entry.pack(fill=tk.X, padx=5, pady=(0, 5))
                var.trace_add("write", lambda *args, p=path, v=var: self.on_quick_setting_changed(p, v))

            elif setting_type == "number":
                value = str(value) if value is not None else "0"
                var = tk.StringVar(value=value)
                entry = ttk.Entry(frame, textvariable=var, width=10, font=("微软雅黑", 12))
                entry.pack(fill=tk.X, padx=5, pady=(0, 5))
                var.trace_add("write", lambda *args, p=path, v=var: self.on_quick_setting_changed(p, v))

            elif setting_type == "list":
                # 对于列表类型，创建一个按钮来打开编辑窗口
                button = ttk.Button(
                    frame, text="编辑列表", command=lambda p=path, s=setting: self.open_list_editor(p, s)
                )
                button.pack(anchor=tk.W, padx=5, pady=(0, 5))

    def create_list_item(self, parent, value, index, entry_vars, path):
        """创建单个列表项的输入框"""
        item_frame = ttk.Frame(parent)
        item_frame.pack(fill=tk.X, pady=1)

        # 创建输入框
        var = tk.StringVar(value=str(value))
        entry = ttk.Entry(item_frame, textvariable=var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        var.trace_add("write", lambda *args: self.on_value_changed())

        # 创建删除按钮
        del_button = ttk.Button(
            item_frame,
            text="-",
            width=3,
            command=lambda: self.remove_list_item(parent, item_frame, entry_vars, index, path),
        )
        del_button.pack(side=tk.RIGHT, padx=5)

        # 存储变量引用
        entry_vars.append(var)

    def add_list_item(self, parent, path):
        """添加新的列表项"""
        items_frame = parent.winfo_children()[1]  # 获取列表项框架
        entry_vars = self.widgets[tuple(path)][1]  # 获取变量列表

        # 创建新的列表项
        self.create_list_item(items_frame, "", len(entry_vars), entry_vars, path)
        self.on_value_changed()

    def remove_list_item(self, parent, item_frame, entry_vars, index, path):
        """删除列表项"""
        item_frame.destroy()
        entry_vars.pop(index)
        self.on_value_changed()

    def get_widget_value(self, widget) -> Any:
        """获取控件的值"""
        if isinstance(widget, tk.BooleanVar):
            return widget.get()
        elif isinstance(widget, tk.StringVar):
            value = widget.get()
            try:
                # 尝试转换为数字
                if "." in value:
                    return float(value)
                return int(value)
            except ValueError:
                return value
        elif isinstance(widget, tuple):  # 列表类型
            items_frame, entry_vars = widget
            # 获取所有非空输入框的值
            return [var.get() for var in entry_vars if var.get().strip()]
        return None

    def save_config(self):
        """保存配置到文件"""
        if not self.pending_save:
            return

        with self.save_lock:
            try:
                # 获取所有控件的值
                for path, widget in self.widgets.items():
                    # 跳过 env_vars 的控件赋值（只用于.env，不写回config）
                    if len(path) >= 2 and path[0] == "env_vars":
                        continue
                    value = self.get_widget_value(widget)
                    current = self.config
                    for key in path[:-1]:
                        current = current[key]
                    final_key = path[-1]
                    current[final_key] = value

                # === 只保存 TOML，不包含 env_vars ===
                env_vars = self.config.pop("env_vars", None)
                with open(self.config_path, "wb") as f:
                    tomli_w.dump(self.config, f)
                if env_vars is not None:
                    self.config["env_vars"] = env_vars

                # === 保存 env_vars 到 .env 文件（只覆盖特定key，其他内容保留） ===
                env_path = self.editor_config["config"].get("env_file", ".env")
                if not os.path.isabs(env_path):
                    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), env_path)
                # 1. 读取原有.env内容
                old_lines = []
                if os.path.exists(env_path):
                    with open(env_path, "r", encoding="utf-8") as f:
                        old_lines = f.readlines()
                # 2. 收集所有目标key的新值（直接从widgets取）
                new_env_dict = {}
                for path, widget in self.widgets.items():
                    if len(path) == 2 and path[0] == "env_vars":
                        k = path[1]
                        if k.endswith("_BASE_URL") or k.endswith("_KEY"):
                            new_env_dict[k] = self.get_widget_value(widget)
                # 3. 遍历原有行，替换目标key，保留所有其他内容
                result_lines = []
                found_keys = set()
                for line in old_lines:
                    if "=" in line and not line.strip().startswith("#"):
                        k = line.split("=", 1)[0].strip()
                        if k in new_env_dict:
                            result_lines.append(f"{k}={new_env_dict[k]}\n")
                            found_keys.add(k)
                        else:
                            result_lines.append(line)
                    else:
                        result_lines.append(line)
                # 4. 新key如果原.env没有，则追加
                for k, v in new_env_dict.items():
                    if k not in found_keys:
                        result_lines.append(f"{k}={v}\n")
                # 5. 写回.env
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(result_lines)
                # === 结束 ===

                # === 保存完 .env 后，同步 widgets 的值回 self.config['env_vars'] ===
                for path, widget in self.widgets.items():
                    if len(path) == 2 and path[0] == "env_vars":
                        prefix_key = path[1]
                        if prefix_key.endswith("_BASE_URL") or prefix_key.endswith("_KEY"):
                            prefix = prefix_key[:-9] if prefix_key.endswith("_BASE_URL") else prefix_key[:-4]
                            if "env_vars" not in self.config:
                                self.config["env_vars"] = {}
                            if prefix not in self.config["env_vars"]:
                                self.config["env_vars"][prefix] = {}
                            self.config["env_vars"][prefix][prefix_key] = self.get_widget_value(widget)

                self.last_save_time = time.time()
                self.pending_save = False
            except Exception as e:
                messagebox.showerror("错误", f"保存配置失败: {str(e)}")

    def refresh_config(self):
        # 如果有待保存的更改，先保存
        if self.pending_save:
            self.save_config()

        self.load_config()
        self.tree.delete(*self.tree.get_children())
        for section in self.config:
            # 获取节的中文名称
            section_trans = self.translations.get("sections", {}).get(section, {})
            section_name = section_trans.get("name", section)
            self.tree.insert("", "end", text=section_name, values=(section,))
        messagebox.showinfo("成功", "配置已刷新")

    def open_list_editor(self, path, setting):
        """打开列表编辑窗口"""
        # 创建新窗口
        dialog = tk.Toplevel(self.root)
        dialog.title(f"编辑 {setting['name']}")
        dialog.geometry("400x300")

        # 获取当前值
        current = self.config
        for key in path[:-1]:
            current = current.get(key, {})
        value = current.get(path[-1], [])

        # 创建编辑区
        frame = ttk.Frame(dialog, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)

        # 创建列表项框架
        items_frame = ttk.Frame(frame)
        items_frame.pack(fill=tk.BOTH, expand=True)

        # 存储所有输入框的变量
        entry_vars = []

        # 为每个列表项创建输入框
        for i, item in enumerate(value):
            self.create_list_item(items_frame, item, i, entry_vars, path)

        # 创建按钮框架
        button_frame = ttk.Frame(frame)
        button_frame.pack(fill=tk.X, pady=10)

        # 添加按钮
        add_button = ttk.Button(button_frame, text="添加", command=lambda: self.add_list_item(items_frame, path))
        add_button.pack(side=tk.LEFT, padx=5)

        # 保存按钮
        save_button = ttk.Button(
            button_frame, text="保存", command=lambda: self.save_list_editor(dialog, path, entry_vars)
        )
        save_button.pack(side=tk.RIGHT, padx=5)

    def save_list_editor(self, dialog, path, entry_vars):
        """保存列表编辑窗口的内容"""
        # 获取所有非空输入框的值
        values = [var.get() for var in entry_vars if var.get().strip()]

        # 更新配置
        current = self.config
        for key in path[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[path[-1]] = values

        # 触发保存
        self.on_value_changed()

        # 关闭窗口
        dialog.destroy()

    def on_quick_setting_changed(self, path, var):
        """快捷设置值改变时的处理"""
        # 更新配置
        current = self.config
        for key in path[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        # 根据变量类型设置值
        if isinstance(var, tk.BooleanVar):
            current[path[-1]] = var.get()
        elif isinstance(var, tk.StringVar):
            value = var.get()
            try:
                # 尝试转换为数字
                if "." in value:
                    current[path[-1]] = float(value)
                else:
                    current[path[-1]] = int(value)
            except ValueError:
                current[path[-1]] = value
        # 触发保存
        self.on_value_changed()

    def toggle_quick_setting(self, full_path, widget_type, name, desc, already_in_quick):
        quick_settings = (
            self.editor_config.setdefault("editor", {}).setdefault("quick_settings", {}).setdefault("items", [])
        )
        if already_in_quick:
            # 移除
            self.editor_config["editor"]["quick_settings"]["items"] = [
                item for item in quick_settings if item.get("path") != full_path
            ]
        else:
            # 添加
            quick_settings.append({"name": name, "description": desc, "path": full_path, "type": widget_type})
        # 保存到configexe.toml
        import tomli_w
        import os

        config_path = os.path.join(os.path.dirname(__file__), "configexe.toml")
        with open(config_path, "wb") as f:
            tomli_w.dump(self.editor_config, f)
        self.refresh_quick_settings()

    def refresh_quick_settings(self):
        # 重新渲染快捷设置栏（如果当前在快捷设置页）
        if self.current_section == "quick_settings":
            for widget in self.content_frame.winfo_children():
                widget.destroy()
            self.widgets.clear()
            self.create_quick_settings_widgets()

    def create_env_var_group(self, parent: ttk.Frame, prefix: str, values: Dict[str, str], path: List[str]) -> None:
        """创建环境变量组"""
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, padx=5, pady=2)

        # 创建组标题
        title_frame = ttk.Frame(frame)
        title_frame.pack(fill=tk.X, pady=(5, 0))

        title_label = ttk.Label(title_frame, text=f"API配置组: {prefix}", font=("微软雅黑", 16, "bold"))
        title_label.pack(side=tk.LEFT, padx=5)

        # 删除按钮
        del_button = ttk.Button(title_frame, text="删除组", command=lambda: self.delete_env_var_group(prefix))
        del_button.pack(side=tk.RIGHT, padx=5)

        # 创建BASE_URL输入框
        base_url_frame = ttk.Frame(frame)
        base_url_frame.pack(fill=tk.X, padx=5, pady=2)

        base_url_label = ttk.Label(base_url_frame, text="BASE_URL:", font=("微软雅黑", 12))
        base_url_label.pack(side=tk.LEFT, padx=5)

        base_url_var = tk.StringVar(value=values.get(f"{prefix}_BASE_URL", ""))
        base_url_entry = ttk.Entry(base_url_frame, textvariable=base_url_var, font=("微软雅黑", 12))
        base_url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        base_url_var.trace_add("write", lambda *args: self.on_value_changed())

        # 创建KEY输入框
        key_frame = ttk.Frame(frame)
        key_frame.pack(fill=tk.X, padx=5, pady=2)

        key_label = ttk.Label(key_frame, text="API KEY:", font=("微软雅黑", 12))
        key_label.pack(side=tk.LEFT, padx=5)

        key_var = tk.StringVar(value=values.get(f"{prefix}_KEY", ""))
        key_entry = ttk.Entry(key_frame, textvariable=key_var, font=("微软雅黑", 12))
        key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        key_var.trace_add("write", lambda *args: self.on_value_changed())

        # 存储变量引用
        self.widgets[tuple(path + [f"{prefix}_BASE_URL"])] = base_url_var
        self.widgets[tuple(path + [f"{prefix}_KEY"])] = key_var

        # 添加分隔线
        separator = ttk.Separator(frame, orient="horizontal")
        separator.pack(fill=tk.X, pady=5)

    def create_env_vars_section(self, parent: ttk.Frame) -> None:
        """创建环境变量编辑区"""
        # 创建添加新组的按钮
        add_button = ttk.Button(parent, text="添加新的API配置组", command=self.add_new_env_var_group)
        add_button.pack(pady=10)

        # 创建现有组的编辑区
        if "env_vars" in self.config:
            for prefix, values in self.config["env_vars"].items():
                self.create_env_var_group(parent, prefix, values, ["env_vars"])

    def add_new_env_var_group(self):
        """添加新的环境变量组"""
        # 创建新窗口
        dialog = tk.Toplevel(self.root)
        dialog.title("添加新的API配置组")
        dialog.geometry("400x200")

        # 创建输入框架
        frame = ttk.Frame(dialog, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)

        # 前缀输入
        prefix_label = ttk.Label(frame, text="API前缀名称:", font=("微软雅黑", 12))
        prefix_label.pack(pady=5)

        prefix_var = tk.StringVar()
        prefix_entry = ttk.Entry(frame, textvariable=prefix_var, font=("微软雅黑", 12))
        prefix_entry.pack(fill=tk.X, pady=5)

        # 确认按钮
        def on_confirm():
            prefix = prefix_var.get().strip()
            if prefix:
                if "env_vars" not in self.config:
                    self.config["env_vars"] = {}
                self.config["env_vars"][prefix] = {f"{prefix}_BASE_URL": "", f"{prefix}_KEY": ""}
                # 刷新显示
                self.refresh_env_vars_section()
                self.on_value_changed()
                dialog.destroy()

        confirm_button = ttk.Button(frame, text="确认", command=on_confirm)
        confirm_button.pack(pady=10)

    def delete_env_var_group(self, prefix: str):
        """删除环境变量组"""
        if messagebox.askyesno("确认", f"确定要删除 {prefix} 配置组吗？"):
            if "env_vars" in self.config:
                del self.config["env_vars"][prefix]
                # 刷新显示
                self.refresh_env_vars_section()
                self.on_value_changed()

    def refresh_env_vars_section(self):
        """刷新环境变量编辑区"""
        # 清空当前显示
        for widget in self.content_frame.winfo_children():
            widget.destroy()
        self.widgets.clear()

        # 重新创建编辑区
        self.create_env_vars_section(self.content_frame)

    def open_advanced_options(self):
        """弹窗显示高级配置"""
        dialog = tk.Toplevel(self.root)
        dialog.title("高级选项")
        dialog.geometry("700x800")

        notebook = ttk.Notebook(dialog)
        notebook.pack(fill=tk.BOTH, expand=True)

        # 遥测栏
        if "telemetry" in self.config:
            telemetry_frame = ttk.Frame(notebook)
            notebook.add(telemetry_frame, text="遥测")
            self.create_section_widgets(telemetry_frame, "telemetry", self.config["telemetry"], ["telemetry"])
        # 实验性功能栏
        if "experimental" in self.config:
            exp_frame = ttk.Frame(notebook)
            notebook.add(exp_frame, text="实验性功能")
            self.create_section_widgets(exp_frame, "experimental", self.config["experimental"], ["experimental"])
        # 消息服务栏
        if "maim_message" in self.config:
            msg_frame = ttk.Frame(notebook)
            notebook.add(msg_frame, text="消息服务")
            self.create_section_widgets(msg_frame, "maim_message", self.config["maim_message"], ["maim_message"])
        # 消息接收栏
        if "message_receive" in self.config:
            recv_frame = ttk.Frame(notebook)
            notebook.add(recv_frame, text="消息接收")
            self.create_section_widgets(
                recv_frame, "message_receive", self.config["message_receive"], ["message_receive"]
            )
        # 关系栏
        if "relationship" in self.config:
            rel_frame = ttk.Frame(notebook)
            notebook.add(rel_frame, text="关系")
            self.create_section_widgets(rel_frame, "relationship", self.config["relationship"], ["relationship"])

    def open_path_config(self):
        """打开路径配置对话框"""
        dialog = tk.Toplevel(self.root)
        dialog.title("配置路径")
        dialog.geometry("600x200")

        # 创建输入框架
        frame = ttk.Frame(dialog, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)

        # bot_config.toml路径配置
        bot_config_frame = ttk.Frame(frame)
        bot_config_frame.pack(fill=tk.X, pady=5)

        bot_config_label = ttk.Label(bot_config_frame, text="bot_config.toml路径:", font=("微软雅黑", 12))
        bot_config_label.pack(side=tk.LEFT, padx=5)

        bot_config_var = tk.StringVar(value=self.config_path)
        bot_config_entry = ttk.Entry(bot_config_frame, textvariable=bot_config_var, font=("微软雅黑", 12))
        bot_config_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        def apply_config():
            new_bot_config_path = bot_config_var.get().strip()
            new_env_path = env_var.get().strip()

            if not new_bot_config_path or not new_env_path:
                messagebox.showerror("错误", "路径不能为空")
                return

            if not os.path.exists(new_bot_config_path):
                messagebox.showerror("错误", "bot_config.toml文件不存在")
                return

            # 更新配置
            self.config_path = new_bot_config_path
            self.editor_config["config"]["bot_config_path"] = new_bot_config_path
            self.editor_config["config"]["env_file"] = new_env_path

            # 保存编辑器配置
            config_path = os.path.join(os.path.dirname(__file__), "configexe.toml")
            with open(config_path, "wb") as f:
                tomli_w.dump(self.editor_config, f)

            # 重新加载配置
            self.load_config()
            self.load_env_vars()

            # 刷新显示
            self.refresh_config()

            messagebox.showinfo("成功", "路径配置已更新，程序将重新启动")
            dialog.destroy()

            # 重启程序
            self.root.quit()
            os.execv(sys.executable, ["python"] + sys.argv)

        def browse_bot_config():
            file_path = filedialog.askopenfilename(
                title="选择bot_config.toml文件", filetypes=[("TOML文件", "*.toml"), ("所有文件", "*.*")]
            )
            if file_path:
                bot_config_var.set(file_path)
                apply_config()

        browse_bot_config_btn = ttk.Button(bot_config_frame, text="浏览", command=browse_bot_config)
        browse_bot_config_btn.pack(side=tk.LEFT, padx=5)

        # .env路径配置
        env_frame = ttk.Frame(frame)
        env_frame.pack(fill=tk.X, pady=5)

        env_label = ttk.Label(env_frame, text=".env路径:", font=("微软雅黑", 12))
        env_label.pack(side=tk.LEFT, padx=5)

        env_path = self.editor_config["config"].get("env_file", ".env")
        if not os.path.isabs(env_path):
            env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), env_path)
        env_var = tk.StringVar(value=env_path)
        env_entry = ttk.Entry(env_frame, textvariable=env_var, font=("微软雅黑", 12))
        env_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        def browse_env():
            file_path = filedialog.askopenfilename(
                title="选择.env文件", filetypes=[("环境变量文件", "*.env"), ("所有文件", "*.*")]
            )
            if file_path:
                env_var.set(file_path)
                apply_config()

        browse_env_btn = ttk.Button(env_frame, text="浏览", command=browse_env)
        browse_env_btn.pack(side=tk.LEFT, padx=5)


def main():
    root = tk.Tk()
    _app = ConfigEditor(root)
    root.mainloop()


if __name__ == "__main__":
    main()
