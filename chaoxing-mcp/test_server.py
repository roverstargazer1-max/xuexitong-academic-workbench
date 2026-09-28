import json
import os
import tempfile
import unittest
from unittest.mock import patch

import server


SAMPLE_COURSE_LIST_HTML = """
<ul>
<li class="course clearfix" courseId="267157766" clazzId="154870267" personId="482522549">
  <a title="数据结构" href="https://mooc1-1.chaoxing.com/visit/stucoursemiddle">数据结构</a>
  <p>蒋莉</p>
  <p>开课时间：2026-09-17～2028-09-17</p>
</li>
<li class="course clearfix" courseId="264046870" clazzId="147551182" personId="482522549">
  <a title="人工智能导论" href="https://mooc1-1.chaoxing.com/visit/stucoursemiddle">人工智能导论</a>
  <p>郝鹏翼</p>
  <p>开课时间：2026-05-22～2028-05-22</p>
</li>
</ul>
"""

SAMPLE_WORK_LIST_DS_HTML = """
<ul>
<li data="https://mooc1.chaoxing.com/mooc-ans/mooc2/work/task?courseId=267157766&classId=154870267&cpi=482522549&workId=56227546&answerId=0&enc=5d0cea" aria-label="复习3-模板 ; 未交">
  <div class="time">剩余2613小时46分钟</div>
</li>
<li data="https://mooc1.chaoxing.com/mooc-ans/mooc2/work/task?courseId=267157766&classId=154870267&cpi=482522549&workId=56227524&answerId=0&enc=eed633" aria-label="复习2-C++函数及运算符重载.xls ; 未交">
  <div class="time">剩余2613小时46分钟</div>
</li>
</ul>
"""


class FakeResponse:
    def __init__(self, text="", url="https://i.chaoxing.com/base", json_data=None):
        self.text = text
        self.url = url
        self._json = json_data or {}

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, course_html=SAMPLE_COURSE_LIST_HTML, work_html_by_course=None):
        self.course_html = course_html
        self.work_html_by_course = work_html_by_course or {
            "267157766": SAMPLE_WORK_LIST_DS_HTML,
        }
        self.requested_urls = []
        self.cookies = {}

    def get(self, url, **kwargs):
        self.requested_urls.append(("GET", url))
        if "work/task" in url:
            raise AssertionError(f"Forbidden request to homework detail URL during scan: {url}")
        if "work/list" in url:
            for cid, html in self.work_html_by_course.items():
                if f"courseId={cid}" in url:
                    return FakeResponse(text=html, url=url)
            return FakeResponse(text="<ul></ul>", url=url)
        return FakeResponse(text="ok", url=url)

    def post(self, url, **kwargs):
        self.requested_urls.append(("POST", url))
        if "courselistdata" in url:
            return FakeResponse(text=self.course_html, url=url)
        return FakeResponse(text="ok", url=url, json_data={"status": True})


class TestChaoxingMCPSeam(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace_dir = self._tmp.name
        self._old_ws = os.environ.get("XT_WORKSPACE_DIR")
        os.environ["XT_WORKSPACE_DIR"] = self.workspace_dir
        self.fake_session = FakeSession()
        self._patcher = patch.object(server, "ensure_session", return_value=self.fake_session)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        if self._old_ws is None:
            os.environ.pop("XT_WORKSPACE_DIR", None)
        else:
            os.environ["XT_WORKSPACE_DIR"] = self._old_ws
        self._tmp.cleanup()

    def call_tool(self, name, arguments=None):
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        resp = server.handle(req)
        self.assertIn("result", resp, f"Expected result in response, got: {resp}")
        return resp["result"]["content"][0]["text"]

    def test_workspace_override_and_baseline_tools(self):
        # Seed enc for 267157766 into the overridden workspace
        seed_out = self.call_tool("xt_seed_enc", {
            "course_id": "267157766",
            "stuenc": "stu_test_1",
            "work_enc": "work_test_1",
        })
        self.assertIn("267157766", seed_out)

        # Verify workspace directory actually received the persisted index/enc file
        ws_files = os.listdir(self.workspace_dir)
        self.assertTrue(
            "courses_index.json" in ws_files or "enc_map.json" in ws_files,
            f"Expected courses_index.json or enc_map.json in {self.workspace_dir}, found {ws_files}",
        )

        # Verify xt_courses marks 267157766 with [enc]
        courses_out = self.call_tool("xt_courses")
        self.assertIn("数据结构", courses_out)
        self.assertIn("[enc]", courses_out)

        # Verify xt_homework queries work/list and parses homework items
        hw_out = self.call_tool("xt_homework", {"course": "数据结构"})
        self.assertIn("数据结构 — 2 homework:", hw_out)
        self.assertIn("[未交] 复习3-模板 (剩余2613小时46分钟)", hw_out)

        # Verify xt_homework_all scans cached courses and counts unsubmitted items
        hw_all_out = self.call_tool("xt_homework_all")
        self.assertIn("[未交] 数据结构: 复习3-模板", hw_all_out)
        self.assertIn("未交合计: 2", hw_all_out)

    def test_profile_semester_inference_and_courses_filtering(self):
        # Write profile.md with enrollment 2025-09 and current_semester: auto
        profile_path = os.path.join(self.workspace_dir, "profile.md")
        with open(profile_path, "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                'name: "测试学生"\n'
                'student_id: "20250001"\n'
                'enrollment: "2025-09"\n'
                'current_semester: "auto"\n'
                "---\n"
            )

        # Pre-populate courses_index.json with 1 archived course and 1 long_term course,
        # while remote list_courses also returns "267157766" (数据结构, new course not in index yet)
        index_path = os.path.join(self.workspace_dir, "courses_index.json")
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump({
                "264046870": {
                    "courseId": "264046870",
                    "clazzId": "147551182",
                    "cpi": "482522549",
                    "name": "人工智能导论",
                    "teacher": "郝鹏翼",
                    "semester": "2025-2026-2",
                    "status": "archived",
                    "stuenc": "4305",
                    "work_enc": "ed8c",
                },
                "257030642": {
                    "courseId": "257030642",
                    "clazzId": "131809592",
                    "cpi": "482522549",
                    "name": "形势与政策2025",
                    "teacher": "王骁炜",
                    "semester": "2025-2026-1",
                    "status": "long_term",
                    "stuenc": "194f",
                    "work_enc": "f807",
                },
            }, f, ensure_ascii=False)

        with patch.dict(os.environ, {"XT_CURRENT_DATE": "2026-09-28"}):
            out_default = self.call_tool("xt_courses")

        # Current semester should be inferred as 2026-2027-1
        self.assertIn("2026-2027-1", out_default)
        # Active (newly discovered 数据结构) and long_term (形势与政策2025) should be shown
        self.assertIn("数据结构", out_default)
        self.assertIn("形势与政策2025", out_default)
        # Archived course (人工智能导论) should be hidden by default, with archived count summarized
        self.assertNotIn("人工智能导论", out_default)
        self.assertIn("archived", out_default)

        # Newly discovered course 267157766 should be persisted in courses_index.json as active in 2026-2027-1
        with open(index_path, encoding="utf-8") as f:
            saved_idx = json.load(f)
        self.assertEqual(saved_idx["267157766"]["status"], "active")
        self.assertEqual(saved_idx["267157766"]["semester"], "2026-2027-1")

        # Calling xt_courses with include_archived=True should include 人工智能导论
        with patch.dict(os.environ, {"XT_CURRENT_DATE": "2026-09-28"}):
            out_all = self.call_tool("xt_courses", {"include_archived": True})
        self.assertIn("人工智能导论", out_all)

        # Also verify semester calculation for Jan (2027-01-15 -> 2026-2027-1) and Spring (2026-04-10 -> 2025-2026-2)
        with patch.dict(os.environ, {"XT_CURRENT_DATE": "2027-01-15"}):
            self.assertIn("2026-2027-1", self.call_tool("xt_courses"))
        with patch.dict(os.environ, {"XT_CURRENT_DATE": "2026-04-10"}):
            self.assertIn("2025-2026-2", self.call_tool("xt_courses"))

    def test_course_status_mutation_and_persistence(self):
        # First sync courses so 267157766 (数据结构) and 264046870 (人工智能导论) are in index
        with patch.dict(os.environ, {"XT_CURRENT_DATE": "2026-09-28"}):
            self.call_tool("xt_courses")

        # Reclassify 人工智能导论 by name substring to archived and semester 2025-2026-2
        out = self.call_tool("xt_course_status", {
            "course": "人工智能导论",
            "status": "archived",
            "semester": "2025-2026-2",
        })
        self.assertIn("人工智能导论", out)
        self.assertIn("archived", out)
        self.assertIn("2025-2026-2", out)

        # Verify persisted in courses_index.json
        index_path = os.path.join(self.workspace_dir, "courses_index.json")
        with open(index_path, encoding="utf-8") as f:
            saved_idx = json.load(f)
        self.assertEqual(saved_idx["264046870"]["status"], "archived")
        self.assertEqual(saved_idx["264046870"]["semester"], "2025-2026-2")

        # Verify tools/list exposes xt_course_status
        list_resp = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tool_names = [t["name"] for t in list_resp["result"]["tools"]]
        self.assertIn("xt_course_status", tool_names)

    def test_filtered_homework_scan_and_skeleton_materialization(self):
        # Setup workspace with active, long_term, and archived courses
        index_path = os.path.join(self.workspace_dir, "courses_index.json")
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump({
                "267157766": {
                    "courseId": "267157766",
                    "clazzId": "154870267",
                    "cpi": "482522549",
                    "name": "数据结构",
                    "teacher": "蒋莉",
                    "semester": "2026-2027-1",
                    "status": "active",
                    "stuenc": "9e29",
                    "work_enc": "36e6",
                },
                "264046870": {
                    "courseId": "264046870",
                    "clazzId": "147551182",
                    "cpi": "482522549",
                    "name": "人工智能导论",
                    "teacher": "郝鹏翼",
                    "semester": "2025-2026-2",
                    "status": "archived",
                    "stuenc": "4305",
                    "work_enc": "ed8c",
                },
            }, f, ensure_ascii=False)

        # Configure fake session work lists: archived course has unsubmitted homework,
        # active course has a homework with special characters in title
        self.fake_session.work_html_by_course["264046870"] = """
        <ul>
        <li data="https://mooc1.chaoxing.com/mooc-ans/mooc2/work/task?courseId=264046870&workId=54230728&answerId=0" aria-label="第三次汇报（第11到21组） ; 未交">
          <div class="time"></div>
        </li>
        </ul>
        """
        self.fake_session.work_html_by_course["267157766"] = """
        <ul>
        <li data="https://mooc1.chaoxing.com/mooc-ans/mooc2/work/task?courseId=267157766&workId=56227524&answerId=0" aria-label="复习2:C++/函数重载.xls ; 未交">
          <div class="time">剩余2613小时46分钟</div>
        </li>
        </ul>
        """

        with patch.dict(os.environ, {"XT_CURRENT_DATE": "2026-09-28"}):
            scan_out = self.call_tool("xt_homework_all")

        # 1. Default scan only checks active + long_term, skipping archived 人工智能导论
        self.assertIn("数据结构", scan_out)
        self.assertNotIn("人工智能导论", scan_out)
        self.assertNotIn("第三次汇报", scan_out)

        # 2. Verify zero requests to work/task detail URLs
        for _, url in self.fake_session.requested_urls:
            self.assertNotIn("work/task", url)
            self.assertNotIn("courseId=264046870", url)

        # 3. Verify Chinese directory skeleton and course_meta.md created for 数据结构
        course_dir = os.path.join(self.workspace_dir, "semesters", "2026-2027-1", "数据结构")
        self.assertTrue(os.path.isdir(os.path.join(course_dir, "通知")))
        self.assertTrue(os.path.isdir(os.path.join(course_dir, "资料")))
        self.assertTrue(os.path.isdir(os.path.join(course_dir, "作业")))
        meta_file = os.path.join(course_dir, "course_meta.md")
        self.assertTrue(os.path.isfile(meta_file))
        with open(meta_file, encoding="utf-8") as f:
            meta_txt = f.read()
        self.assertIn("蒋莉", meta_txt)

        # 4. Verify sanitized homework folder name and 信息/作业元信息.json + 成果/
        hw_dir = os.path.join(course_dir, "作业", "复习2-C++-函数重载.xls")
        self.assertTrue(os.path.isdir(os.path.join(hw_dir, "信息")))
        self.assertTrue(os.path.isdir(os.path.join(hw_dir, "成果")))
        hw_meta_file = os.path.join(hw_dir, "信息", "作业元信息.json")
        self.assertTrue(os.path.isfile(hw_meta_file))
        with open(hw_meta_file, encoding="utf-8") as f:
            hw_meta = json.load(f)
        self.assertEqual(hw_meta["title"], "复习2:C++/函数重载.xls")
        self.assertEqual(hw_meta["workId"], "56227524")
        self.assertEqual(hw_meta["answerId"], "0")
        self.assertEqual(hw_meta["status"], "未交")
        self.assertEqual(hw_meta["remaining"], "剩余2613小时46分钟")

        # 5. Modify course_meta.md and re-scan via xt_homework: existing course_meta.md must NOT be overwritten
        custom_meta = "---\nteacher: \"蒋莉\"\ngroup: \"第3组\"\n---\n用户自定义备注\n"
        with open(meta_file, "w", encoding="utf-8") as f:
            f.write(custom_meta)
        self.call_tool("xt_homework", {"course": "数据结构"})
        with open(meta_file, encoding="utf-8") as f:
            self.assertEqual(f.read(), custom_meta)

        # 6. Calling xt_homework_all with include_archived=True scans 人工智能导论 and creates its skeleton
        scan_all_out = self.call_tool("xt_homework_all", {"include_archived": True})
        self.assertIn("人工智能导论", scan_all_out)
        ai_meta_file = os.path.join(
            self.workspace_dir, "semesters", "2025-2026-2", "人工智能导论", "course_meta.md"
        )
        self.assertTrue(os.path.isfile(ai_meta_file))

    def test_dotenv_file_loading_via_login_and_workspace(self):
        env_path = os.path.join(self.workspace_dir, ".env")
        custom_ws = os.path.join(self.workspace_dir, "custom_ws")
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(
                "# Test .env file\n"
                "XT_PHONE=13800000000\n"
                'XT_PASSWORD="test_dummy_password"\n'
                "XT_WORKSPACE_DIR=./custom_ws\n"
            )

        with patch.dict(os.environ, {"XT_ENV_FILE": env_path}, clear=False):
            os.environ.pop("XT_WORKSPACE_DIR", None)
            os.environ.pop("XT_PHONE", None)
            os.environ.pop("XT_PASSWORD", None)
            self.assertEqual(server.get_workspace_dir(), os.path.abspath(custom_ws))
            with patch.object(server, "new_session", return_value=self.fake_session), \
                 patch.object(server, "save_cookies"):
                login_out = self.call_tool("xt_login")
                self.assertIn("login ok", login_out)


if __name__ == "__main__":
    unittest.main()

