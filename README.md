# Ranbooru Forge Neo

面向 Stable Diffusion WebUI Forge / Forge Neo 的 Booru Prompt 与本地 Tag 缓存扩展。它可以在线获取 Booru 标签、清理并写入 txt2img / img2img，也可以建立 SQLite 缓存、顺序取词和批量管理，并将筛选后的 Tag Prompt 交给 LLM Prompt Studio 处理。

![Ranbooru](pics/logo.png)

![Ranbooru 面板](pics/ranbooru.png)

## 核心功能

- 支持 Safebooru、Danbooru、AIBooru、Gelbooru、Rule34、e621、XBooru、Konachan 和 yande.re。
- 支持按 Tag、Post ID、评分、分级、标签分类和排序方式获取内容。
- 支持清理坏 Tag、自定义排除、下划线转空格、乱序、Tag 数量限制、背景和颜色处理。
- 支持追加到后面、追加到前面、替换或只输出四种 Prompt 写入方式。
- 使用 SQLite 保存本地 Tag 缓存，支持顺序读取、循环读取、搜索、删除、去重、备份、撤销和导入导出。
- 可在缓存工作区将 Tag 转换为自然语言 Prompt；原始 Tag 始终保留。
- 可将筛选后的 Tag Prompt 发送到 LLM Prompt Studio；格式转换、扩写和润色由 LLM Studio 的对应模型模板负责，原始 Tag 不会被覆盖。
- Ranbooru → LLM Prompt Studio 使用 `prompt_batch.v1` 交换逐条 Prompt；PNG Prompt Collector 走独立的 PNG → LLM 链路。
- 保留 Img2Img、ControlNet、DeepBooru、LoRAnado、Chaos 和文件驱动 Tag 池。

## 推荐工作流

1. 在 `Tag Prompt` 输入区填写搜索 Tag，选择 Booru 来源，点击“生成提示词 Generate”。
2. 需要重复使用时，在“本地缓存工作区”采集并浏览缓存，确认当前序号后写入 Prompt。
3. 需要模型化转换、润色或扩写时，在 LLM Prompt Studio 的 Ranbooru 联动区点击“载入到 LLM 批处理”，即可按筛选结果批量处理，原始 Tag 始终保留。
4. 通过 `prompt_batch.v1` 将 Ranbooru 的筛选结果传递到 LLM Studio。

页面状态会显示来源、缓存数量和最近一次操作。目标扩展未加载时，原始取词和缓存仍可独立使用，刷新 Forge 后可再次交接。

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

### 推荐工作流

1. 在对应的 txt2img 或 img2img 区域填写预搜索标签，点击 `生成提示词 Generate`。
2. 需要批量复用时打开“本地缓存工作区”，先采集，再在“浏览与联动”中取出并写入提示词。
3. 可将当前批次通过 `prompt_batch.v1` 发送到 LLM Prompt Studio；转换结果会回写到输出框，原始 Tag 保留。

两个生图页使用独立的 DOM ID（`ranbooru_*` 与 `ranbooru_*_img2img`），避免 Gradio 重复 ID 导致脚本和浏览器自动化失效；旧 txt2img ID 保持兼容。

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
- `发送到 LLM 提示词工作室`：将当前完整记录交给 LLM Prompt Studio 继续处理。

界面中的可见序号始终是 `1..总数`，不等同于 SQLite 内部 ID。

#### LLM 批处理联动

在 LLM Prompt Studio 的 Ranbooru 联动区按来源、分级、评分和数量筛选缓存，点击“载入到 LLM 批处理”。转换、扩写和润色由 LLM Prompt Studio 负责，Ranbooru 保留原始 Tag 缓存。

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
3. 根据需要启用循环读取。
4. 开始 Forge 批次或连续生成；每张图按顺序取得一条记录。

### 送入 LLM 批处理

1. 在 LLM Prompt Studio 的 Ranbooru 联动区填写 `tag_cache.db` 路径。
2. 预览并按来源、分级、评分和数量筛选缓存。
3. 点击“载入到 LLM 批处理”，选择格式转换、扩写或润色。
4. 在 LLM Studio 中写入 txt2img/img2img 或导出结果。

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

Safebooru 当前 API 的帖子查询最多接受 2 个搜索 Tag。Ranbooru 不会再为 Safebooru 自动追加 `-animated`；输入超过 2 个 Tag 时会在请求前提示减少查询条件，批量需求请使用 Tag 缓存分批抓取。

在线生成模式会从 Safebooru 第 1 页读取结果，再从返回的帖子中随机选择；`Max Pages` 仍用于 Tag 缓存的逐页抓取。这样单个稀有 Tag 不会因为随机命中空页而被误判为无结果。

## 跨插件链路

Ranbooru 与 LLM Prompt Studio 使用 `prompt_batch.v1` 交换逐条 Tag Prompt。PNG Prompt Collector 是独立的 PNG → LLM 链路：

```text
Ranbooru 缓存
  -> 一条 Tag Prompt
  -> LLM Prompt Studio 格式转换 / 扩写 / 润色
  -> Forge txt2img / img2img

PNG Prompt Collector
  -> 一图一条 positive Prompt
  -> LLM Prompt Studio 格式转换 / 扩写 / 润色
  -> Forge txt2img 正面 Prompt
```

LLM 处理结果保存在批次记录的 `prompt.processed`，不会覆盖 Ranbooru 的原始 Tag 缓存。

未安装其他插件时，Ranbooru 的在线抓取、本地缓存、LLM 联动和生图链路仍可独立使用。

## 数据目录

```text
user/
|-- cache/tag_cache.db
|-- credentials/credentials.json
|-- search/tags_search.txt
`-- remove/tags_remove.txt
```

- `tag_cache.db` 保存原始 Tag、清理结果、来源、Post ID 和 Score。
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

