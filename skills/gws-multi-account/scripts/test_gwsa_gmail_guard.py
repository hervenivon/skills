"""Offline tests for gwsa-gmail-guard.py and the gwsa wrapper. A fake `gws` on PATH stands in for Gmail.

Run: python3 -m unittest discover -s skills/gws-multi-account/scripts -p 'test_*.py'
"""
import base64
import contextlib
import email
import io
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from email.mime.application import MIMEApplication
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.policy import compat32

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "gwsa-gmail-guard.py")
GWSA = os.path.join(HERE, "gwsa")
spec = importlib.util.spec_from_file_location("gwsa_gmail_guard", GUARD)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
GWS_STYLE = compat32.clone(max_line_length=None, linesep="\r\n")

# An .eml file that itself carries an attachment without Content-ID: the guard must not touch it.
INNER_EML = (b"From: a@example.com\r\nTo: b@example.com\r\nSubject: inner\r\nMIME-Version: 1.0\r\n"
             b"Content-Type: multipart/mixed; boundary=XX\r\n\r\n--XX\r\nContent-Type: text/plain\r\n\r\n"
             b"inner body\r\n--XX\r\nContent-Type: application/pdf\r\n"
             b"Content-Disposition: attachment; filename=inner.pdf\r\nContent-Transfer-Encoding: base64\r\n\r\n"
             b"JVBERi0x\r\n--XX--\r\n")

# Fake gws. Helper drafts are built the way gws 0.22.5 builds them: long headers on 1 line, CRLF,
# attachments without ids (one with no Content-ID, one with an empty one), an .eml as message/rfc822
# in base64, and for replies an inline image that already has an id. +forward keeps the original's
# files unless --no-original-attachments: one without id, one from Outlook with a Content-ID only.
FAKE_GWS = r'''#!@PYTHON@
import base64, json, os, sys
from email.mime.application import MIMEApplication
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.policy import compat32

GWS_STYLE = compat32.clone(max_line_length=None, linesep="\r\n")
argv = sys.argv[1:]
store = os.environ["FAKE_GWS_STORE"]
path = os.path.join(store, "drafts.json")
drafts = json.load(open(path)) if os.path.exists(path) else {}

def save():
    json.dump(drafts, open(path, "w"))

def val(flag):
    return argv[argv.index(flag) + 1] if flag in argv else None

def attached_files():
    files = []
    for i, a in enumerate(argv):
        if a in ("-a", "--attach"):
            files.append(argv[i + 1])
        elif a.startswith("--attach="):
            files.append(a[len("--attach="):])
        elif a.startswith("-a") and not a.startswith("--") and len(a) > 2:
            files.append(a[3:] if a.startswith("-a=") else a[2:])
    return files

def attachment(name, data, content_id=None):
    if name.endswith(".eml"):
        part = MIMEBase("message", "rfc822")
        part.set_payload(base64.encodebytes(data).decode())
        part["Content-Transfer-Encoding"] = "base64"
    else:
        part = MIMEApplication(data)
    part.add_header("Content-Disposition", "attachment", filename=name)
    if content_id is not None:
        part["Content-ID"] = content_id
    return part

sys.stderr.write("Using keyring backend: file\n")
cfg = os.environ.get("GOOGLE_WORKSPACE_CLI_CONFIG_DIR")
if cfg and not os.path.isdir(cfg):
    sys.stderr.write("error: no credentials in %s\n" % cfg)
    sys.exit(2)
if argv[:1] == ["gmail"] and argv[1] in ("+send", "+reply", "+reply-all", "+forward"):
    helper = argv[1]
    msg = MIMEMultipart("mixed")
    msg["To"] = ", ".join("person%d@example.com" % n for n in range(6))
    subject = "Quarterly report: the figures, the comments and the follow-up actions we agreed on"
    msg["Subject"] = subject if helper == "+send" else "Re: " + subject
    if helper != "+send":
        msg["In-Reply-To"] = "<orig@example.com>"
        msg["References"] = "<CAAbc1234567890123456789012345678@mail.gmail.com> <orig@example.com>"
    msg.attach(MIMEText(val("--body") or "", "html" if "--html" in argv else "plain"))
    if helper != "+send":
        img = MIMEImage(@PNG@, "png")
        img["Content-ID"] = "<img1>"
        img.add_header("Content-Disposition", "inline", filename="logo.png")
        msg.attach(img)
    if helper == "+forward" and "--no-original-attachments" not in argv:
        msg.attach(attachment("original.pdf", b"%PDF-1.4 original"))
        msg.attach(attachment("outlook.docx", b"PK outlook", "<part1.abc@outlook>"))
    for n, f in enumerate(attached_files()):
        msg.attach(attachment(f, open(f, "rb").read(), "<>" if n == 1 else None))
    drafts["r1"] = {"mid": "m1", "thread": "t1",
                    "raw": base64.urlsafe_b64encode(msg.as_bytes(policy=GWS_STYLE)).decode()}
    save()
    fmt = val("--format") or next((a.split("=", 1)[1] for a in argv if a.startswith("--format=")), "json")
    if fmt != "json":
        print("id  r1")
        sys.exit(0)
    print(json.dumps({"id": "r1", "message": {"id": "m1", "labelIds": ["DRAFT"], "threadId": "t1"},
                      "argv": argv, "config_dir": cfg}))
elif argv[:4] == ["gmail", "users", "messages", "get"]:
    mid = json.loads(val("--params"))["id"]
    raw = next(d["raw"] for d in drafts.values() if d["mid"] == mid)
    print(json.dumps({"id": mid, "raw": raw}))
elif argv[:4] == ["gmail", "users", "drafts", "create"]:
    raw = open(val("--upload"), "rb").read().replace(b"\r\n", b"\n")
    thread = json.loads(val("--json"))["message"]["threadId"]
    if b"\nIn-Reply-To:" not in b"\n" + raw.split(b"\n\n", 1)[0]:
        thread = "t-new"  # Gmail may start a new thread for a message that is not a reply
    if os.environ.get("FAKE_GWS_WRONG_THREAD") == "1":
        thread = "t-other"
    if os.environ.get("FAKE_GWS_DROP_XAID") == "1":
        raw = b"\n".join(l for l in raw.split(b"\n") if not l.startswith(b"X-Attachment-Id:"))
    drafts["r2"] = {"mid": "m2", "thread": thread, "raw": base64.urlsafe_b64encode(raw).decode()}
    save()
    if os.environ.get("FAKE_GWS_BAD_JSON") == "1":
        print("{ not json")
        sys.exit(0)
    print(json.dumps({"id": "r2", "message": {"id": "m2", "labelIds": ["DRAFT"], "threadId": thread}}))
elif argv[:4] == ["gmail", "users", "drafts", "delete"]:
    did = json.loads(val("--params"))["id"]
    if did == os.environ.get("FAKE_GWS_FAIL_DELETE"):
        sys.exit(1)
    drafts.pop(did, None)
    save()
else:
    print(json.dumps({"argv": argv, "config_dir": cfg}))
'''.replace("@PYTHON@", sys.executable).replace("@PNG@", repr(PNG))


def attachment_parts(msg):
    return [p for p in guard.leaf_parts(msg) if p.get_content_disposition() == "attachment"]


def eml_attachment(data):
    part = MIMEBase("message", "rfc822")
    part.set_payload(base64.encodebytes(data).decode())
    part["Content-Transfer-Encoding"] = "base64"
    part.add_header("Content-Disposition", "attachment", filename="thread.eml")
    return part


class Pure(unittest.TestCase):
    def setUp(self):
        quiet = contextlib.redirect_stderr(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def body(self, argv):
        return argv[argv.index("--body") + 1]

    def test_to_html(self):
        out = guard.to_html("Hi <b>\n\nSee https://example.com/a.")
        self.assertIn("<div>Hi &lt;b&gt;</div>", out)
        self.assertIn("<div><br></div>", out)
        self.assertIn('<a href="https://example.com/a">https://example.com/a</a>.', out)

    def test_plain_draft_becomes_html(self):
        out = guard.guard_helper(["gmail", "+send", "--draft", "--body", "line 1\nline 2"])
        self.assertIn("--html", out)
        self.assertEqual(self.body(out), "<div>line 1</div>\n<div>line 2</div>")

    def test_html_body_without_flag_is_kept_as_html(self):
        for body in ("<p>Hello</p>", "<p>a</p>\n<p>b</p>", "Hi,<br>thanks", '<a href="https://x.y">x</a>\nbye'):
            out = guard.guard_helper(["gmail", "+send", "--draft", "--body", body])
            self.assertEqual(self.body(out), body)
            self.assertIn("--html", out)

    def test_plain_text_that_names_a_tag_is_escaped(self):
        for body, shown in (("Use the <b> tag for bold.\nSecond paragraph", "&lt;b&gt;"),
                            ("use <a and b>\nthen c", "&lt;a and b&gt;")):
            out = guard.guard_helper(["gmail", "+reply", "--draft", "--body", body])
            self.assertIn(shown, self.body(out))
            self.assertIn("<div>", self.body(out))
            self.assertIn("--html", out)

    def test_html_body_and_direct_send_untouched(self):
        argv = ["gmail", "+send", "--draft", "--html", "--body", "<p>a</p>\n<p>b</p>"]
        self.assertEqual(guard.guard_helper(argv), argv)
        argv = ["gmail", "+send", "--body", "a\nb"]
        self.assertEqual(guard.guard_helper(argv), argv)

    def test_html_flag_body_with_any_markup_is_untouched(self):
        for body in ("<pre>\nlog line 1\nlog line 2\n</pre>", "Tom &amp; Jerry\nline 2",
                     "<table><tr><td>a</td></tr>\n<tr><td>b</td></tr></table>", "a&nbsp;b\nc &#39;d&#39;"):
            argv = ["gmail", "+send", "--draft", "--html", "--body", body]
            self.assertEqual(guard.guard_helper(argv), argv)

    def test_html_flag_multiline_body_without_markup_is_converted(self):
        out = guard.guard_helper(["gmail", "+send", "--draft", "--html", "--body", "a < b\nc"])
        self.assertEqual(self.body(out), "<div>a &lt; b</div>\n<div>c</div>")

    def test_adds_attachments_in_every_flag_form(self):
        for flags in (["-a", "x.pdf"], ["-a=x.pdf"], ["-ax.pdf"], ["--attach", "x.pdf"], ["--attach=x.pdf"]):
            self.assertTrue(guard.adds_attachments(["gmail", "+send", "--draft"] + flags), flags)
        self.assertFalse(guard.adds_attachments(["gmail", "+send", "--draft", "--body", "x"]))
        self.assertTrue(guard.adds_attachments(["gmail", "+forward", "--draft"]))
        self.assertFalse(guard.adds_attachments(["gmail", "+forward", "--draft", "--no-original-attachments"]))

    def build(self):
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
        outlook = MIMEApplication(b"outlook")
        outlook.add_header("Content-Disposition", "attachment", filename="o.xlsx")
        outlook["Content-ID"] = "<part1.abc@outlook>"
        msg.attach(outlook)
        return msg, img, outlook

    def test_assign_ids(self):
        msg, img, outlook = self.build()
        assigned = guard.assign_attachment_ids(msg)
        self.assertEqual(len(assigned), 2)
        fixed = [p for p in attachment_parts(msg) if p.get_filename() != "o.xlsx"]
        self.assertEqual(len({p["Content-ID"] for p in fixed}), 2)
        for p in fixed:
            self.assertRegex(p["X-Attachment-Id"], r"^f_[0-9a-f]{10}$")
            self.assertEqual(p["Content-ID"], "<%s>" % p["X-Attachment-Id"])
            self.assertEqual(p.get_param("name"), p.get_filename())
        self.assertEqual(img["Content-ID"], "<img1>")
        self.assertEqual(outlook["Content-ID"], "<part1.abc@outlook>")
        self.assertIsNone(outlook["X-Attachment-Id"])
        self.assertEqual(guard.assign_attachment_ids(msg), {})  # idempotent

    def test_existing_content_id_does_not_fail_the_check(self):
        msg, _, _ = self.build()
        first = email.message_from_bytes(msg.as_bytes(policy=GWS_STYLE))
        fixed = email.message_from_bytes(msg.as_bytes(policy=GWS_STYLE))
        assigned = guard.assign_attachment_ids(fixed)
        back = email.message_from_bytes(fixed.as_bytes(policy=GWS_STYLE).replace(b"\r\n", b"\n"))
        self.assertEqual(guard.compare_drafts(first, back, "t1", "t1", assigned), [])

    def test_attached_eml_is_1_attachment_and_written_back_unchanged(self):
        msg = MIMEMultipart("mixed")
        msg.attach(MIMEText("<p>x</p>", "html"))
        msg.attach(eml_attachment(INNER_EML))
        raw = msg.as_bytes(policy=GWS_STYLE)
        parsed = guard.parse(raw)
        self.assertEqual([p.get_content_type() for p in guard.leaf_parts(parsed)], ["text/html", "message/rfc822"])
        self.assertEqual(parsed.as_bytes(policy=guard.GEN), raw)
        assigned = guard.assign_attachment_ids(parsed)
        self.assertEqual(list(assigned), [1])
        eml = guard.leaf_parts(guard.parse(parsed.as_bytes(policy=guard.GEN)))[1]
        self.assertEqual(eml["Content-ID"], "<%s>" % assigned[1])
        self.assertEqual(eml.get_payload(decode=True), INNER_EML)

    def test_7bit_attached_mail_is_not_entered(self):
        msg = MIMEMultipart("mixed")
        msg.attach(MIMEText("<p>x</p>", "html"))
        part = MIMEBase("message", "rfc822")
        part.set_payload([email.message_from_bytes(INNER_EML)])
        part.add_header("Content-Disposition", "attachment", filename="fwd.eml")
        msg.attach(part)
        parsed = guard.parse(msg.as_bytes(policy=GWS_STYLE))
        self.assertEqual(list(guard.assign_attachment_ids(parsed)), [1])
        out = parsed.as_bytes(policy=guard.GEN)
        self.assertEqual(out.count(b"X-Attachment-Id"), 1)
        self.assertIn(b"filename=inner.pdf\r\nContent-Transfer-Encoding: base64\r\n\r\nJVBERi0x", out)

    def test_header_folding_and_line_endings_are_not_changes(self):
        to = ", ".join("person%d@example.com" % n for n in range(6))
        refs = "<CAAbc1234567890123456789012345678@mail.gmail.com> <orig@example.com>"
        flat = b"To: %s\r\nReferences: %s\r\nIn-Reply-To: <orig@example.com>\r\n\r\nx\r\n" % (to.encode(), refs.encode())
        folded = flat.replace(b", person3", b",\n person3").replace(b"> <orig", b">\n <orig").replace(b"\r\n", b"\n")
        first, back = email.message_from_bytes(flat), email.message_from_bytes(folded)
        self.assertEqual(guard.compare_drafts(first, back, "t1", "t1", {}), [])
        back.replace_header("To", "someone@else.example")
        self.assertEqual(guard.compare_drafts(first, back, "t1", "t1", {}), ["header To changed"])

    def test_thread_is_checked_for_replies_only(self):
        reply = email.message_from_bytes(b"In-Reply-To: <orig@example.com>\r\n\r\nx\r\n")
        new = email.message_from_bytes(b"Subject: new\r\n\r\nx\r\n")
        self.assertEqual(guard.compare_drafts(reply, reply, "t1", "t2", {}), ["new draft is in another thread"])
        self.assertEqual(guard.compare_drafts(new, new, "t1", "t2", {}), [])

    def test_comparable_ignores_line_endings_in_text_only(self):
        self.assertEqual(guard.part_bytes(MIMEText("a\r\nb", "html")), guard.part_bytes(MIMEText("a\nb", "html")))
        a = MIMEApplication(b"a\r\nb")
        a.add_header("Content-Disposition", "attachment", filename="f.bin")
        b = MIMEApplication(b"a\nb")
        b.add_header("Content-Disposition", "attachment", filename="f.bin")
        self.assertNotEqual(guard.part_bytes(a), guard.part_bytes(b))


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
        self.files = {"a.pdf": b"%PDF-1.4 first", "résumé été.docx": b"PK second"}
        for name, data in dict(self.files, **{"thread.eml": INNER_EML}).items():
            with open(os.path.join(self.work, name), "wb") as f:
                f.write(data)

    def tearDown(self):
        self.tmp.cleanup()

    def env(self, **env):
        e = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"], FAKE_GWS_STORE=self.store)
        for k in ("GWSA_ALLOW_PLAIN", "GWSA_KEEP_ATTACHMENT_IDS", "GOOGLE_WORKSPACE_CLI_CONFIG_DIR", "GWSA_ROOT",
                  "GWSA_PYTHON"):
            e.pop(k, None)
        e.update(env)
        return e

    def run_cmd(self, cmd, **env):
        r = subprocess.run(cmd, cwd=self.work, env=self.env(**env), capture_output=True, text=True)
        out = r.stdout[r.stdout.find("{"):] if "{" in r.stdout else ""
        return r, (json.JSONDecoder().raw_decode(out)[0] if out else {})

    def run_guard(self, args, **env):
        return self.run_cmd([sys.executable, GUARD] + args, **env)

    def drafts(self):
        with open(os.path.join(self.store, "drafts.json")) as f:
            return json.load(f)

    def stored(self, draft_id):
        return guard.parse(base64.urlsafe_b64decode(self.drafts()[draft_id]["raw"]))

    def reply_args(self, attach=("-a", "{}")):
        args = ["gmail", "+reply", "--message-id", "m0", "--draft", "--html", "--body", "<p>x</p>"]
        for name in self.files:
            args += [a.format(name) for a in attach]
        return args

    def assert_fixed(self, r, out, names):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.get("id"), "r2", r.stderr)
        self.assertIn("given unique Content-IDs", r.stderr)
        self.assertEqual(list(self.drafts()), ["r2"])
        atts = attachment_parts(self.stored("r2"))
        self.assertEqual([p.get_filename() for p in atts], names)
        ids = [p["Content-ID"] for p in atts]
        self.assertTrue(all(i and i != "<>" for i in ids) and len(set(ids)) == len(ids), ids)
        return atts

    def test_draft_with_attachments_is_replaced_with_unique_ids(self):
        r, out = self.run_guard(self.reply_args())
        atts = self.assert_fixed(r, out, list(self.files))
        self.assertEqual([p.get_payload(decode=True) for p in atts], list(self.files.values()))
        msg = self.stored("r2")
        self.assertEqual(msg["In-Reply-To"], "<orig@example.com>")
        inline = [p for p in msg.walk() if p.get_content_disposition() == "inline"]
        self.assertEqual(inline[0]["Content-ID"], "<img1>")

    def test_long_headers_are_kept(self):
        r, out = self.run_guard(self.reply_args())
        self.assert_fixed(r, out, list(self.files))
        msg = self.stored("r2")
        self.assertEqual(msg["To"], ", ".join("person%d@example.com" % n for n in range(6)))
        self.assertEqual(msg["References"], "<CAAbc1234567890123456789012345678@mail.gmail.com> <orig@example.com>")

    def test_every_attach_flag_form_is_fixed(self):
        for attach in (("-a={}",), ("-a{}",), ("--attach={}",)):
            r, out = self.run_guard(self.reply_args(attach))
            self.assert_fixed(r, out, list(self.files))

    def test_forward_keeps_original_files_and_is_fixed(self):
        r, out = self.run_guard(["gmail", "+forward", "--message-id", "m0", "--to", "y@example.com", "--draft",
                                 "--html", "--body", "<p>fyi</p>"])
        atts = self.assert_fixed(r, out, ["original.pdf", "outlook.docx"])
        self.assertEqual(atts[1]["Content-ID"], "<part1.abc@outlook>")

    def test_forward_with_attach_and_outlook_ids_is_fixed(self):
        r, out = self.run_guard(["gmail", "+forward", "--message-id", "m0", "--to", "y@example.com", "--draft",
                                 "--html", "--body", "<p>fyi</p>", "-a", "a.pdf"])
        self.assert_fixed(r, out, ["original.pdf", "outlook.docx", "a.pdf"])

    def test_forward_without_original_files_is_not_touched(self):
        r, out = self.run_guard(["gmail", "+forward", "--message-id", "m0", "--to", "y@example.com", "--draft",
                                 "--html", "--body", "<p>fyi</p>", "--no-original-attachments"])
        self.assertEqual(out.get("id"), "r1", r.stderr)
        self.assertNotIn("Content-IDs", r.stderr)

    def test_new_message_draft_may_change_thread(self):
        r, out = self.run_guard(["gmail", "+send", "--to", "y@example.com", "--subject", "s", "--draft", "--html",
                                 "--body", "<p>hi</p>", "-a", "a.pdf", "-a", "thread.eml"])
        atts = self.assert_fixed(r, out, ["a.pdf", "thread.eml"])
        self.assertEqual(atts[1].get_payload(decode=True), INNER_EML)

    def test_relative_config_dir(self):
        os.mkdir(os.path.join(self.work, "profile"))
        r, out = self.run_guard(self.reply_args(), GOOGLE_WORKSPACE_CLI_CONFIG_DIR="profile")
        self.assert_fixed(r, out, list(self.files))

    def test_failed_check_keeps_first_draft(self):
        r, out = self.run_guard(self.reply_args(), FAKE_GWS_WRONG_THREAD="1")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(out["id"], "r1")
        self.assertIn("attachment-id fix failed", r.stderr)
        self.assertIn("draft r1 is kept", r.stderr)
        self.assertEqual(list(self.drafts()), ["r1"])

    def test_readback_without_attachment_id_keeps_first_draft(self):
        r, out = self.run_guard(self.reply_args(), FAKE_GWS_DROP_XAID="1")
        self.assertEqual(out["id"], "r1")
        self.assertIn("X-Attachment-Id missing", r.stderr)
        self.assertEqual(list(self.drafts()), ["r1"])

    def test_unreadable_create_answer_reports_the_second_draft(self):
        r, out = self.run_guard(self.reply_args(), FAKE_GWS_BAD_JSON="1")
        self.assertEqual(out["id"], "r1")
        self.assertIn("a second draft may exist in thread t1", r.stderr)

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

    # ---------- the gwsa wrapper ----------

    def wrapper_in(self, name, with_guard):
        d = os.path.join(self.tmp.name, name)
        os.mkdir(d)
        shutil.copy(GWSA, d)
        if with_guard:
            shutil.copy(GUARD, d)
        return os.path.join(d, "gwsa")

    def test_wrapper_finds_the_guard_through_a_symlink_and_makes_root_absolute(self):
        link_dir = os.path.join(self.tmp.name, "links")
        os.mkdir(link_dir)
        os.symlink(self.wrapper_in("real", with_guard=True), os.path.join(link_dir, "gwsa"))
        os.makedirs(os.path.join(self.work, "accounts", "p"))
        r, out = self.run_cmd([os.path.join(link_dir, "gwsa"), "p", "gmail", "+send", "--to", "x@example.com",
                               "--draft", "--body", "a\nb"], GWSA_ROOT="accounts", GWSA_PYTHON=sys.executable)
        self.assertIn("--html", out.get("argv", []), r.stderr)
        self.assertEqual(out["config_dir"], os.path.join(os.path.realpath(self.work), "accounts", "p"))

    def test_wrapper_without_guard_warns_on_drafts_only(self):
        gwsa = self.wrapper_in("lonely", with_guard=False)
        os.makedirs(os.path.join(self.work, "accounts", "p"))
        r, out = self.run_cmd([gwsa, "p", "gmail", "+send", "--to", "x@example.com", "--draft", "--body", "a\nb"],
                              GWSA_ROOT="accounts")
        self.assertNotIn("--html", out["argv"])
        self.assertIn("Gmail guard not run", r.stderr)
        r, _ = self.run_cmd([gwsa, "p", "gmail", "+triage"], GWSA_ROOT="accounts")
        self.assertNotIn("Gmail guard not run", r.stderr)

    def test_wrapper_guard_status(self):
        r, _ = self.run_cmd([self.wrapper_in("real", with_guard=True), "guard"], GWSA_PYTHON=sys.executable)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Gmail guard: on", r.stdout)
        self.assertIn(sys.executable, r.stdout)
        r, _ = self.run_cmd([self.wrapper_in("lonely", with_guard=False), "guard"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("Gmail guard: OFF", r.stdout)


if __name__ == "__main__":
    unittest.main()
