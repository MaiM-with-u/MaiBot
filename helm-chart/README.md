# MaiBot Helm Chart

这是麦麦的Helm Chart，可以方便地将麦麦部署在Kubernetes集群中。

当前Helm Chart对应的麦麦版本可以在`Chart.yaml`中查看`appVersion`项。

## Values项说明

`values.yaml`分为几个大部分。

1. EULA & PRIVACY: 用户必须同意这里的协议才能成功部署麦麦。

2. `adapter`: 麦麦的Adapter的部署配置。

3. `core`: 麦麦本体的部署配置。

4. `statistics_dashboard`: 麦麦的运行统计看板部署配置。

   麦麦每隔一段时间会自动输出html格式的运行统计报告，此统计报告可以部署为看板。

   出于隐私考虑，默认禁用。

5. `napcat`: Napcat的部署配置。

   考虑到复用外部Napcat实例的情况，Napcat部署已被解耦。用户可选是否要部署Napcat。

   默认会捆绑部署Napcat。

6. `sqlite_web`: sqlite-web的部署配置。

   通过sqlite-web可以在网页上操作麦麦的数据库，方便调试。不部署对麦麦的运行无影响。

   此服务如果暴露在公网会十分危险，默认不会部署。

7. `config`: 这里填写麦麦各部分组件的运行配置文件。

   这里填写的配置文件需要严格遵守yaml文件的缩进格式。

   - `adapter_config`: 对应adapter的`config.toml`。

     此配置文件中对于`host`和`port`的配置会被上面`adapter.service`中的配置覆盖，因此不需要改动。

   - `core_model_config`: 对应core的`model_config.toml`。

   - `core_bot_config`: 对应core的`bot_config.toml`。

## 部署说明

使用此Helm Chart的一些注意事项。

### 修改麦麦配置

麦麦的配置文件会通过ConfigMap资源注入各个组件内。

对于通过Helm Chart部署的麦麦，如果需要修改配置，不应该直接修改这些ConfigMap，否则下次Helm更新可能会覆盖掉所有配置。

最佳实践是重新配置Helm Chart的values，然后通过`helm upgrade`更新实例。

### 动态生成的ConfigMap

adapter的ConfigMap是每次部署/更新Helm安装实例时动态生成的。

动态生成的原因：

- core服务的DNS名称是动态的，无法在adapter服务的配置文件中提前确定。
- 一些与k8s现有资源冲突的配置需要被重置。

因此，首次部署时，ConfigMap的生成会需要一些时间，部分Pod会无法启动，等待几分钟即可。

### 运行统计看板与core的挂载冲突

如果启用了运行统计看板，那么statistics_dashboard会与core共同挂载statistics_dashboard存储卷，用于同步html文件。

如果k8s集群有多个节点，且statistics_dashboard与core未调度到同一节点，那么就需要statistics_dashboard的PVC访问模式具备`ReadWriteMany`访问模式。

不是所有存储卷的底层存储都支持`ReadWriteMany`访问模式。

如果你的存储底层无法支持`ReadWriteMany`访问模式，你可以通过`nodeSelector`配置将statistics_dashboard与core调度到同一节点来避免问题。
