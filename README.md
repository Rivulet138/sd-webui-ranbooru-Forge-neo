# Ranbooru Forge / Ranbooru Reforge

> 适配 **AUTOMATIC1111 Stable Diffusion WebUI / Forge** 的增强扩展。  
> 从多个 Booru 站点随机抓取标签，自动生成可用提示词（Prompt），支持 Img2Img、Deepbooru 自动打标、LoRAnado、标签缓存与筛选池等能力，帮助你快速构造多样化生成工作流。

![Ranbooru 面板](pics/ranbooru.png)

---

## 目录

- [项目简介](#项目简介)
- [核心特性](#核心特性)
- [支持站点](#支持站点)
- [安装方式](#安装方式)
- [快速开始（3 分钟上手）](#快速开始3-分钟上手)
- [参数总览](#参数总览)
- [高级功能](#高级功能)
  - [Img2Img / ControlNet / Deepbooru](#img2img--controlnet--deepbooru)
  - [LoRAnado（LoRA 随机器）](#loranadoloRA-随机器)
  - [Tag 缓存系统（SQLite）](#tag-缓存系统sqlite)
  - [标签筛选池（Filtered Pool）](#标签筛选池filtered-pool)
- [凭证与隐私](#凭证与隐私)
- [典型使用场景](#典型使用场景)
- [目录结构](#目录结构)
- [已知问题](#已知问题)
- [常见问题 FAQ](#常见问题-faq)
- [更新记录与反馈](#更新记录与反馈)

---

## 项目简介

Ranbooru Forge（Reforge）是 Ranbooru 的增强分支，围绕以下目标优化：

- 更稳定的标签生成流程
- 更丰富的随机化和控制能力
- 更实用的缓存与离线工作流
- 更适合批量生成与实验型工作流

适合人群：

- 需要快速构建提示词的人
- 需要批量生成且希望每张图提示词不同的人
- 需要在“随机”与“可控”之间平衡的人

---

## 核心特性

- ✅ 多 Booru 数据源随机抓取标签
- ✅ 标签去重、打乱、数量限制、坏标签清理
- ✅ `Tags to Search` + `Tags to Remove` 双向控制
- ✅ Img2Img 模式，支持使用上一张图、中心裁剪、ControlNet 发送
- ✅ Deepbooru 自动打标（前置 / 后置 / 替换）
- ✅ LoRAnado：随机挑选 LoRA + 权重控制 + 锁定上轮
- ✅ 文件驱动标签（`tags_search.txt` / `tags_remove.txt`）
- ✅ SQLite 标签缓存（本地离线顺序读取）
- ✅ 标签筛选池（从缓存中按关键词筛选并建立可切换池）

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

> 注：不同站点对内容分级、Post ID、API 等支持不完全一致。

---

## 安装方式

### 方式一（推荐）

将仓库克隆到 WebUI 的 `extensions` 目录：

```bash
git clone -b kemomimi --single-branch https://github.com/Rivulet138/sd-webui-ranbooru-reforge.git extensions/sd-webui-ranbooru-reforge
```
## 快速开始（3 分钟上手）

1. 打开 Ranbooru 面板并勾选 **Enabled**
2. 选择 Booru
3. 在 **Tags to Search (Pre)** 填写搜索标签（逗号分隔，如 `1girl,blue_eyes,long_hair`）
4. 点击 **生成提示词**
5. 生成结果会显示在输出框，并可写入 WebUI prompt
6. 点击 WebUI **Generate** 开始出图

---

## 参数总览

### 基础参数
- **Booru**：数据源站点
- **Max Pages**：随机搜索页上限（影响样本范围）
- **Post ID**：指定图片 ID（留空则随机）
- **Tags to Search (Pre)**：预搜索标签（逗号分隔）
- **Tags to Remove (Post)**：后处理移除标签（支持 `*` 通配）
- **Mature Rating**：按站点支持筛选分级内容

### 标签处理参数
- **Remove bad tags**：清理水印/文字/打码等低价值标签
- **Shuffle tags**：打乱标签顺序
- **Convert "_" to spaces**：下划线转空格（`blue_eyes` -> `blue eyes`）
- **Limit tags**：保留比例
- **Max tags**：保留标签最大数量

### 视觉风格参数
- **Change Background**：背景相关标签策略（添加/移除/清空）
- **Change Color**：色彩倾向策略（彩色/有限调色板/单色）
- **Sorting Order**：随机/高分优先/低分优先

### 批处理相关
- **Use same prompt for all images**：同批次共用提示词
- **Use same seed**：同批次共用随机种子

---

## 高级功能

### Img2Img / ControlNet / Deepbooru
- **Use img2img**：启用图生图模式
- **Send to Controlnet**：发送图像到 ControlNet（需已安装）
- **Denoising**：重绘强度（建议从 0.5~0.75 测试）
- **Use last image as img2img**：使用上一轮图像
- **Crop Center**：中心裁剪
- **Use Deepbooru**：自动识别图像标签
- **Deepbooru Tags Position**：
  - Add Before（前置）
  - Add After（后置）
  - Replace（替换原提示词）

### LoRAnado（LoRA 随机器）
- 从指定 LoRA 文件夹随机选取 N 个 LoRA
- 支持最小/最大权重范围
- 支持自定义权重列表（逗号分隔）
- 支持锁定上轮 LoRA 组合（Lock previous LoRAs）

**常见搭配：**
- LoRAs Amount = 2~3
- Min/Max Weight = 0.3~0.8
- 与 Mix prompts 一起可增加随机性

### Tag 缓存系统（SQLite）
**缓存文件**：`user/cache/tag_cache.db`

**支持能力：**
- 批量爬取标签到本地数据库
- 追加模式 / 覆盖模式
- 顺序读取下一条标签（可循环）
- 按关键词搜索缓存
- 按 ID 查看、填充、删除
- 重置索引并持久化保存（重启后可续）

**适合场景：**
- 大批量生成时避免每次联网
- 想要“每张图不同提示词但可追踪”的流程

### 标签筛选池（Filtered Pool）
这是本分支的重要增强能力：
- 从主缓存按关键词筛选（必须包含 / 必须排除）
- 将选中的 ID 生成独立 `filtered_pool`
- 可在“主缓存 / 筛选池”之间切换
- 切换状态持久化保存到数据库 metadata
- 生成时可按当前数据源顺序输出标签

**典型示例：**
- **只保留单人**： 包含：`1girl` 排除：`2girls,multiple_girls`
- **指定发色**： 包含：`1girl,blue_hair` 排除：`blonde_hair,red_hair`

---

## 凭证与隐私

gelbooru 与 rule34 可能需要 API 凭证：
- API Key
- User ID

**保存后写入本地：**
`user/credentials/credentials.json`

你可通过 **Clear saved credentials** 清除。

> **注意：** 建议不要公开上传你的凭证文件。

---

## 典型使用场景

**场景 1：快速随机探索**
- 开启 Remove bad tags + Shuffle tags
- Sorting Order = Random
- 多次点击生成，快速探索构图方向

**场景 2：批量不重复生成**
- 先批量抓取标签到缓存
- 生成时启用缓存读取 + 循环
- 关闭 Use same prompt for all images

**场景 3：高可控角色批量**
- 使用筛选池保留“必须元素”
- 排除不希望出现的角色数或题材标签
- 再结合 LoRAnado 做风格微随机

---

## 目录结构

```text
sd-webui-ranbooru-reforge/
├── scripts/
│   ├── ranbooru.py
│   ├── cache_db.py
│   └── prompt_cleaner.py
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

## 已知问题
- 批次较大时，Chaos/Negative 模式可能偶发异常（重试通常可恢复）
- 与 `sd-dynamic-prompts` 同用时可能冲突
- 当前 Img2Img/ControlNet 流程存在占位图步骤（属于现有实现限制）
---
## 常见问题 FAQ
Q1：提示 No posts found？

A：降低 Max Pages、减少标签数量、切换站点、检查网络。

Q2：标签太多/太少？

A：调整 Limit tags 与 Max tags，并结合标签分类/移除规则。

Q3：如何完全离线使用？

A：先批量爬取到本地缓存，然后生成时启用缓存读取。

Q4：缓存索引怎么重置？

A：在缓存管理面板点击“重置索引
---
## 更新记录与反馈
详细说明请查看：
- `更新日志.txt`
- `使用说明.txt`
- `usage.md`
欢迎提交 Issue / PR 提出改进建议。  
本项目基于社区版本改造与增强，感谢原作者及贡献。
