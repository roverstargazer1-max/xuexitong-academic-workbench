# 学习通个人学业工作台 (Xuexitong Academic Workbench)

基于**双层用户画像（Global Profile + Course Meta）**与**本地全中文分层数据仓库（`workspace/`）**的学习通（超星）个人学业辅助工作台。结合 `chaoxing-mcp` 直连 HTTP 服务与 `xuexitong-skill` 浏览器自动化技能，实现秒级精准扫描、自动建立作业目录骨架、多模态上下文聚合与本地编译验证。

## 核心特性

1. **三态课程索引与秒级精准扫描**：
   - 将选修课程划分为 `active`（本学期在修）、`long_term`（跨学期长线课）与 `archived`（往期归档），日常扫描默认仅查询活跃课程，告别近 40 门历史课程全量盲扫。
2. **双层用户画像与学期自动推算**：
   - 全局画像（`workspace/profile.md`）维护学号、姓名、专业班级、入学年月（自动推算当前学期如 `2026-2027-1`）与开发环境偏好；
   - 学科画像（`<学科>/course_meta.md`）隔离记录各科任课教师、所在分组号（自动识别并跳过非本组作业）与专属附件命名规范。
3. **扫描即建骨架（零误触安全边界）**：
   - 扫描列表时自动创建 `workspace/semesters/<学期>/<学科>/作业/<作业名>/{信息, 成果}` 中文目录树与 `信息/作业元信息.json`；
   - 列表扫描阶段**绝不请求** `work/task` 详情页 URL，严防触发平台为 `answerId=0` 的未开始作业提前分配答题记录。
4. **「信息 → 成果」协同工作流**：
   - **信息区（`信息/`）**：聚合完整题面（`题目原文.md`）、课程通知语义关联摘要（`关联通知.md`）及用户随手拖入的课堂板书照片/截图/附件模板（零配置多模态遍历读取，赋予最高优先级）。
   - **成果区（`成果/`）**：生成低置信度置顶的 `答案审核单.md`、在 `成果/src/` 下编写并本地编译测试通过的实验源码、以及按画像规范命名的待上传成品文件，回填平台后**只点“暂时保存”并二次进页核验**，绝不自动提交。

## 目录结构

```text
.
├── .env.example                # 环境变量配置模板（复制为 .env 使用，.env 不入库）
├── SPEC.md                     # 产品规格说明书与架构蓝图
├── chaoxing-mcp/               # 学习通直连 MCP Server 与离线集成测试套件
│   ├── server.py               # MCP JSON-RPC 服务端实现
│   └── test_server.py          # 基于顶层 handle(req) Seam 的离线自动化测试
├── xuexitong-skill/            # 学习通 AI 技能主源定义与参考手册
│   └── skills/xuexitong/       # SKILL.md、references/ 与浏览器注入脚本 scripts/
└── workspace/                  # 本地个人学业数据仓库（Git 忽略，绝不入库）
    ├── profile.md              # 全局学生画像
    ├── courses_index.json      # 课程三态索引与 stuenc/work_enc 签名缓存
    └── semesters/              # 按「学期 / 学科 / {通知, 资料, 作业/<作业名>/{信息, 成果}}」组织
```

## 快速开始

1. **配置环境变量**：
   ```bash
   cp .env.example .env
   # 编辑 .env 填入 XT_PHONE 与 XT_PASSWORD
   ```
2. **安装依赖并运行离线测试套件**：
   ```bash
   ./chaoxing-mcp/.venv/bin/python -m unittest discover -s chaoxing-mcp -v
   ```
