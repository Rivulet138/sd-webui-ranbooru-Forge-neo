# Ranbooru Forge Neo

面向 Stable Diffusion WebUI Forge / Forge Neo 的 Booru Prompt 与本地 Tag 缓存扩展。它可以在线获取 Booru 标签、清理并写入 txt2img / img2img，也可以建立 SQLite 缓存，顺序取词、批量管理，并将整条 Tag Prompt 转换成自然语言。

![Ranbooru](pics/logo.png)

![Ranbooru 面板](pics/ranbooru.png)

## 核心功能

- 支持 Safebooru、Danbooru、AIBooru、Gelbooru、Rule34、e621、XBooru、Konachan 和 yande.re。
- 支持按 Tag、Post ID、评分、分级、标签分类和排序方式获取内容。
- 支持清理坏 Tag、自定义排除、下划线转空格、乱序、Tag 数量限制、背景和颜色处理。
- 支持追加到后面、追加到前面、替换或只输出四种 Prompt 写入方式。
- 使用 SQLite 保存本地 Tag 缓存，支持顺序读取、循环读取、搜索、删除、去重、备份、撤销和导入导出。
- 可通过 Ollama 或 OpenAI Compatible 服务把整条 Tag Prompt 批量转换为自然语言，原始 Tag 不会被覆盖。
- 与 LLM Prompt Studio、PNG Prompt Collector 通过 `prompt_batch.v1` 交换逐条 Prompt。
- 保留 Img2Img、ControlNet、DeepBooru、LoRAnado、Chaos 和文件驱动 Tag 池。

## 安装

在 Forge Neo 的 `extensions` 目录执行：

```powershell
git clone -b kemomimi --single-branch https://github.com/Rivulet138/sd-webui-ranbooru-Forge-neo.git sd-webui-ranbooru-reforge
```

重新启动 Forge Neo，然后在浏览器执行一次 `Ctrl + F5`。

更新现有安装：

```powershell
cd E:\sd-webui-forge-neo\extensions\sd-webui-ranbooru-reforge
git pull --ff-only origin kemomimi
```

扩展只安装自身声明的 `requests-cache>=1.2,<2`。Gradio、Pillow、NumPy 等包由 Forge 主程序管理。

## 面板说明

Ranbooru 同时嵌入 txt2img 和 img2img。顶部的 `Tag Prompt` 是当前写入内容，`生成提示词` 用于只更新 Prompt，不开始生图。

### Ranbooru

在线抓取和即时生图区域。

| 控件 | 作用 |
| --- | --- |
| `Enabled` | 启用在线 Booru 取词链路 |
| `Tags to Search (Pre)` | 输入搜索 Tag，例如 `1girl,solo` |
| `Booru` | 选择来源站点 |
| `Tag Categories` | 在支持的站点选择 general、character、artist 等分类 |
| `Post ID` | 直接读取指定帖子 |
| `Remove bad tags` | 移除水印、文字、元数据等内置坏 Tag |
| `Tags to Remove (Post)` | 移除自定义 Tag，支持 `*` 通配 |
| `Shuffle / Limit / Max tags` | 调整 Tag 顺序和数量 |
| `Write mode` | 追加、前置、替换或只输出 |
| `Use same prompt` | 当前 Forge 批次中的所有图片复用同一条结果 |
| `Use same seed` | 当前 Forge 批次中的所有图片复用同一种子 |

Gelbooru 和 Rule34 可填写 API Key / User ID。凭据保存在服务器端，重新打开页面时不会把明文回填到浏览器。

### Tag 缓存管理

这是本地缓存的主工作区，分为四个标签页。

#### 缓存采集

批量抓取指定页数并写入本地 SQLite。可设置起始页、追加或覆盖、最低 Score、必须包含、任意包含和排除 Tag。

- 追加模式保留已有缓存。
- 覆盖模式只在抓取完整成功后替换旧缓存。
- 完全相同 Tag 可在入库时去重。

#### 浏览与联动

用于顺序取出、按可见序号读取、跳转、预览和写入 `Tag Prompt`。

- `生成时使用此缓存`：Forge 每次生成时自动取下一条。
- `生成时循环读取`：到末尾后从第一条继续。
- `优先使用已预转换的自然语言 Prompt`：记录有有效自然语言结果时使用自然语言，否则回退原始 Tag。
- `发送到 LLM 提示词工作室`：将当前完整记录交给 LLM Prompt Studio 继续处理。

界面中的可见序号始终是 `1..总数`，不等同于 SQLite 内部 ID。

#### 自然语言与 RAG

把缓存中的整条 `tags_prompt` 逐条转换为自然语言，并写入独立字段。支持：

- Ollama 本地服务。
- OpenAI Compatible 本地或远程服务。
- Krea 2 紧凑自然语言预设。
- 按可见序号或范围批量转换，例如 `1-100,205-240`。
- 只转换尚无有效结果的记录。

本页仍提供可选的本地 RAG / Few-Shot，默认关闭。启用后，只从当前 SQLite 缓存中选择相似且高分的已转换记录作为示例；没有合适样例时自动使用 Zero-Shot。此功能属于 Ranbooru，不属于 LLM Prompt Studio。

每条待转换记录发起一次模型请求。点击取消后会在当前请求返回或超时后停止，并保留已经完成的结果。插件不维护跨标签页任务所有者、租约或“另一标签页正在使用”状态。

#### 维护与导入导出

提供搜索、按 ID 或可见范围删除、完全一致去重、相似去重、手动备份、撤销上一次删除，以及 JSON / CSV 导入导出。

删除、覆盖和整理会在数据库事务内执行，并在需要时先创建 SQLite 备份。这些机制只保护缓存文件一致性，不会阻止另一个浏览器标签页启动普通按钮操作。

### Img2Img

可把在线 Booru 图片送入 img2img 或 ControlNet，也可调用 DeepBooru 分析图片。仅使用本地 Tag 缓存时没有可靠的源图片，因此图片注入链路会跳过。

### File

从以下文件读取搜索或排除 Tag 组：

```text
user/search/tags_search.txt
user/remove/tags_remove.txt
```

每行是一组逗号分隔的 Tag。

### Extra

提供 Prompt 混合、Chaos / Negative 分流、同种子和在线请求缓存等原有高级选项。

### LoRAnado

从指定 LoRA 子目录随机选择 LoRA，可设置数量、权重范围、自定义权重，并锁定上一次组合。

## 常用流程

### 在线生成

1. 打开 txt2img 或 img2img 底部的 `Ranbooru`。
2. 勾选 `Enabled`，选择站点并输入搜索 Tag。
3. 设置清理和写入方式。
4. 点击 `生成提示词` 预览，或直接开始 Forge 生图。

### 缓存连续生成

1. 在 `缓存采集` 建立或追加缓存。
2. 在 `浏览与联动` 勾选 `生成时使用此缓存`。
3. 根据需要启用循环读取和自然语言优先。
4. 开始 Forge 批次或连续生成；每张图按顺序取得一条记录。

### 自然语言预转换

1. 在 `自然语言与 RAG` 选择范围和后端。
2. 填写模型服务地址、模型 ID 和 API Key。
3. 先预览，再执行转换。
4. 返回 `浏览与联动`，启用自然语言优先。

## 支持站点

| 站点 | 普通抓取 | Post ID | Tag 分类 | 凭据 |
| --- | --- | --- | --- | --- |
| Safebooru | 支持 | 支持 | 支持 | 不需要 |
| Danbooru | 支持 | 支持 | 支持 | 不需要 |
| AIBooru | 支持 | 不支持 | 支持 | 不需要 |
| Gelbooru | 支持 | 支持 | - | 可选 API Key / User ID |
| Rule34 | 支持 | 支持 | - | 可选 API Key / User ID |
| e621 | 支持 | 支持 | 支持 | 不需要 |
| XBooru | 支持 | 支持 | - | 不需要 |
| Konachan | 支持 | 不支持 | - | 不需要 |
| yande.re | 支持 | 不支持 | - | 不需要 |

在线接口可能限流。超时、连接错误或 HTTP 429 会按站点请求逻辑执行有限重试。

## 跨插件链路

三个插件使用统一的 `prompt_batch.v1`：

```text
PNG Prompt Collector
  -> 一图一条 positive Prompt
  -> LLM Prompt Studio 润色/扩写
  -> Ranbooru 导入或缓存
  -> Forge txt2img / img2img
```

当记录包含 `prompt.processed` 时，生产方应同时写入 `prompt.processed_kind` 或 `prompt.output_kind`。Ranbooru 只把 `natural`、`natural_language` 或 `prose` 类型存入 `natural_prompt`；Tag 或混合结果不会误存为自然语言。

未安装其他插件时，Ranbooru 的在线抓取、本地缓存、自然语言转换和生图链路仍可独立使用。

## 数据目录

```text
user/
|-- cache/tag_cache.db
|-- credentials/credentials.json
|-- search/tags_search.txt
`-- remove/tags_remove.txt
```

- `tag_cache.db` 保存原始 Tag、清理结果、来源、Post ID、Score 和自然语言转换元数据。
- `credentials.json` 保存 Booru 与 LLM 凭据，不应提交或分享。
- 数据库操作使用事务；删除、覆盖和整理操作支持备份与撤销。

## 开发与验证

```powershell
cd E:\sd-webui-forge-neo\extensions\sd-webui-ranbooru-reforge
E:\sd-webui-forge-neo\venv\Scripts\python.exe -m unittest discover -s tests -v
E:\sd-webui-forge-neo\venv\Scripts\python.exe -m ruff check scripts tests
E:\sd-webui-forge-neo\venv\Scripts\python.exe -m compileall -q scripts tests
```

当前版本：`1.1.0`，支持 Python `>=3.10,<3.14`。

## 已知边界

- 同步 HTTP 请求开始后不能被强制中断；取消会在当前请求返回或超时后生效。
- 本地缓存只保证 Prompt 数据，不保证源图片仍可访问。
- 不建议多个独立 Forge 进程同时写入同一个缓存目录。
- 修改 Python 后需要重启 Forge，并执行 `Ctrl + F5`。

## 致谢

本项目基于 [liming123332/sd-webui-ranbooru-reforge](https://github.com/liming123332/sd-webui-ranbooru-reforge) 继续维护和增强。请遵守各 Booru 站点的服务条款、内容规则和 API 频率限制。
