# 学习通个人学业工作台（用户画像与本地分层数据仓库）产品规格说明书 (SPEC)

> 状态：`ready-for-agent`

---

## Problem Statement

当前的学习通辅助工具（MCP 服务与浏览器自动化 Skill）采用“无状态、扁平目录、全量扫描”的设计模式，在实际高频学业场景中暴露出三大核心痛点：

1. **全量盲扫效率低且信息噪音大**：学生账户下累计选修/加入了近 40 门历史与当前课程，而学习通平台大量往期课程未标注“已结课”或未填写规范的开课时间。每次检查未交作业时，系统不得不遍历全部未关闭课程，不仅耗时，还会把往期遗留未交项、非本组汇报项与本学期真正需要处理的作业混杂输出。
2. **缺失学生个人画像（User Profile）导致误判与重复追问**：系统不知道学生的入学年月（无法自动推算当前所处学期）、不知道学生在特定课程中的分组编号（如小组汇报课属于第几组，导致将其他组的未交作业误报为待办）、也不知道学生的学号、姓名、专业班级、开发环境偏好及课程特定的附件命名规范，导致生成实验报告、代码或上传附件时无法自动遵循规范。
3. **作业上下文割裂且缺乏本地结构化沉淀**：学习通上的作业要求往往分散在“作业列表页”、“课程通知”甚至线下课堂板书/微信群截图中。现有工具将输出简单平铺在扁平临时目录下，没有按「学期 → 学科 → 作业批次」建立分层结构，也没有区分「信息（题目原文 + 关联通知 + 用户手动补充的截图/说明）」与「成果（答案审核单 + 源码与测试 + 最终可交附件）」，导致复杂作业（如编程实验、表格填写、课程论文）难以多轮迭代与复查。

---

## Solution

在保持“绝不自动提交、不进正式考试、不伪造签到、账号密码不入库”四大安全红线的前提下，将现有工具升级为**带双层用户画像与本地分层数据仓库的个人学业工作台**：

1. **独立的数据工作区隔离**：在项目下建立独立的本地数据工作区（与工具源码解耦并纳入版本忽略），统一承载用户画像、课程状态索引和按学期组织的学科/作业归档树。
2. **双层用户画像（Global + Course Meta）**：
   - **全局画像**：维护学生的姓名、学号、学院、专业班级、入学年月（据此结合当前日期自动推算当前学年学期，如大二上 `2026-2027-1`）、通用开发环境与报告排版偏好。
   - **学科画像**：在每门学科目录下维护学科元信息，记录任课教师、学生在该课的所在分组号、附件提交命名模板及教师口头要求。
3. **课程学期切分与三态过滤机制**：维护统一的课程索引（合并原有的签名参数缓存），将所有课程划分为 `active`（本学期在修）、`long_term`（跨学期长线课，如形势与政策）和 `archived`（往期归档）。日常作业扫描默认仅查询 `active + long_term` 课程，实现秒级精准扫描。
4. **全中文直观分层目录与「信息 + 成果」双区契约**：
   - 按 `学期 / 学科 / {course_meta, 通知, 资料, 作业}` 组织本地目录。
   - 每个作业批次下严格划分为 `信息/` 与 `成果/` 两个子目录：
     - **扫描即建骨架**：日常扫描发现新作业时，仅根据列表元数据自动创建本地 `<作业名>/信息/` 与 `成果/` 空骨架及基础元信息，不盲目打开未作答作业的详情页（避免误触发平台分配答题记录），方便学生随时打开 Finder 将课堂照片、作业本截图或补充说明直接拖入 `信息/`。
     - **深查/作答时聚合信息**：当学生指定查看详情或辅助作答时，Agent 拉取完整题面、公式图片、附件模板写入 `信息/`，语义关联 `<学科>/通知/` 中的相关通知生成关联摘要，并**零配置遍历读取 `信息/` 下学生放入的所有截图与文档**（用户补充信息优先级最高）。
     - **自适应生成成果并暂存**：Agent 在 `成果/` 下生成统一的答案审核单、编译测试通过的实验源码及按画像规范命名的最终交付附件，回填/上传至学习通草稿框并执行“暂时保存”，交由学生最终把关提交。
5. **工程结构去重与单一主源**：清理工作区中重复挂载的插件与 Skill 副本，确保 MCP 服务与 Skill 指令单一主源且与新的分层数据模型完全对齐。

---

## User Stories

### 一、用户画像与学期自动推算
1. As a student, I want to maintain a global user profile containing my name, student ID, major/class, enrollment year-month, and development environment preferences, so that the assistant never has to ask me for basic personal details repeatedly.
2. As a student, I want the system to automatically calculate my current academic semester (e.g., `2026-2027-1`, Sophomore Fall) from my enrollment date and the current date, so that it always knows which semester is currently active.
3. As a student, I want the system to pre-populate my initial global profile and course index from already-discovered course data, so that I only need to review and tweak a ready-made template instead of writing it from scratch.
4. As a student, I want to maintain a per-course metadata file (`course_meta`) inside each subject folder to record my group number, teacher name, file naming rules, and special grading criteria, so that course-specific rules are cleanly isolated per subject.
5. As a student in a group-based course, I want the assistant to check my group number in the course metadata when scanning or reviewing homework, so that homework entries assigned to other groups (e.g., Groups 11–21 when I am in Group 1–10) are automatically flagged as non-applicable rather than urgent to-dos.

### 二、课程索引、学期切分与精准扫描过滤
6. As a student, I want all my enrolled courses and their signature tokens (`stuenc` / `work_enc`) stored in a unified course index with explicit lifecycle statuses (`active`, `long_term`, `archived`), so that course metadata and authentication tokens are managed in one place.
7. As a student, I want newly discovered courses to be automatically classified into a semester and assigned an initial status based on their start date and course ID ordering, so that courses without explicit start dates on the platform are still categorized sensibly.
8. As a student, I want daily homework scans to check only `active` (current semester) and `long_term` (cross-semester) courses by default, so that scanning finishes in under a second without noise from 20+ finished historical courses.
9. As a student, I want the ability to optionally request a full scan including `archived` courses when needed, so that I can still inspect historical coursework on demand.
10. As a student, I want to easily reclassify any course (such as a summer-term course that the teacher never closed on the platform) to `archived`, so that its unsubmitted group items stop appearing in my daily homework dashboard.

### 三、本地分层目录与“扫描即建骨架”
11. As a student, I want my local academic data stored in a dedicated workspace directory separated from tool source code and ignored by Git, so that my personal profile, cookies, and homework files are safe from accidental commits.
12. As a student, I want the local filesystem organized using intuitive Chinese directory names (`学期 / 学科 / {通知, 资料, 作业 / <作业名> / {信息, 成果}}`), so that I can browse folders directly in Finder or my editor and drag-and-drop files effortlessly.
13. As a student, I want the homework list scanner to automatically create the local folder skeleton (`<作业名>/信息/` and `<作业名>/成果/`) and save basic list metadata (work ID, status, remaining time, entry URL) whenever a course homework item is detected, so that the local folder is ready before I even start working on the assignment.
14. As a student, I want the list scanner to strictly avoid opening the detail/answering URL of unstarted homework (`answerId=0`) during routine scans, so that routine checking never prematurely triggers the platform to allocate an answering session or start a timer.

### 四、信息层（`信息/`）：题目提取、通知关联与用户手动补充
15. As a student, I want the assistant to fetch the full homework prompt, question list, formula images, and teacher-provided attachment templates into `<作业名>/信息/题目原文.md` (and accompanying asset files) when I ask to inspect or complete a specific homework, so that I have a complete local snapshot of the problem.
16. As a student, I want course notifications synced into `<学科>/通知/` and semantically matched by the assistant to relevant homework batches (written to `<作业名>/信息/关联通知.md`), so that extra instructions or deadline changes posted in course notices are never missed even when notice titles differ from homework titles.
17. As a student, I want to drop arbitrary screenshots (e.g., blackboard photos, notebook pages, WeChat group notices), templates, or markdown notes into `<作业名>/信息/` without renaming them to rigid filenames, so that adding extra context is frictionless.
18. As a student, I want the assistant to mandatory-scan all files inside `<作业名>/信息/` (using multimodal vision for images and text extraction for documents) before solving any homework, treating my manual supplements as the highest-priority requirements.
19. As a student, I want downloaded courseware and extracted lecture notes stored under `<学科>/资料/`, so that the assistant can reference authoritative course slides when answering homework questions for that subject.

### 五、成果层（`成果/`）：多题型自适应产出、本地验证与草稿暂存
20. As a student, I want every completed homework batch to produce a standardized review sheet (`<作业名>/成果/答案审核单.md`) containing question numbers, my answers, reasoning/citations, confidence levels (`高/中/低`), low-confidence highlights at the top, and platform draft-save status, so that I can quickly audit the AI's work before submitting.
21. As a student working on programming or lab assignments, I want the assistant to write source code and test scripts in `<作业名>/成果/src/` and compile/execute them locally until tests pass, so that code deliverables are verified before being packaged or written into reports.
22. As a student working on document or spreadsheet assignments (e.g., `.xls` or `.docx` attachments), I want the assistant to generate the final deliverable in `<作业名>/成果/` named strictly according to my global profile and course metadata conventions, so that the file is ready for upload without manual renaming.
23. As a student, I want the assistant to fill answers or upload deliverable attachments into the platform's answering page, click only "暂时保存" (Save Draft), and re-open the page to verify that the saved draft persists, so that I only need to do a final visual check and click "提交" (Submit) myself.

### 六、工程结构收敛与一致性
24. As a developer/user of this workspace, I want duplicate plugin and skill registrations removed so that only a single canonical MCP server set and a single canonical Skill definition are active, preventing tool namespace collisions and instruction drift.
25. As a developer/user, I want the Skill instructions and reference docs updated to reflect the new `workspace/` hierarchy, profile loading step, `信息/` aggregation step, and `成果/` output contract, so that any AI agent invoking the skill follows the exact same workflow.

---

## Implementation Decisions

### 1. 本地工作区与目录树契约（Workspace Hierarchy Contract）
- 在项目根目录下设立独立的数据工作区目录 `workspace/`（可通过环境变量覆盖路径，默认指向项目根目录下的 `workspace/`），并将 `workspace/` 加入根级与子仓库的 `.gitignore`。
- 标准目录树结构约定如下：
  ```text
  workspace/
  ├── profile.md                        # 全局学生画像（YAML Frontmatter + Markdown 补充说明）
  ├── courses_index.json                # 课程全局索引（含学期归属、status 状态、stuenc/work_enc 签名缓存）
  └── semesters/
      └── <学期标识，如 2026-2027-1>/
          └── <学科名称>/
              ├── course_meta.md        # 学科级画像与规则（任课教师、所在分组、命名模板、特殊要求）
              ├── 通知/                 # 同步的课程通知归档（按通知或汇总 Markdown 存放）
              ├── 资料/                 # 课程课件下载与提取文本（原 notes_<课程> 迁移至此）
              └── 作业/
                  └── <作业名称（做文件系统安全字符清洗）>/
                      ├── 信息/
                      │   ├── 作业元信息.json   # 扫描列表时自动写入（workId, answerId, status, remaining, detail_url, updated_at）
                      │   ├── 题目原文.md       # 深查/作答时拉取的完整题面、选项与公式图片引用
                      │   ├── 关联通知.md       # Agent 从 <学科>/通知/ 语义关联摘录的本作业相关通知要求
                      │   └── <用户任意文件>    # 用户随手拖入的截图(.png/.jpg)、补充说明(.md/.txt)或附件模板(.xls/.docx)
                      └── 成果/
                          ├── 答案审核单.md     # 必有：题号|题型|答案|理由/测试结果|置信度 + 暂存核验状态
                          ├── src/              # 选有（编程/实验题）：源码、测试数据与验证脚本
                          └── <最终交付文件>    # 选有（报告/表格/附件题）：按画像命名规范生成的待上传成品
  ```

### 2. 双层用户画像 Schema 与学期推算逻辑
- **全局画像（`workspace/profile.md`）**：采用 `YAML Frontmatter + Markdown 正文` 结构，既方便 Python MCP 直接解析关键字段，又方便用户和 Agent 直接用自然语言阅读与编辑。核心结构如下：
  ```yaml
  ---
  name: "待填写姓名"
  student_id: "待填写学号"
  college: "计算机科学与技术学院（软件学院）"
  major_class: "软件工程（中外合作）2025班"
  enrollment: "2025-09"          # 入学年月 YYYY-MM
  current_semester: "auto"       # "auto" 或显式指定如 "2026-2027-1"
  attachment_naming_default: "{student_id}_{name}_{homework_title}"
  ---
  ## 开发与作答环境偏好
  - 操作系统：macOS
  - 编程语言偏好：C++ (Clang/G++ C++17)、Python 3.10+、Java、JavaScript (Node.js)
  ```
- **学期自动推算算法**：
  - 当 `current_semester` 为 `"auto"` 时，根据当前日期 `(year, month)` 与 `enrollment` `(enroll_year, 9)` 自动计算：
  - 若 `month >= 9`：当前学年为 `f"{year}-{year+1}"`，学期序号为 `1`（秋冬学期，即 `YYYY-(YYYY+1)-1`）；若 `month == 1`：归属上一自然年开启的秋冬学期 `f"{year-1}-{year}-1"`；若 `2 <= month <= 8`：当前学年为 `f"{year-1}-{year}"`，学期序号为 `2`（春夏学期，即 `(YYYY-1)-YYYY-2`）。
- **学科级画像（`course_meta.md`）**：当某个学科目录首次创建时，自动生成带默认模板的 `course_meta.md`（包含 `teacher`、`group`、`naming_rule`、`notes`），对于已知分组信息的课程（如《人工智能导论》处于第 1~10 组）直接预填。

### 3. 课程索引（`courses_index.json`）与三态过滤机制
- 将原 `chaoxing-mcp` 内部的 `enc_map.json` 升级并迁移/桥接至 `workspace/courses_index.json`（同时保留对旧路径的向后兼容读取，写入统一落盘至 `workspace/courses_index.json`）。
- 每门课的索引条目 Schema（来自实测数据结构）：
  ```json
  {
    "267157766": {
      "courseId": "267157766",
      "clazzId": "154870267",
      "cpi": "482522549",
      "name": "数据结构",
      "teacher": "蒋莉",
      "semester": "2026-2027-1",
      "status": "active",
      "stuenc": "9e29a8ab805bdaab789f7fa64d6e85fa",
      "work_enc": "36e6fec31a44e94f9c19885b8b444b0c",
      "updated": "2026-09-28"
    }
  }
  ```
- **课程三态（`status`）定义与默认扫描行为**：
  1. `active`：当前学期在修课程（如 2026-2027-1 的《数据结构》《概率论与数理统计A》《嵌入式系统》《面向软件技术的离散数学》《体育》《毛概》《马原》《用Javascript程序设计网页和服务程序》）。
  2. `long_term`：跨学期持续有效课程（如《形势与政策2025》《计算机学院25新生晚自习》）。
  3. `archived`：已结课或上一学期课程（含官方已标“课程已结束”的 5 门课，以及 2025-2026 学年春夏/秋冬课程和 2026-05 开课的《人工智能导论》）。
- **新课自动推断初筛规则**：当远端课程列表出现尚未记录在 `courses_index.json` 中的新 `courseId` 时，默认将其归入当前计算出的学期并标为 `status: "active"`，提醒存在缺少 `enc` 的新课需补种。

### 4. MCP 服务接口扩展与“扫描即建骨架”契约
- **`xt_courses` 升级**：
  - 支持按 `status` 过滤（默认展示 `active` 与 `long_term` 课程并汇总 `archived` 数量，传 `include_archived=true` 时列出全部），显示每门课的学期、状态及是否已配置 `enc`。
- **`xt_homework` 与 `xt_homework_all` 升级**：
  - `xt_homework_all` 默认仅扫描 `status` 为 `active` 或 `long_term`（且具备 `enc`）的课程；支持可选参数 `include_archived: bool = False` 与 `semester: str` 过滤。
  - **骨架自动生成（Skeleton Materialization）**：在 `xt_homework` 和 `xt_homework_all` 解析出某门课的作业列表后，自动在 `workspace/semesters/<semester>/<course_name>/作业/<safe_homework_title>/` 下创建 `信息/` 和 `成果/` 目录（若不存在），并将列表级元数据（`workId`, `answerId`, `title`, `status`, `remaining`, `detail_url`, `last_seen`）写入 `信息/作业元信息.json`。
  - **安全边界**：列表扫描阶段**绝不请求** `detail_url`（避免触发 `answerId=0` 的作业分配答题记录）。
- **新增课程状态与画像管理辅助工具（或参数）**：
  - 提供 `xt_course_status(course, status, semester)` 工具，支持在对话中一键将某门课程设为 `active` / `long_term` / `archived` 或调整所属学期，并同步更新 `workspace/courses_index.json`。

### 5. Skill 工作流改造与“信息 → 成果”协同协议
- 更新 Skill 主指令与参考文档（`SKILL.md`、`homework.md`、`download.md`、`info.md`）：
  1. **废弃 `./xuexitong-work` 扁平目录**，全面切换至 `workspace/semesters/<学期>/<学科>/` 体系。
  2. **作业仪表盘流程**：优先调用 MCP `xt_homework_all` 秒级获取本学期（`active + long_term`）未交作业并自动生成本地目录骨架；仅当发现新课缺失 `enc` 时才动用浏览器进入该课程的“作业”标签提取 `stuenc/work_enc` 并调用 `xt_seed_enc`。
  3. **作答前强制 Step 0（加载画像与聚合 `信息/`）**：
     - 读取 `workspace/profile.md` 与当前学科的 `course_meta.md`（核对分组号、命名规范、环境要求）。
     - 检查 `<学科>/通知/`，将与当前作业语义相关的通知要求摘录至 `<作业名>/信息/关联通知.md`。
     - 进入作答页提取题目与附件，保存至 `<作业名>/信息/题目原文.md`。
     - **强制遍历 `<作业名>/信息/` 下的所有文件**：对所有图片（`.png/.jpg/.jpeg/.webp`）执行视觉读题/转录，对文档/表格提取文本与结构，将用户手动放入的补充信息作为最高优先级约束。
  4. **作答与成果生成（`成果/`）**：
     - 在 `<作业名>/成果/答案审核单.md` 生成结构化审核单（低置信度置顶）。
     - 编程实验在 `<作业名>/成果/src/` 编写源码并执行本地编译与测试验证，测试结果写入审核单。
     - 需要上传文件的作业，按画像命名规则在 `<作业名>/成果/` 生成成品文件，通过浏览器注入上传至学习通，点击“暂时保存”并二次进页核验草稿状态。

### 6. 工程结构收敛（Single Source of Truth）
- 移除 `.agents/plugins/xuexitong` 重复插件目录及 `.agents/plugins.json` 中的冗余挂载，消除 `xt` / `xuexitong_xt` 与 `playwright` / `xuexitong_playwright` 的双倍加载。
- 将 `.agents/skills/xuexitong` 与 `xuexitong-skill/skills/xuexitong` 统一为单一主源（通过软链接或同步保持完全一致）。

---

## Testing Decisions

### 1. 什么是好测试（Testing Philosophy）
- **只测外部可观测行为，不测内部实现细节**：测试不应绑死内部私有正则或辅助函数名，而应验证给定输入（MCP JSON-RPC 请求 + 模拟的远端 HTML 响应 + 临时 `workspace/` 目录状态）时，系统的返回文本与落盘的文件系统结构是否符合契约。

### 2. 测试边界（Testing Seam）
- 以 `chaoxing-mcp` 的顶层请求分发入口 `handle(req)`（及对应的 `tool_*` 顶层接口）作为**唯一高层测试边界（Seam）**：
  - 通过注入/替身（Stub/Mock）HTTP 会话层（返回真实录制的学习通课程列表 HTML 与作业列表 HTML 样本），结合临时创建的 `workspace/` 目录（通过环境变量或配置指向 `tmp_path`），端到端验证以下核心行为：
    1. **画像读取与学期自动推算**：给定 `profile.md` 中的 `enrollment: "2025-09"` 与测试时钟 `2026-09-28`，自动推算出当前学期为 `2026-2027-1`。
    2. **三态过滤准确性**：`xt_homework_all` 默认只请求并汇报 `status` 为 `active` 和 `long_term` 的课程，自动跳过 `archived` 课程（如已归档的《人工智能导论》或上学期课程）；当传入 `include_archived=True` 时才包含归档课程。
    3. **骨架自动创建与安全红线**：调用 `xt_homework` 或 `xt_homework_all` 后，自动在临时 `workspace/semesters/2026-2027-1/<学科>/作业/<作业名>/` 下生成 `信息/作业元信息.json` 和 `成果/` 空目录，且全程**零次请求** `work/task` 详情页 URL（确保不误触发 `answerId=0` 的答题记录分配）。
    4. **课程状态变更**：调用课程状态更新工具后，`workspace/courses_index.json` 中对应课程的 `status` 与 `semester` 正确持久化。

### 3. 测试先例（Prior Art）
- 采用 Python 标准库 `unittest`（或 `pytest`）编写独立的离线集成测试套件，不依赖真实外网连接或真实账号密码，所有测试均可在毫秒级离线可重复运行。

---

## Out of Scope

1. **绝不自动提交作业**：任何情况下都不提供自动点击学习通“提交”按钮的无确认流水线，“提交”权永远保留给学生本人或学生在审核完答案单后的当场明确指令。
2. **绝不触碰正式考试与防作弊机制**：不进入有监考、限时或防作弊机制的考试页面，不提取考试题面。
3. **不伪造签到证据与不主动挂机刷课**：不在本次升级中改动签到与视频任务点的默认关闭策略及证据红线。
4. **不在列表扫描阶段全量预抓所有未开始作业的题面**：为避免污染学习通后台的首次点开记录（`answerId=0`），列表扫描只建本地目录骨架与列表元信息，不自动批量爬取未打开作业的内部题干。
5. **不开发独立的图形化前端界面（GUI）**：直接复用本地文件系统（Finder / IDE 目录树）+ Markdown 文件 + MCP 对话作为最自然的人机交互界面。

---

## Further Notes

- **平滑迁移与预填数据**：在实施阶段，将直接使用此前真机扫描获取的 38 门课程数据与 33 组 `stuenc/work_enc` 签名，一次性生成预填好的 `workspace/profile.md`、`workspace/courses_index.json` 以及本学期存在未交作业课程（如《数据结构》《形势与政策2025》）的初始目录骨架与 `course_meta.md`。
- **文件名清洗规则**：学习通作业标题可能包含 `/`、`:`、`;` 或尾部空格（例如 `复习2-C++函数及运算符重载.xls`、`25/26心理健康与自我成长`），落盘为文件夹名时需统一将非法路径字符（如 `/`、`\`、`:`）替换为 `-` 或 `_`，同时在 `信息/作业元信息.json` 中保留原始标题。
