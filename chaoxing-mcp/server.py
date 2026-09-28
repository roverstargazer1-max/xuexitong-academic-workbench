"""Chaoxing (Xuexitong) MCP server — direct HTTP with a persistent cookie session.

Tools:
  xt_login()                 AES login, cookie jar persisted to disk
  xt_courses()               list courses filtered by status (active/long_term by default)
  xt_course_status(...)      update a course's status (active/long_term/archived) or semester
  xt_homework(course)        homework list for one course + materialize workspace skeleton
  xt_homework_all()          scan active + long_term courses + materialize workspace skeleton
  xt_seed_enc(course_id, stuenc, work_enc)  add a course's enc pair (one-time,
                             captured from a browser session; values are stable)
  xt_page_text(url)          fetch any chaoxing page as cleaned text

Login (reverse-engineered from passport2 login.js, 2026-09):
  POST https://passport2.chaoxing.com/fanyalogin with AES-CBC(phone) and
  AES-CBC(password); key = iv = "u2oh6Vu^HWe4_AES", PKCS7, Base64 out.

The homework list endpoint requires per-course `stuenc` + `enc` query params.
These are stable per (course, clazz, student) and stored alongside course lifecycle
metadata in `workspace/courses_index.json` (with fallback read from `enc_map.json`).

Account source (first match wins):
  1. Process env vars `XT_PHONE` and `XT_PASSWORD`
  2. `.env` file (`XT_ENV_FILE`, `<project_root>/.env`, or `chaoxing-mcp/.env`)
  3. Legacy fallback: `XT_ACCOUNT_FILE` / `account.md` / `../账号信息.md`

Protocol: newline-delimited JSON-RPC 2.0 over stdio (MCP stdio transport).
"""
import base64
import json
import os
import re
import sys
import time

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
DEFAULT_WORKSPACE_DIR = os.path.join(PROJECT_ROOT, "workspace")
COOKIE_FILE = os.path.join(HERE, "cookies.json")
ENC_FILE = os.path.join(HERE, "enc_map.json")
KEY = b"u2oh6Vu^HWe4_AES"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")


def parse_dotenv_file(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            else:
                v = re.sub(r"\s+#.*$", "", v).strip()
            if k:
                out[k] = v
    return out


def load_dotenv_dict(env_file: str | None = None) -> tuple[dict[str, str], str | None]:
    explicit = env_file or os.environ.get("XT_ENV_FILE")
    if explicit:
        p = os.path.abspath(explicit)
        return parse_dotenv_file(p), (p if os.path.exists(p) else None)
    for candidate in (os.path.join(PROJECT_ROOT, ".env"), os.path.join(HERE, ".env")):
        if os.path.exists(candidate):
            return parse_dotenv_file(candidate), candidate
    return {}, None


def get_workspace_dir(workspace_dir: str | None = None) -> str:
    ws = workspace_dir or os.environ.get("XT_WORKSPACE_DIR")
    if ws:
        return os.path.abspath(ws)
    dotenv, dotenv_path = load_dotenv_dict()
    dotenv_ws = dotenv.get("XT_WORKSPACE_DIR")
    if dotenv_ws:
        if os.path.isabs(dotenv_ws):
            return os.path.abspath(dotenv_ws)
        base = os.path.dirname(dotenv_path) if dotenv_path else PROJECT_ROOT
        return os.path.abspath(os.path.join(base, dotenv_ws))
    return os.path.abspath(DEFAULT_WORKSPACE_DIR)


def get_index_file(workspace_dir: str | None = None) -> str:
    return os.path.join(get_workspace_dir(workspace_dir), "courses_index.json")


def aes_enc(text: str) -> str:
    cipher = AES.new(KEY, AES.MODE_CBC, KEY)
    return base64.b64encode(cipher.encrypt(pad(text.encode(), 16))).decode()


def load_account():
    """Return (phone, password). Process env -> .env file -> legacy markdown fallback."""
    phone, pwd = os.environ.get("XT_PHONE"), os.environ.get("XT_PASSWORD")
    if phone and pwd:
        return phone, pwd
    dotenv, _ = load_dotenv_dict()
    if dotenv.get("XT_PHONE") and dotenv.get("XT_PASSWORD"):
        return dotenv["XT_PHONE"], dotenv["XT_PASSWORD"]
    for candidate in (
        os.environ.get("XT_ACCOUNT_FILE"),
        os.path.join(HERE, "account.md"),
        os.path.join(PROJECT_ROOT, "账号信息.md"),
    ):
        if candidate and os.path.exists(os.path.normpath(candidate)):
            txt = open(os.path.normpath(candidate), encoding="utf-8").read()
            m_phone = re.search(r"账号（手机号）\s*\|\s*(\d+)", txt)
            m_pwd = re.search(r"密码\s*\|\s*(\S+)", txt)
            if m_phone and m_pwd:
                return m_phone.group(1), m_pwd.group(1)
    raise RuntimeError(
        "no account: configure XT_PHONE and XT_PASSWORD in .env (see .env.example) "
        "or environment variables")


_session = None


def new_session():
    s = requests.Session()
    s.trust_env = False  # chaoxing is domestic; a dead local proxy must not interfere
    s.headers.update({"User-Agent": UA})
    if os.path.exists(COOKIE_FILE):
        try:
            jar = json.load(open(COOKIE_FILE, encoding="utf-8"))
            for dom, cookies in jar.items():
                for k, v in cookies.items():
                    s.cookies.set(k, v, domain=dom)
        except Exception:
            pass
    return s


def save_cookies(s):
    jar = {}
    for c in s.cookies:
        jar.setdefault(c.domain, {})[c.name] = c.value
    json.dump(jar, open(COOKIE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def robust(s, method, url, tries=4, **kw):
    """mooc1-1.chaoxing.com often RSTs the first connection; retry wins."""
    last = None
    for i in range(tries):
        try:
            return getattr(s, method)(url, timeout=30, **kw)
        except Exception as e:
            last = e
            time.sleep(1.2 * (i + 1))
    raise last


def logged_in(s) -> bool:
    try:
        r = robust(s, "get", "https://i.chaoxing.com/base")
        return "passport2.chaoxing.com" not in str(r.url)
    except Exception:
        return False


def ensure_session():
    global _session
    if _session is not None and logged_in(_session):
        return _session
    if _session is None:
        _session = new_session()
    if not logged_in(_session):
        phone, pwd = load_account()
        _session.get("https://passport2.chaoxing.com/login", timeout=20)
        r = _session.post("https://passport2.chaoxing.com/fanyalogin", data={
            "fid": "-1", "uname": aes_enc(phone), "password": aes_enc(pwd),
            "refer": "http%3A%2F%2Fi.mooc.chaoxing.com",
            "t": "true", "forbidotherlogin": "0", "validate": "",
            "doubleFactorLogin": "0", "independentId": "0", "independentNameId": "0",
        }, timeout=20)
        if r.json().get("status") is not True:
            raise RuntimeError("login failed: " + r.text[:150])
        save_cookies(_session)
    return _session


def get_today_str() -> str:
    env_date = os.environ.get("XT_CURRENT_DATE", "").strip()
    if env_date and re.match(r"^\d{4}-\d{2}(-\d{2})?$", env_date):
        return env_date if len(env_date) == 10 else env_date + "-01"
    return time.strftime("%Y-%m-%d")


def load_profile(workspace_dir: str | None = None) -> dict:
    ws = get_workspace_dir(workspace_dir)
    pfile = os.path.join(ws, "profile.md")
    profile = {
        "name": "待填写姓名",
        "student_id": "待填写学号",
        "college": "计算机科学与技术学院（软件学院）",
        "major_class": "软件工程（中外合作）2025班",
        "enrollment": "2025-09",
        "current_semester": "auto",
        "attachment_naming_default": "{student_id}_{name}_{homework_title}",
    }
    if not os.path.exists(pfile):
        return profile
    with open(pfile, encoding="utf-8") as f:
        txt = f.read()
    m = re.match(r"^---\s*\n(.*?)\n---", txt, re.S)
    front = m.group(1) if m else txt
    for line in front.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        v = re.sub(r"#.*$", "", v).strip().strip('"').strip("'")
        profile[k.strip()] = v
    return profile


def resolve_current_semester(workspace_dir: str | None = None) -> str:
    prof = load_profile(workspace_dir)
    sem = prof.get("current_semester", "auto").strip()
    if sem and sem.lower() != "auto":
        return sem
    today = get_today_str()
    parts = today.split("-")
    year, month = int(parts[0]), int(parts[1])
    if month >= 9:
        return f"{year}-{year + 1}-1"
    if month == 1:
        return f"{year - 1}-{year}-1"
    return f"{year - 1}-{year}-2"


def load_enc_map(workspace_dir: str | None = None):
    ws = get_workspace_dir(workspace_dir)
    idx_file = os.path.join(ws, "courses_index.json")
    if os.path.exists(idx_file):
        with open(idx_file, encoding="utf-8") as f:
            return json.load(f)
    ws_enc_file = os.path.join(ws, "enc_map.json")
    if os.path.exists(ws_enc_file):
        with open(ws_enc_file, encoding="utf-8") as f:
            return json.load(f)
    if not workspace_dir and not os.environ.get("XT_WORKSPACE_DIR") and os.path.exists(ENC_FILE):
        with open(ENC_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_enc_map(data: dict, workspace_dir: str | None = None):
    ws = get_workspace_dir(workspace_dir)
    os.makedirs(ws, exist_ok=True)
    idx_file = os.path.join(ws, "courses_index.json")
    with open(idx_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def has_valid_enc(entry: dict | None) -> bool:
    return bool(entry and entry.get("stuenc") and entry.get("work_enc"))


def sync_courses_index(remote_courses: list[dict], workspace_dir: str | None = None):
    idx = load_enc_map(workspace_dir)
    cur_sem = resolve_current_semester(workspace_dir)
    today = get_today_str()
    changed = False
    for c in remote_courses:
        cid = str(c["courseId"])
        if cid not in idx:
            idx[cid] = {
                "courseId": cid,
                "clazzId": str(c.get("clazzId", "")),
                "cpi": str(c.get("cpi", "")),
                "name": c.get("name", "?"),
                "teacher": c.get("teacher", ""),
                "semester": cur_sem,
                "status": "active",
                "stuenc": "",
                "work_enc": "",
                "updated": today,
            }
            changed = True
        else:
            entry = idx[cid]
            for k in ("courseId", "clazzId", "cpi", "name"):
                if c.get(k) and (not entry.get(k) or entry.get(k) == "?"):
                    entry[k] = c[k]
                    changed = True
            if "semester" not in entry or not entry["semester"]:
                entry["semester"] = cur_sem
                changed = True
            if "status" not in entry or not entry["status"]:
                entry["status"] = "active"
                changed = True
            if "stuenc" not in entry:
                entry["stuenc"] = ""
                changed = True
            if "work_enc" not in entry:
                entry["work_enc"] = ""
                changed = True
    if changed:
        save_enc_map(idx, workspace_dir)
    return idx, cur_sem


def list_courses(s):
    r = robust(s, "post", "https://mooc1-1.chaoxing.com/visit/courselistdata",
               data={"courseType": "1", "courseFolderId": "0",
                     "courseFolderSize": "-1", "digest": "_all"})
    out = []
    for chunk in r.text.split('<li class="course ')[1:]:
        cid = re.search(r'courseId="(\d+)"', chunk)
        clz = re.search(r'clazzId="(\d+)"', chunk)
        pid = re.search(r'personId="(\d+)"', chunk)
        name = re.search(r'title="([^"]{2,60})"', chunk)
        if cid and clz:
            out.append({"courseId": cid.group(1), "clazzId": clz.group(1),
                        "cpi": pid.group(1) if pid else "",
                        "name": (name.group(1) if name else "?").strip()})
    return out


def parse_work_list(html):
    items = []
    for m in re.finditer(r'<li[^>]*data="([^"]*work/task[^"]*)"[^>]*aria-label="([^"]*)"(.*?)</li>',
                         html, re.S):
        url, label, body = m.group(1), m.group(2), m.group(3)
        title, _, status = label.partition(";")
        title = title.strip()
        status = status.strip() or "?"
        rem = re.search(r"(剩余[\d小时天分钟]+)", re.sub(r"<[^>]+>", "", body))
        wid = re.search(r"workId=(\d+)", url)
        aid = re.search(r"answerId=(\d+)", url)
        items.append({
            "title": title,
            "status": status,
            "remaining": rem.group(1) if rem else "",
            "workId": wid.group(1) if wid else "",
            "answerId": aid.group(1) if aid else "",
            "detail_url": url,
        })
    return items


def tool_login(_args):
    global _session
    _session = new_session()
    phone, pwd = load_account()
    _session.get("https://passport2.chaoxing.com/login", timeout=20)
    r = _session.post("https://passport2.chaoxing.com/fanyalogin", data={
        "fid": "-1", "uname": aes_enc(phone), "password": aes_enc(pwd),
        "refer": "http%3A%2F%2Fi.mooc.chaoxing.com",
        "t": "true", "forbidotherlogin": "0", "validate": "",
        "doubleFactorLogin": "0", "independentId": "0", "independentNameId": "0",
    }, timeout=20)
    if r.json().get("status") is True:
        save_cookies(_session)
        return [{"type": "text", "text": "login ok, cookies saved (%d)" % len(_session.cookies)}]
    return [{"type": "text", "text": "login FAILED: " + r.text[:150]}]


def tool_courses(args):
    s = ensure_session()
    include_archived = bool((args or {}).get("include_archived", False))
    remote_courses = list_courses(s)
    idx, cur_sem = sync_courses_index(remote_courses)
    visible = []
    archived_count = 0
    missing_enc = []
    for cid, entry in idx.items():
        st = entry.get("status", "active")
        if st == "archived":
            archived_count += 1
            if not include_archived:
                continue
        visible.append(entry)
        if st in ("active", "long_term") and not has_valid_enc(entry):
            missing_enc.append(entry.get("name", cid))

    lines = [f"当前学期: {cur_sem} | 展示 {len(visible)} 门课程:"]
    for c in visible:
        mark = " [enc]" if has_valid_enc(c) else " [no-enc]"
        st = c.get("status", "active")
        sem = c.get("semester", cur_sem)
        lines.append(
            f"  [{st}] [{sem}] {c.get('name', '?')}  "
            f"(courseId={c.get('courseId', '')}, classId={c.get('clazzId', '')}){mark}"
        )
    if not include_archived and archived_count > 0:
        lines.append(f"（已折叠 {archived_count} 门 archived 归档课程，传 include_archived=true 查看全部）")
    if missing_enc:
        lines.append(f"提示：发现缺少 enc 的活跃课程（{', '.join(missing_enc)}），请在浏览器打开作业页提取 stuenc/enc 后调用 xt_seed_enc。")
    return [{"type": "text", "text": "\n".join(lines)}]


def sanitize_dir_name(name: str) -> str:
    cleaned = re.sub(r'[/\\:*?"<>|;]+', "-", str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "unnamed"


def materialize_course_skeleton(entry: dict, items: list[dict], workspace_dir: str | None = None) -> str:
    ws = get_workspace_dir(workspace_dir)
    cur_sem = resolve_current_semester(workspace_dir)
    sem = entry.get("semester") or cur_sem
    safe_course = sanitize_dir_name(entry.get("name") or entry.get("courseId") or "未知课程")
    course_dir = os.path.join(ws, "semesters", sem, safe_course)
    for sub in ("通知", "资料", "作业"):
        os.makedirs(os.path.join(course_dir, sub), exist_ok=True)

    meta_path = os.path.join(course_dir, "course_meta.md")
    if not os.path.exists(meta_path):
        teacher = entry.get("teacher", "")
        default_group = "第1~10组" if "人工智能导论" in safe_course else "无"
        meta_content = (
            "---\n"
            f'course_id: "{entry.get("courseId", "")}"\n'
            f'name: "{entry.get("name", safe_course)}"\n'
            f'teacher: "{teacher}"\n'
            f'semester: "{sem}"\n'
            f'group: "{default_group}"\n'
            'naming_rule: "{student_id}_{name}_{homework_title}"\n'
            "---\n\n"
            "## 课程特殊要求与备注\n"
            "- 分组说明：若本课程含按组布置的作业，请核对上方 `group` 字段，非本组作业无需提交。\n"
            "- 提交规范：默认遵循 `naming_rule` 命名附件。\n"
        )
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(meta_content)

    today = get_today_str()
    for it in items:
        safe_hw = sanitize_dir_name(it.get("title", "未命名作业"))
        hw_dir = os.path.join(course_dir, "作业", safe_hw)
        info_dir = os.path.join(hw_dir, "信息")
        out_dir = os.path.join(hw_dir, "成果")
        os.makedirs(info_dir, exist_ok=True)
        os.makedirs(out_dir, exist_ok=True)
        hw_meta_path = os.path.join(info_dir, "作业元信息.json")
        hw_meta = {
            "title": it.get("title", ""),
            "workId": it.get("workId", ""),
            "answerId": it.get("answerId", ""),
            "status": it.get("status", ""),
            "remaining": it.get("remaining", ""),
            "detail_url": it.get("detail_url", ""),
            "updated_at": today,
            "last_seen": today,
        }
        with open(hw_meta_path, "w", encoding="utf-8") as f:
            json.dump(hw_meta, f, ensure_ascii=False, indent=2)
    return course_dir


def tool_homework(args):
    s = ensure_session()
    query = str(args.get("course", "")).strip()
    courses = list_courses(s)
    idx, _ = sync_courses_index(courses)
    target = None
    for c in courses:
        if query and (query == c["courseId"] or query in c["name"]):
            target = c
            break
    if target is None:
        for cid, entry in idx.items():
            if query and (query == cid or query in entry.get("name", "")):
                target = entry
                break
    if target is None:
        return [{"type": "text", "text": f"course '{query}' not found. Use xt_courses to list."}]
    cid = str(target["courseId"])
    e = idx.get(cid)
    if not has_valid_enc(e):
        return [{"type": "text", "text":
                 f"no cached enc for courseId {cid} ({target.get('name', '?')}). "
                 "One-time setup: open the course 作业 tab in a browser, copy the "
                 "work/list URL's stuenc & enc params, then call xt_seed_enc."}]
    clazz_id = target.get("clazzId") or e.get("clazzId", "")
    cpi = target.get("cpi") or e.get("cpi", "")
    r = robust(s, "get",
               "https://mooc1.chaoxing.com/mooc2/work/list"
               f"?courseId={cid}&classId={clazz_id}&cpi={cpi}&ut=s"
               f"&stuenc={e['stuenc']}&enc={e['work_enc']}")
    items = parse_work_list(r.text)
    materialize_course_skeleton(e, items)
    lines = [f"{e.get('name', target.get('name', '?'))} — {len(items)} homework:"]
    for it in items:
        rem = f" ({it['remaining']})" if it["remaining"] else ""
        lines.append(f"  [{it['status']}] {it['title']}{rem}")
        lines.append(f"      {it['detail_url']}")
    return [{"type": "text", "text": "\n".join(lines) or "no homework entries"}]


def tool_homework_all(args):
    s = ensure_session()
    include_archived = bool((args or {}).get("include_archived", False))
    sem_filter = str((args or {}).get("semester", "")).strip()
    courses = list_courses(s)
    idx, cur_sem = sync_courses_index(courses)
    scope_desc = "全部课程" if include_archived else "active + long_term 活跃课程"
    if sem_filter:
        scope_desc += f" (semester={sem_filter})"
    lines = [f"课程作业扫描（范围: {scope_desc}，当前学期: {cur_sem}）:"]
    found = 0
    for cid, e in idx.items():
        st = e.get("status", "active")
        if not include_archived and st not in ("active", "long_term"):
            continue
        if sem_filter and e.get("semester") != sem_filter:
            continue
        if not has_valid_enc(e):
            continue
        try:
            r = robust(s, "get",
                       "https://mooc1.chaoxing.com/mooc2/work/list"
                       f"?courseId={e.get('courseId', cid)}&classId={e.get('clazzId', '')}&cpi={e.get('cpi', '')}&ut=s"
                       f"&stuenc={e['stuenc']}&enc={e['work_enc']}")
            items = parse_work_list(r.text)
            materialize_course_skeleton(e, items)
            for it in items:
                rem = f" ({it['remaining']})" if it.get("remaining") else ""
                if it["status"] == "未交":
                    lines.append(f"  [未交] {e.get('name', cid)}: {it['title']}{rem}")
                    found += 1
                else:
                    lines.append(f"  [{it['status']}] {e.get('name', cid)}: {it['title']}{rem}")
        except Exception as ex:
            lines.append(f"  [error] {e.get('name', cid)}: {str(ex)[:60]}")
    lines.append(f"—— 未交合计: {found}")
    return [{"type": "text", "text": "\n".join(lines)}]


def tool_seed_enc(args):
    enc = load_enc_map()
    cid = str(args.get("course_id", ""))
    entry = enc.get(cid, {})
    entry.setdefault("courseId", cid)
    entry.setdefault("semester", resolve_current_semester())
    entry.setdefault("status", "active")
    entry["stuenc"] = args.get("stuenc", "")
    entry["work_enc"] = args.get("work_enc", "")
    entry["updated"] = get_today_str()
    enc[cid] = entry
    save_enc_map(enc)
    return [{"type": "text", "text": f"enc cached for courseId {cid}"}]


def tool_course_status(args):
    query = str((args or {}).get("course", "")).strip()
    new_status = (args or {}).get("status")
    new_semester = (args or {}).get("semester")
    if not query:
        return [{"type": "text", "text": "course argument is required"}]
    if new_status and new_status not in ("active", "long_term", "archived"):
        return [{"type": "text", "text": f"invalid status '{new_status}': must be active, long_term, or archived"}]

    idx = load_enc_map()
    target_cid = None
    for cid, entry in idx.items():
        if query == cid or query in entry.get("name", ""):
            target_cid = cid
            break
    if target_cid is None:
        s = ensure_session()
        idx, _ = sync_courses_index(list_courses(s))
        for cid, entry in idx.items():
            if query == cid or query in entry.get("name", ""):
                target_cid = cid
                break
    if target_cid is None:
        return [{"type": "text", "text": f"course '{query}' not found in index or remote list"}]

    entry = idx[target_cid]
    if new_status:
        entry["status"] = new_status
    if new_semester:
        entry["semester"] = new_semester
    entry["updated"] = get_today_str()
    save_enc_map(idx)
    return [{"type": "text", "text": (
        f"updated {entry.get('name', target_cid)} (courseId={target_cid}): "
        f"status={entry.get('status')}, semester={entry.get('semester')}"
    )}]


def tool_page_text(args):
    s = ensure_session()
    url = args.get("url", "")
    r = robust(s, "get", url)
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"[ \t\r]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n", txt)
    return [{"type": "text", "text": txt.strip()[:6000]}]


TOOLS = {
    "xt_login": {
        "description": "Login to Chaoxing (Xuexitong), persist cookies to disk. "
                       "Credentials come from XT_PHONE/XT_PASSWORD env vars or an "
                       "account markdown file. Other xt_* tools auto-login when the "
                       "session is dead, so calling this explicitly is rarely needed.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "xt_courses": {
        "description": "List enrolled courses filtered by status (default: active + long_term, "
                       "summarizing archived count; pass include_archived=true for all). "
                       "Shows semester, status, and whether enc is configured.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_archived": {"type": "boolean"},
            },
        },
    },
    "xt_course_status": {
        "description": "Update a course's lifecycle status (active / long_term / archived) "
                       "or semester in workspace/courses_index.json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {"type": "string", "description": "Course ID or course name substring"},
                "status": {"type": "string", "enum": ["active", "long_term", "archived"]},
                "semester": {"type": "string", "description": "e.g. 2026-2027-1"},
            },
            "required": ["course"],
        },
    },
    "xt_homework": {
        "description": "List homework entries (title/status/remaining time/detail URL) "
                       "for one course and materialize local workspace skeleton. "
                       "Arg: course = course name substring or courseId.",
        "inputSchema": {"type": "object",
                        "properties": {"course": {"type": "string"}},
                        "required": ["course"]},
    },
    "xt_homework_all": {
        "description": "Scan homework across active and long_term courses (or all if "
                       "include_archived=true), materialize local workspace skeleton, "
                       "and highlight 未交 (unsubmitted) items.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_archived": {"type": "boolean"},
                "semester": {"type": "string"},
            },
        },
    },
    "xt_seed_enc": {
        "description": "Cache a course's stuenc/work_enc pair (stable per course+student). "
                       "Get the values once from a browser: open the course 作业 tab, copy "
                       "them from the work/list URL query string.",
        "inputSchema": {"type": "object", "properties": {
            "course_id": {"type": "string"}, "stuenc": {"type": "string"},
            "work_enc": {"type": "string"}},
            "required": ["course_id", "stuenc", "work_enc"]},
    },
    "xt_page_text": {
        "description": "Fetch any chaoxing URL with the logged-in session and return "
                       "cleaned page text (for reading homework requirements etc.).",
        "inputSchema": {"type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"]},
    },
}


def handle(req):
    rid = req.get("id")
    method = req.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "chaoxing-mcp", "version": "1.0.0"}}}
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [
            {"name": n, "description": d["description"], "inputSchema": d["inputSchema"]}
            for n, d in TOOLS.items()]}}
    if method == "tools/call":
        name = req["params"]["name"]
        args = req["params"].get("arguments", {})
        try:
            if name == "xt_login":
                result = {"content": tool_login(args)}
            elif name == "xt_courses":
                result = {"content": tool_courses(args)}
            elif name == "xt_course_status":
                result = {"content": tool_course_status(args)}
            elif name == "xt_homework":
                result = {"content": tool_homework(args)}
            elif name == "xt_homework_all":
                result = {"content": tool_homework_all(args)}
            elif name == "xt_seed_enc":
                result = {"content": tool_seed_enc(args)}
            elif name == "xt_page_text":
                result = {"content": tool_page_text(args)}
            else:
                return {"jsonrpc": "2.0", "id": rid, "error": {
                    "code": -32601, "message": "unknown tool %s" % name}}
        except Exception as e:
            result = {"content": [{"type": "text",
                                   "text": "[ERROR] %s: %s" % (type(e).__name__, e)}]}
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid, "error": {
            "code": -32601, "message": "method not found: %s" % method}}
    return None


def main():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            resp = handle(json.loads(raw))
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": None, "error": {
                "code": -32700, "message": "%s: %s" % (type(e).__name__, e)}}
        if resp:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
