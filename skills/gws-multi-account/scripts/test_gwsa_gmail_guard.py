"""Offline tests for gwsa-gmail-guard.py. A fake `gws` on PATH stands in for Gmail.

Run: python3 -m unittest discover -s skills/gws-multi-account/scripts -p 'test_*.py'
"""
import base64
import contextlib
import email
import io
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "gwsa-gmail-guard.py")
spec = importlib.util.spec_from_file_location("gwsa_gmail_guard", GUARD)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

# Fake gws: helper drafts are built the way gws 0.22.5 builds them (attachments without ids, here
# one with no Content-ID and one with an empty one) plus an inline image that already has an id.
FAKE_GWS = textwrap.dedent('''\
    #!%s
    import base64, json, os, sys
    from email.mime.application import MIMEApplication
    from email.mime.image import MIMEImage
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    store = os.environ["FAKE_GWS_STORE"]
    path = os.path.join(store, "drafts.json")
    drafts = json.load(open(path)) if os.path.exists(path) else {}
    def save():
        json.dump(drafts, open(path, "w"))
    def val(flag):
        return argv[argv.index(flag) + 1] if flag in argv else None
    argv = sys.argv[1:]
    sys.stderr.write("Using keyring backend: file\\n")
    if argv[:1] == ["gmail"] and argv[1] in ("+send", "+reply", "+reply-all", "+forward"):
        if "--draft" not in argv or "-a" not in argv:
            print(json.dumps({"id": "r-noatt", "message": {"id": "m-noatt", "threadId": "t1"}, "argv": argv}))
            sys.exit(0)
        msg = MIMEMultipart("mixed")
        msg["To"] = "someone@example.com"
        msg["Subject"] = "Re: test"
        msg["In-Reply-To"] = "<orig@example.com>"
        msg.attach(MIMEText(val("--body"), "html"))
        img = MIMEImage(%r, "png")
        img["Content-ID"] = "<img1>"
        img.add_header("Content-Disposition", "inline", filename="logo.png")
        msg.attach(img)
        files = [argv[i + 1] for i, a in enumerate(argv) if a == "-a"]
        for n, f in enumerate(files):
            part = MIMEApplication(open(f, "rb").read())
            part.add_header("Content-Disposition", "attachment", filename=f)
            if n == 1:
                part["Content-ID"] = "<>"
            msg.attach(part)
        drafts["r1"] = {"mid": "m1", "thread": "t1", "raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
        save()
        fmt = val("--format") or next((a.split("=", 1)[1] for a in argv if a.startswith("--format=")), "json")
        if fmt != "json":
            print("id  r1")
            sys.exit(0)
        print(json.dumps({"id": "r1", "message": {"id": "m1", "labelIds": ["DRAFT"], "threadId": "t1"}}))
    elif argv[:4] == ["gmail", "users", "messages", "get"]:
        mid = json.loads(val("--params"))["id"]
        raw = next(d["raw"] for d in drafts.values() if d["mid"] == mid)
        print(json.dumps({"id": mid, "raw": raw}))
    elif argv[:4] == ["gmail", "users", "drafts", "create"]:
        thread = json.loads(val("--json"))["message"]["threadId"]
        if os.environ.get("FAKE_GWS_WRONG_THREAD") == "1":
            thread = "t-other"
        raw = open(val("--upload"), "rb").read().replace(b"\\r\\n", b"\\n")
        if os.environ.get("FAKE_GWS_DROP_XAID") == "1":
            raw = b"\\n".join(l for l in raw.split(b"\\n") if not l.startswith(b"X-Attachment-Id:"))
        drafts["r2"] = {"mid": "m2", "thread": thread, "raw": base64.urlsafe_b64encode(raw).decode()}
        save()
        print(json.dumps({"id": "r2", "message": {"id": "m2", "labelIds": ["DRAFT"], "threadId": thread}}))
    elif argv[:4] == ["gmail", "users", "drafts", "delete"]:
        did = json.loads(val("--params"))["id"]
        if did == os.environ.get("FAKE_GWS_FAIL_DELETE"):
            sys.exit(1)
        drafts.pop(did, None)
        save()
    else:
        print("{}")
    ''') % (sys.executable, PNG)


def attachment_parts(msg):
    return [p for p in msg.walk() if not p.is_multipart() and p.get_content_disposition() == "attachment"]


class Pure(unittest.TestCase):
    def setUp(self):
        quiet = contextlib.redirect_stderr(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def test_to_html(self):
        out = guard.to_html("Hi <b>\n\nSee https://example.com/a.")
        self.assertIn("<div>Hi &lt;b&gt;</div>", out)
        self.assertIn("<div><br></div>", out)
        self.assertIn('<a href="https://example.com/a">https://example.com/a</a>.', out)

    def test_plain_draft_becomes_html(self):
        out = guard.guard_helper(["gmail", "+send", "--draft", "--body", "line 1\nline 2"])
        self.assertIn("--html", out)
        self.assertEqual(out[out.index("--body") + 1], "<div>line 1</div>\n<div>line 2</div>")

    def test_html_body_without_flag_is_kept_as_html(self):
        out = guard.guard_helper(["gmail", "+send", "--draft", "--body", "<p>Hello</p>"])
        self.assertEqual(out[out.index("--body") + 1], "<p>Hello</p>")
        self.assertIn("--html", out)

    def test_html_body_and_direct_send_untouched(self):
        argv = ["gmail", "+send", "--draft", "--html", "--body", "<p>a</p>\n<p>b</p>"]
        self.assertEqual(guard.guard_helper(argv), argv)
        argv = ["gmail", "+send", "--body", "a\nb"]
        self.assertEqual(guard.guard_helper(argv), argv)

    def test_assign_ids(self):
        msg = MIMEMultipart("mixed")
        msg.attach(MIMEText("<p>x</p>", "html"))
        img = MIMEImage(PNG, "png")
        img["Content-ID"] = "<img1>"
        img.add_header("Content-Disposition", "inline", filename="logo.png")
        msg.attach(img)
        for n, name in enumerate(["a.pdf", "b.docx"]):
            part = MIMEApplication(b"data %d" % n)
            part.add_header("Content-Disposition", "attachment", filename=name)
            if n == 1:
                part["Content-ID"] = "<>"
            msg.attach(part)
        self.assertEqual(guard.assign_attachment_ids(msg), 2)
        ids = [p["Content-ID"].strip("<>") for p in attachment_parts(msg)]
        self.assertEqual(len(set(ids)), 2)
        for p in attachment_parts(msg):
            self.assertRegex(p["X-Attachment-Id"], r"^f_[0-9a-f]{10}$")
            self.assertEqual(p["Content-ID"], "<%s>" % p["X-Attachment-Id"])
            self.assertEqual(p.get_param("name"), p.get_filename())
        self.assertEqual(img["Content-ID"], "<img1>")
        self.assertEqual(guard.assign_attachment_ids(msg), 0)  # idempotent

    def test_comparable_ignores_line_endings_in_text_only(self):
        self.assertEqual(guard.comparable(MIMEText("a\r\nb", "html")), guard.comparable(MIMEText("a\nb", "html")))
        a = MIMEApplication(b"a\r\nb")
        a.add_header("Content-Disposition", "attachment", filename="f.bin")
        b = MIMEApplication(b"a\nb")
        b.add_header("Content-Disposition", "attachment", filename="f.bin")
        self.assertNotEqual(guard.comparable(a), guard.comparable(b))


class WithFakeGws(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.bin, self.store, self.work = (os.path.join(root, d) for d in ("bin", "store", "work"))
        for d in (self.bin, self.store, self.work):
            os.mkdir(d)
        with open(os.path.join(self.bin, "gws"), "w") as f:
            f.write(FAKE_GWS)
        os.chmod(os.path.join(self.bin, "gws"), 0o755)
        self.files = {"a.pdf": b"%PDF-1.4 first", "r\u00e9sum\u00e9 \u00e9t\u00e9.docx": b"PK second"}
        for name, data in self.files.items():
            with open(os.path.join(self.work, name), "wb") as f:
                f.write(data)

    def tearDown(self):
        self.tmp.cleanup()

    def run_guard(self, args, **env):
        e = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"], FAKE_GWS_STORE=self.store, **env)
        e.pop("GWSA_ALLOW_PLAIN", None)
        e.pop("GWSA_KEEP_ATTACHMENT_IDS", None)
        e.update(env)
        r = subprocess.run([sys.executable, GUARD] + args, cwd=self.work, env=e, capture_output=True, text=True)
        out = r.stdout[r.stdout.find("{"):]
        return r, (json.JSONDecoder().raw_decode(out)[0] if out else {})

    def drafts(self):
        with open(os.path.join(self.store, "drafts.json")) as f:
            return json.load(f)

    def reply_args(self):
        args = ["gmail", "+reply", "--message-id", "m0", "--draft", "--html", "--body", "<p>x</p>"]
        for name in self.files:
            args += ["-a", name]
        return args

    def test_draft_with_attachments_is_replaced_with_unique_ids(self):
        r, out = self.run_guard(self.reply_args())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out["id"], "r2")
        self.assertIn("given unique Content-IDs", r.stderr)
        self.assertEqual(list(self.drafts()), ["r2"])
        msg = email.message_from_bytes(base64.urlsafe_b64decode(self.drafts()["r2"]["raw"]))
        atts = attachment_parts(msg)
        self.assertEqual([p.get_filename() for p in atts], list(self.files))
        self.assertEqual([p.get_payload(decode=True) for p in atts], list(self.files.values()))
        ids = [p["Content-ID"] for p in atts]
        self.assertTrue(all(i and i != "<>" for i in ids) and len(set(ids)) == 2)
        self.assertEqual(msg["In-Reply-To"], "<orig@example.com>")
        inline = [p for p in msg.walk() if p.get_content_disposition() == "inline"]
        self.assertEqual(inline[0]["Content-ID"], "<img1>")

    def test_failed_check_keeps_first_draft(self):
        r, out = self.run_guard(self.reply_args(), FAKE_GWS_WRONG_THREAD="1")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(out["id"], "r1")
        self.assertIn("attachment-id fix failed", r.stderr)
        self.assertEqual(list(self.drafts()), ["r1"])

    def test_readback_without_attachment_id_keeps_first_draft(self):
        r, out = self.run_guard(self.reply_args(), FAKE_GWS_DROP_XAID="1")
        self.assertEqual(out["id"], "r1")
        self.assertIn("X-Attachment-Id missing", r.stderr)
        self.assertEqual(list(self.drafts()), ["r1"])

    def test_failed_delete_of_first_draft_is_reported(self):
        r, out = self.run_guard(self.reply_args(), FAKE_GWS_FAIL_DELETE="r1")
        self.assertEqual(out["id"], "r2")
        self.assertIn("first draft r1 could not be deleted", r.stderr)
        self.assertEqual(sorted(self.drafts()), ["r1", "r2"])

    def test_failed_rollback_is_reported(self):
        r, out = self.run_guard(self.reply_args(), FAKE_GWS_WRONG_THREAD="1", FAKE_GWS_FAIL_DELETE="r2")
        self.assertEqual(out["id"], "r1")
        self.assertIn("new draft r2 could not be deleted", r.stderr)

    def test_other_output_format_runs_as_json(self):
        for fmt in (["--format", "table"], ["--format=table"]):
            r, out = self.run_guard(self.reply_args() + fmt)
            self.assertEqual(out["id"], "r2", r.stderr)
            self.assertIn("output shown as JSON", r.stderr)

    def test_opt_out_keeps_gws_draft(self):
        r, out = self.run_guard(self.reply_args(), GWSA_KEEP_ATTACHMENT_IDS="1")
        self.assertEqual(out["id"], "r1")
        self.assertEqual(list(self.drafts()), ["r1"])

    def test_plain_draft_reaches_gws_as_html(self):
        r, out = self.run_guard(["gmail", "+send", "--to", "x@example.com", "--draft", "--body", "a\nb"])
        self.assertIn("--html", out["argv"])
        self.assertIn("<div>a</div>", out["argv"][out["argv"].index("--body") + 1])


if __name__ == "__main__":
    unittest.main()
