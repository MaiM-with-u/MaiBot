import tkinter as tk
from tkinter import ttk, messagebox
import tomli
import tomli_w
import os
from typing import Any, Dict, List
import threading
import time


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

    def create_version_label(self):
        """创建版本号显示标签"""
        version = self.config.get("inner", {}).get("version", "未知版本")
        version_frame = ttk.Frame(self.main_frame)
        version_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))

        version_label = ttk.Label(version_frame, text=f"麦麦版本：{version}", font=("", 10, "bold"))
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

        # 添加配置项到树
        for section in self.config:
            if section != "inner":  # 跳过inner部分
                # 获取节的中文名称
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
        self.editor_title = ttk.Label(self.editor_frame, text="")
        self.editor_title.pack(fill=tk.X)

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
        self.refresh_button = ttk.Button(self.button_frame, text="刷新", command=self.refresh_config)
        self.refresh_button.pack(side=tk.RIGHT, padx=5)

    def create_widget_for_value(self, parent: ttk.Frame, key: str, value: Any, path: List[str]) -> None:
        """为不同类型的值创建对应的编辑控件"""
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, padx=5, pady=2)

        # --- 修改开始: 改进翻译查找逻辑 ---
        full_config_path_key = ".".join(path + [key])  # 例如 "chinese_typo.enable"

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
        # --- 修改结束 ---

        # 配置名（大号字体）
        label = ttk.Label(frame, text=item_name_to_display, font=("", 20, "bold"))
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
            # 重新渲染本分组
            if hasattr(self, "current_section") and self.current_section and self.current_section != "quick_settings":
                self.create_section_widgets(
                    parent, self.current_section, self.config[self.current_section], [self.current_section]
                )
            elif hasattr(self, "current_section") and self.current_section == "quick_settings":
                self.create_quick_settings_widgets()  # 如果当前是快捷设置，也刷新它

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
            desc_label = ttk.Label(frame, text=item_desc_to_display, foreground="gray", font=("", 16))
            desc_label.grid(
                row=desc_row, column=0, columnspan=content_col_offset_for_star + 1, sticky=tk.W, padx=5, pady=(0, 4)
            )
            widget_row = desc_row + 1  # 内容控件在描述下方
        else:
            widget_row = desc_row  # 内容控件直接在第二行

        # 配置内容控件（第三行或第二行）
        if path[0] == "inner":
            value_label = ttk.Label(frame, text=str(value), font=("", 20))
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
            entry = ttk.Entry(frame, textvariable=var, font=("", 20))
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
            entry = ttk.Entry(frame, textvariable=var, font=("", 20))
            entry.grid(row=widget_row, column=0, columnspan=content_col_offset_for_star + 1, sticky=tk.W + tk.E, padx=5)
            var.trace_add("write", lambda *args: self.on_value_changed())
            self.widgets[tuple(path + [key])] = var
            widget_type = "text"

    def create_section_widgets(self, parent: ttk.Frame, section: str, data: Dict, path=None) -> None:
        """为配置节创建编辑控件"""
        if path is None:
            path = [section]
        # 获取节的中文名称和描述
        section_trans = self.translations.get("sections", {}).get(section, {})
        section_name = section_trans.get("name", section)
        section_desc = section_trans.get("description", "")

        # 创建节的标签框架
        section_frame = ttk.Frame(parent)
        section_frame.pack(fill=tk.X, padx=5, pady=10)

        # 创建节的名称标签
        section_label = ttk.Label(section_frame, text=f"[{section_name}]", font=("", 12, "bold"))
        section_label.pack(side=tk.LEFT, padx=5)

        # 创建节的描述标签
        if section_desc:
            desc_label = ttk.Label(section_frame, text=f"({section_desc})", foreground="gray")
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

        # 获取节的中文名称
        if section == "quick_settings":
            section_name = "快捷设置"
        else:
            section_trans = self.translations.get("sections", {}).get(section, {})
            section_name = section_trans.get("name", section)
        self.editor_title.config(text=f"编辑 {section_name}")

        # 清空编辑器
        for widget in self.content_frame.winfo_children():
            widget.destroy()

        # 清空控件字典
        self.widgets.clear()

        # 创建编辑控件
        if section == "quick_settings":
            self.create_quick_settings_widgets()
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

            # 创建名称标签
            name_label = ttk.Label(frame, text=setting["name"], font=("", 18))
            name_label.pack(fill=tk.X, padx=5, pady=(2, 0))

            # 创建描述标签
            if setting.get("description"):
                desc_label = ttk.Label(frame, text=setting["description"], foreground="gray", font=("", 16))
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
                entry = ttk.Entry(frame, textvariable=var, width=40, font=("", 18))
                entry.pack(fill=tk.X, padx=5, pady=(0, 5))
                var.trace_add("write", lambda *args, p=path, v=var: self.on_quick_setting_changed(p, v))

            elif setting_type == "number":
                value = str(value) if value is not None else "0"
                var = tk.StringVar(value=value)
                entry = ttk.Entry(frame, textvariable=var, width=10, font=("", 18))
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
                    value = self.get_widget_value(widget)
                    # 更新配置
                    current = self.config
                    for key in path[:-1]:
                        current = current[key]
                    final_key = path[-1]  # 直接用最后一个key
                    current[final_key] = value

                # 保存到文件
                with open(self.config_path, "wb") as f:
                    tomli_w.dump(self.config, f)

                self.last_save_time = time.time()
                self.pending_save = False
                self.editor_title.config(text=f"{self.editor_title.cget('text')} (已保存)")
                self.root.after(
                    2000, lambda: self.editor_title.config(text=self.editor_title.cget("text").replace(" (已保存)", ""))
                )
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


def main():
    root = tk.Tk()
    _app = ConfigEditor(root)
    root.mainloop()


if __name__ == "__main__":
    main()
