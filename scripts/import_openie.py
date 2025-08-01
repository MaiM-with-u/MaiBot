import sys
import os
from time import sleep
from multiprocessing import Manager

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.chat.knowledge.embedding_store import EmbeddingManager
from src.chat.knowledge.open_ie import OpenIE
from src.chat.knowledge.kg_manager import KGManager
from src.common.logger import get_logger
from src.chat.knowledge.utils.hash import get_sha256
from src.manager.local_store_manager import local_storage
from dotenv import load_dotenv

ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OPENIE_DIR = os.path.join(ROOT_PATH, "data", "openie")
logger = get_logger("OpenIE导入")
ENV_FILE = os.path.join(ROOT_PATH, ".env")

if os.path.exists(".env"):
    load_dotenv(".env", override=True)
    print("成功加载环境变量配置")
else:
    print("未找到.env文件，请确保程序所需的环境变量被正确设置")
    raise FileNotFoundError(".env 文件不存在，请创建并配置所需的环境变量")

env_mask = {key: os.getenv(key) for key in os.environ}
def scan_provider(env_config: dict):
    provider = {}
    env_config = dict(filter(lambda item: item[0] not in env_mask, env_config.items()))
    for key in env_config:
        if key.endswith("_BASE_URL") or key.endswith("_KEY"):
            provider_name = key.split("_", 1)[0]
            if provider_name not in provider:
                provider[provider_name] = {"url": None, "key": None}
            if key.endswith("_BASE_URL"):
                provider[provider_name]["url"] = env_config[key]
            elif key.endswith("_KEY"):
                provider[provider_name]["key"] = env_config[key]
    for provider_name, config in provider.items():
        if config["url"] is None or config["key"] is None:
            logger.error(f"provider 内容：{config}\nenv_config 内容：{env_config}")
            raise ValueError(f"请检查 '{provider_name}' 提供商配置是否丢失 BASE_URL 或 KEY 环境变量")

def ensure_openie_dir():
    os.makedirs(OPENIE_DIR, exist_ok=True)
    logger.info(f"OpenIE数据目录已存在或已创建：{OPENIE_DIR}")

def hash_deduplicate(raw_paragraphs: dict, triple_list_data: dict, stored_pg_hashes: set, stored_paragraph_hashes: set):
    new_raw_paragraphs = {}
    new_triple_list_data = {}
    for pg_hash, raw_paragraph in raw_paragraphs.items():
        if f"{local_storage['pg_namespace']}-{pg_hash}" not in stored_pg_hashes and pg_hash not in stored_paragraph_hashes:
            new_raw_paragraphs[pg_hash] = raw_paragraph
            new_triple_list_data[pg_hash] = triple_list_data[pg_hash]
    return new_raw_paragraphs, new_triple_list_data

def handle_import_openie(openie_data: OpenIE, embed_manager: EmbeddingManager, kg_manager: KGManager, lock) -> bool:
    raw_paragraphs = openie_data.extract_raw_paragraph_dict()
    entity_list_data = openie_data.extract_entity_dict()
    triple_list_data = openie_data.extract_triple_dict()

    if not (len(raw_paragraphs) == len(entity_list_data) == len(triple_list_data)):
        # ... (error handling logic as before) ...
        sys.exit(1)

    logger.info("正在进行段落去重与重索引")
    raw_paragraphs, triple_list_data = hash_deduplicate(
        raw_paragraphs, triple_list_data, embed_manager.stored_pg_hashes, kg_manager.stored_paragraph_hashes
    )
    if raw_paragraphs:
        logger.info(f"段落去重完成，剩余待处理的段落数量：{len(raw_paragraphs)}")
        logger.info("开始Embedding")
        embed_manager.store_new_data_set(raw_paragraphs, triple_list_data, lock)
        embed_manager.rebuild_faiss_index()
        embed_manager.save_to_file()
        logger.info("Embedding完成")
        logger.info("开始构建RAG")
        kg_manager.build_kg(triple_list_data, embed_manager)
        kg_manager.save_to_file()
        logger.info("RAG构建完成")
    else:
        logger.info("无新段落需要处理")
    return True

def main():
    manager = Manager()
    lock = manager.Lock()
    # ... (user confirmation prompt as before) ...
    
    ensure_openie_dir()
    logger.info("----开始导入openie数据----\n")
    logger.info("创建LLM客户端")
    
    embed_manager = EmbeddingManager(lock)
    logger.info("正在从文件加载Embedding库")
    try:
        embed_manager.load_from_file()
    except Exception as e:
        logger.warning(f"加载嵌入库时发生错误 (可忽略): {e}")
    logger.info("Embedding库加载完成")
    
    kg_manager = KGManager()
    logger.info("正在从文件加载KG")
    try:
        kg_manager.load_from_file()
    except Exception as e:
        logger.warning(f"加载KG时发生错误 (可忽略): {e}")
    logger.info("KG加载完成")

    # ... (rest of the main function as before) ...
    try:
        openie_data = OpenIE.load()
    except Exception as e:
        logger.error(f"导入OpenIE数据文件时发生错误：{e}")
        return False
    if handle_import_openie(openie_data, embed_manager, kg_manager, lock) is False:
        logger.error("处理OpenIE数据时发生错误")
        return False
    return None

if __name__ == "__main__":
    main()