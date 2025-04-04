from src.common.logger import get_module_logger
from ...common.database import db
import copy
import hashlib
from typing import Any, Callable, Dict, TypeVar
T = TypeVar('T')  # 泛型类型

"""
PersonInfoManager 类方法功能摘要：
1. get_person_id - 根据平台和用户ID生成MD5哈希的唯一person_id
2. create_person_info - 创建新个人信息文档（自动合并默认值）
3. update_one_field - 更新单个字段值（若文档不存在则创建）
4. del_one_document - 删除指定person_id的文档
5. get_value - 获取单个字段值（返回实际值或默认值）
6. get_values - 批量获取字段值（任一字段无效则返回空字典）
7. del_all_undefined_field - 清理全集合中未定义的字段
8. get_specific_value_list - 根据指定条件，返回person_id,value字典
"""

logger = get_module_logger("person_info")

person_info_default = {
    "person_id" : None,
    "platform" : None,
    "user_id" : None,
    "nickname" : None,
    # "age" : 0,
    "relationship_value" : 0,
    # "saved" : True,
    # "impression" : None,
    # "gender" : Unkown,
    "konw_time" : 0,
}  # 个人信息的各项与默认值在此定义，以下处理会自动创建/补全每一项

class PersonInfoManager:
    def __init__(self):
        if "person_info" not in db.list_collection_names():
            db.create_collection("person_info")
            db.person_info.create_index("person_id", unique=True)

    def get_person_id(self, platform:str, user_id:int):
        """获取唯一id"""
        components = [platform, str(user_id)]
        key = "_".join(components)
        return hashlib.md5(key.encode()).hexdigest()

    async def create_person_info(self, person_id:str, data:dict = None):
        """创建一个项"""
        if not person_id:
            logger.debug("创建失败，personid不存在")
            return
        
        _person_info_default = copy.deepcopy(person_info_default)
        _person_info_default["person_id"] = person_id

        if data:
            for key in _person_info_default:
                if key != "person_id" and key in data:
                    _person_info_default[key] = data[key]

        db.person_info.insert_one(_person_info_default)

    async def update_one_field(self, person_id:str, field_name:str, value, Data:dict = None):
        """更新某一个字段，会补全"""
        if field_name not in person_info_default.keys():
            logger.debug(f"更新'{field_name}'失败，未定义的字段")
            return
        
        document = db.person_info.find_one({"person_id": person_id})

        if document:
            db.person_info.update_one(
                {"person_id": person_id},
                {"$set": {field_name: value}}
            )
        else:
            Data[field_name] = value
            logger.debug(f"更新时{person_id}不存在，已新建")
            await self.create_person_info(person_id, Data)

    async def del_one_document(self, person_id: str):
        """删除指定 person_id 的文档"""
        if not person_id:
            logger.debug("删除失败：person_id 不能为空")
            return

        result = db.person_info.delete_one({"person_id": person_id})
        if result.deleted_count > 0:
            logger.debug(f"删除成功：person_id={person_id}")
        else:
            logger.debug(f"删除失败：未找到 person_id={person_id}")

    async def get_value(self, person_id: str, field_name: str):
        """获取指定person_id文档的字段值，若不存在该字段，则返回该字段的全局默认值"""
        if not person_id:
            logger.debug("get_value获取失败：person_id不能为空")
            return None
        
        if field_name not in person_info_default:
            logger.debug(f"get_value获取失败：字段'{field_name}'未定义")
            return None
        
        document = db.person_info.find_one(
            {"person_id": person_id},
            {field_name: 1}
        )
        
        if document and field_name in document:
            return document[field_name]
        else:
            logger.debug(f"获取{person_id}的{field_name}失败，已返回默认值{person_info_default[field_name]}")
            return person_info_default[field_name]
        
    async def get_values(self, person_id: str, field_names: list) -> dict:
        """获取指定person_id文档的多个字段值，若不存在该字段，则返回该字段的全局默认值"""
        if not person_id:
            logger.debug("get_values获取失败：person_id不能为空")
            return {}

        # 检查所有字段是否有效
        for field in field_names:
            if field not in person_info_default:
                logger.debug(f"get_values获取失败：字段'{field}'未定义")
                return {}

        # 构建查询投影（所有字段都有效才会执行到这里）
        projection = {field: 1 for field in field_names}

        document = db.person_info.find_one(
            {"person_id": person_id},
            projection
        )

        result = {}
        for field in field_names:
            result[field] = document.get(field, person_info_default[field]) if document else person_info_default[field]

        return result
    
    async def del_all_undefined_field(self):
        """删除所有项里的未定义字段"""
        # 获取所有已定义的字段名
        defined_fields = set(person_info_default.keys())
        
        try:
            # 遍历集合中的所有文档
            for document in db.person_info.find({}):
                # 找出文档中未定义的字段
                undefined_fields = set(document.keys()) - defined_fields - {'_id'}
                
                if undefined_fields:
                    # 构建更新操作，使用$unset删除未定义字段
                    update_result = db.person_info.update_one(
                        {'_id': document['_id']},
                        {'$unset': {field: 1 for field in undefined_fields}}
                    )
                    
                    if update_result.modified_count > 0:
                        logger.debug(f"已清理文档 {document['_id']} 的未定义字段: {undefined_fields}")
        
            return
        
        except Exception as e:
            logger.error(f"清理未定义字段时出错: {e}")
            return
        
    async def get_specific_value_list(
    self, 
    field_name: str,
    way: Callable[[Any], bool],  # 接受任意类型值
) ->Dict[str, Any]:
        """
        获取满足条件的字段值字典
        
        Args:
            field_name: 目标字段名
            way: 判断函数 (value: Any) -> bool
            
        Returns:
            {person_id: value} | {}
            
        Example:
            # 查找所有nickname包含"admin"的用户
            result = manager.specific_value_list(
                "nickname",
                lambda x: "admin" in x.lower()
            )
        """
        if field_name not in person_info_default:
            logger.error(f"字段检查失败：'{field_name}'未定义")
            return {}

        try:
            result = {}
            for doc in db.person_info.find(
                {field_name: {"$exists": True}},
                {"person_id": 1, field_name: 1, "_id": 0}
            ):
                try:
                    value = doc[field_name]
                    if way(value):
                        result[doc["person_id"]] = value
                except (KeyError, TypeError, ValueError) as e:
                    logger.debug(f"记录{doc.get('person_id')}处理失败: {str(e)}")
                    continue

            return result

        except Exception as e:
            logger.error(f"数据库查询失败: {str(e)}", exc_info=True)
            return {}

person_info_manager =  PersonInfoManager()