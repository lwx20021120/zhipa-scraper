# AI 增强版网页数据采集桌面应用 —— 设计文档

> 项目代号：**Scrapling Desktop（暂定名，中文可叫"智爬"）**
> 版本：v0.1（设计稿）｜日期：2026-08-31
> 技术底座：Scrapling 0.4.15（已装好）+ DeepSeek API + Flet

---

## 1. 项目概述

### 1.1 一句话定位
一个**用自然语言驱动**的零代码网页数据采集桌面工具：用户说"爬这个页面的商品价格和标题"，程序自动生成爬取方案、执行抓取、校验修正，最后导出表格。

### 1.2 解决什么问题
| 现有方式 | 痛点 |
|---|---|
| 手写爬虫代码 | 要懂 Python、CSS 选择器、反爬，门槛高 |
| 现成爬虫软件 | 配置复杂、不支持动态/反爬页面、功能僵化 |
| 外包/在线服务 | 数据敏感、按量收费 |

本工具让**不懂编程的人也能爬任意网站**，且内置反爬伪装与 AI 自动纠错。

### 1.3 目标用户
- 需要采集公开数据的学生、运营、市场、研究人群
- 不会写代码，但能说清楚"要什么数据"

### 1.4 核心价值
1. **零代码**：全程自然语言操作
2. **能爬"难爬"的**：动态渲染、反爬检测自动处理
3. **AI 自纠错**：抓取失败自动修正重试，无需人工介入
4. **一键导出**：CSV / Excel / JSON

---

## 2. 技术栈

| 层次 | 技术 | 说明 |
|---|---|---|
| 界面框架 | **Flet**（Python） | 用 Python 写 Flutter 风格桌面 UI，现代美观、打包简单 |
| AI 服务 | **DeepSeek API** | `https://api.deepseek.com`，OpenAI 兼容格式，`deepseek-chat` 模型，JSON 输出模式 |
| 爬虫引擎 | **Scrapling 0.4.15** | Fetcher（静态）/ DynamicFetcher（动态）/ StealthyFetcher（反爬伪装），已安装 |
| 浏览器内核 | **patchright + Chromium 151** | 伪装指纹浏览器，已安装（约 300MB） |
| 数据处理 | pandas + openpyxl | 表格预览、CSV/Excel 导出 |
| 打包发布 | **PyInstaller** | 打包为单目录 / 单文件 .exe |

---

## 3. 系统架构

```
┌─────────────────────────────────────────────────────┐
│  界面层（Flet）                                       │
│  ┌──────────────┐  ┌──────────────────────────────┐  │
│  │ 指令输入区     │  │ 数据预览表格                  │  │
│  │ URL + 自然语言 │→ │ 抓取结果实时展示              │  │
│  │ 抓取器选择     │  │ 导出按钮（CSV/Excel/JSON）    │  │
│  └──────────────┘  └──────────────────────────────┘  │
└───────────────┬─────────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────┐
│  业务层                                               │
│  AI 配置器 ──► 爬取配置 JSON ──► Schema 校验           │
│       │                │                             │
│       │                ▼                             │
│       │          Scrapling 执行器                     │
│       │          （自动选择 Fetcher / Dynamic /       │
│       │            Stealthy，执行翻页）               │
│       │                │                             │
│       └── 校验失败 ──► 数据校验器 ──► 通过 ──► 导出    │
│           （AI 修正选择器重试 ≤3 次）                  │
└─────────────────────────────────────────────────────┘
```

---

## 4. 模块划分

| 模块 | 文件 | 职责 |
|---|---|---|
| 程序入口 | `app/main.py` | 启动 Flet 应用 |
| 全局配置 | `app/config.py` | 读取/保存 API Key、代理、默认参数（`config.json`） |
| AI 配置器 | `app/llm.py` | 调 DeepSeek：自然语言 → 爬取配置 JSON；失败修正选择器 |
| 爬取执行器 | `app/scraper.py` | 封装三种 Fetcher，执行抓取、翻页、字段提取 |
| 数据校验 | `app/validator.py` | 校验字段是否抓空、数量是否异常；触发 AI 修正 |
| 数据导出 | `app/exporter.py` | 转 pandas DataFrame → CSV / Excel / JSON |
| 主界面 | `app/ui/main_view.py` | 主窗口：输入区 + 预览表格 + 进度 |
| 设置界面 | `app/ui/settings_view.py` | API Key、代理、超时等设置 |

---

## 5. 爬取配置 JSON 格式（核心协议）

AI 的输出必须严格符合此结构（程序用 `jsonschema` 校验，不合法则让 AI 重生成）：

```json
{
  "url": "https://example.com/products",
  "fetcher": "auto",
  "fields": [
    { "name": "标题", "selector": "h2 a", "type": "text" },
    { "name": "价格", "selector": ".price", "type": "text" },
    { "name": "链接", "selector": "h2 a", "type": "attr", "attr": "href" }
  ],
  "pagination": {
    "mode": "next_button",
    "next_selector": "a.next",
    "max_pages": 5
  },
  "wait_seconds": 2
}
```

**字段说明**

| 键 | 取值 | 含义 |
|---|---|---|
| `fetcher` | `auto / static / dynamic / stealthy` | 抓取器类型，`auto` 由程序探测 |
| `fields[].type` | `text / attr / html / json` | 提取类型：文本 / 属性（如 href）/ HTML / JSON 字段 |
| `fields[].attr` | 如 `href` | `type=attr` 时的属性名 |
| `pagination.mode` | `none / next_button / url_pattern` | 翻页方式：不翻 / 点击下一页 / 按 URL 规律拼接 |
| `pagination.url_pattern` | 如 `https://x.com/page/{page}` | `url_pattern` 时的占位模板 |
| `wait_seconds` | 数字 | 动态页等待渲染秒数（默认 2） |

**示例输出（真实场景）**：用户输入"爬豆瓣 Top250 的电影名、评分和链接，翻 5 页" → AI 生成：

```json
{
  "url": "https://movie.douban.com/top250",
  "fetcher": "stealthy",
  "fields": [
    { "name": "电影名", "selector": ".hd a span:first-child", "type": "text" },
    { "name": "评分", "selector": ".rating_num", "type": "text" },
    { "name": "链接", "selector": ".hd a", "type": "attr", "attr": "href" }
  ],
  "pagination": { "mode": "url_pattern", "url_pattern": "https://movie.douban.com/top250?start={page}", "max_pages": 5 },
  "wait_seconds": 3
}
```

---

## 6. 界面布局设计（Flet）

### 6.1 主窗口
```
┌──────────────────────────────────────────────────────────────┐
│ 智爬 · AI 网页数据采集                            [设置] [历史] │
├───────────────────────────────┬──────────────────────────────┤
│ 目标网址  [https://...........]│                              │
│ 想抓什么  [爬这个页面的商品价格 ]│     数据预览表格              │
│           [和标题，翻3页      ]│  ┌────┬──────┬──────┬─────┐  │
│ 抓取器    (•)自动 ( )静态      │  │ #  │ 标题 │ 价格 │ 链接│  │
│           ( )动态 ( )伪装      │  ├────┼──────┼──────┼─────┤  │
│ [▶ 开始抓取]                   │  │ 1  │ ...  │ ...  │ ...│  │
│                               │  └────┴──────┴──────┴─────┘  │
│ 状态：[AI 正在生成爬取配置...]  │      [导出 CSV] [导出 Excel]   │
│ 进度：████████░░ 80%           │                               │
└───────────────────────────────┴──────────────────────────────┘
```

### 6.2 设置页
- DeepSeek API Key（保存到 `config.json`，首次使用提示填写）
- 代理地址（可选，默认走系统代理）
- 默认等待秒数、默认最大翻页数

### 6.3 高级页（P3 阶段）
- 内置网页预览：打开目标页面，**鼠标点击元素即自动生成 CSS 选择器**（兜底，防止 AI 猜错）
- 抓取历史：保存最近配置与结果，一键重跑

---

## 7. 核心流程

### 7.1 主流程（一次抓取）
```
用户输入（URL + 自然语言）
        │
        ▼
① AI 生成配置  ── 系统提示词："你是爬虫配置专家，根据用户需求输出 JSON..."
        │  （response_format = json_object，强制 JSON）
        ▼
② Schema 校验  ── 不合法 ──► 附带错误信息让 AI 重生成（≤2 次）
        │
        ▼
③ Scrapling 执行 ── fetcher=auto 时：先静态试抓，判断页面是动态/反爬再升级
        │  抓取 + 按 fields 提取 + 翻页
        ▼
④ 数据校验  ── 字段全空 / 行数异常 ──► 把失败原因发给 AI 修正选择器 → 重试（≤3 次）
        │
        ▼
⑤ 表格预览 + 导出
```

### 7.2 抓取器自动选择逻辑（`auto` 模式）
```
1. 先用 Fetcher 静态抓取，解析 HTML
2. 检查关键字段是否提取到数据
   ├─ 有 → 保持 static（最快）
   ├─ 无 → 怀疑 JS 渲染 → 升级 DynamicFetcher 重试
   │        ├─ 有 → dynamic
   │        └─ 无 → 怀疑反爬 → 升级 StealthyFetcher 重试
   └─ 仍无 → 触发 AI 修正选择器
```

### 7.3 AI 修正闭环（核心卖点）
```
抓取结果为空 / 字段缺失
        │
        ▼
组装修正提示：原需求 + 生成的配置 + 页面片段（前 2000 字符）
        │
        ▼
AI 输出修正后的完整配置 JSON
        │
        ▼
重新执行（最多 3 轮，仍失败则报错并给出建议）
```

---

## 8. 关键实现细节

### 8.1 DeepSeek 调用
```python
import openai

client = openai.OpenAI(
    api_key="sk-...",
    base_url="https://api.deepseek.com",
)

resp = client.chat.completions.create(
    model="deepseek-chat",
    response_format={"type": "json_object"},   # 强制 JSON 输出
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},  # 爬虫配置专家提示词
        {"role": "user", "content": user_instruction},
    ],
)
config = json.loads(resp.choices[0].message.content)
```

### 8.2 线程模型
- Flet 界面跑在主线程；抓取 + AI 调用放**后台线程**
- 用 `ft.ProgressRing` + 状态文本实时反馈，避免界面假死
- 抓取任务可取消（设置取消标志）

### 8.3 Scrapling 调用封装
```python
from scrapling.fetchers import Fetcher, DynamicFetcher, StealthyFetcher

FETCHERS = {
    "static":    lambda url, **kw: Fetcher.get(url, **kw),
    "dynamic":   lambda url, **kw: DynamicFetcher.fetch(url, **kw),
    "stealthy":  lambda url, **kw: StealthyFetcher.fetch(url, **kw),
}

# 提取字段（0.4.x API）
def extract_field(el, ftype, attr=None):
    if ftype == "text":
        return el.text
    if ftype == "attr":
        return el.attrib.get(attr, "")
    if ftype == "html":
        return el.html_content
    return ""
```

### 8.4 数据导出
```python
import pandas as pd

df = pd.DataFrame(rows)          # rows = [{字段名: 值}, ...]
df.to_csv("结果.csv", index=False, encoding="utf-8-sig")   # 带 BOM，Excel 打开不乱码
df.to_excel("结果.xlsx", index=False)                       # 需 openpyxl
df.to_json("结果.json", orient="records", ensure_ascii=False, indent=2)
```

---

## 9. 项目目录结构

```
D:\workbuudy\Scrapling\
├── app\
│   ├── main.py              # 程序入口（Flet）
│   ├── config.py            # 全局配置读写
│   ├── llm.py               # DeepSeek：配置生成 + 修正
│   ├── scraper.py           # Scrapling 执行器
│   ├── validator.py         # 数据校验与重试
│   ├── exporter.py          # 导出 CSV/Excel/JSON
│   └── ui\
│       ├── main_view.py     # 主窗口
│       └── settings_view.py # 设置页
├── config.json              # 运行时生成（API Key 等）
├── requirements.txt
├── AI爬虫工具-设计文档.md     # 本文档
└── 使用指南.md
```

**requirements.txt**
```
scrapling[all]>=0.4.15
flet
openai
pandas
openpyxl
jsonschema
pyinstaller
```

---

## 10. 里程碑与验收标准

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| **P1 骨架** | Flet 窗口 + 手动模式（URL + 选择器 + 导出） | 能手动爬一个静态页面并导出 CSV |
| **P2 AI 接入** | 自然语言 → JSON → 执行 → 校验修正闭环 | 对 3 个不同类型网站（静态/动态/反爬）输入一句话即可出数据 |
| **P3 增强** | 可视化点选 + 翻页 + 任务队列 + 历史记录 | 点选生成选择器成功；翻页正确；历史可重跑 |
| **P4 打包** | PyInstaller 出 .exe，处理体积与误报 | 在未装 Python 的电脑可运行 |

---

## 11. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 打包体积大（Chromium 300MB+） | 分发困难 | 方案 A：打包排除浏览器，首次启动自动下载；方案 B：内置，接受体积 |
| PyInstaller 被杀软误报 | 用户不敢用 | 加图标/版本信息；UPX 压缩；发布说明；考虑 Nuitka |
| AI 生成选择器不准 | 抓取失败 | 校验闭环自动修正 + 可视化点选兜底 |
| 伪装抓取慢（3~8 秒/次） | 体验差 | 进度提示 + 并发（AsyncFetcher）+ 缓存 |
| API Key 泄露 | 安全 | 仅存本地 config.json，不写入代码；支持环境变量 |
| 反爬升级 | 失效 | 保持 Scrapling 版本更新；伪装数据定期更新 |

---

## 12. 后续扩展方向（可选）

- **定时任务**：每天自动抓取，数据变化监控与推送
- **模板市场**：共享抓取配置 JSON，一键导入
- **数据入库**：导出到 SQLite / MySQL
- **导出图表**：内置简单统计图表
- **更多 AI 模型**：兼容 OpenAI / 硅基流动 / 本地 Ollama
