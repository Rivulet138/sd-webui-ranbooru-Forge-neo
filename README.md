# Ranbooru Forge / Ranbooru Reforge

适配 **AUTOMATIC1111 Stable Diffusion WebUI / Forge / Forge Classic Neo** 的增强扩展。

Ranbooru Reforge 可以从多个 Booru 站点抓取图片标签，清理后生成可直接写入 Prompt 的 tag。它同时提供本地 SQLite 标签缓存、可复用规则池、增强搜索语法、批量抓取过滤、LoRAnado、Img2Img / ControlNet 辅助流程等能力，适合批量生成和离线 tag 库工作流。

![Ranbooru 面板](pics/ranbooru.png)

---

## 目录

- [核心特性](#核心特性)
- [支持站点](#支持站点)
- [安装方式](#安装方式)
- [快速开始](#快速开始)
- [Tag 缓存系统](#tag-缓存系统)
- [搜索语法](#搜索语法)
- [规则池](#规则池)
- [提示词写入模式](#提示词写入模式)
- [抓取过滤与质量控制](#抓取过滤与质量控制)
- [其他功能](#其他功能)
- [凭证与隐私](#凭证与隐私)
- [常见问题](#常见问题)
- [目录结构](#目录结构)
- [更新记录与反馈](#更新记录与反馈)

---

## 核心特性

- 多 Booru 数据源随机抓取标签。
- `Tags to Search` + `Tags to Remove` 双向控制。
- 内置坏标签黑名单，抓取和生成时可永久过滤水印、文字、打码等低价值 tag。
- 支持标签打乱、数量限制、下划线转空格、背景/色彩 tag 策略。
- SQLite 本地缓存，支持离线顺序读取、循环读取、按 ID 取 tag。
- 缓存记录升级为元数据结构：保存 `booru / post_id / score / rating / source_url / preview_url / raw tags / prompt tags`。
- 写入 Prompt 时只会写入清理后的 tag，不会把元数据写入 Prompt。
- 增强搜索语法：支持 `+include -exclude`。
- 可复用规则池：保存筛选规则、排序方式和读取上限，适合大型缓存库。
- 批量抓取支持入库去重、可选最低分过滤，并显示新增/重复/跳过统计。
- 提示词写入模式：追加到后面、追加到前面、替换、只输出。
- Img2Img、ControlNet、LoRAnado、DeepBooru 兼容路径。

---

## 支持站点

- `gelbooru`
- `rule34`
- `safebooru`
- `danbooru`
- `yande.re`
- `konachan`
- `aibooru`
- `xbooru`
- `e621`

不同站点对 `Post ID`、分级、API 凭证、score 字段和 tag 分类的支持并不完全一致。

---

## 安装方式

在 WebUI 的 `extensions` 目录执行：

```bash
git clone -b kemomimi --single-branch https://github.com/Rivulet138/sd-webui-ranbooru-reforge.git extensions/sd-webui-ranbooru-reforge
```

然后重启 WebUI / Forge。

---

## 快速开始

1. 打开 Ranbooru 面板并勾选 **Enabled**。
2. 选择 Booru。
3. 在 **Tags to Search (Pre)** 输入搜索 tag，例如 `1girl,blue_eyes,long_hair`。
4. 点击 **生成提示词**。
5. 选择写入模式后，把结果写入 WebUI Prompt。
6. 点击 WebUI **Generate** 出图。

离线批量工作流：

1. 在 **Tag 缓存管理** 中批量爬取 tag。
2. 根据需要启用入库去重和最低 score 过滤。
3. 在 **标签筛选池** 中用规则筛选缓存。
4. 勾选 **生成时使用此缓存**。
5. 生成时会按主缓存或当前规则池顺序取 tag。

---

## Tag 缓存系统

缓存文件：

```text
user/cache/tag_cache.db
```

缓存现在不再只保存一串 tag，而是保存完整记录：

```text
booru
post_id
tags_raw
tags_prompt
score
rating
source_url
preview_url
search_query
created_at
```

说明：

- `tags_raw` 是站点返回的原始 tag。
- `tags_prompt` 是经过坏标签过滤、移除规则、数量限制等处理后的 tag。
- 写入 Prompt、顺序读取、按 ID 取出时只返回 `tags_prompt`。
- 旧版只有 `tags` 字段的缓存库会自动迁移，不需要手动重建。

常用操作：

- **开始批量爬取**：从当前 Booru 和搜索条件抓取 tag 并入库。
- **追加模式**：保留旧缓存，只追加新记录。
- **覆盖模式**：清空旧缓存并重新保存。
- **入库去重**：按 `booru + post_id` 去重；没有 post id 时按 tag 字符串去重。
- **按缓存 ID 取 Tag**：例如输入 `544`，可直接取出缓存 ID 为 544 的清理后 tag。
- **取出下一条**：按主缓存或规则池顺序读取下一条。
- **重置索引**：把顺序读取位置重置到 0。

---

## 搜索语法

缓存搜索和规则池都支持增强语法：

```text
+1girl +blue_eyes -2girls -text
```

含义：

- `+tag`：必须包含。
- `-tag`：必须排除。
- 普通词：等同于必须包含。
- 逗号、中文逗号、换行也可以作为分隔。

示例：

```text
+1girl +blue_hair -2girls -watermark
```

也可以使用面板里的 **必须包含** / **必须排除** 输入框：

```text
必须包含：1girl, blue_eyes
必须排除：2girls, multiple_girls, text
```

搜索结果会显示：

```text
ID / Booru / Post ID / Score / Rating / Tags
```

---

## 规则池

规则池是筛选池的升级版，适合大量缓存时反复使用。

一个规则包含：

- 规则名称。
- 搜索语法。
- 必须包含。
- 必须排除。
- 排序方式：`ID`、`Newest`、`Oldest`、`High Score`、`Low Score`、`Random`。
- 读取上限：`0` 表示不限。

典型流程：

1. 在 **标签筛选池** 输入搜索语法，例如：

   ```text
   +1girl +blue_eyes -2girls -text
   ```

2. 点击 **筛选标签** 预览命中记录。
3. 填写规则名称，例如 `blue eyes solo`。
4. 点击 **保存并启用规则池**。
5. 勾选 **使用筛选池（而非主缓存）** 并点击 **切换**。
6. 之后 **取出下一条** 或 **生成时使用此缓存** 会从当前规则池读取。

仍然保留手动 ID 池：

- 在 **手动 ID** 中输入 `1,5,10,23`。
- 点击 **用手动 ID 创建筛选池**。
- 适合少量精确挑选。

批量操作：

- **刷新规则列表**：查看所有规则。
- **启用规则**：输入规则 ID 后切换到该规则池。
- **删除规则**：删除不再需要的规则。
- **删除当前筛选命中**：按当前搜索/包含/排除条件批量删除缓存记录。为空条件时会拒绝执行，避免误删全部缓存。

---

## 提示词写入模式

写入模式控制 tag 如何写入目标 Prompt：

- **追加到后面**：`原 prompt,缓存 tag`
- **追加到前面**：`缓存 tag,原 prompt`
- **替换**：直接用缓存 tag 替换原 prompt
- **只输出**：只显示结果，不写入 Prompt

这些模式适用于：

- 生成提示词按钮。
- 缓存下一条写入。
- 按缓存 ID 写入。
- 生成时使用本地缓存。

---

## 抓取过滤与质量控制

批量抓取时可以控制入库质量：

- **Remove bad tags**：使用插件内置坏标签黑名单永久过滤低价值 tag。
- **Tags to Remove (Post)**：追加你自己的移除规则，支持 `*` 通配。
- **入库去重**：避免同一站点同一 post 重复入库。
- **启用最低分过滤**：只保存 score 大于等于指定值的记录。
- 抓取结果会显示新增、重复跳过、低分跳过、空 tag 跳过和缓存总数。

没有做的控制：

- 不按 tag 字符串相似度去重。
- 不做最少 tag 数过滤。

---

## 其他功能

### Img2Img / ControlNet / DeepBooru

- **Use img2img**：启用图生图流程。
- **Send to Controlnet**：把图像发送到 ControlNet，需安装 ControlNet。
- **Denoising**：重绘强度。
- **Use last image as img2img**：使用上一张图作为输入。
- **Crop Center**：中心裁剪。
- **Use Deepbooru**：如果当前 WebUI 构建提供 DeepBooru，则可自动打标；Forge Classic Neo 不提供旧 DeepBooru 模块时，此项会禁用。

### LoRAnado

- 从指定 LoRA 子文件夹随机挑选 LoRA。
- 支持数量、权重范围、自定义权重和锁定上一轮组合。

### 文件驱动 tag

- `user/search/tags_search.txt`
- `user/remove/tags_remove.txt`

适合维护固定的搜索池和移除池。

---

## 凭证与隐私

`gelbooru` 与 `rule34` 可能需要 API 凭证：

- API Key
- User ID

保存后写入：

```text
user/credentials/credentials.json
```

不要公开上传你的凭证文件。

---

## 常见问题

### 提示 No posts found？

降低 `Max Pages`、减少搜索 tag、切换站点，或检查网络/API 凭证。

### 缓存 ID 和站点 Post ID 是一回事吗？

不是。缓存 ID 是本地 SQLite 的自增 ID；站点 Post ID 是 Booru 返回的原始帖子 ID。按 ID 取 tag 使用的是本地缓存 ID。

### 规则池会把 score、URL 等元数据写进 Prompt 吗？

不会。规则池只用元数据筛选和排序，写入 Prompt 的始终只有清理后的 `tags_prompt`。

### 如何完全离线使用？

先批量爬取到本地缓存，然后勾选 **生成时使用此缓存**。如果启用了规则池，生成会从规则池读取；否则从主缓存读取。

### 缓存索引怎么重置？

在缓存管理面板点击 **重置索引**。

---

## 目录结构

```text
sd-webui-ranbooru-reforge/
├── scripts/
│   ├── ranbooru.py
│   └── cache_db.py
├── user/
│   ├── search/
│   │   └── tags_search.txt
│   ├── remove/
│   │   └── tags_remove.txt
│   ├── credentials/
│   │   └── credentials.json
│   └── cache/
│       └── tag_cache.db
├── pics/
├── README.md
├── usage.md
├── 使用说明.txt
├── 更新日志.txt
└── install.py
```

---

## 更新记录与反馈

详细说明可查看：

- `更新日志.txt`
- `使用说明.txt`
- `usage.md`

欢迎提交 Issue / PR 提出改进建议。
