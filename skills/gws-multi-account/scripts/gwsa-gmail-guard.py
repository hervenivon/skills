#!/usr/bin/env python3
"""gwsa Gmail guard: called by `gwsa` for every `gmail` command, after the profile is selected.

It fixes 2 problems of the drafts that the gws Gmail helpers (`+send`, `+reply`, `+reply-all`,
`+forward` with `--draft`) create, then hands over to `gws`.

1. Plain-text drafts get hard-wrapped.
   A draft that holds only text/plain opens in Gmail's plain-text compose mode. When it is sent
   from the Gmail UI, Gmail hard-wraps every line at about 70 characters and sends text/plain only:
   the recipient sees broken lines and a `>` quote block. An HTML draft keeps its lines intact.
   - helper `--draft` without `--html`: the `--body` text becomes HTML (escaped, 1 <div> per line,
     URLs as links) and `--html` is added. A body that already holds HTML tags is taken as HTML
     written without the flag: it is kept as is and `--html` is added;
   - helper `--draft` with `--html` but a multi-line body without any HTML tag: same conversion
     (otherwise the line breaks collapse into 1 paragraph);
   - `users drafts create|update` with a raw message that has no text/html part: a warning.
   Opt out for 1 call: GWSA_ALLOW_PLAIN=1.

2. Attachments of a draft share 1 empty Content-ID.
   The helpers build attachment parts without an attachment id. When such a draft is sent from the
   Gmail UI, every attachment leaves with an empty `X-Attachment-Id:` and the same empty
   `Content-ID: <>`. Clients that key attachments on Content-ID merge them: Outlook shows only the
   first file, Apple Mail shows 1 file twice. Gmail web gives each attachment its own id.
   So after gws creates a helper draft with `-a/--attach`, the guard gives each attachment a unique
   `f_<10 hex>` id (X-Attachment-Id + matching Content-ID), uploads the fixed message as a new draft
   in the same thread, reads it back (thread, headers, parts, bytes, ids), deletes the first draft
   and prints the new draft JSON (a requested `--format table|yaml|csv` is run as JSON). If a check
   fails, the new draft is deleted and the first one kept; every draft id left behind by a failed
   cleanup is printed. Drafts are re-created rather than updated: `drafts update` on a reply drops
   it from its thread. Opt out for 1 call: GWSA_KEEP_ATTACHMENT_IDS=1.

Direct sends (no `--draft`) pass through unchanged; `--dry-run` gets the HTML step only. If the
HTML step fails, gws runs with the original arguments; if the attachment step fails, the draft
made by gws is kept as it is.
"""
import base64
import email
import html
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile

HELPERS = {"+send", "+reply", "+reply-all", "+forward"}
ATTACH_FLAGS = ("-a", "--attach")
TAG = re.compile(r"<(p|div|br|a|b|i|u|strong|em|ul|ol|li|table|span|blockquote|h[1-6]|img|hr)(\s[^>]*)?/?>", re.I)
URL = re.compile(r"https?://[^\s<>\"']+")


# ---------- 1. HTML bodies ----------

def linkify(line):
    out, pos = [], 0
    for m in URL.finditer(line):
        url = m.group(0)
        while url and (url[-1] in ".,;:!?'\"" or (url[-1] == ")" and url.count("(") < url.count(")"))):
            url = url[:-1]
        out.append(html.escape(line[pos:m.start()], quote=False))
        out.append('<a href="%s">%s</a>' % (html.escape(url, quote=True), html.escape(url, quote=False)))
        pos = m.start() + len(url)
    out.append(html.escape(line[pos:], quote=False))
    return "".join(out)


def to_html(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    divs = []
    for line in text.split("\n"):
        if not line.strip():
            divs.append("<div><br></div>")
            continue
        stripped = line.lstrip(" \t")
        indent = line[: len(line) - len(stripped)].replace("\t", "    ")
        divs.append("<div>%s%s</div>" % ("&nbsp;" * len(indent), linkify(stripped.rstrip())))
    return "\n".join(divs)


def body_index(argv):
    """Return (index of the body value, True if it is the `--body=value` form)."""
    for i, a in enumerate(argv):
        if a == "--body" and i + 1 < len(argv):
            return i + 1, False
        if a.startswith("--body="):
            return i, True
    return None, False


def guard_helper(argv):
    """Rewrite the arguments of a helper draft so its body is HTML."""
    if "--draft" not in argv:
        return argv
    argv = list(argv)
    i, joined = body_index(argv)
    body = None
    if i is not None:
        body = argv[i][len("--body="):] if joined else argv[i]
    if "--html" in argv:
        if body is not None and "\n" in body.strip() and not TAG.search(body):
            new = to_html(body)
            argv[i] = ("--body=" + new) if joined else new
            sys.stderr.write("gwsa: --html body had no HTML tag, line breaks converted to <div> lines\n")
        return argv
    if body is not None and not TAG.search(body):
        new = to_html(body)
        argv[i] = ("--body=" + new) if joined else new
    argv.append("--html")
    sys.stderr.write("gwsa: plain draft converted to HTML (Gmail hard-wraps text/plain drafts at send). "
                     "Write bodies as HTML with --html.\n")
    return argv


def raw_from_args(argv):
    for i, a in enumerate(argv):
        if a == "--upload" and i + 1 < len(argv):
            try:
                with open(argv[i + 1], "rb") as f:
                    return f.read()
            except OSError:
                return None
        if a == "--json" and i + 1 < len(argv):
            try:
                raw = json.loads(argv[i + 1]).get("message", {}).get("raw")
                if raw:
                    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            except (ValueError, AttributeError):
                return None
    return None


def warn_raw(argv):
    raw = raw_from_args(argv)
    if not raw:
        return
    msg = email.message_from_bytes(raw)
    has_html = has_text = False
    for part in msg.walk():
        if part.is_multipart() or part.get_filename():
            continue
        if part.get_content_type() == "text/html":
            has_html = True
        elif part.get_content_type() == "text/plain" and (part.get_payload() or "").strip():
            has_text = True
    if has_text and not has_html:
        sys.stderr.write("gwsa: WARNING this draft is text/plain only. Gmail will hard-wrap it at about 70 "
                         "characters when it is sent from the Gmail UI. Add an HTML part.\n")


# ---------- 2. Attachment ids ----------

def has_attachments(argv):
    return any(a in ATTACH_FLAGS or a.startswith("--attach=") for a in argv)


def force_json(argv):
    """Return (argv with `--format json`, True if another format was asked)."""
    argv = list(argv)
    for i, a in enumerate(argv):
        if a == "--format" and i + 1 < len(argv):
            asked = argv[i + 1]
            argv[i + 1] = "json"
            return argv, asked != "json"
        if a.startswith("--format="):
            asked = a[len("--format="):]
            argv[i] = "--format=json"
            return argv, asked != "json"
    return argv, False


def leaf_parts(msg):
    return [p for p in msg.walk() if not p.is_multipart()]


def assign_attachment_ids(msg):
    """Give every attachment part without a real Content-ID a unique `f_<10 hex>` id, in place.
    Parts that already have one (inline images of a quoted reply) are left alone. Returns the count."""
    fixed = 0
    for part in leaf_parts(msg):
        if part.get_content_disposition() != "attachment":
            continue
        if (part.get("Content-ID") or "").strip().strip("<>").strip():
            continue
        aid = "f_" + secrets.token_hex(5)
        del part["X-Attachment-Id"]
        del part["Content-ID"]
        part["X-Attachment-Id"] = aid
        part["Content-ID"] = "<%s>" % aid
        fn = part.get_filename()
        if fn and fn.isascii() and not part.get_param("name"):
            part.set_param("name", fn)
        fixed += 1
    return fixed


def comparable(part):
    """Decoded bytes of a part. Body text parts ignore line endings (Gmail turns CRLF into LF in
    text parts on upload); attachments must stay byte-identical."""
    data = part.get_payload(decode=True) or b""
    if part.get_content_maintype() == "text" and part.get_content_disposition() != "attachment":
        data = data.replace(b"\r\n", b"\n")
    return data


def compare_drafts(sent, back, thread, back_thread):
    """List what differs between the first draft (`sent`, as gws made it) and the new draft Gmail
    stored (`back`). Only the attachment id headers may differ."""
    problems = []
    if back_thread != thread:
        problems.append("new draft is in another thread")
    a, b = leaf_parts(sent), leaf_parts(back)
    if [(p.get_content_type(), p.get_filename()) for p in a] != [(p.get_content_type(), p.get_filename()) for p in b]:
        problems.append("parts differ")
    elif any(comparable(x) != comparable(y) for x, y in zip(a, b)):
        problems.append("part content differs")
    atts = [p for p in b if p.get_content_disposition() == "attachment"]
    ids = [(p.get("Content-ID") or "").strip("<> ") for p in atts]
    if not all(ids) or len(set(ids)) != len(ids):
        problems.append("attachment ids empty or repeated: %s" % ids)
    elif any((p.get("X-Attachment-Id") or "").strip() != i for p, i in zip(atts, ids)):
        problems.append("X-Attachment-Id missing or different from Content-ID")
    for h in ("From", "To", "Cc", "Bcc", "Subject", "In-Reply-To", "References"):
        if (sent.get(h) or "") != (back.get(h) or ""):
            problems.append("header %s changed" % h)
    return problems


def gws_json(args, cwd=None):
    r = subprocess.run(["gws"] + args, capture_output=True, text=True, cwd=cwd)
    i = r.stdout.find("{")
    if r.returncode != 0 or i < 0:
        raise RuntimeError("gws %s failed: %s" % (" ".join(args[:4]), (r.stderr or r.stdout)[-300:].strip()))
    return json.JSONDecoder().raw_decode(r.stdout[i:])[0]


def get_raw(message_id):
    m = gws_json(["gmail", "users", "messages", "get", "--params",
                  json.dumps({"userId": "me", "id": message_id, "format": "raw"})])
    return base64.urlsafe_b64decode(m["raw"] + "=" * (-len(m["raw"]) % 4))


def delete_draft(draft_id):
    r = subprocess.run(["gws", "gmail", "users", "drafts", "delete", "--params",
                        json.dumps({"userId": "me", "id": draft_id})], capture_output=True)
    return r.returncode == 0


def fix_draft(draft):
    """Re-create `draft` with unique attachment ids. Returns (draft to report, number of parts fixed)."""
    old_id, thread = draft["id"], draft["message"]["threadId"]
    raw = get_raw(draft["message"]["id"])
    first = email.message_from_bytes(raw)   # untouched copy, the reference for the checks
    msg = email.message_from_bytes(raw)
    fixed = assign_attachment_ids(msg)
    if not fixed:
        return draft, 0
    with tempfile.TemporaryDirectory() as tmp:  # gws --upload reads from the current directory only
        with open(os.path.join(tmp, "draft.eml"), "wb") as f:
            f.write(msg.as_bytes())
        try:
            new = gws_json(["gmail", "users", "drafts", "create", "--params", json.dumps({"userId": "me"}),
                            "--json", json.dumps({"message": {"threadId": thread}}),
                            "--upload", "draft.eml", "--upload-content-type", "message/rfc822"], cwd=tmp)
        except RuntimeError as exc:
            raise RuntimeError("%s; a second draft may exist in thread %s" % (exc, thread))
    if "id" not in new or "message" not in new:
        raise RuntimeError("unexpected drafts.create answer, a second draft may exist in thread %s: %s"
                           % (thread, str(new)[:200]))
    try:
        back = email.message_from_bytes(get_raw(new["message"]["id"]))
        problems = compare_drafts(first, back, thread, new["message"].get("threadId"))
    except Exception as exc:
        problems = ["read-back failed (%s)" % exc]
    if problems:
        if not delete_draft(new["id"]):
            problems.append("the new draft %s could not be deleted: 2 drafts exist, delete it by hand" % new["id"])
        raise RuntimeError("; ".join(problems))
    if not delete_draft(old_id):
        sys.stderr.write("gwsa: WARNING the first draft %s could not be deleted: 2 drafts exist, delete %s "
                         "by hand (the new draft %s is the fixed one)\n" % (old_id, old_id, new["id"]))
    sys.stderr.write("gwsa: %d attachment(s) given unique Content-IDs; draft %s replaced by %s "
                     "(same thread, checked)\n" % (fixed, old_id, new["id"]))
    return new, fixed


def run_draft_with_attachments(argv):
    argv, other_format = force_json(argv)
    if other_format:
        sys.stderr.write("gwsa: draft with attachments, output shown as JSON (the guard reads the draft id)\n")
    r = subprocess.run(["gws"] + argv, stdout=subprocess.PIPE, text=True)  # stderr passes through
    out = r.stdout
    if r.returncode != 0:
        sys.stdout.write(out)
        return r.returncode
    try:
        i = out.find("{")
        draft = json.JSONDecoder().raw_decode(out[i:])[0]
        if "id" not in draft or "message" not in draft:
            raise RuntimeError("no draft id in gws output")
        new, fixed = fix_draft(draft)
        sys.stdout.write(json.dumps(new, indent=2) + "\n" if fixed else out)
    except Exception as exc:
        sys.stderr.write("gwsa: WARNING attachment-id fix failed (%s). The draft is kept as gws made it: its "
                         "attachments may share an empty Content-ID and show as 1 file in Outlook or "
                         "Apple Mail.\n" % exc)
        sys.stdout.write(out)
    return 0


# ---------- entry point ----------

def main(argv):
    try:
        if os.environ.get("GWSA_ALLOW_PLAIN") != "1" and len(argv) >= 2 and argv[0] == "gmail":
            if argv[1] in HELPERS:
                argv = guard_helper(argv)
            elif "drafts" in argv[1:4] and ("create" in argv[1:5] or "update" in argv[1:5]):
                warn_raw(argv)
            elif argv[1] in ("users.drafts.create", "users.drafts.update"):
                warn_raw(argv)
    except Exception as exc:  # never block a mail command because of the guard
        sys.stderr.write("gwsa: html guard skipped (%s)\n" % exc)
        argv = sys.argv[1:]
    if (len(argv) >= 2 and argv[0] == "gmail" and argv[1] in HELPERS and "--draft" in argv
            and "--dry-run" not in argv and has_attachments(argv)
            and os.environ.get("GWSA_KEEP_ATTACHMENT_IDS") != "1"):
        sys.exit(run_draft_with_attachments(argv))
    os.execvp("gws", ["gws"] + argv)


if __name__ == "__main__":
    main(sys.argv[1:])
