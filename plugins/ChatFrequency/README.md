# BetterFrequency 频率控制插件

这是一个用于控制MaiBot聊天频率的插件，支持实时调整talk_frequency参数。

## 功能特性

- 💬 **Talk Frequency控制**: 调整机器人的发言频率
- 📊 **状态显示**: 实时查看当前频率控制状态
- ⚡ **实时生效**: 设置后立即生效，无需重启
- 💾 **不保存消息**: 命令执行反馈不会保存到数据库
- 🚀 **简化命令**: 支持完整命令和简化命令两种形式

## 命令列表

### 1. 设置Talk Frequency
```
/chat talk_frequency <数字>  # 完整命令
/chat t <数字>              # 简化命令
```
- 功能：设置当前聊天的talk_frequency调整值
- 参数：支持0到1之间的数值
- 示例：
  - `/chat talk_frequency 1.0` 或 `/chat t 1.0` - 设置发言频率调整为1.0（最高频率）
  - `/chat talk_frequency 0.5` 或 `/chat t 0.5` - 设置发言频率调整为0.5
  - `/chat talk_frequency 0.0` 或 `/chat t 0.0` - 设置发言频率调整为0.0（最低频率）

### 2. 显示当前状态
```
/chat show                  # 完整命令
/chat s                    # 简化命令
```
- 功能：显示当前聊天的频率控制状态
- 显示内容：
  - 当前talk_frequency值
  - 可用命令提示（包含简化命令）

## 配置说明

插件配置文件 `config.toml` 包含以下选项：

```toml
[plugin]
name = "better_frequency_plugin"
version = "1.0.0"
enabled = true

[frequency]
default_talk_adjust = 1.0         # 默认talk_frequency调整值
max_adjust_value = 1.0           # 最大调整值
min_adjust_value = 0.0           # 最小调整值
```

## 使用场景

- **提高机器人活跃度**: 设置较高的talk_frequency值（接近1.0）
- **降低机器人活跃度**: 设置较低的talk_frequency值（接近0.0）
- **精细调节**: 使用小数进行微调
- **实时监控**: 通过show命令查看当前状态
- **快速操作**: 使用简化命令提高操作效率

## 注意事项

1. 调整值会立即生效，影响当前聊天的机器人行为
2. 命令执行反馈消息不会保存到数据库
3. 支持0到1之间的数值
4. 每个聊天都有独立的频率控制设置
5. 简化命令和完整命令功能完全相同，可根据个人习惯选择

## 技术实现

- 基于MaiCore插件系统开发
- 使用frequency_api进行频率控制操作
- 使用send_api发送反馈消息
- 支持异步操作和错误处理
- 正则表达式支持多种命令格式
