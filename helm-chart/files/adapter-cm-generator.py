#!/bin/python3
# 这个脚本的作用是在部署helm chart时动态生成adapter的配置文件，保存在configmap中
# 需要动态生成的原因是core服务的DNS名称是动态的，无法在adapter服务的配置文件中提前确定
# 一些与k8s现有资源冲突的配置也会在这里重置

import os
import toml
import base64
from kubernetes import client, config

config.load_incluster_config()
v1 = client.CoreV1Api()

# 读取部署的关键信息
namespace = os.getenv("NAMESPACE")
release_name = os.getenv("RELEASE_NAME")
data_b64 = os.getenv("DATA_B64")

# 解析并覆盖关键配置
# 这里被覆盖的配置应当在helm chart中针对对应的k8s资源来灵活修改
data = toml.loads(base64.b64decode(data_b64).decode("utf-8"))
if data.get('napcat_server', None) is None:
    data['napcat_server'] = {}
data['napcat_server']['host'] = '0.0.0.0'
data['napcat_server']['port'] = 8095
if data.get('maibot_server', None) is None:
    data['maibot_server'] = {}
data['maibot_server']['host'] = f'{release_name}-maibot-core'  # 根据release名称动态拼接core服务的DNS名称
data['maibot_server']['port'] = 8000

# 创建/修改configmap
cm_name = f'{release_name}-maibot-adapter'
cm = client.V1ConfigMap(
    metadata=client.V1ObjectMeta(name=cm_name),
    data={'config.toml': toml.dumps(data)}
)
try:
    v1.create_namespaced_config_map(namespace, cm)
    print(f"ConfigMap `{cm_name}` created successfully")
except client.exceptions.ApiException as e:
    if e.status == 409:  # 已存在，更新
        v1.replace_namespaced_config_map(cm_name, namespace, cm)
        print(f"ConfigMap `{cm_name}` replaced successfully")
    else:
        raise
