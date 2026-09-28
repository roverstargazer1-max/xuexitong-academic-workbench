# 课件资料下载与知识点提取(2026-09 真机校准)

学习通网页版多数资料**没有下载按钮**,本模块用资料页 dataId 拼直链下载,并统一归档至对应学科的 `workspace/semesters/<学期>/<学科>/资料/` 目录下。

## 资料列表页

1. 课程页点"资料"标签 → `#frame_content-zl`(新版 src 是 `mooc2-ans/coursedata/stu-datalist?...`)→ **frameLocator 读取**(不要 goto 其 src,会报无权限)。
2. 文件条目 `dt[onclick]`,onclick 形如:
   `toOpen('%E5%AE%9E%E9%AA%8C1...ppt','ppt',1336228939,'',...)`
   —— 文件名是 URL 编码的,`decodeURIComponent` 还原;第 3 个参数是 **dataId**。
3. 列出 `{文件名, 类型, dataId}` 给用户确认后下载至 `workspace/semesters/<学期>/<学科>/资料/`。

## 下载直链(真机验证:302 → d0.cldisk.com CDN)

```text
https://mooc1.chaoxing.com/coursedata/downloadData?dataId=<dataId>&classId=<classId>&cpi=<cpi>&courseId=<courseId>&ut=s
```

- `classId`/`cpi`/`courseId` 可直接从 `workspace/courses_index.json` 读取。
- **导航即下载**:goto 直链 → 302 到 `d0.cldisk.com/download/<token>?at_=..&ak_=..` → 浏览器下载管理器接管并落盘到 `workspace/semesters/<学期>/<学科>/资料/`。
- 批量:逐个触发,间隔 ~6 秒;下载完核对文件数与大小(0 字节 = 失败重试)。
- 环境差异:Playwright MCP / chrome-devtools MCP 用 download 事件接文件并移入 `<学科>/资料/`;IAB 类环境不落盘,把直链给用户或引导其在自己浏览器打开。

## 章节卡片里的附件

知识点卡片页(`mooc1-1.chaoxing.com/knowledge/cards?...&knowledgeid=...`,见 `tasks.md`)里的 PPT/PDF:

- 卡片 DOM 里通常能找到 `objectid`;
- 请求 `https://mooc1-1.chaoxing.com/ananas/status/<objectId>?k=&flag=normal` 返回 JSON,`download` 字段是直链(`null` = 教师禁止下载,如实告知,不要绕);
- 页面上已有下载按钮的直接点按钮最稳。

## 文本提取(供作业作答引用,统一存入 `<学科>/资料/`)

- `.pptx`/`.docx`:`python-pptx` / `python-docx`;旧版 `.ppt`/`.doc` 可用 LibreOffice / Office COM 转换提取。
- `.pdf`:PyMuPDF(`import fitz`, `page.get_text()`)。
- 提取目标:核心定义、定理公式、算法伪代码、例题与课后习题——提取出的 Markdown/文本统一存放在 `workspace/semesters/<学期>/<学科>/资料/<课件名>.md`(或 `.txt`),供作业作答时直接检索并标注页码引用。
