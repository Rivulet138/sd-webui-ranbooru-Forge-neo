# Ranbooru Forge Neo

Ranbooru Forge Neo 是适配 Stable Diffusion WebUI / Forge 的 Booru 标签扩展。它可以从多个 Booru 站点抓取图片 tag，清理后写入 Prompt，也可以把 tag 批量缓存到本地 SQLite 数据库，用于离线、顺序、筛选池或规则池生成。

![Ranbooru 面板](pics/ranbooru.png)

## 核心功能

- 从多个 Booru 站点抓取 post tag。
- 按搜索 tag、评分、分类、移除规则和缓存过滤条件清理 tag。
- 支持随机、高分、低分排序取图。
- 支持把 tag 追加、前置、替换到 Prompt，或只输出不写入。
- 支持本地 SQLite tag 缓存。
- 支持生成时直接从本地缓存读取 tag。
- 支持从指定页开始批量缓存。
- 支持缓存必须全部包含 / 任意包含过滤。
- 支持 `1girl/2girl` 这类人数 tag 的规范化匹配。
- 支持按规范化后的完整 tag 集合强制去重。
- 支持手动清除完全一致的 tag 缓存。
- 支持缓存搜索、临时筛选池、可复用规则池。
- 保留 Img2Img、ControlNet、DeepBooru、LoRAnado 等辅助流程。

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

Post ID 查询支持 Gelbooru、Rule34、Safebooru、Danbooru、XBooru 和 e621。Konachan、yande.re、AIBooru 在当前插件路径中不支持 Post ID 查询。

Gelbooru、Rule34 可能需要 API 凭证或会触发限流。当前验证环境中 Gelbooru 返回 HTTP 429；Safebooru、Danbooru、AIBooru 已用 `kafuu_chino` 样本测试通过。

## 安装

在 WebUI 的 `extensions` 目录中执行：

```bash
git clone -b kemomimi --single-branch https://github.com/Rivulet138/sd-webui-ranbooru-Forge-neo.git sd-webui-ranbooru-reforge
```

然后重启 WebUI / Forge。

如果你已经从旧的 `sd-webui-ranbooru-reforge` 地址安装，拉取 `kemomimi` 分支并重启即可。GitHub 目前会把旧地址重定向到新的仓库位置。

## 主面板工作流

1. 勾选 `Enabled`。
2. 选择 `Booru`。
3. 在 `Tags to Search (Pre)` 输入搜索 tag，例如：

   ```text
   kafuu_chino,1girl
   ```

4. 按需要设置分级、分类、排序、tag 数量限制和移除规则。
5. 点击 `生成提示词`。
6. 通过写入模式把结果追加、前置、替换到 Prompt，或只输出结果。
7. 在 WebUI 中正常生成。

插件会统一解析常见 Booru 返回格式，包括 `tags`、`tag_string`、分类 tag dict、list、空格分隔和逗号分隔字符串。

## 本地 Tag 缓存

缓存文件位置：

```text
user/cache/tag_cache.db
```

每条缓存记录包含：

- `booru`
- `post_id`
- `tags_raw`
- `tags_prompt`
- `score`
- `rating`
- `source_url`
- `preview_url`
- `search_query`
- `duplicate_key`
- `created_at`

写入 Prompt 时只使用 `tags_prompt`。其他字段用于搜索、排序、筛选、去重和维护。

## 批量缓存工作流

打开 `Tag 缓存管理`，设置：

- `爬取页数`：要抓取多少页。
- `从第几页开始缓存`：从第几页开始抓取，UI 使用 1 基页码。
- `追加模式`：保留已有缓存，只追加新记录。
- `强制入库去重`：固定开启，完全一致的规范化 tag 集合不会重复入库。
- `启用最低分过滤`：只保存 score 达到阈值的 post。
- `缓存必须全部包含`：这里列出的 tag 必须全部存在。
- `缓存必须任意包含`：这里列出的 tag 至少命中一个。

设置好后点击 `开始批量爬取`。

### 示例：只缓存 Kafuu Chino 的 1 人或 2 人图

推荐配置：

```text
Tags to Search (Pre): kafuu_chino
缓存必须全部包含: kafuu_chino
缓存必须任意包含: 1girl,2girl
```

说明：

- `2girl` 会自动规范化为 Booru 常见 tag `2girls`。
- `1girls` 会自动规范化为 `1girl`。
- 搜索框里的正向 tag 会自动并入入库硬过滤；如果站点意外返回不含 `kafuu_chino` 的 post，它不会进入缓存。
- 旧缓存中已经混入的错误数据不会自动变正确，需要删除旧缓存后重新抓取。

### 从指定页开始缓存

例如从第 5 页开始抓 10 页：

```text
爬取页数: 10
从第几页开始缓存: 5
```

UI 中的页码始终按用户视角从 1 开始。插件会在内部转换成对应站点的请求页码。

## 去重规则

缓存去重基于规范化后的完整 tag 集合，不只看 `post_id`。

下面两条会被视为完全一致：

```text
kafuu_chino,2girls,blue_eyes
blue eyes,kafuu_chino,2girl
```

规范化会处理：

- 大小写。
- 空格和下划线。
- 半角逗号、全角逗号、换行。
- `2girl` / `2girls`、`1girls` / `1girl` 这类人数 tag。

点击 `手动清除完全一致 Tag` 可以整理旧缓存，删除规范化后 tag 集合完全一致的重复行。存在重复时会优先保留高分记录，然后保留更早的记录。

## 缓存搜索语法

缓存搜索、筛选池、规则池、按筛选删除都支持：

```text
+必须包含 -必须排除
```

示例：

```text
+kafuu_chino +1girl -3girls
+blue_eyes -text -watermark
```

普通词等同于必须包含。多数输入框支持空格、逗号、全角逗号和换行作为分隔符。

## 缓存管理

`缓存管理操作` 面板支持：

- 刷新缓存状态。
- 搜索缓存。
- 按本地缓存 ID 取出 tag。
- 按本地缓存 ID 写入 Prompt。
- 删除单条缓存。
- 删除全部缓存。
- 重置顺序读取索引。
- 手动清除完全一致 tag。

注意：本地缓存 ID 是 SQLite 自增 ID，不是 Booru 站点的 Post ID。

## 筛选池和规则池

打开 `缓存筛选 / 标签筛选池` 可管理缓存子集。

常用操作：

- `筛选标签`：预览当前条件命中的缓存记录。
- `直接启用当前筛选`：不保存规则，直接创建临时筛选池。
- `保存并启用规则池`：把当前筛选条件保存成可复用规则。
- `用手动 ID 创建筛选池`：从指定本地缓存 ID 创建筛选池。
- `启用规则`：按规则 ID 启用已保存规则。
- `删除规则`：删除规则。
- `删除当前筛选命中`：按当前筛选条件批量删除缓存。空条件会被拒绝，避免误删全部缓存。

规则池排序方式：

- `ID`
- `Newest`
- `Oldest`
- `High Score`
- `Low Score`
- `Random`

`规则读取上限` 为 `0` 表示不限制读取数量。

## 生成时使用本地缓存

勾选：

```text
生成时使用此缓存
```

生成时会从当前缓存来源取 tag：

- 如果启用了临时筛选池或规则池，从筛选池 / 规则池读取。
- 否则从主缓存读取。

`生成时循环读取` 控制读到末尾后是否从头继续。

写入模式支持：

- `追加到后面`
- `追加到前面`
- `替换`
- `只输出`

插件会尽量避免同一个生成任务重复注入本地缓存，并跳过 Hires.fix 第二阶段的重复注入。

## HTTP 缓存和 Tag 缓存的区别

`Use cache` 是 HTTP 请求缓存，依赖 `requests-cache`，用于减少重复网络请求。

SQLite Tag 缓存由这些功能控制：

- `Tag 缓存管理`
- `生成时使用此缓存`
- `缓存筛选 / 标签筛选池`

这两者不是同一个东西。

## Tag 清理

Tag 清理支持：

- `Remove bad tags`：使用内置坏 tag 黑名单。
- `Tags to Remove (Post)`：输入自定义移除 tag。
- `user/remove` 文件驱动移除。
- `*` 通配移除。
- 随机打乱 tag。
- 下划线转空格。
- `Limit tags` 和 `Max tags` 数量限制。

移除规则会被规范化，所以 `blue eyes` 可以匹配 Booru 风格的 `blue_eyes`。

## 分类过滤

Danbooru、Safebooru、AIBooru、e621 支持分类过滤：

- `general`
- `artist`
- `copyright`
- `character`
- `species`
- `meta`

未选择分类时，插件会使用站点提供的综合 tag 字符串或全部支持分类。

## 凭证

Gelbooru 和 Rule34 可以使用：

- API Key
- User ID

勾选 `Save credentials` 后会保存到：

```text
user/credentials/credentials.json
```

不要公开上传这个文件。

## 其他功能

### Img2Img / ControlNet / DeepBooru

插件可以可选抓取源图用于 Img2Img，把图像发送到 ControlNet，中心裁剪图片，或在当前 WebUI 构建提供 DeepBooru 时追加自动 tag。

### LoRAnado

LoRAnado 可以从指定 LoRA 文件夹随机选择 LoRA，并按随机或自定义权重写入 LoRA prompt。

### 文件驱动 tag 池

插件会读取：

```text
user/search/
user/remove/
```

适合维护可复用搜索 tag 列表和移除 tag 列表。

## 常见问题

### 缓存里不是我搜索的角色

更新到当前分支后重启 WebUI，删除旧缓存并重新抓取。旧版本已经写入的错误缓存不会自动变正确。

角色缓存建议同时设置：

```text
Tags to Search (Pre): kafuu_chino
缓存必须全部包含: kafuu_chino
```

### `2girl` 匹配不到

当前版本里 `2girl` 和 `2girls` 都会规范成 `2girls`，可以任选一种输入。

### Gelbooru 抓不到或报错

Gelbooru 可能返回 HTTP 429。降低抓取页数、等待后重试、填写凭证，或切换到其他站点。

### 旧缓存有重复

点击 `手动清除完全一致 Tag`。它会重建 duplicate key，并删除规范化后完整 tag 集合完全一致的重复行。

### 如何完全离线生成

先批量抓取到 SQLite Tag 缓存，然后勾选 `生成时使用此缓存`。如需固定子集，先启用筛选池或规则池。

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
├─ pics/
├─ README.md
├─ usage.md
└─ install.py
```

## 验证说明

当前缓存链路已做过这些验证：

- `scripts/ranbooru.py` 和 `scripts/cache_db.py` 的 Python 编译检查。
- 起始页缓存、必须包含 / 任意包含过滤、`1girl/2girl` 规范化、本地 SQLite 完整链路测试。
- 缓存 UI 回调参数顺序静态检查。
- Safebooru、Danbooru、AIBooru 的 `kafuu_chino` 真实样本过滤测试。

Gelbooru 在验证环境中返回 HTTP 429，因此没有拿到正向样本。
