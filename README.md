# Ranbooru Forge

一个适配 AUTOMATIC1111 Stable Diffusion WebUI 的扩展面板，从各大 Booru 随机抓取图片标签并生成可用的提示词。支持 Img2Img、Deepbooru 自动打标、LoRA 随机混用等功能，帮助快速构造丰富、多样化的提示词组合。

![Ranbooru 面板](pics/ranbooru.png)

## 安装
- 方式一（推荐）：将仓库克隆到 WebUI 的 `extensions` 目录：
  ```bash
  git clone -b kemomimi --single-branch https://github.com/Rivulet138/sd-webui-ranbooru-reforge.git extensions/sd-webui-ranbooru-reforge
  ```
- 方式二：将 `scripts/ranbooru.py` 复制到你的扩展目录中。

安装完成后，在 WebUI 底部点击 `Reload UI`，页面底部会出现一个名为 `Ranbooru` 的面板。

## 快速开始
- 打开 `Ranbooru` 面板。
- 选择 `Booru`、设置 `Tags to Search (Pre)`（预搜索标签，逗号分隔）。
- 点击 `生成提示词`，生成的内容会显示在 `提示词输出`，并可自动填入 WebUI 的 `prompt`。

## 功能概览
- 支持的 Booru：`gelbooru`、`rule34`、`safebooru`、`danbooru`、`yande.re`、`konachan`、`aibooru`、`xbooru`、`e621`。
- 提示词生成：从随机页面/图片抽取标签，支持去重、打乱、限制数量与最大数量。
- 过滤与变换：移除坏标签、将 `_` 转为空格、改变背景/色彩模式。
- 文件驱动：可从 `user/search/tags_search.txt` 与 `user/remove/tags_remove.txt` 读取搜索/移除标签，支持一键刷新文件列表。
- Img2Img：支持 `denoising`、使用上次图片、裁剪中心；可发送到 ControlNet；可与 Deepbooru 自动打标组合使用（前/后/替换）。
- 额外模式：混合多个提示词（`Mix prompts`）、`Chaos/Negative` 模式、批次同一随机种子、请求缓存。
- LoRAnado：随机从指定文件夹挑选 LoRA 并为其设置权重；支持锁定上一轮 LoRA、权重范围或自定义权重、数量控制。

## 面板参数说明
- `Booru`：选择数据源站点。
- `Max Pages`：随机选择的最大页面数。
- `Post ID`：指定图片 ID；不填则随机抽取。
- `Tags to Search (Pre)`：参与搜索的标签（逗号分隔）。
- `Tags to Remove (Post)`：生成后要移除的标签；支持通配符 `*`（如 `hair*`）。
- `Mature Rating`：按站点支持选择分级（NSFW/SFW 等）。
- `Remove bad tags`：移除水印、文字、打码等无用标签。
- `Shuffle tags`：打乱标签顺序。
- `Convert "_" to spaces`：将下划线转为空格。
- `Use same prompt for all images`：同一批次使用相同提示词。
- `Fringe Benefits`（Gelbooru）：启用网站隐藏内容（等价于站点设置的显示全部内容）。
- `Limit tags`/`Max tags`：限制标签保留比例或最大数量。
- `Change Background`/`Change Color`：通过添加/移除标签改变背景与色彩风格。
- `Sorting Order`：按随机/高分/低分排序抽取。
- `Img2Img` 区：`Use img2img`、`Send to Controlnet`、`Denoising`、`Use last image as img2img`、`Crop Center`、`Use Deepbooru`、`Deepbooru Tags Position`。
- `File` 区：`Use tags_search.txt`、`Choose tags_search.txt`、`Use tags_remove.txt`、`Choose tags_remove.txt`、`Refresh`。
- `Extra` 区：`Mix prompts`、`Mix amount`、`Chaos Mode`、`Chaos Amount %`、`Negative Mode`、`Use same seed`、`Use cache`。
- `LoRAnado` 区：`Lock previous LoRAs`、`LoRAs Subfolder`、`LoRAs Amount`、`Min/Max LoRAs Weight`、`LoRAs Custom Weights`。

## 凭证与隐私
- Gelbooru 与 Rule34 的 API 调用可能需要凭证：`API Key` 与 `User ID`。
- 在选择站点后，面板会显示对应的输入框，并提供 `Save credentials` 选项。
- 勾选保存后，凭证会持久化到：
  - `extensions/sd-webui-ranbooru-reforge/user/credentials/credentials.json`
- 当保存成功时，UI 会隐藏输入框并显示状态信息；你可以随时点击 `Clear saved credentials` 清除。

## 使用示例
- 基础搜索：设置 `Booru`、`Tags to Search (Pre)`、选择分级与排序，点击 `生成提示词`。
- 指定 Post ID：在支持站点填写 `Post ID`，可直接抽取该图标签。
- Img2Img：勾选 `Use img2img`、设置 `Denoising`，可选 `Use last image` 搭配上轮图片；如需发送到 ControlNet，打开 `Send to Controlnet` 并设置强度。
- Deepbooru：勾选 `Use Deepbooru` 并选择标签位置（前/后/替换），用于自动补充图像标签。
- LoRAnado：设置 LoRA 文件夹、数量与权重范围，自动为提示词加上随机 LoRA 调用。

## 已知问题
- 在批次较大时，`Chaos/Negative` 模式可能报错；重跑通常可恢复。
- 与 `sd-dynamic-prompts` 同用时，多提示词模式可能冲突；建议暂时关闭该扩展。
- 当前 `Img2Img` 会先生成一个 1 步的占位图再进行正式生成。
- 发送到 ControlNet 需要一个占位图像作为输入。

## 反馈与支持
- 欢迎在 Issues 中反馈问题或提出改进建议。
- 如果你需要更详细的操作说明，可参考 `usage.md`（示例与说明）。

---
本项目基于社区版本改造与增强，感谢原作者及贡献者的工作。
