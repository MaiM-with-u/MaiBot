# Changelog

## [0.0.11] - 2025-3-12
### Added
- 新增了 `schedule` 配置项，用于配置日程表生成功能
- 新增了 `response_spliter` 配置项，用于控制回复分割
- 新增了 `experimental` 配置项，用于实验性功能开关
- 新增了 `llm_outer_world` 和 `llm_sub_heartflow` 模型配置
- 新增了 `llm_heartflow` 模型配置
- 在 `personality` 配置项中新增了 `prompt_schedule_gen` 参数

### Changed
- 优化了模型配置的组织结构
- 调整了部分配置项的默认值
- 调整了配置项的顺序，将 `groups` 配置项移到了更靠前的位置
- 在 `message` 配置项中：
  - 新增了 `max_response_length` 参数
- 在 `willing` 配置项中新增了 `emoji_response_penalty` 参数
- 将 `personality` 配置项中的 `prompt_schedule` 重命名为 `prompt_schedule_gen`

### Removed
- 移除了 `min_text_length` 配置项
- 移除了 `cq_code` 配置项
- 移除了 `others` 配置项（其功能已整合到 `experimental` 中）

## [0.0.5] - 2025-3-11
### Added
- 新增了 `alias_names` 配置项，用于指定麦麦的别名。

## [0.0.4] - 2025-3-9
### Added
- 新增了 `memory_ban_words` 配置项，用于指定不希望记忆的词汇。