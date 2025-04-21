import json
import time
from typing import List

from .global_logger import logger
from . import prompt_template
from .lpmmconfig import global_config, INVALID_ENTITY
from .llm_client import LLMClient
from .utils.json_fix import fix_broken_generated_json


def _entity_extract(llm_client: LLMClient, paragraph: str) -> List[str]:
    """对段落进行实体提取，返回提取出的实体列表（JSON格式）"""
    entity_extract_context = prompt_template.build_entity_extract_context(paragraph)
    _, request_result = llm_client.send_chat_request(
        global_config["entity_extract"]["llm"]["model"], entity_extract_context
    )

    # 去除‘{’前的内容（结果中可能有多个‘{’）
    if "[" in request_result:
        request_result = request_result[request_result.index("[") :]

    # 去除最后一个‘}’后的内容（结果中可能有多个‘}’）
    if "]" in request_result:
        request_result = request_result[: request_result.rindex("]") + 1]

    entity_extract_result = json.loads(fix_broken_generated_json(request_result))

    entity_extract_result = [
        entity
        for entity in entity_extract_result
        if (entity is not None) and (entity != "") and (entity not in INVALID_ENTITY)
    ]

    if len(entity_extract_result) == 0:
        raise Exception("实体提取结果为空")

    return entity_extract_result


def _rdf_triple_extract(llm_client: LLMClient, paragraph: str, entities: list) -> List[List[str]]:
    """对段落进行实体提取，返回提取出的实体列表（JSON格式）"""
    entity_extract_context = prompt_template.build_rdf_triple_extract_context(
        paragraph, entities=json.dumps(entities, ensure_ascii=False)
    )
    _, request_result = llm_client.send_chat_request(global_config["rdf_build"]["llm"]["model"], entity_extract_context)

    # 去除‘{’前的内容（结果中可能有多个‘{’）
    if "[" in request_result:
        request_result = request_result[request_result.index("[") :]

    # 去除最后一个‘}’后的内容（结果中可能有多个‘}’）
    if "]" in request_result:
        request_result = request_result[: request_result.rindex("]") + 1]

    entity_extract_result = json.loads(fix_broken_generated_json(request_result))

    for triple in entity_extract_result:
        if len(triple) != 3 or (triple[0] is None or triple[1] is None or triple[2] is None) or "" in triple:
            raise Exception("RDF提取结果格式错误")

    return entity_extract_result


def info_extract_from_str(
    llm_client_for_ner: LLMClient, llm_client_for_rdf: LLMClient, paragraph: str
) -> tuple[None, None] | tuple[list[str], list[list[str]]]:
    try_count = 0
    while True:
        try:
            entity_extract_result = _entity_extract(llm_client_for_ner, paragraph)
            break
        except Exception as e:
            logger.warning(f"实体提取失败，错误信息：{e}")
            try_count += 1
            if try_count < 3:
                logger.warning("将于5秒后重试")
                time.sleep(5)
            else:
                logger.error("实体提取失败，已达最大重试次数")
                return None, None

    try_count = 0
    while True:
        try:
            rdf_triple_extract_result = _rdf_triple_extract(llm_client_for_rdf, paragraph, entity_extract_result)
            break
        except Exception as e:
            logger.warning(f"实体提取失败，错误信息：{e}")
            try_count += 1
            if try_count < 3:
                logger.warning("将于5秒后重试")
                time.sleep(5)
            else:
                logger.error("实体提取失败，已达最大重试次数")
                return None, None

    return entity_extract_result, rdf_triple_extract_result
