import tkinter as tk
from tkinter import ttk, messagebox, filedialog, colorchooser
import json
from pathlib import Path
import threading
import toml
from datetime import datetime
from collections import defaultdict
import os
import time


class LogIndex:
    """日志索引，用于快速检索和过滤"""

    def __init__(self):
        self.entries = []  # 所有日志条目
        self.module_index = defaultdict(list)  # 按模块索引
        self.level_index = defaultdict(list)  # 按级别索引
        self.filtered_indices = []  # 当前过滤结果的索引
        self.total_entries = 0

    def add_entry(self, index, entry):
        """添加日志条目到索引"""
        if index >= len(self.entries):
            self.entries.extend([None] * (index - len(self.entries) + 1))

        self.entries[index] = entry
        self.total_entries = max(self.total_entries, index + 1)

        # 更新各种索引
        logger_name = entry.get("logger_name", "")
        level = entry.get("level", "")

        self.module_index[logger_name].append(index)
        self.level_index[level].append(index)

    def filter_entries(self, modules=None, level=None, search_text=None):
        """根据条件过滤日志条目"""
        if not modules and not level and not search_text:
            self.filtered_indices = list(range(self.total_entries))
            return self.filtered_indices

        candidate_indices = set(range(self.total_entries))

        # 模块过滤
        if modules and "全部" not in modules:
            module_indices = set()
            for module in modules:
                module_indices.update(self.module_index.get(module, []))
            candidate_indices &= module_indices

        # 级别过滤
        if level and level != "全部":
            level_indices = set(self.level_index.get(level, []))
            candidate_indices &= level_indices

        # 文本搜索过滤
        if search_text:
            search_text = search_text.lower()
            text_indices = set()
            for i in candidate_indices:
                if i < len(self.entries) and self.entries[i]:
                    entry = self.entries[i]
                    text_content = f"{entry.get('logger_name', '')} {entry.get('event', '')}".lower()
                    if search_text in text_content:
                        text_indices.add(i)
            candidate_indices &= text_indices

        self.filtered_indices = sorted(list(candidate_indices))
        return self.filtered_indices

    def get_filtered_count(self):
        """获取过滤后的条目数量"""
        return len(self.filtered_indices)

    def get_entry_at_filtered_position(self, position):
        """获取过滤结果中指定位置的条目"""
        if 0 <= position < len(self.filtered_indices):
            index = self.filtered_indices[position]
            return self.entries[index] if index < len(self.entries) else None
        return None


class LogFormatter:
    """日志格式化器"""

    def __init__(self, config, custom_module_colors=None, custom_level_colors=None):
        self.config = config

        # 日志级别颜色
        self.level_colors = {
            "debug": "#FFA500",
            "info": "#0000FF",
            "success": "#008000",
            "warning": "#FFFF00",
            "error": "#FF0000",
            "critical": "#800080",
        }

        # 模块颜色映射
        self.module_colors = {
            "api": "#00FF00",
            "emoji": "#00FF00",
            "chat": "#0080FF",
            "config": "#FFFF00",
            "common": "#FF00FF",
            "tools": "#00FFFF",
            "lpmm": "#00FFFF",
            "plugin_system": "#FF0080",
            "experimental": "#FFFFFF",
            "person_info": "#008000",
            "manager": "#800080",
            "llm_models": "#008080",
            "plugins": "#800000",
            "plugin_api": "#808000",
            "remote": "#8000FF",
        }

        # 应用自定义颜色
        if custom_module_colors:
            self.module_colors.update(custom_module_colors)
        if custom_level_colors:
            self.level_colors.update(custom_level_colors)

        # 根据配置决定颜色启用状态
        color_text = self.config.get("color_text", "full")
        if color_text == "none":
            self.enable_colors = False
            self.enable_module_colors = False
            self.enable_level_colors = False
        elif color_text == "title":
            self.enable_colors = True
            self.enable_module_colors = True
            self.enable_level_colors = False
        elif color_text == "full":
            self.enable_colors = True
            self.enable_module_colors = True
            self.enable_level_colors = True
        else:
            self.enable_colors = True
            self.enable_module_colors = True
            self.enable_level_colors = False

    def format_log_entry(self, log_entry):
        """格式化日志条目，返回格式化后的文本和样式标签"""
        timestamp = log_entry.get("timestamp", "")
        level = log_entry.get("level", "info")
        logger_name = log_entry.get("logger_name", "")
        event = log_entry.get("event", "")

        # 格式化时间戳
        formatted_timestamp = self.format_timestamp(timestamp)

        # 构建输出部分
        parts = []
        tags = []

        # 日志级别样式配置
        log_level_style = self.config.get("log_level_style", "lite")

        # 时间戳
        if formatted_timestamp:
            if log_level_style == "lite" and self.enable_level_colors:
                parts.append(formatted_timestamp)
                tags.append(f"level_{level}")
            else:
                parts.append(formatted_timestamp)
                tags.append("timestamp")

        # 日志级别显示
        if log_level_style == "full":
            level_text = f"[{level.upper():>8}]"
            parts.append(level_text)
            if self.enable_level_colors:
                tags.append(f"level_{level}")
            else:
                tags.append("level")
        elif log_level_style == "compact":
            level_text = f"[{level.upper()[0]:>8}]"
            parts.append(level_text)
            if self.enable_level_colors:
                tags.append(f"level_{level}")
            else:
                tags.append("level")

        # 模块名称
        if logger_name:
            module_text = f"[{logger_name}]"
            parts.append(module_text)
            if self.enable_module_colors:
                tags.append(f"module_{logger_name}")
            else:
                tags.append("module")

        # 消息内容
        if isinstance(event, str):
            parts.append(event)
        elif isinstance(event, dict):
            try:
                parts.append(json.dumps(event, ensure_ascii=False, indent=None))
            except (TypeError, ValueError):
                parts.append(str(event))
        else:
            parts.append(str(event))
        tags.append("message")

        # 处理其他字段
        extras = []
        for key, value in log_entry.items():
            if key not in ("timestamp", "level", "logger_name", "event"):
                if isinstance(value, (dict, list)):
                    try:
                        value_str = json.dumps(value, ensure_ascii=False, indent=None)
                    except (TypeError, ValueError):
                        value_str = str(value)
                else:
                    value_str = str(value)
                extras.append(f"{key}={value_str}")

        if extras:
            parts.append(" ".join(extras))
            tags.append("extras")

        return parts, tags

    def format_timestamp(self, timestamp):
        """格式化时间戳"""
        if not timestamp:
            return ""

        try:
            if "T" in timestamp:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            else:
                return timestamp

            date_style = self.config.get("date_style", "m-d H:i:s")
            format_map = {
                "Y": "%Y",
                "m": "%m",
                "d": "%d",
                "H": "%H",
                "i": "%M",
                "s": "%S",
            }

            python_format = date_style
            for php_char, python_char in format_map.items():
                python_format = python_format.replace(php_char, python_char)

            return dt.strftime(python_format)
        except Exception:
            return timestamp


class VirtualLogDisplay:
    """虚拟滚动日志显示组件"""

    def __init__(self, parent, formatter):
        self.parent = parent
        self.formatter = formatter
        self.line_height = 20  # 每行高度（像素）
        self.visible_lines = 30  # 可见行数

        # 创建主框架
        self.main_frame = ttk.Frame(parent)

        # 创建文本框和滚动条
        self.scrollbar = ttk.Scrollbar(self.main_frame)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.text_widget = tk.Text(
            self.main_frame,
            wrap=tk.WORD,
            yscrollcommand=self.scrollbar.set,
            background="#1e1e1e",
            foreground="#ffffff",
            insertbackground="#ffffff",
            selectbackground="#404040",
            font=("Consolas", 10),
        )
        self.text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.config(command=self.text_widget.yview)

        # 配置文本标签样式
        self.configure_text_tags()

        # 数据源
        self.log_index = None
        self.current_page = 0
        self.page_size = 500  # 每页显示条数
        self.max_display_lines = 2000  # 最大显示行数

    def pack(self, **kwargs):
        """包装pack方法"""
        self.main_frame.pack(**kwargs)

    def configure_text_tags(self):
        """配置文本标签样式"""
        # 基础标签
        self.text_widget.tag_configure("timestamp", foreground="#808080")
        self.text_widget.tag_configure("level", foreground="#808080")
        self.text_widget.tag_configure("module", foreground="#808080")
        self.text_widget.tag_configure("message", foreground="#ffffff")
        self.text_widget.tag_configure("extras", foreground="#808080")

        # 日志级别颜色标签
        for level, color in self.formatter.level_colors.items():
            self.text_widget.tag_configure(f"level_{level}", foreground=color)

        # 模块颜色标签
        for module, color in self.formatter.module_colors.items():
            self.text_widget.tag_configure(f"module_{module}", foreground=color)

    def set_log_index(self, log_index):
        """设置日志索引数据源"""
        self.log_index = log_index
        self.current_page = 0
        self.refresh_display()

    def refresh_display(self):
        """刷新显示"""
        if not self.log_index:
            self.text_widget.delete(1.0, tk.END)
            return

        # 清空显示
        self.text_widget.delete(1.0, tk.END)

        # 批量加载和显示日志
        total_count = self.log_index.get_filtered_count()
        if total_count == 0:
            self.text_widget.insert(tk.END, "没有符合条件的日志记录\n")
            return

        # 计算显示范围
        start_index = 0
        end_index = min(total_count, self.max_display_lines)

        # 批量处理和显示
        batch_size = 100
        for batch_start in range(start_index, end_index, batch_size):
            batch_end = min(batch_start + batch_size, end_index)
            self.display_batch(batch_start, batch_end)

            # 让UI有机会响应
            self.parent.update_idletasks()

        # 滚动到底部（如果需要）
        self.text_widget.see(tk.END)

    def display_batch(self, start_index, end_index):
        """批量显示日志条目"""
        for i in range(start_index, end_index):
            log_entry = self.log_index.get_entry_at_filtered_position(i)
            if log_entry:
                self.append_entry(log_entry, scroll=False)

    def append_entry(self, log_entry, scroll=True):
        """将单个日志条目附加到文本小部件"""
        # 检查在添加新内容之前视图是否已滚动到底部
        should_scroll = scroll and self.text_widget.yview()[1] > 0.99

        parts, tags = self.formatter.format_log_entry(log_entry)
        line_text = " ".join(parts) + "\n"

        # 获取插入前的末尾位置
        start_pos = self.text_widget.index(tk.END + "-1c")
        self.text_widget.insert(tk.END, line_text)

        # 为每个部分应用正确的标签
        current_len = 0
        for part, tag_name in zip(parts, tags, strict=False):
            start_index = f"{start_pos}+{current_len}c"
            end_index = f"{start_pos}+{current_len + len(part)}c"
            self.text_widget.tag_add(tag_name, start_index, end_index)
            current_len += len(part) + 1  # 计入空格

        if should_scroll:
            self.text_widget.see(tk.END)


class AsyncLogLoader:
    """异步日志加载器"""

    def __init__(self, callback):
        self.callback = callback
        self.loading = False
        self.should_stop = False

    def load_file_async(self, file_path, progress_callback=None):
        """异步加载日志文件"""
        if self.loading:
            return

        self.loading = True
        self.should_stop = False

        def load_worker():
            try:
                log_index = LogIndex()

                if not os.path.exists(file_path):
                    self.callback(log_index, "文件不存在")
                    return

                file_size = os.path.getsize(file_path)
                processed_size = 0

                with open(file_path, "r", encoding="utf-8") as f:
                    line_count = 0
                    batch_size = 1000  # 批量处理

                    while not self.should_stop:
                        lines = []
                        for _ in range(batch_size):
                            line = f.readline()
                            if not line:
                                break
                            lines.append(line)
                            processed_size += len(line.encode("utf-8"))

                        if not lines:
                            break

                        # 处理这批数据
                        for line in lines:
                            try:
                                log_entry = json.loads(line.strip())
                                log_index.add_entry(line_count, log_entry)
                                line_count += 1
                            except json.JSONDecodeError:
                                continue

                        # 更新进度
                        if progress_callback:
                            progress = min(100, (processed_size / file_size) * 100)
                            progress_callback(progress, line_count)

                if not self.should_stop:
                    self.callback(log_index, None)

            except Exception as e:
                self.callback(None, str(e))
            finally:
                self.loading = False

        thread = threading.Thread(target=load_worker)
        thread.daemon = True
        thread.start()

    def stop_loading(self):
        """停止加载"""
        self.should_stop = True
        self.loading = False


class LogViewer:
    def __init__(self, root):
        self.root = root
        self.root.title("MaiBot日志查看器 (优化版)")
        self.root.geometry("1200x800")

        # 加载配置
        self.load_config()

        # 初始化日志格式化器
        self.formatter = LogFormatter(self.log_config, self.custom_module_colors, self.custom_level_colors)

        # 初始化日志文件路径
        self.current_log_file = Path("logs/app.log.jsonl")
        self.last_file_size = 0
        self.watching_thread = None
        self.is_watching = tk.BooleanVar(value=True)

        # 初始化异步加载器
        self.async_loader = AsyncLogLoader(self.on_file_loaded)

        # 初始化日志索引
        self.log_index = LogIndex()

        # 创建主框架
        self.main_frame = ttk.Frame(root)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 创建菜单栏
        self.create_menu()

        # 创建控制面板
        self.create_control_panel()

        # 创建虚拟滚动日志显示区域
        self.log_display = VirtualLogDisplay(self.main_frame, self.formatter)
        self.log_display.pack(fill=tk.BOTH, expand=True)

        # 模块名映射
        self.module_name_mapping = {
            "api": "API接口",
            "async_task_manager": "异步任务管理器",
            "background_tasks": "后台任务",
            "base_tool": "基础工具",
            "chat_stream": "聊天流",
            "component_registry": "组件注册器",
            "config": "配置",
            "database_model": "数据库模型",
            "emoji": "表情",
            "heartflow": "心流",
            "local_storage": "本地存储",
            "lpmm": "LPMM",
            "maibot_statistic": "MaiBot统计",
            "main_message": "主消息",
            "main": "主程序",
            "memory": "内存",
            "mood": "情绪",
            "plugin_manager": "插件管理器",
            "remote": "远程",
            "willing": "意愿",
        }

        # 加载自定义映射
        self.load_module_mapping()

        # 选中的模块集合
        self.selected_modules = set()
        self.modules = set()

        # 绑定事件
        self.level_combo.bind("<<ComboboxSelected>>", self.filter_logs)
        self.search_var.trace("w", self.filter_logs)

        # 绑定快捷键
        self.root.bind("<Control-o>", lambda e: self.select_log_file())
        self.root.bind("<F5>", lambda e: self.refresh_log_file())
        self.root.bind("<Control-s>", lambda e: self.export_logs())

        # 初始加载文件
        if self.current_log_file.exists():
            self.load_log_file_async()

    def load_config(self):
        """加载配置文件"""
        # 默认配置
        self.default_config = {
            "log": {"date_style": "m-d H:i:s", "log_level_style": "lite", "color_text": "full", "log_level": "INFO"},
            "viewer": {
                "theme": "dark",
                "font_size": 10,
                "max_lines": 1000,
                "auto_scroll": True,
                "show_milliseconds": False,
                "window": {"width": 1200, "height": 800, "remember_position": True},
            },
        }

        # 从bot_config.toml加载日志配置
        config_path = Path("config/bot_config.toml")
        self.log_config = self.default_config["log"].copy()
        self.viewer_config = self.default_config["viewer"].copy()

        try:
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    bot_config = toml.load(f)
                    if "log" in bot_config:
                        self.log_config.update(bot_config["log"])
        except Exception as e:
            print(f"加载bot配置失败: {e}")

        # 从viewer配置文件加载查看器配置
        viewer_config_path = Path("config/log_viewer_config.toml")
        self.custom_module_colors = {}
        self.custom_level_colors = {}

        try:
            if viewer_config_path.exists():
                with open(viewer_config_path, "r", encoding="utf-8") as f:
                    viewer_config = toml.load(f)
                    if "viewer" in viewer_config:
                        self.viewer_config.update(viewer_config["viewer"])

                        # 加载自定义模块颜色
                        if "module_colors" in viewer_config["viewer"]:
                            self.custom_module_colors = viewer_config["viewer"]["module_colors"]

                        # 加载自定义级别颜色
                        if "level_colors" in viewer_config["viewer"]:
                            self.custom_level_colors = viewer_config["viewer"]["level_colors"]

                    if "log" in viewer_config:
                        self.log_config.update(viewer_config["log"])
        except Exception as e:
            print(f"加载查看器配置失败: {e}")

        # 应用窗口配置
        window_config = self.viewer_config.get("window", {})
        window_width = window_config.get("width", 1200)
        window_height = window_config.get("height", 800)
        self.root.geometry(f"{window_width}x{window_height}")

    def save_viewer_config(self):
        """保存查看器配置"""
        # 准备完整的配置数据
        viewer_config_copy = self.viewer_config.copy()

        # 保存自定义颜色（只保存与默认值不同的颜色）
        if self.custom_module_colors:
            viewer_config_copy["module_colors"] = self.custom_module_colors
        if self.custom_level_colors:
            viewer_config_copy["level_colors"] = self.custom_level_colors

        config_data = {"log": self.log_config, "viewer": viewer_config_copy}

        config_path = Path("config/log_viewer_config.toml")
        config_path.parent.mkdir(exist_ok=True)

        try:
            with open(config_path, "w", encoding="utf-8") as f:
                toml.dump(config_data, f)
        except Exception as e:
            print(f"保存查看器配置失败: {e}")

    def create_menu(self):
        """创建菜单栏"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # 配置菜单
        config_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="配置", menu=config_menu)
        config_menu.add_command(label="日志格式设置", command=self.show_format_settings)
        config_menu.add_command(label="颜色设置", command=self.show_color_settings)
        config_menu.add_command(label="查看器设置", command=self.show_viewer_settings)
        config_menu.add_separator()
        config_menu.add_command(label="重新加载配置", command=self.reload_config)

        # 文件菜单
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="选择日志文件", command=self.select_log_file, accelerator="Ctrl+O")
        file_menu.add_command(label="刷新当前文件", command=self.refresh_log_file, accelerator="F5")
        file_menu.add_separator()
        file_menu.add_command(label="导出当前日志", command=self.export_logs, accelerator="Ctrl+S")

        # 工具菜单
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="工具", menu=tools_menu)
        tools_menu.add_command(label="清空日志显示", command=self.clear_log_display)

    def show_format_settings(self):
        """显示格式设置窗口"""
        format_window = tk.Toplevel(self.root)
        format_window.title("日志格式设置")
        format_window.geometry("400x300")

        frame = ttk.Frame(format_window)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 日期格式
        ttk.Label(frame, text="日期格式:").pack(anchor="w", pady=2)
        date_style_var = tk.StringVar(value=self.log_config.get("date_style", "m-d H:i:s"))
        date_entry = ttk.Entry(frame, textvariable=date_style_var, width=30)
        date_entry.pack(anchor="w", pady=2)
        ttk.Label(frame, text="格式说明: Y=年份, m=月份, d=日期, H=小时, i=分钟, s=秒", font=("", 8)).pack(
            anchor="w", pady=2
        )

        # 日志级别样式
        ttk.Label(frame, text="日志级别样式:").pack(anchor="w", pady=(10, 2))
        level_style_var = tk.StringVar(value=self.log_config.get("log_level_style", "lite"))
        level_frame = ttk.Frame(frame)
        level_frame.pack(anchor="w", pady=2)

        ttk.Radiobutton(level_frame, text="简洁(lite)", variable=level_style_var, value="lite").pack(
            side="left", padx=(0, 10)
        )
        ttk.Radiobutton(level_frame, text="紧凑(compact)", variable=level_style_var, value="compact").pack(
            side="left", padx=(0, 10)
        )
        ttk.Radiobutton(level_frame, text="完整(full)", variable=level_style_var, value="full").pack(
            side="left", padx=(0, 10)
        )

        # 颜色文本设置
        ttk.Label(frame, text="文本颜色设置:").pack(anchor="w", pady=(10, 2))
        color_text_var = tk.StringVar(value=self.log_config.get("color_text", "full"))
        color_frame = ttk.Frame(frame)
        color_frame.pack(anchor="w", pady=2)

        ttk.Radiobutton(color_frame, text="无颜色(none)", variable=color_text_var, value="none").pack(
            side="left", padx=(0, 10)
        )
        ttk.Radiobutton(color_frame, text="仅标题(title)", variable=color_text_var, value="title").pack(
            side="left", padx=(0, 10)
        )
        ttk.Radiobutton(color_frame, text="全部(full)", variable=color_text_var, value="full").pack(
            side="left", padx=(0, 10)
        )

        # 按钮
        button_frame = ttk.Frame(frame)
        button_frame.pack(fill="x", pady=(20, 0))

        def apply_format():
            self.log_config["date_style"] = date_style_var.get()
            self.log_config["log_level_style"] = level_style_var.get()
            self.log_config["color_text"] = color_text_var.get()

            # 重新初始化格式化器
            self.formatter = LogFormatter(self.log_config, self.custom_module_colors, self.custom_level_colors)
            self.log_display.formatter = self.formatter
            self.log_display.configure_text_tags()

            # 保存配置
            self.save_viewer_config()

            # 重新过滤日志以应用新格式
            self.filter_logs()

            format_window.destroy()

        ttk.Button(button_frame, text="应用", command=apply_format).pack(side="right", padx=(5, 0))
        ttk.Button(button_frame, text="取消", command=format_window.destroy).pack(side="right")

    def show_viewer_settings(self):
        """显示查看器设置窗口"""
        viewer_window = tk.Toplevel(self.root)
        viewer_window.title("查看器设置")
        viewer_window.geometry("350x250")

        frame = ttk.Frame(viewer_window)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 主题设置
        ttk.Label(frame, text="主题:").pack(anchor="w", pady=2)
        theme_var = tk.StringVar(value=self.viewer_config.get("theme", "dark"))
        theme_frame = ttk.Frame(frame)
        theme_frame.pack(anchor="w", pady=2)
        ttk.Radiobutton(theme_frame, text="深色", variable=theme_var, value="dark").pack(side="left", padx=(0, 10))
        ttk.Radiobutton(theme_frame, text="浅色", variable=theme_var, value="light").pack(side="left")

        # 字体大小
        ttk.Label(frame, text="字体大小:").pack(anchor="w", pady=(10, 2))
        font_size_var = tk.IntVar(value=self.viewer_config.get("font_size", 10))
        font_size_spin = ttk.Spinbox(frame, from_=8, to=20, textvariable=font_size_var, width=10)
        font_size_spin.pack(anchor="w", pady=2)

        # 最大行数
        ttk.Label(frame, text="最大显示行数:").pack(anchor="w", pady=(10, 2))
        max_lines_var = tk.IntVar(value=self.viewer_config.get("max_lines", 1000))
        max_lines_spin = ttk.Spinbox(frame, from_=100, to=10000, increment=100, textvariable=max_lines_var, width=10)
        max_lines_spin.pack(anchor="w", pady=2)

        # 自动滚动
        auto_scroll_var = tk.BooleanVar(value=self.viewer_config.get("auto_scroll", True))
        ttk.Checkbutton(frame, text="自动滚动到底部", variable=auto_scroll_var).pack(anchor="w", pady=(10, 2))

        # 按钮
        button_frame = ttk.Frame(frame)
        button_frame.pack(fill="x", pady=(20, 0))

        def apply_viewer_settings():
            self.viewer_config["theme"] = theme_var.get()
            self.viewer_config["font_size"] = font_size_var.get()
            self.viewer_config["max_lines"] = max_lines_var.get()
            self.viewer_config["auto_scroll"] = auto_scroll_var.get()

            # 应用主题
            self.apply_theme()

            # 保存配置
            self.save_viewer_config()

            viewer_window.destroy()

        ttk.Button(button_frame, text="应用", command=apply_viewer_settings).pack(side="right", padx=(5, 0))
        ttk.Button(button_frame, text="取消", command=viewer_window.destroy).pack(side="right")

    def apply_theme(self):
        """应用主题设置"""
        theme = self.viewer_config.get("theme", "dark")
        font_size = self.viewer_config.get("font_size", 10)

        # 更新虚拟显示组件的主题
        if theme == "dark":
            bg_color = "#1e1e1e"
            fg_color = "#ffffff"
            select_bg = "#404040"
        else:
            bg_color = "#ffffff"
            fg_color = "#000000"
            select_bg = "#c0c0c0"

        self.log_display.text_widget.config(
            background=bg_color, foreground=fg_color, selectbackground=select_bg, font=("Consolas", font_size)
        )

        # 重新配置标签样式
        self.log_display.configure_text_tags()

    def reload_config(self):
        """重新加载配置"""
        self.load_config()
        self.formatter = LogFormatter(self.log_config, self.custom_module_colors, self.custom_level_colors)
        self.log_display.formatter = self.formatter
        self.log_display.configure_text_tags()
        self.apply_theme()
        self.filter_logs()

    def clear_log_display(self):
        """清空日志显示"""
        self.log_display.text_widget.delete(1.0, tk.END)

    def export_logs(self):
        """导出当前显示的日志"""
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt", filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
        )
        if filename:
            try:
                # 获取当前显示的所有日志条目
                if self.log_index:
                    filtered_count = self.log_index.get_filtered_count()
                    log_lines = []
                    for i in range(filtered_count):
                        log_entry = self.log_index.get_entry_at_filtered_position(i)
                        if log_entry:
                            parts, tags = self.formatter.format_log_entry(log_entry)
                            line_text = " ".join(parts)
                            log_lines.append(line_text)

                    with open(filename, "w", encoding="utf-8") as f:
                        f.write("\n".join(log_lines))
                    messagebox.showinfo("导出成功", f"日志已导出到: {filename}")
                else:
                    messagebox.showwarning("导出失败", "没有日志可导出")
            except Exception as e:
                messagebox.showerror("导出失败", f"导出日志时出错: {e}")

    def load_module_mapping(self):
        """加载自定义模块映射"""
        mapping_file = Path("config/module_mapping.json")
        if mapping_file.exists():
            try:
                with open(mapping_file, "r", encoding="utf-8") as f:
                    custom_mapping = json.load(f)
                    self.module_name_mapping.update(custom_mapping)
            except Exception as e:
                print(f"加载模块映射失败: {e}")

    def save_module_mapping(self):
        """保存自定义模块映射"""
        mapping_file = Path("config/module_mapping.json")
        mapping_file.parent.mkdir(exist_ok=True)
        try:
            with open(mapping_file, "w", encoding="utf-8") as f:
                json.dump(self.module_name_mapping, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存模块映射失败: {e}")

    def show_color_settings(self):
        """显示颜色设置窗口"""
        color_window = tk.Toplevel(self.root)
        color_window.title("颜色设置")
        color_window.geometry("300x400")

        # 创建滚动框架
        frame = ttk.Frame(color_window)
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 创建滚动条
        scrollbar = ttk.Scrollbar(frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 创建颜色设置列表
        canvas = tk.Canvas(frame, yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=canvas.yview)

        # 创建内部框架
        inner_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner_frame, anchor="nw")

        # 添加日志级别颜色设置
        ttk.Label(inner_frame, text="日志级别颜色", font=("", 10, "bold")).pack(anchor="w", padx=5, pady=5)
        for level in ["info", "warning", "error"]:
            frame = ttk.Frame(inner_frame)
            frame.pack(fill=tk.X, padx=5, pady=2)
            ttk.Label(frame, text=level).pack(side=tk.LEFT)
            color_btn = ttk.Button(
                frame, text="选择颜色", command=lambda level_name=level: self.choose_color(level_name)
            )
            color_btn.pack(side=tk.RIGHT)
            # 显示当前颜色
            color_label = ttk.Label(frame, text="■", foreground=self.formatter.level_colors[level])
            color_label.pack(side=tk.RIGHT, padx=5)

        # 添加模块颜色设置
        ttk.Label(inner_frame, text="\n模块颜色", font=("", 10, "bold")).pack(anchor="w", padx=5, pady=5)
        for module in sorted(self.modules):
            frame = ttk.Frame(inner_frame)
            frame.pack(fill=tk.X, padx=5, pady=2)
            ttk.Label(frame, text=module).pack(side=tk.LEFT)
            color_btn = ttk.Button(frame, text="选择颜色", command=lambda m=module: self.choose_module_color(m))
            color_btn.pack(side=tk.RIGHT)
            # 显示当前颜色
            color = self.formatter.module_colors.get(module, "black")
            color_label = ttk.Label(frame, text="■", foreground=color)
            color_label.pack(side=tk.RIGHT, padx=5)

        # 更新画布滚动区域
        inner_frame.update_idletasks()
        canvas.config(scrollregion=canvas.bbox("all"))

        # 添加确定按钮
        ttk.Button(color_window, text="确定", command=color_window.destroy).pack(pady=5)

    def choose_color(self, level):
        """选择日志级别颜色"""
        color = colorchooser.askcolor(color=self.formatter.level_colors[level])[1]
        if color:
            self.formatter.level_colors[level] = color
            self.custom_level_colors[level] = color  # 保存到自定义颜色
            self.log_display.formatter = self.formatter
            self.log_display.configure_text_tags()
            self.save_viewer_config()  # 自动保存配置
            self.filter_logs()

    def choose_module_color(self, module):
        """选择模块颜色"""
        color = colorchooser.askcolor(color=self.formatter.module_colors.get(module, "black"))[1]
        if color:
            self.formatter.module_colors[module] = color
            self.custom_module_colors[module] = color  # 保存到自定义颜色
            self.log_display.formatter = self.formatter
            self.log_display.configure_text_tags()
            self.save_viewer_config()  # 自动保存配置
            self.filter_logs()

    def create_control_panel(self):
        """创建控制面板"""
        # 控制面板
        self.control_frame = ttk.Frame(self.main_frame)
        self.control_frame.pack(fill=tk.X, pady=(0, 5))

        # 文件选择框架
        self.file_frame = ttk.LabelFrame(self.control_frame, text="日志文件")
        self.file_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=(0, 5))

        # 当前文件显示
        self.current_file_var = tk.StringVar(value=str(self.current_log_file))
        self.file_label = ttk.Label(self.file_frame, textvariable=self.current_file_var, foreground="blue")
        self.file_label.pack(side=tk.LEFT, padx=5, pady=2)

        # 进度条
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(self.file_frame, variable=self.progress_var, length=200)
        self.progress_bar.pack(side=tk.LEFT, padx=5, pady=2)
        self.progress_bar.pack_forget()

        # 状态标签
        self.status_var = tk.StringVar(value="就绪")
        self.status_label = ttk.Label(self.file_frame, textvariable=self.status_var)
        self.status_label.pack(side=tk.LEFT, padx=5, pady=2)

        # 按钮区域
        button_frame = ttk.Frame(self.file_frame)
        button_frame.pack(side=tk.RIGHT, padx=5, pady=2)

        ttk.Button(button_frame, text="选择文件", command=self.select_log_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(button_frame, text="刷新", command=self.refresh_log_file).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(button_frame, text="实时更新", variable=self.is_watching, command=self.toggle_watching).pack(
            side=tk.LEFT, padx=2
        )

        # 模块选择框架
        self.module_frame = ttk.LabelFrame(self.control_frame, text="模块")
        self.module_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # 创建模块选择滚动区域
        self.module_canvas = tk.Canvas(self.module_frame, height=80)
        self.module_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 创建模块选择内部框架
        self.module_inner_frame = ttk.Frame(self.module_canvas)
        self.module_canvas.create_window((0, 0), window=self.module_inner_frame, anchor="nw")

        # 创建右侧控制区域（级别和搜索）
        self.right_control_frame = ttk.Frame(self.control_frame)
        self.right_control_frame.pack(side=tk.RIGHT, padx=5)

        # 映射编辑按钮
        mapping_btn = ttk.Button(self.right_control_frame, text="模块映射", command=self.edit_module_mapping)
        mapping_btn.pack(side=tk.TOP, fill=tk.X, pady=1)

        # 日志级别选择
        level_frame = ttk.Frame(self.right_control_frame)
        level_frame.pack(side=tk.TOP, fill=tk.X, pady=1)
        ttk.Label(level_frame, text="级别:").pack(side=tk.LEFT, padx=2)
        self.level_var = tk.StringVar(value="全部")
        self.level_combo = ttk.Combobox(level_frame, textvariable=self.level_var, width=8)
        self.level_combo["values"] = ["全部", "debug", "info", "warning", "error", "critical"]
        self.level_combo.pack(side=tk.LEFT, padx=2)

        # 搜索框
        search_frame = ttk.Frame(self.right_control_frame)
        search_frame.pack(side=tk.TOP, fill=tk.X, pady=1)
        ttk.Label(search_frame, text="搜索:").pack(side=tk.LEFT, padx=2)
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=15)
        self.search_entry.pack(side=tk.LEFT, padx=2)

    def on_file_loaded(self, log_index, error):
        """文件加载完成回调"""
        self.progress_bar.pack_forget()

        if error:
            self.status_var.set(f"加载失败: {error}")
            messagebox.showerror("错误", f"加载日志文件失败: {error}")
            return

        self.log_index = log_index
        try:
            self.last_file_size = os.path.getsize(self.current_log_file)
        except OSError:
            self.last_file_size = 0
        self.status_var.set(f"已加载 {log_index.total_entries} 条日志")

        # 更新模块列表
        self.modules = set(log_index.module_index.keys())
        self.update_module_list()

        # 应用过滤并显示
        self.filter_logs()

        # 如果开启了实时更新，则开始监视
        if self.is_watching.get():
            self.start_watching()

    def on_loading_progress(self, progress, line_count):
        """加载进度回调"""
        self.root.after(0, lambda: self.update_progress(progress, line_count))

    def update_progress(self, progress, line_count):
        """更新进度显示"""
        self.progress_var.set(progress)
        self.status_var.set(f"正在加载... {line_count} 条 ({progress:.1f}%)")

    def load_log_file_async(self):
        """异步加载日志文件"""
        self.stop_watching()  # 停止任何正在运行的监视器

        if not self.current_log_file.exists():
            self.status_var.set("文件不存在")
            return

        # 显示进度条
        self.progress_bar.pack(side=tk.LEFT, padx=5, pady=2, before=self.status_label)
        self.progress_var.set(0)
        self.status_var.set("正在加载...")

        # 清空当前数据
        self.log_index = LogIndex()
        self.selected_modules.clear()

        # 开始异步加载
        self.async_loader.load_file_async(str(self.current_log_file), self.on_loading_progress)

    def filter_logs(self, *args):
        """过滤日志"""
        if not self.log_index:
            return

        # 获取过滤条件
        selected_modules = self.selected_modules if self.selected_modules else None
        level = self.level_var.get() if self.level_var.get() != "全部" else None
        search_text = self.search_var.get().strip() if self.search_var.get().strip() else None

        # 应用过滤
        self.log_index.filter_entries(selected_modules, level, search_text)

        # 更新显示
        self.log_display.set_log_index(self.log_index)

        # 更新状态
        filtered_count = self.log_index.get_filtered_count()
        total_count = self.log_index.total_entries
        if filtered_count == total_count:
            self.status_var.set(f"显示 {total_count} 条日志")
        else:
            self.status_var.set(f"显示 {filtered_count}/{total_count} 条日志")

    def select_log_file(self):
        """选择日志文件"""
        filename = filedialog.askopenfilename(
            title="选择日志文件",
            filetypes=[("JSONL日志文件", "*.jsonl"), ("所有文件", "*.*")],
            initialdir="logs" if Path("logs").exists() else ".",
        )
        if filename:
            new_file = Path(filename)
            if new_file != self.current_log_file:
                self.current_log_file = new_file
                self.current_file_var.set(str(self.current_log_file))
                self.load_log_file_async()

    def refresh_log_file(self):
        """刷新日志文件"""
        self.load_log_file_async()

    def toggle_watching(self):
        """切换实时更新状态"""
        if self.is_watching.get():
            self.start_watching()
        else:
            self.stop_watching()

    def start_watching(self):
        """开始监视文件变化"""
        if self.watching_thread and self.watching_thread.is_alive():
            return  # 已经在监视

        if not self.current_log_file.exists():
            self.is_watching.set(False)
            messagebox.showwarning("警告", "日志文件不存在，无法开启实时更新。")
            return

        self.watching_thread = threading.Thread(target=self.watch_file_loop, daemon=True)
        self.watching_thread.start()

    def stop_watching(self):
        """停止监视文件变化"""
        self.is_watching.set(False)
        # 线程通过检查 is_watching 变量来停止，这里不需要强制干预
        self.watching_thread = None

    def watch_file_loop(self):
        """监视文件循环"""
        while self.is_watching.get():
            try:
                if not self.current_log_file.exists():
                    self.root.after(
                        0,
                        lambda: messagebox.showwarning("警告", "日志文件丢失，已停止实时更新。"),
                    )
                    self.root.after(0, self.is_watching.set, False)
                    break

                current_size = os.path.getsize(self.current_log_file)
                if current_size > self.last_file_size:
                    new_entries = self.read_new_logs(self.last_file_size)
                    self.last_file_size = current_size
                    if new_entries:
                        self.root.after(0, self.append_new_logs, new_entries)
                elif current_size < self.last_file_size:
                    # 文件被截断或替换
                    self.last_file_size = 0
                    self.root.after(0, self.refresh_log_file)
                    break  # 刷新会重新启动监视（如果需要），所以结束当前循环

            except Exception as e:
                print(f"监视日志文件时出错: {e}")
                self.root.after(0, self.is_watching.set, False)
                break

            time.sleep(1)

        self.watching_thread = None

    def read_new_logs(self, from_position):
        """读取新的日志条目并返回它们"""
        new_entries = []
        new_modules = set()  # 收集新发现的模块
        with open(self.current_log_file, "r", encoding="utf-8") as f:
            f.seek(from_position)
            line_count = self.log_index.total_entries
            for line in f:
                if line.strip():
                    try:
                        log_entry = json.loads(line)
                        self.log_index.add_entry(line_count, log_entry)
                        new_entries.append(log_entry)

                        logger_name = log_entry.get("logger_name", "")
                        if logger_name and logger_name not in self.modules:
                            new_modules.add(logger_name)

                        line_count += 1
                    except json.JSONDecodeError:
                        continue

        # 如果发现了新模块，在主线程中更新模块集合
        if new_modules:

            def update_modules():
                self.modules.update(new_modules)
                self.update_module_list()

            self.root.after(0, update_modules)

        return new_entries

    def append_new_logs(self, new_entries):
        """将新日志附加到显示中"""
        # 检查是否应附加或执行完全刷新（例如，如果过滤器处于活动状态）
        selected_modules = (
            self.selected_modules if (self.selected_modules and "全部" not in self.selected_modules) else None
        )
        level = self.level_var.get() if self.level_var.get() != "全部" else None
        search_text = self.search_var.get().strip() if self.search_var.get().strip() else None

        is_filtered = selected_modules or level or search_text

        if is_filtered:
            # 如果过滤器处于活动状态，我们必须执行完全刷新以应用它们
            self.filter_logs()
            return

        # 如果没有过滤器，只需附加新日志
        for entry in new_entries:
            self.log_display.append_entry(entry)

        # 更新状态
        total_count = self.log_index.total_entries
        self.status_var.set(f"显示 {total_count} 条日志")

    def update_module_list(self):
        """更新模块列表"""
        # 清空现有选项
        for widget in self.module_inner_frame.winfo_children():
            widget.destroy()

        # 计算总模块数（包括"全部"）
        total_modules = len(self.modules) + 1
        max_cols = min(4, max(2, total_modules))  # 减少最大列数，避免超出边界

        # 配置网格列权重，让每列平均分配空间
        for i in range(max_cols):
            self.module_inner_frame.grid_columnconfigure(i, weight=1, uniform="module_col")

        # 创建一个多行布局
        current_row = 0
        current_col = 0

        # 添加"全部"选项
        all_frame = ttk.Frame(self.module_inner_frame)
        all_frame.grid(row=current_row, column=current_col, padx=3, pady=2, sticky="ew")

        all_var = tk.BooleanVar(value="全部" in self.selected_modules)
        all_check = ttk.Checkbutton(
            all_frame, text="全部", variable=all_var, command=lambda: self.toggle_module("全部", all_var)
        )
        all_check.pack(side=tk.LEFT)

        # 使用颜色标签替代按钮
        all_color = self.formatter.module_colors.get("全部", "black")
        all_color_label = ttk.Label(all_frame, text="■", foreground=all_color, width=2, cursor="hand2")
        all_color_label.pack(side=tk.LEFT, padx=2)
        all_color_label.bind("<Button-1>", lambda e: self.choose_module_color("全部"))

        current_col += 1

        # 添加其他模块选项
        for module in sorted(self.modules):
            if current_col >= max_cols:
                current_row += 1
                current_col = 0

            frame = ttk.Frame(self.module_inner_frame)
            frame.grid(row=current_row, column=current_col, padx=3, pady=2, sticky="ew")

            var = tk.BooleanVar(value=module in self.selected_modules)

            # 使用中文映射名称显示
            display_name = self.get_display_name(module)
            if len(display_name) > 12:
                display_name = display_name[:10] + "..."

            check = ttk.Checkbutton(
                frame, text=display_name, variable=var, command=lambda m=module, v=var: self.toggle_module(m, v)
            )
            check.pack(side=tk.LEFT)

            # 添加工具提示显示完整名称和英文名
            full_tooltip = f"{self.get_display_name(module)}"
            if module != self.get_display_name(module):
                full_tooltip += f"\n({module})"
            self.create_tooltip(check, full_tooltip)

            # 使用颜色标签替代按钮
            color = self.formatter.module_colors.get(module, "black")
            color_label = ttk.Label(frame, text="■", foreground=color, width=2, cursor="hand2")
            color_label.pack(side=tk.LEFT, padx=2)
            color_label.bind("<Button-1>", lambda e, m=module: self.choose_module_color(m))

            current_col += 1

        # 更新画布滚动区域
        self.module_inner_frame.update_idletasks()
        self.module_canvas.config(scrollregion=self.module_canvas.bbox("all"))

        # 添加垂直滚动条
        if not hasattr(self, "module_scrollbar"):
            self.module_scrollbar = ttk.Scrollbar(
                self.module_frame, orient=tk.VERTICAL, command=self.module_canvas.yview
            )
            self.module_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            self.module_canvas.config(yscrollcommand=self.module_scrollbar.set)

    def create_tooltip(self, widget, text):
        """为控件创建工具提示"""

        def on_enter(event):
            tooltip = tk.Toplevel()
            tooltip.wm_overrideredirect(True)
            tooltip.wm_geometry(f"+{event.x_root + 10}+{event.y_root + 10}")
            label = ttk.Label(tooltip, text=text, background="lightyellow", relief="solid", borderwidth=1)
            label.pack()
            widget.tooltip = tooltip

        def on_leave(event):
            if hasattr(widget, "tooltip"):
                widget.tooltip.destroy()
                del widget.tooltip

        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)

    def toggle_module(self, module, var):
        """切换模块选择状态"""
        if module == "全部":
            if var.get():
                self.selected_modules = {"全部"}
            else:
                self.selected_modules.clear()
        else:
            if var.get():
                self.selected_modules.add(module)
                if "全部" in self.selected_modules:
                    self.selected_modules.remove("全部")
            else:
                self.selected_modules.discard(module)

        self.filter_logs()

    def get_display_name(self, module_name):
        """获取模块的显示名称"""
        return self.module_name_mapping.get(module_name, module_name)

    def edit_module_mapping(self):
        """编辑模块映射"""
        mapping_window = tk.Toplevel(self.root)
        mapping_window.title("编辑模块映射")
        mapping_window.geometry("500x600")

        # 创建滚动框架
        frame = ttk.Frame(mapping_window)
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 创建滚动条
        scrollbar = ttk.Scrollbar(frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 创建映射编辑列表
        canvas = tk.Canvas(frame, yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=canvas.yview)

        # 创建内部框架
        inner_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner_frame, anchor="nw")

        # 添加标题
        ttk.Label(inner_frame, text="模块映射编辑", font=("", 12, "bold")).pack(anchor="w", padx=5, pady=5)
        ttk.Label(inner_frame, text="英文名 -> 中文名", font=("", 10)).pack(anchor="w", padx=5, pady=2)

        # 映射编辑字典
        mapping_vars = {}

        # 添加现有模块的映射编辑
        all_modules = sorted(self.modules)
        for module in all_modules:
            frame_row = ttk.Frame(inner_frame)
            frame_row.pack(fill=tk.X, padx=5, pady=2)

            ttk.Label(frame_row, text=module, width=20).pack(side=tk.LEFT, padx=5)
            ttk.Label(frame_row, text="->").pack(side=tk.LEFT, padx=5)

            var = tk.StringVar(value=self.module_name_mapping.get(module, module))
            mapping_vars[module] = var
            entry = ttk.Entry(frame_row, textvariable=var, width=25)
            entry.pack(side=tk.LEFT, padx=5)

        # 更新画布滚动区域
        inner_frame.update_idletasks()
        canvas.config(scrollregion=canvas.bbox("all"))

        def save_mappings():
            # 更新映射
            for module, var in mapping_vars.items():
                new_name = var.get().strip()
                if new_name and new_name != module:
                    self.module_name_mapping[module] = new_name
                elif module in self.module_name_mapping and not new_name:
                    del self.module_name_mapping[module]

            # 保存到文件
            self.save_module_mapping()
            # 更新模块列表显示
            self.update_module_list()
            mapping_window.destroy()

        # 添加按钮
        button_frame = ttk.Frame(mapping_window)
        button_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(button_frame, text="保存", command=save_mappings).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="取消", command=mapping_window.destroy).pack(side=tk.RIGHT, padx=5)


def main():
    root = tk.Tk()
    LogViewer(root)
    root.mainloop()


if __name__ == "__main__":
    main()
