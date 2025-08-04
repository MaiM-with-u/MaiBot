from enum import Enum
from typing import Optional, Any

from pydantic import BaseModel
from typing_extensions import TypedDict, Required


class RespFormatType(Enum):
    TEXT = "text"  # 文本
    JSON_OBJ = "json_object"  # JSON
    JSON_SCHEMA = "json_schema"  # JSON Schema


class JsonSchema(TypedDict, total=False):
    name: Required[str]
    """
    The name of the response format.

    Must be a-z, A-Z, 0-9, or contain underscores and dashes, with a maximum length
    of 64.
    """

    description: Optional[str]
    """
    A description of what the response format is for, used by the model to determine
    how to respond in the format.
    """

    schema: dict[str, object]
    """
    The schema for the response format, described as a JSON Schema object. Learn how
    to build JSON schemas [here](https://json-schema.org/).
    """

    strict: Optional[bool]
    """
    Whether to enable strict schema adherence when generating the output. If set to
    true, the model will always follow the exact schema defined in the `schema`
    field. Only a subset of JSON Schema is supported when `strict` is `true`. To
    learn more, read the
    [Structured Outputs guide](https://platform.openai.com/docs/guides/structured-outputs).
    """


def _json_schema_type_check(instance) -> str | None:
    if "name" not in instance:
        return "schema必须包含'name'字段"
    elif not isinstance(instance["name"], str) or instance["name"].strip() == "":
        return "schema的'name'字段必须是非空字符串"
    if "description" in instance and (
        not isinstance(instance["description"], str)
        or instance["description"].strip() == ""
    ):
        return "schema的'description'字段只能填入非空字符串"
    if "schema" not in instance:
        return "schema必须包含'schema'字段"
    elif not isinstance(instance["schema"], dict):
        return "schema的'schema'字段必须是字典，详见https://json-schema.org/"
    if "strict" in instance and not isinstance(instance["strict"], bool):
        return "schema的'strict'字段只能填入布尔值"

    return None


def _remove_title(schema: dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]:
    """
    递归移除JSON Schema中的title字段
    """
    if isinstance(schema, list):
        # 如果当前Schema是列表，则对所有dict/list子元素递归调用
        for idx, item in enumerate(schema):
            if isinstance(item, (dict, list)):
                schema[idx] = _remove_title(item)
    elif isinstance(schema, dict):
        # 是字典，移除title字段，并对所有dict/list子元素递归调用
        if "title" in schema:
            del schema["title"]
        for key, value in schema.items():
            if isinstance(value, (dict, list)):
                schema[key] = _remove_title(value)

    return schema


def _link_definitions(schema: dict[str, Any]) -> dict[str, Any]:
    """
    链接JSON Schema中的definitions字段
    """

    def link_definitions_recursive(
        path: str, sub_schema: list[Any] | dict[str, Any], defs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        递归链接JSON Schema中的definitions字段
        :param path: 当前路径
        :param sub_schema: 子Schema
        :param defs: Schema定义集
        :return:
        """
        if isinstance(sub_schema, list):
            # 如果当前Schema是列表，则遍历每个元素
            for i in range(len(sub_schema)):
                if isinstance(sub_schema[i], dict):
                    sub_schema[i] = link_definitions_recursive(
                        f"{path}/{str(i)}", sub_schema[i], defs
                    )
        else:
            # 否则为字典
            if "$defs" in sub_schema:
                # 如果当前Schema有$def字段，则将其添加到defs中
                key_prefix = f"{path}/$defs/"
                for key, value in sub_schema["$defs"].items():
                    def_key = key_prefix + key
                    if def_key not in defs:
                        defs[def_key] = value
                del sub_schema["$defs"]
            if "$ref" in sub_schema:
                # 如果当前Schema有$ref字段，则将其替换为defs中的定义
                def_key = sub_schema["$ref"]
                if def_key in defs:
                    sub_schema = defs[def_key]
                else:
                    raise ValueError(f"Schema中引用的定义'{def_key}'不存在")
            # 遍历键值对
            for key, value in sub_schema.items():
                if isinstance(value, (dict, list)):
                    # 如果当前值是字典或列表，则递归调用
                    sub_schema[key] = link_definitions_recursive(
                        f"{path}/{key}", value, defs
                    )

        return sub_schema

    return link_definitions_recursive("#", schema, {})


def _remove_defs(schema: dict[str, Any]) -> dict[str, Any]:
    """
    递归移除JSON Schema中的$defs字段
    """
    if isinstance(schema, list):
        # 如果当前Schema是列表，则对所有dict/list子元素递归调用
        for idx, item in enumerate(schema):
            if isinstance(item, (dict, list)):
                schema[idx] = _remove_title(item)
    elif isinstance(schema, dict):
        # 是字典，移除title字段，并对所有dict/list子元素递归调用
        if "$defs" in schema:
            del schema["$defs"]
        for key, value in schema.items():
            if isinstance(value, (dict, list)):
                schema[key] = _remove_title(value)

    return schema


class RespFormat:
    """
    响应格式
    """

    @staticmethod
    def _generate_schema_from_model(schema):
        json_schema = {
            "name": schema.__name__,
            "schema": _remove_defs(
                _link_definitions(_remove_title(schema.model_json_schema()))
            ),
            "strict": False,
        }
        if schema.__doc__:
            json_schema["description"] = schema.__doc__
        return json_schema

    def __init__(
        self,
        format_type: RespFormatType = RespFormatType.TEXT,
        schema: type | JsonSchema | None = None,
    ):
        """
        响应格式
        :param format_type: 响应格式类型（默认为文本）
        :param schema: 模板类或JsonSchema（仅当format_type为JSON Schema时有效）
        """
        self.format_type: RespFormatType = format_type

        if format_type == RespFormatType.JSON_SCHEMA:
            if schema is None:
                raise ValueError("当format_type为'JSON_SCHEMA'时，schema不能为空")
            if isinstance(schema, dict):
                if check_msg := _json_schema_type_check(schema):
                    raise ValueError(f"schema格式不正确，{check_msg}")

                self.schema = schema
            elif issubclass(schema, BaseModel):
                try:
                    json_schema = self._generate_schema_from_model(schema)

                    self.schema = json_schema
                except Exception as e:
                    raise ValueError(
                        f"自动生成JSON Schema时发生异常，请检查模型类{schema.__name__}的定义，详细信息：\n"
                        f"{schema.__name__}:\n"
                    ) from e
            else:
                raise ValueError("schema必须是BaseModel的子类或JsonSchema")
        else:
            self.schema = None

    def to_dict(self):
        """
        将响应格式转换为字典
        :return: 字典
        """
        if self.schema:
            return {
                "format_type": self.format_type.value,
                "schema": self.schema,
            }
        else:
            return {
                "format_type": self.format_type.value,
            }
