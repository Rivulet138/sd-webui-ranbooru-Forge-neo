# Ranbooru Forge Neo

面向 Stable Diffusion WebUI Forge / Forge Neo 的 Booru Prompt 与本地缓存扩展。

它可以从多个 Booru 站点抓取图片标签、清理并写入 Prompt，也可以把结果保存到本地 SQLite 数据库，按可见序号读取、批量管理和离线生成。缓存中的整条 Tag Prompt 还能提前通过 Ollama 或 OpenAI 兼容 LLM 转换为适合 Krea 2 的自然语言描述。

> Forge owns the host packages; this extension manages only the dependency declared
> by `install.py`.

![Ranbooru](pics/logo.png)

![Ranbooru 面板](pics/ranbooru.png)

## 功能概览

- 支持 Safebooru、Danbooru、Gelbooru、Rule34、AIBooru、e621 等多个站点。
- 按 Tag、Post ID、评分、分级、标签分类和排序方式获取内容。
- 清理水印、文字、元数据等无用 Tag，并支持自定义排除规则。
- 将结果追加、前置、替换到 WebUI Prompt，或只输出不写入。
- 使用 SQLite 建立本地 Tag 缓存，支持离线顺序生成。
- 按连续的可见序号 `1..总数` 跳转、读取、选择、删除和批量转换。
- 支持 JSON / CSV 导入导出、导入预检、自动备份和撤销上一次删除。
- 支持完全一致去重和高相似度 Tag 清理。
- 支持把几十或几百条完整缓存 Prompt 批量转换为自然语言。
- 可把当前缓存记录实时发送到 LLM 提示词工作室，或直接调用其已保存的 LLM 设置处理、评分并缓存。
- 内置 Krea 2 紧凑自然语言预设。
- 支持 Ollama、本地 OpenAI 兼容服务和远程 OpenAI 兼容 API。
- 保留 Img2Img、ControlNet、DeepBooru、LoRAnado、Chaos 和文件驱动 Tag 池。

## 安装

在 WebUI / Forge 的 `extensions` 目录执行：

```bash
git clone -b kemomimi --single-branch https://github.com/Rivulet138/sd-webui-ranbooru-Forge-neo.git sd-webui-ranbooru-reforge
```

然后重启 WebUI。

已有安装可在扩展目录更新：

```bash
git fetch origin
git checkout kemomimi
git pull --ff-only origin kemomimi
```

扩展会检查并安装兼容版本的 `requests-cache>=1.2,<2`。

## 快速开始

1. 打开 txt2img 或 img2img 页面底部的 `Ranbooru` 面板。
2. 勾选 `Enabled`。
3. 选择 Booru 站点。
4. 在 `Tags to Search (Pre)` 输入搜索 Tag，例如：

   ```text
   kafuu_chino,1girl
   ```

5. 按需要设置分级、排序、Tag 分类和清理选项。
6. 点击生成提示词按钮，或者直接开始 WebUI 图片生成。
7. 选择 Prompt 写入方式：追加到后面、追加到前面、替换或只输出。

## 支持站点

| 站点 | 普通抓取 | Post ID | Tag 分类 | API 凭据 |
| --- | --- | --- | --- | --- |
| Safebooru | 支持 | 支持 | 支持 | 不需要 |
| Danbooru | 支持 | 支持 | 支持 | 不需要 |
| AIBooru | 支持 | 不支持 | 支持 | 不需要 |
| Gelbooru | 支持 | 支持 | — | 可选 API Key / User ID |
| Rule34 | 支持 | 支持 | — | 可选 API Key / User ID |
| e621 | 支持 | 支持 | 支持 | 不需要 |
| XBooru | 支持 | 支持 | — | 不需要 |
| Konachan | 支持 | 不支持 | — | 不需要 |
| yande.re | 支持 | 不支持 | — | 不需要 |

站点接口可能限流。在线抓取遇到超时、连接错误或 HTTP 429 时，插件会执行有限次数的退避重试。

## 在线 Prompt 处理

常用选项：

- `Remove bad tags`：移除水印、翻译、文字、评论气泡和其他内置坏 Tag。
- `Tags to Remove (Post)`：从最终 Prompt 移除指定 Tag，支持 `*` 通配。
- `Convert "_" to spaces`：将 `blue_eyes` 转为 `blue eyes`。
- `Shuffle tags`：随机打乱 Tag 顺序。
- `Limit tags`：按比例保留 Tag。
- `Max tags`：限制 Tag 最大数量。
- `Change Background / Color`：增加或清除背景、颜色相关 Tag。
- `Chaos / Less Chaos / Negative`：把部分或全部抓取 Tag 移入负面 Prompt。
- `Use same prompt`：整批图片使用同一条结果。
- `Use same seed`：整批图片使用同一种子。

Danbooru、Safebooru、AIBooru 和 e621 支持选择 Tag 分类：

- `general`
- `character`
- `copyright`
- `artist`
- `meta`
- e621 的 `species`

## 本地 Tag 缓存

主数据库位置：

```text
user/cache/tag_cache.db
```

每条记录保存原始 Tag、清理后的 Prompt、站点、Post ID、评分、分级、来源地址、搜索条件和自然语言转换元数据。

### 批量建立缓存

打开 `Tag 缓存管理`，设置：

- 抓取页数和起始页。
- 追加或覆盖模式。
- 最低评分。
- 必须全部包含的 Tag。
- 至少包含一个的 Tag。
- 命中任意一个就不入库的排除 Tag。

覆盖模式只有在抓取完整成功后才会替换旧缓存；分页请求或 JSON 解析失败不会拿不完整结果覆盖数据库。

### 可见序号

界面中的“序号”是主缓存中的连续位置，而不是 SQLite 自增 ID。

即使数据库内部 ID 因删除出现空洞，用户仍然按：

```text
1, 2, 3 ... 总数
```

进行跳转、读取、范围删除和批量转换。

范围语法示例：

```text
1-100,205-240,301
```

单次位置选择最多 10000 条。超大范围会先按照主缓存总数收敛，避免无意义的内存占用。

### 生成时使用缓存

勾选：

```text
生成时使用此缓存
```

可同时启用循环读取和“优先使用已预转换的自然语言 Prompt”。

缓存读取游标只在当前 Forge 运行期间共享；重启 Forge 或重新加载插件后会从第 1 条开始，不会沿用上次运行保存的“已读”状态。

本地缓存模式只有 Prompt，不保证存在源图片，因此 Img2Img、ControlNet 图片注入和 DeepBooru 图片分析会跳过。需要图片链路时请使用在线 Booru 模式。

### 与 LLM 提示词工作室实时联动

同时安装 [`sd-webui-llm-prompt-studio`](https://github.com/Rivulet138/sd-webui-llm-prompt-studio) 后，`Tag 缓存管理` 的“当前取出的 Tag / 转换后 Prompt”下方提供：

- `发送到 LLM 提示词工作室`：将当前记录写入提示词工作室的实时交接箱，便于继续编辑模型预设、用户要求和 SFW / NSFW 模式。
- `使用 LLM 处理并缓存`：直接使用提示词工作室已保存的 Provider、URL、模型、API Key 和工作参数生成 Prompt，并写入其本地缓存；启用自动评分时会继续执行 LLM 质量评价。

联动不是只传当前文本。Ranbooru 会反查并发送完整缓存记录，包括内部 ID、原 Tag Prompt、有效自然语言 Prompt、分级、站点源评分、Booru、Post ID 和来源地址。重复发送同一源记录会更新同一交接项。

直接处理沿用提示词工作室保存的失败重试次数。重试耗尽后，错误会保留在提示词工作室的 `Ranbooru 实时交接箱`，可以统一查看并手动重试；也可以将暂不处理的记录标记为跳过。Ranbooru 的站点 Score 只是来源元数据，不会直接成为高分 RAG 评分。

该功能是可选联动。未安装提示词工作室时，Ranbooru 的抓取、缓存、自然语言转换和生成流程仍可独立使用，点击联动按钮会显示明确的未安装提示。

## 批量转换为自然语言

位置：

```text
Tag 缓存管理
└─ 生成设置
   └─ 批量预转换缓存为自然语言
```

该功能会从主缓存挑选完整记录，把每条记录的整个 `tags_prompt` 一次性转换为自然语言，并保存到独立字段。它不会覆盖原始 Tag，也没有“每条只转换前 N 个 Tag”的逻辑。

### 支持后端

- `不转换`
- `Ollama（本地）`
- `OpenAI 兼容 LLM`

Ollama 默认地址：

```text
http://127.0.0.1:11434
```

OpenAI 兼容默认地址：

```text
https://api.openai.com/v1
```

本地兼容服务也可以使用：

```text
http://127.0.0.1:1234/v1
```

填写服务端真实存在的模型名称或模型 ID。

### Krea 2 自然语言预设

内置的 `Krea 2｜紧凑自然语言（推荐）` 会按以下顺序组织视觉信息：

1. 媒介或渲染形式。
2. 主体数量与身份。
3. 外观与服装。
4. 动作与姿势。
5. 场景和重要物体。
6. 画面取景与构图。
7. 时间、天气与光线。
8. 色彩、材质和一个明确的风格锚点。

预设会保留原 Tag 中明确存在的角色、数量、颜色、关系和镜头信息，不虚构缺失内容；同时删除 `masterpiece`、`best quality`、`beautiful`、`stunning`、`8k`、score/source 等空泛质量词。

### 本地高分 Prompt RAG / Few-Shot

启用后，批量转换会从本地缓存中读取来源未变化、预设一致、转换器版本有效的自然语言记录。插件先在每个 Booru 内计算 Score 分位，再按 Tag Jaccard 相似度、查询 Tag 覆盖率和 Score 分位混合排序，选取最多 5 条作为 `user / assistant` 示例插入当前模型请求。默认只使用各站点前 25% 的高分记录，并拒绝只命中一个普通 Tag 的弱相关样例。

检索只在本地 SQLite 缓存中完成，不需要 Embedding 模型或额外向量数据库。RAG 默认关闭；启用后，召回样例和当前 Tags 都会发送到配置的模型服务。示例只提供输出格式参考，最终请求仍要求模型不得复制当前 Tags 中不存在的视觉事实。没有相关且有效的已转换记录时会自动回退到原来的 Zero-Shot 转换；因此首次建立自然语言库时可以先转换一批高分记录，再对后续记录启用 RAG。

### 推荐操作流程

1. 输入可见序号或范围，例如 `1-100`。
2. 点击预览，确认选中记录。
3. 保持“只转换尚未转换的记录”开启。
4. 选择后端、模型和 Krea 2 预设。
5. 根据缓存质量设置 RAG 候选最低分位、Few-Shot 样例数和上下文预算。
6. 点击批量整条转换并保存；当前 LLM 与 RAG 设置会同时自动保存。
7. 生成时保持“优先使用已预转换的自然语言 Prompt”开启。

未转换或已经失效的记录会自动回退到原始 Tag。

### 超时、重试与取消

- 默认请求超时为 120 秒。
- 每条记录遇到超时、HTTP 408/425/429/500/502/503/504 或典型临时连接错误时自动重试 2 次。
- 两次重试后仍失败，会跳过当前记录并继续处理下一条。
- 连续 6 条记录都在重试后仍然超时，任务才会停止。
- 配置错误、无效响应等永久错误会立即停止任务。
- 已完成结果每 10 条分批落库，并在错误、取消或生成器关闭时保存剩余结果。
- 点击取消会取消 Gradio 任务；已经处于阻塞状态的 HTTP 请求仍需等到当前请求返回或超时。

完全相同的整条 `tags_prompt` 在同一次任务中只请求模型一次，后续相同记录复用结果。

如果转换期间源记录被修改或删除，插件会通过 `ID + 原始 tags_prompt` 乐观检查拒绝写入陈旧结果。

### 自然语言 Prompt 的生成行为

已转换的自然语言 Prompt 被视为完整文本，不会再执行以下 Tag 操作：

- 按逗号拆分。
- 随机乱序。
- 坏 Tag 或自定义 Tag 过滤。
- 背景、颜色 Tag 改写。
- Chaos 或 Negative 迁移。
- Tag 比例和最大数量限制。

同一批中的原始 Tag 记录仍正常执行这些选项。

## LLM 设置和 API Key

点击 `保存 LLM 设置（含 API Key）`，或直接开始批量转换，都会把配置写入：

```text
user/credentials/credentials.json
```

保存内容包括：

- 预设。
- 后端。
- 端点兼容策略。
- 服务地址。
- 模型名称。
- API Key。
- 请求超时。

服务地址会在重新打开界面时自动回填。API Key 不会写入缓存数据库，也不会把明文回填到浏览器；界面会提示 Key 已保存，输入框留空即可继续使用。插件只会为相同后端和相同服务地址复用服务端保存的 Key；更换地址不会携带旧 Key。

凭据文件采用临时文件替换写入，并在支持的平台上尽量限制为当前用户读写。不要提交或分享 `user/credentials/credentials.json`。

## 端点兼容策略

| 策略 | 行为 |
| --- | --- |
| `unrestricted` | 默认。兼容系统代理、Fake-IP、本地、LAN 和任意模型地址，不进行 DNS/IP 目标限制。 |
| `allow_private` | 允许本机、LAN 和 Fake-IP，但仍执行地址校验。 |
| `default` | 允许本机 Ollama 和公网服务；OpenAI 兼容接口不能访问私网或链路本地地址。 |
| `public_only` | 只允许公网地址。 |

`unrestricted` 是面向个人本地插件环境的兼容性选择。共享或公网 WebUI 应限制此面板访问，或者改用更严格的策略。

无论选择哪种策略，模型服务地址都不能包含用户名、密码、query 或 fragment，插件也不会跟随 HTTP 重定向。

## 缓存管理和数据安全

支持的主要操作：

- 搜索和按序号读取。
- 跳转并读取或写入 Prompt。
- 按 ID、范围、筛选结果或任意 Tag 删除。
- 清理完全一致记录。
- 按 Jaccard 相似度清理高相似记录。
- JSON / CSV 导出。
- JSON / CSV 导入预检和导入。
- 手动备份。
- 撤销上一次删除。
- 清除指定记录的自然语言结果。

危险操作前会使用 SQLite Online Backup 创建一致性快照。覆盖、删除和压缩操作会先取得数据库写锁，再备份修改前状态。

自动备份默认限制：

- 最多 20 份。
- 最长保留 30 天。
- 总大小不超过 2 GiB。

数据库事务提交和撤销日志发布会按照数据库真实路径在进程内串行，避免多个 Gradio 事件让撤销记录顺序倒置。

导入限制：

- 仅接受界面文件选择器提供的 `.json` / `.csv`。
- 单文件最大 64 MiB。
- 单次最多 100000 条记录。
- 覆盖导入失败时完整回滚，旧缓存保持不变。

## 其他功能

### Img2Img 与图片处理

- 将选中的 Booru 图片用于 Img2Img。
- 使用最后一张图片。
- 居中裁剪。
- 调整 Denoising。
- 发送到兼容的 ControlNet API。
- 使用 DeepBooru 重新分析图片。

数据抓取客户端和图片下载客户端使用独立 Session，并在正常、错误和提前返回路径关闭。

### LoRAnado

- 从指定目录随机选择 LoRA。
- 设置数量、最小/最大权重和自定义权重。
- 锁定上一次 LoRA 组合。

### 文件驱动 Tag 池

搜索文件：

```text
user/search/tags_search.txt
```

移除文件：

```text
user/remove/tags_remove.txt
```

每行可以保存一组逗号分隔的 Tag。

## 故障排查

### 某条转换超时

插件会自动重试 2 次并跳过该条。重新运行相同范围且保持“只转换尚未转换的记录”开启，即可继续处理剩余记录。

### 点击取消后没有立即结束

取消会在请求边界生效。已经发出的 HTTP 请求不能由普通同步 Requests 调用强制中断，需要等当前请求返回或达到超时。

### 生成时没有使用自然语言结果

检查：

1. “优先使用已预转换的自然语言 Prompt”是否开启。
2. 该记录是否已经转换。
3. 转换后原始 `tags_prompt` 是否被修改。源 Prompt 改变后旧转换会自动失效。

### API Key 没有显示在输入框

这是预期行为。保存的 Key 只在服务端为相同后端和端点复用，不会发送回浏览器。

### 缓存序号和数据库 ID 不一致

这是正常现象。界面操作使用当前活动池的连续可见序号；数据库 ID 只作为内部稳定标识。

## 项目结构

```text
sd-webui-ranbooru-reforge/
├─ install.py
├─ pyproject.toml
├─ README.md
├─ scripts/
│  ├─ booru_pipeline.py
│  ├─ cache_db.py
│  ├─ natural_language.py
│  ├─ ranbooru.py
│  ├─ ranbooru_logging.py
│  └─ version.py
├─ pics/
└─ user/
   ├─ cache/
   ├─ credentials/
   ├─ search/
   └─ remove/
```

## 已知边界

- 同步 HTTP 请求开始后，取消只能等待当前请求返回或超时。
- 进程内支持多个 `TagCacheManager` 实例共享同一数据库；不建议多个独立 Forge 进程同时写同一个缓存目录。
- `unrestricted` 适合个人本地环境，但共享部署应使用访问控制或更严格的端点策略。
- 本地缓存模式不包含可靠的源图，无法替代在线模式的 Img2Img / ControlNet / DeepBooru 图片链路。
- 筛选池（`filtered_pool`）和规则池已经移除，不再是可用功能。升级时旧数据库中的相关表和数据会保留为惰性恢复数据；请使用主缓存的 Tag 搜索、范围操作和删除/整理工具。

## 版本与兼容性

- 版本：`1.1.0`，支持 Python `>=3.10,<3.14`。
- Forge 集成目标为当前 Forge Neo 主机和 Gradio `4.40.0`。Pillow `<11` 是该 Gradio 版本的声明兼容上限；当前主机的 Pillow `12.2.0` 冲突不会被本扩展自动降级。
- 扩展只管理 `requests-cache>=1.2,<2`。Gradio、Pillow、NumPy 和其他 WebUI 包由 Forge 主机管理。

## 致谢

本项目基于原 Ranbooru 扩展继续维护和增强：

- 上游项目：[liming123332/sd-webui-ranbooru-reforge](https://github.com/liming123332/sd-webui-ranbooru-reforge)

请遵守各 Booru 站点的服务条款、内容规则和 API 频率限制。
