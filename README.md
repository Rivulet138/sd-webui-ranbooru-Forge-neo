# Ranbooru Forge Neo

Ranbooru Forge Neo 是一个面向 Stable Diffusion WebUI / Forge 的 Booru 标签扩展。它可以从多个 Booru 站点抓取图片标签，清理后写入 Prompt；也可以把标签批量保存到本地 SQLite 缓存，再按真实缓存序号顺序读取、跳转、筛选、导入导出和安全删除。

![Ranbooru 面板](pics/ranbooru.png)

## 主要能力

- 从 Safebooru、Danbooru、Gelbooru、Rule34、AIBooru、e621 等站点抓取标签。
- 支持按搜索 tag、Post ID、评分、分级、标签分类和排序方式取图。
- 支持坏 tag 清理、自定义移除、下划线转空格、随机打乱、数量限制。
- 支持把生成结果追加、前置、替换到 Prompt，或只输出不写入。
- 支持本地 SQLite 标签缓存，可离线顺序生成。
- 缓存读取、跳转、搜索、删除和筛选池都使用用户可见的真实缓存序号 `1..总数`，不再暴露 SQLite 内部 ID。
- 支持按序号、序号范围、Tag、搜索筛选批量删除缓存。
- 删除前可预览，危险操作前自动备份，支持撤销上一次删除。
- 支持 JSON / CSV 导出导入，并提供导入预检。
- 支持临时筛选池、可复用规则池、规则排序和规则读取上限。
- 支持完全一致 tag 去重和高相似度 tag 清理。
- 保留 Img2Img、ControlNet、DeepBooru、LoRAnado、文件驱动 tag 池等辅助流程。

## 安装

在 WebUI / Forge 的 `extensions` 目录执行：

```bash
git clone -b kemomimi --single-branch https://github.com/Rivulet138/sd-webui-ranbooru-Forge-neo.git sd-webui-ranbooru-reforge
```

然后重启 WebUI / Forge。

如果已经安装过旧版本，请进入扩展目录后拉取 `kemomimi` 分支并重启。

```bash
git fetch
git checkout kemomimi
git pull
```

## 支持站点

当前 Booru 下拉框包含：

- `safebooru`
- `rule34`
- `danbooru`
- `gelbooru`
- `konachan`
- `yande.re`
- `aibooru`
- `xbooru`
- `e621`

Post ID 查询支持 Gelbooru、Rule34、Safebooru、Danbooru、XBooru 和 e621。Konachan、yande.re、AIBooru 当前不支持 Post ID 查询。

Gelbooru 和 Rule34 可以填写 API Key / User ID。部分站点可能限流，遇到 HTTP 429 时请降低频率、等待后重试，或切换站点。

## 快速开始

1. 展开 `Ranbooru` 面板。
2. 勾选 `Enabled`。
3. 选择 `Booru`。
4. 在 `Tags to Search (Pre)` 输入搜索 tag，例如：

   ```text
   kafuu_chino,1girl
   ```

5. 按需要设置分级、排序、Tag Categories、移除规则和 tag 数量限制。
6. 点击 `生成提示词`。
7. 根据写入模式把结果追加、前置、替换到 Prompt，或只输出结果。

写入模式：

- `追加到后面`
- `追加到前面`
- `替换`
- `只输出`

## 常用搜索示例

搜索一个角色：

```text
kafuu_chino +1girl
```

排除文字和多人图：

```text
kafuu_chino -text -comic -multiple_girls
```

指定 Post ID：

```text
Booru: danbooru
Post ID: 123456
```

## Tag 清理

常用清理选项：

- `Remove bad tags`：移除内置坏 tag，例如水印、翻译、文字、评论气泡等。
- `Tags to Remove (Post)`：从最终 prompt 中移除指定 tag。
- `Convert "_" to spaces`：把 `blue_eyes` 转成 `blue eyes`。
- `Shuffle tags`：打乱 tag 顺序。
- `Limit tags`：保留 tag 比例。
- `Max tags`：限制最终 tag 数量。

`Tags to Remove (Post)` 只影响最终 prompt，不会把整条 post 排除出缓存。批量缓存时如果想整条跳过，请使用 `缓存排除 Tag（命中任意一个就不入库）`。

## 标签分类

Danbooru、Safebooru、AIBooru、e621 支持按标签分类取 tag。

可选分类：

- `general`
- `character`
- `copyright`
- `artist`
- `meta`

推荐新手保留默认的 `general`、`character`、`copyright`。如果不想把画师、元数据写入 prompt，不要勾选 `artist` 和 `meta`。

## 本地 Tag 缓存

本地缓存使用 SQLite，文件位置：

```text
user/cache/tag_cache.db
```

缓存记录会保存：

- booru
- post_id
- tags_raw
- tags_prompt
- score
- rating
- source_url
- preview_url
- search_query
- duplicate_key
- created_at

生成时写入 Prompt 使用的是 `tags_prompt`。其他字段用于搜索、排序、去重、筛选和维护。

## 真实缓存序号

界面里的“序号”指当前用户可见的真实缓存顺序，从 `1` 到 `总数` 连续编号。

它不是 SQLite 内部自增 ID。删除、去重或导入之后，SQLite 内部 ID 可能有空洞，但界面序号仍然按当前可见缓存重新排列。

例如状态显示：

```text
下一条: 110 / 总数: 1832（已读 109）
```

表示下一次顺序读取会取第 110 条真实缓存。

### 跳转到指定缓存

在 `跳转到第几条` 输入 `110`，可以：

- `跳转到该条`：把读取游标移动到第 110 条。
- `跳转并取出`：移动后立即取出第 110 条。
- `跳转并写入 Tag Prompt`：移动后立即写入 WebUI Prompt。

如果当前启用了筛选池或规则池，跳转序号基于当前池；如果没有启用池，跳转序号基于主缓存。

## 批量缓存

打开 `Tag 缓存管理`，设置：

- `爬取页数`：要抓取多少页。
- `从第几页开始缓存`：从用户视角的第几页开始，使用 1 基页码。
- `追加模式（不覆盖已有缓存）`：开启时保留已有缓存并追加新记录；关闭时覆盖保存。
- `强制入库去重（完全相同 Tag 必删）`：批量抓取时固定启用。
- `启用最低分过滤`：只保存 score 达到阈值的 post。
- `缓存必须全部包含`：这些 tag 必须全部命中。
- `缓存必须任意包含`：这些 tag 至少命中一个。
- `缓存排除 Tag（命中任意一个就不入库）`：命中任意一个就整条 post 不入库。

点击 `开始批量爬取` 后，插件会抓取、清理、过滤并写入本地缓存。

### 推荐角色缓存配置

只缓存 Kafuu Chino 的单人或双人图：

```text
Tags to Search (Pre): kafuu_chino
缓存必须全部包含: kafuu_chino
缓存必须任意包含: 1girl,2girl
缓存排除 Tag: comic,text,speech_bubble
```

说明：

- `1girls` 会规范化为 `1girl`。
- `2girl` 会规范化为 `2girls`。
- 搜索框里的正向 tag 会参与入库过滤，避免站点返回偏题结果。
- 旧版本已经写入的错误缓存不会自动修复，需要删除或筛掉后重新缓存。

## 生成时使用本地缓存

勾选：

```text
生成时使用此缓存
```

生成时会从当前缓存来源读取 tag：

- 启用筛选池或规则池时，从当前池读取。
- 未启用池时，从主缓存读取。

`生成时循环读取` 控制读到末尾后是否回到第 1 条。

插件会尽量避免同一个生成任务重复注入缓存，并跳过 Hires.fix 第二阶段重复注入。

## 缓存管理操作

`缓存管理操作` 面板包含：

- `搜索`：按搜索语法查看缓存。
- `按序号取出`：从指定真实缓存序号取出 tag。
- `按序号写入 Tag Prompt`：把指定序号写入 Prompt。
- `预览下一条`：查看顺序读取的下一条。
- `跳转到该条` / `跳转并取出` / `跳转并写入 Tag Prompt`。
- `预览此条` / `删除此条`。
- `预览全部删除` / `删除全部缓存`。
- `预览删除这些 Tag` / `删除包含这些 Tag`。
- `预览删除这些序号` / `删除这些序号`。
- `手动清除完全一致 Tag`。
- `清除相似 Tag (>=90%)`。
- `导出缓存`。
- `预检导入` / `导入缓存`。
- `备份缓存`。
- `撤销上一次删除`。

## 删除前预览

删除前建议先点预览按钮。

预览会显示：

- 将删除多少条。
- 请求了多少序号。
- 被忽略的不存在或重复项。
- 若干样本记录，包括真实序号、booru、post_id、score 和 tag 摘要。

预览不会修改数据库。

## 自动备份和撤销

危险操作前会自动备份数据库：

```text
user/cache/backups/tag_cache_backup_时间_原因.db
```

会自动备份的操作：

- 删除全部缓存。
- 删除指定序号或序号范围。
- 按 tag 批量删除。
- 删除当前筛选命中。
- 手动清除完全一致 tag。
- 清除相似 tag。
- 覆盖导入。
- 覆盖批量缓存。

删除前还会写入撤销快照：

```text
user/cache/last_deleted.json
```

点击 `撤销上一次删除` 可以把上一次删除的记录恢复回来。恢复会把记录追加到当前缓存尾部，因此恢复后的真实序号会重新排列。

## 导入和导出

支持导出：

- JSON
- CSV

默认导出到：

```text
user/cache/tag_cache_export_时间.json
user/cache/tag_cache_export_时间.csv
```

导入前建议点击 `预检导入`。预检会显示：

- 文件记录数。
- 预计写入数。
- 空记录跳过数。
- 重复跳过数。
- 当前总数。
- 导入后预计总数。
- 可写入样本。

`导入时追加` 开启时保留当前缓存并追加；关闭时覆盖当前缓存，覆盖前会自动备份。

`导入时去重` 开启时跳过重复 tag；关闭时允许重复记录入库。

## 去重和相似清理

完全一致去重基于规范化后的完整 tag 集合，而不是只看 post_id。

下面两条会被视为完全一致：

```text
kafuu_chino,2girls,blue_eyes
blue eyes,kafuu_chino,2girl
```

规范化会处理：

- 大小写。
- 空格和下划线。
- 半角逗号、全角逗号、换行。
- `1girl` / `1girls`、`2girl` / `2girls` 等人数 tag。

`手动清除完全一致 Tag` 会保留高分记录；分数相同时保留更早入库的记录。

`清除相似 Tag (>=90%)` 用于清理父图、sibling 变体、高度相似图。默认相似阈值 `0.90`，默认每组最多保留 `2` 条。

## 缓存搜索语法

缓存搜索、筛选池、规则池和按筛选删除支持：

```text
+必须包含 -必须排除
```

示例：

```text
+kafuu_chino +1girl -3girls
+blue_eyes -text -watermark
```

普通词等同于必须包含。

多数输入框支持：

- 空格
- 英文逗号
- 中文逗号
- 换行

## 筛选池和规则池

打开 `缓存筛选 / 标签筛选池` 可以创建缓存子集。

常用操作：

- `筛选标签`：预览当前条件命中的缓存。
- `直接启用当前筛选`：创建临时筛选池，不保存规则。
- `保存并启用规则池`：保存成可复用规则。
- `用手动序号创建筛选池`：从指定真实缓存序号创建临时池。
- `用范围创建筛选池`：例如 `110-180, 205`。
- `预览删除当前筛选`：预览当前筛选会删除哪些缓存。
- `删除当前筛选命中`：删除当前条件命中的主缓存记录。
- `启用规则`：按规则 ID 启用规则池。
- `删除规则`：删除保存的规则。

规则排序：

- `ID`
- `Newest`
- `Oldest`
- `High Score`
- `Low Score`
- `Random`

`规则读取上限` 为 `0` 表示不限制。

注意：筛选池和规则池启用后，读取、跳转、预览和按序号删除都以当前池的可见序号为准。

## HTTP 缓存和 Tag 缓存

`Use cache` 是 HTTP 请求缓存，依赖 `requests-cache`，用于减少重复联网请求。

SQLite Tag 缓存由这些功能控制：

- `Tag 缓存管理`
- `生成时使用此缓存`
- `缓存管理操作`
- `缓存筛选 / 标签筛选池`

这两者不是同一个缓存。

## Img2Img / ControlNet / DeepBooru

启用 `Use img2img` 后，插件会下载 Booru 图片作为 img2img 输入图像。

可选功能：

- `Send to Controlnet`：发送到 ControlNet。
- `Denoising`：重绘幅度。
- `Use last image as img2img`：复用上一张图。
- `Crop Center`：居中裁剪。
- `Use Deepbooru`：使用 WebUI 的 DeepBooru 自动打标。
- `Deepbooru Tags Position`：控制 DeepBooru 标签添加到前面、后面或替换。

## LoRAnado

LoRAnado 可以从指定 LoRA 文件夹随机选择 LoRA，并按随机或自定义权重写入 prompt。

参数：

- `LoRAs Subfolder`
- `LoRAs Amount`
- `Min LoRAs Weight`
- `Max LoRAs Weight`
- `LoRAs Custom Weights`
- `Lock Previous LoRAs`

## 文件驱动 Tag 池

插件会读取：

```text
user/search/
user/remove/
```

用法：

1. 在对应目录创建 `.txt` 文件。
2. 每行写一组 tag。
3. 在 `File` 面板勾选 `Use tags_search.txt` 或 `Use tags_remove.txt`。
4. 选择文件，必要时点击 `Refresh`。

## 凭证

Gelbooru 和 Rule34 可以填写：

- API Key
- User ID

勾选 `Save credentials` 后会保存到：

```text
user/credentials/credentials.json
```

不要把这个文件上传到公开仓库或发给别人。

## 常见问题

### 为什么数据库 ID 范围很大，但缓存总数没那么多？

SQLite 内部 ID 是自增的，删除后不会自动补洞，所以可能出现数据库 ID 范围 `1-2113`，但真实缓存只有 `1832` 条。

当前界面不再以数据库 ID 作为用户操作依据。跳转、取出、搜索结果、删除范围都按真实缓存序号 `1..总数` 走。

### 我有 140 条缓存，怎么回到第 110 条？

在 `跳转到第几条` 输入 `110`，点击 `跳转到该条`。如果想马上取出，点击 `跳转并取出`。

### 删除范围怎么写？

支持：

```text
100-140
100-140, 150, 166
140-100
```

建议先点 `预览删除这些序号`，确认样本后再删除。

### 撤销删除后，为什么序号变了？

撤销会把上次删除的记录重新追加到缓存尾部。真实缓存序号会按当前顺序重新排列，所以恢复后的记录可能不在原来的序号位置。

### 如何完全离线生成？

先批量抓取到 SQLite Tag 缓存，然后勾选 `生成时使用此缓存`。如果只想使用某个子集，先启用筛选池或规则池。

### 缓存里混入不想要的 tag 怎么办？

可以按层级处理：

1. 未来缓存：设置 `缓存排除 Tag`，命中就不入库。
2. 已有缓存：用 `按包含 Tag 删除缓存` 或搜索筛选后删除。
3. 删除前：先使用预览。
4. 误删后：使用 `撤销上一次删除` 或从 `user/cache/backups` 找备份。

### Gelbooru 抓不到或报 429？

Gelbooru 可能限流。可以降低抓取页数，等待后重试，填写 API 凭证，或切换到 Safebooru / Danbooru / AIBooru。

### 旧缓存有大量重复或相似记录？

先点 `手动清除完全一致 Tag`。如果是父图、sibling 变体或高度相似图，再用 `清除相似 Tag (>=90%)`。

## 目录结构

```text
sd-webui-ranbooru-reforge/
├─ scripts/
│  ├─ ranbooru.py
│  └─ cache_db.py
├─ user/
│  ├─ search/
│  ├─ remove/
│  ├─ credentials/
│  └─ cache/
│     ├─ tag_cache.db
│     ├─ last_deleted.json
│     └─ backups/
├─ pics/
├─ README.md
├─ usage.md
└─ install.py
```

## 安全建议

- 删除前先预览。
- 覆盖导入前先预检。
- 长期使用后定期清理 `user/cache/backups` 中不需要的旧备份。
- 不要公开 `user/credentials/credentials.json`。
- 大规模批量抓取时尊重站点 API 限制，避免过快请求。

## 验证记录

当前缓存链路已验证：

- `scripts/ranbooru.py` 和 `scripts/cache_db.py` Python 编译检查。
- Markdown 本地链接和图片路径检查。
- 临时 SQLite 端到端缓存回归：
  - 真实缓存序号跳转。
  - 按序号和范围删除。
  - 删除预览不写库。
  - 筛选池按真实序号读取和删除。
  - 撤销上一次删除。
  - 自动备份。
  - 导入预检不写库。
  - 覆盖导入备份。
  - 删除全部可撤销。
  - CSV 导出和导入预检。
  - `dedupe=False` 允许重复。
  - 完全一致去重可清理重复。
- UI 按钮绑定覆盖检查。
- 后端缓存方法覆盖检查。

## 免责声明

本插件仅用于学习、研究和个人创作辅助。从 Booru 站点获取的内容版权归原作者所有。请遵守各站点服务条款、API 限制和所在地法律法规。
