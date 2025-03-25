import json
import re
import warnings
import gradio as gr
import os
import signal
import sys
import requests
import tomli

from dotenv import load_dotenv
from src.common.database import db

try:
    from src.common.logger import get_module_logger

    logger = get_module_logger("emoji_reviewer")
except ImportError:
    from loguru import logger

    # 检查并创建日志目录
    log_dir = "logs/emoji_reviewer"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    # 配置控制台输出格式
    logger.remove()  # 移除默认的处理器
    logger.add(sys.stderr, format="{time:MM-DD HH:mm} | emoji_reviewer | {message}")  # 添加控制台输出
    logger.add("logs/emoji_reviewer/{time:YYYY-MM-DD}.log", rotation="00:00", format="{time:MM-DD HH:mm} | emoji_reviewer | {message}")
    logger.warning("检测到src.common.logger并未导入，将使用默认loguru作为日志记录器")
    logger.warning("如果你是用的是低版本(0.5.13)麦麦，请忽略此警告")
# 忽略 gradio 版本警告
warnings.filterwarnings("ignore", message="IMPORTANT: You are using gradio version.*")

root_dir = os.path.dirname(os.path.abspath(__file__))
bot_config_path = os.path.join(root_dir, "config/bot_config.toml")
if os.path.exists(bot_config_path):
    with open(bot_config_path, "rb") as f:
        try:
            toml_dict = tomli.load(f)
            embedding_config = toml_dict['model']['embedding']
            embedding_name = embedding_config["name"]
            embedding_provider = embedding_config["provider"]
        except tomli.TOMLDecodeError as e:
            logger.critical(f"配置文件bot_config.toml填写有误，请检查第{e.lineno}行第{e.colno}处：{e.msg}")
            exit(1)
        except KeyError as e:
            logger.critical(f"配置文件bot_config.toml缺少model.embedding设置，请补充后再编辑表情包")
            exit(1)
else:
    logger.critical(f"没有找到配置文件{bot_config_path}")
    exit(1)
env_path = os.path.join(root_dir, ".env.prod")
if not os.path.exists(env_path):
    logger.critical(f"没有找到环境变量文件{env_path}")
    exit(1)
load_dotenv(env_path)

tags_choices = ["无", "包括", "排除"]
tags = {
    "reviewed": ("已审查", "排除"),
    "blacklist": ("黑名单", "排除"),
}
format_choices = ["包括", "无"]
formats = ["jpg", "png", "gif"]


def signal_handler(signum, frame):
    """处理 Ctrl+C 信号"""
    logger.info("收到终止信号，正在关闭 Gradio 服务器...")
    sys.exit(0)


# 注册信号处理器
signal.signal(signal.SIGINT, signal_handler)
required_fields = ["_id", "path", "description", "hash", *tags.keys()]  # 修复拼写错误的时候记得把这里的一起改了

emojis_db = list(db.emoji.find({}, {k: 1 for k in required_fields}))
emoji_filtered = []
emoji_show = None

max_num = 20
neglect_update = 0


async def get_embedding(text):
    try:
        base_url = os.environ.get(f"{embedding_provider}_BASE_URL")
        if base_url.endswith('/'):
            url = base_url + 'embeddings'
        else:
            url = base_url + '/embeddings'
        key = os.environ.get(f"{embedding_provider}_KEY")
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": embedding_name,
            "input": text,
            "encoding_format": "float"
        }
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code == 200:
            result = response.json()
            embedding = result["data"][0]["embedding"]
            return embedding
        else:
            return f"网络错误{response.status_code}"
    except:
        return None


def set_max_num(slider):
    global max_num
    max_num = slider


def filter_emojis(tag_filters, format_filters):
    global emoji_filtered
    e_filtered = emojis_db

    format_include = []
    for format, value in format_filters.items():
        if value:
            format_include.append(format)

    if len(format_include) == 0:
        return []

    for tag, value in tag_filters.items():
        if value == "包括":
            e_filtered = [d for d in e_filtered if tag in d]
        elif value == "排除":
            e_filtered = [d for d in e_filtered if tag not in d]

    if len(format_include) > 0:
        ff = '|'.join(format_include)
        pattern = rf"\.({ff})$"
        e_filtered = [d for d in e_filtered if re.search(pattern, d.get("path", ""), re.IGNORECASE)]

    emoji_filtered = e_filtered


def update_gallery(from_latest, *filter_values):
    global emoji_filtered
    tf = filter_values[:len(tags)]
    ff = filter_values[len(tags):]
    filter_emojis({k: v for k, v in zip(tags.keys(), tf)}, {k: v for k, v in zip(formats, ff)})
    if from_latest:
        emoji_filtered.reverse()
    if len(emoji_filtered) > max_num:
        info = f"已筛选{len(emoji_filtered)}个表情包中的{max_num}个。"
        emoji_filtered = emoji_filtered[:max_num]
    else:
        info = f"已筛选{len(emoji_filtered)}个表情包。"
    global emoji_show
    emoji_show = None
    return [gr.update(value=[], selected_index=None, allow_preview=False), info]


def update_gallery2():
    thumbnails = [e.get("path", "") for e in emoji_filtered]
    return gr.update(value=thumbnails, allow_preview=True)


def on_select(evt: gr.SelectData, *tag_values):
    new_index = evt.index
    print(new_index)
    global emoji_show, neglect_update
    if new_index is None:
        emoji_show = None
        targets = []
        for current_value, tag in zip(tag_values, tags.keys()):
            if current_value:
                neglect_update += 1
                targets.append(False)
            else:
                targets.append(gr.update())
        return [
            gr.update(selected_index=new_index),
            "",
            *targets
        ]
    else:
        emoji_show = emoji_filtered[new_index]
        targets = []
        neglect_update = 0
        for current_value, tag in zip(tag_values, tags.keys()):
            target = tag in emoji_show
            if current_value != target:
                neglect_update += 1
                targets.append(target)
            else:
                targets.append(gr.update())
        return [
            gr.update(selected_index=new_index),
            emoji_show.get("description", ""),
            *targets
        ]


def desc_change(desc, edited):
    if emoji_show and desc != emoji_show.get("description", ""):
        if edited:
            return [gr.update(), True]
        else:
            return ["(尚未保存)", True]
    if edited:
        return ["", False]
    else:
        return [gr.update(), False]


def revert_desc():
    if emoji_show:
        return emoji_show.get("description", "")
    else:
        return ""


async def save_desc(desc):
    if emoji_show:
        try:
            yield ["正在构建embedding，请勿关闭页面...", gr.update(interactive=False), gr.update(interactive=False)]
            embedding = await get_embedding(desc)
            if embedding is None or isinstance(embedding, str):
                yield [f"<span style='color: red;'>获取embeddings失败！{embedding}</span>", gr.update(interactive=True), gr.update(interactive=True)]
            else:
                e_id = emoji_show["_id"]
                update_dict = {"$set": {"embedding": embedding, "description": desc}}
                db.emoji.update_one({"_id": e_id}, update_dict)

                e_hash = emoji_show["hash"]
                update_dict = {"$set": {"description": desc}}
                db.images.update_one({"hash": e_hash}, update_dict)
                db.image_descriptions.update_one({"hash": e_hash}, update_dict)
                emoji_show["description"] = desc

                logger.info(f'Update description and embeddings: {e_id}(hash={hash})')
                yield ["保存完成", gr.update(value=desc, interactive=True), gr.update(interactive=True)]
        except Exception as e:
            yield [f"<span style='color: red;'>出现异常: {e}</span>", gr.update(interactive=True), gr.update(interactive=True)]

    else:
        yield ["没有选中表情包", gr.update()]


def change_tag(*tag_values):
    if not emoji_show:
        return gr.update()
    global neglect_update
    if neglect_update > 0:
        neglect_update -= 1
        return gr.update()
    set_dict = {}
    unset_dict = {}
    e_id = emoji_show["_id"]
    for value, tag in zip(tag_values, tags.keys()):
        if value:
            if tag not in emoji_show:
                set_dict[tag] = True
                emoji_show[tag] = True
                logger.info(f'Add tag "{tag}" to {e_id}')
        else:
            if tag in emoji_show:
                unset_dict[tag] = ""
                del emoji_show[tag]
                logger.info(f'Delete tag "{tag}" from {e_id}')

    update_dict = {"$set": set_dict, "$unset": unset_dict}
    db.emoji.update_one({"_id": e_id}, update_dict)
    return "已更新标签状态"


with gr.Blocks(title="MaimBot表情包审查器") as app:
    desc_edit = gr.State(value=False)
    gr.Markdown(
        value="""
        # MaimBot表情包审查器
        """
    )
    gr.Markdown(value="---")  # 添加分割线
    gr.Markdown(value="""
        ## 审查器说明\n
        该审查器用于人工修正识图模型对表情包的识别偏差，以及管理表情包黑名单：\n
        每一个表情包都有描述以及“已审查”和“黑名单”两个标签。描述可以编辑并保存。“黑名单”标签可以禁止麦麦使用该表情包。\n
        作者：遗世紫丁香(HexatomicRing)
        """)
    gr.Markdown(value="---")

    with gr.Row():
        with gr.Column(scale=2):
            info_label = gr.Markdown("")
            gallery = gr.Gallery(label="表情包列表", columns=4, rows=6)
            description = gr.Textbox(label="描述", interactive=True)
            description_label = gr.Markdown("")
            tag_boxes = {
                tag: gr.Checkbox(label=name, interactive=True)
                for tag, (name, _) in tags.items()
            }

            with gr.Row():
                revert_btn = gr.Button("还原描述")
                save_btn = gr.Button("保存描述")

        with gr.Column(scale=1):
            max_num_slider = gr.Slider(label="最大显示数量", minimum=1, maximum=500, value=max_num, interactive=True)
            check_from_latest = gr.Checkbox(label="由新到旧", interactive=True)
            tag_filters = {
                tag: gr.Dropdown(tags_choices, value=value, label=f"{name}筛选")
                for tag, (name, value) in tags.items()
            }
            gr.Markdown(value="---")
            gr.Markdown(value="格式筛选:")
            format_filters = {
                f: gr.Checkbox(label=f, value=True)
                for f in formats
            }
            refresh_btn = gr.Button("刷新筛选")
            filters = list(tag_filters.values()) + list(format_filters.values())

    max_num_slider.change(set_max_num, max_num_slider, None)
    description.change(desc_change, [description, desc_edit], [description_label, desc_edit])
    for component in filters:
        component.change(
            fn=update_gallery,
            inputs=[check_from_latest, *filters],
            outputs=[gallery, info_label],
            preprocess=False
        ).then(
            fn=update_gallery2,
            inputs=None,
            outputs=gallery)
    refresh_btn.click(
        fn=update_gallery,
        inputs=[check_from_latest, *filters],
        outputs=[gallery, info_label],
        preprocess=False
    ).then(
        fn=update_gallery2,
        inputs=None,
        outputs=gallery)
    gallery.select(fn=on_select, inputs=list(tag_boxes.values()), outputs=[gallery, description, *tag_boxes.values()])
    revert_btn.click(fn=revert_desc, inputs=None, outputs=description)
    save_btn.click(fn=save_desc, inputs=description, outputs=[description_label, description, save_btn])
    for k, v in tag_boxes.items():
        v.change(fn=change_tag, inputs=list(tag_boxes.values()), outputs=description_label)
    app.load(
        fn=update_gallery,
        inputs=[check_from_latest, *filters],
        outputs=[gallery, info_label],
        preprocess=False
    ).then(
        fn=update_gallery2,
        inputs=None,
        outputs=gallery)
    app.queue().launch(
        server_name="0.0.0.0",
        inbrowser=True,
        share=False,
        server_port=7001,
        debug=True,
        quiet=True,
    )
