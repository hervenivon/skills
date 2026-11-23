#!/usr/bin/env python3
"""gwsa Gmail guard: called by `gwsa` for every `gmail` command, after the profile is selected.

It fixes 2 problems of the drafts that the gws Gmail helpers (`+send`, `+reply`, `+reply-all`,
`+forward` with `--draft`) create, then hands over to `gws`. Python 3.7+, standard library only.

1. Plain-text drafts get hard-wrapped.
   A draft that holds only text/plain opens in Gmail's plain-text compose mode. When it is sent
   from the Gmail UI, Gmail hard-wraps every line at about 70 characters and sends text/plain only:
   the recipient sees broken lines and a `>` quote block. An HTML draft keeps its lines intact.
   - helper `--draft` without `--html`: the `--body` text becomes HTML (escaped, 1 <div> per line,
     URLs as links) and `--html` is added. A body that holds real HTML (a closed tag pair such as
     <p>..</p>, or <br>, <hr>, <img ...>) is taken as HTML written without the flag: it is kept as
     is and `--html` is added. A text that only names a tag ("use the <b> tag") is escaped;
   - helper `--draft` with `--html` but a multi-line body without any markup (no tag, comment or
     entity): same conversion (otherwise the line breaks collapse into 1 paragraph);
   - `users drafts create|update` with a raw message that has no text/html part: a warning.
   Opt out for 1 call: GWSA_ALLOW_PLAIN=1.

2. Attachments of a draft share 1 empty Content-ID.
   The helpers build attachment parts without an attachment id. When such a draft is sent from the
   Gmail UI, every attachment leaves with an empty `X-Attachment-Id:` and the same empty
   `Content-ID: <>`. Clients that key attachments on Content-ID merge them: Outlook shows only the
   first file, Apple Mail shows 1 file twice. Gmail web gives each attachment its own id.
   So after gws creates a helper draft that holds files (`-a/--attach` in any form, or a `+forward`
   that keeps the original's files, the gws default), the guard gives each attachment without a
   Content-ID a unique `f_<10 hex>` id (X-Attachment-Id + matching Content-ID), uploads the fixed
   message as a new draft, reads it back (thread for a reply, headers, parts, bytes, ids), deletes
   the first draft and prints the new draft JSON (a requested `--format table|yaml|csv` is run as
   JSON). Files that already have a Content-ID (from a forwarded Outlook mail) and the mail inside
   an attached .eml are left alone. If a check fails, the new draft is deleted and the first one
   kept; every draft id left behind by a failed cleanup is printed. Drafts are re-created rather
   than updated: `drafts update` on a reply drops it from its thread.
   Opt out for 1 call: GWSA_KEEP_ATTACHMENT_IDS=1.

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
from email.policy import compat32

HELPERS = {"+send", "+reply", "+reply-all", "+forward"}
# How the guard writes a message back: long headers stay on 1 line and lines end in CRLF, so every
# header gws wrote comes back as it was (the default policy re-folds lines over 78 characters).
GEN = compat32.clone(max_line_length=None, linesep="\r\n")
# Without --html, a body is taken as HTML written without the flag only when it holds a closed tag
# pair or a line break, rule or image tag; a plain text that merely names a tag is escaped.
HTML_TAGS = (r"p|div|span|a|b|i|u|s|strong|em|font|center|sup|sub|small|big|ul|ol|li|dl|dt|dd|table|thead"
             r"|tbody|tfoot|tr|td|th|blockquote|pre|code|h[1-6]|html|head|body|section|article|style")
OPEN_TAG = re.compile(r"<(%s)(?:\s[^<>]*)?>" % HTML_TAGS, re.I)
CLOSE_TAG = re.compile(r"</(%s)\s*>" % HTML_TAGS, re.I)
VOID_TAG = re.compile(r"<(br|hr)\s*/?>|<img\s[^<>]*>", re.I)
# With --html, any tag, comment or character reference means the body is HTML as written.
ANY_MARKUP = re.compile(r"</?[a-zA-Z][a-zA-Z0-9-]*(\s[^<>]*)?/?>|<!--|&(#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);")
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


def looks_like_html(body):
    """A tag both opened and closed somewhere in the body, or a <br>, <hr> or <img ...> tag."""
    opened = {m.group(1).lower() for m in OPEN_TAG.finditer(body)}
    closed = {m.group(1).lower() for m in CLOSE_TAG.finditer(body)}
    return bool(opened & closed) or bool(VOID_TAG.search(body))


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

    def set_body(new):
        argv[i] = ("--body=" + new) if joined else new

    if "--html" in argv:
        if body is not None and "\n" in body.strip() and not ANY_MARKUP.search(body):
            set_body(to_html(body))
            sys.stderr.write("gwsa: --html body had no HTML markup, line breaks converted to <div> lines\n")
        return argv
    if body is not None and looks_like_html(body):
        sys.stderr.write("gwsa: draft body holds HTML tags but no --html: sent as HTML (--html added)\n")
    else:
        if body is not None:
            set_body(to_html(body))
        sys.stderr.write("gwsa: plain draft converted to HTML (Gmail hard-wraps text/plain drafts at send). "
                         "Write bodies as HTML with --html.\n")
    argv.append("--html")
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
    has_html = has_text = False
    for part in leaf_parts(email.message_from_bytes(raw)):
        if part.get_filename() or part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() == "text/html":
            has_html = True
        elif part.get_content_type() == "text/plain" and (part.get_payload() or "").strip():
            has_text = True
    if has_text and not has_html:
        sys.stderr.write("gwsa: WARNING this draft is text/plain only. Gmail will hard-wrap it at about 70 "
                         "characters when it is sent from the Gmail UI. Add an HTML part.\n")


# ---------- 2. Attachment ids ----------

def adds_attachments(argv):
    """True when the helper draft will hold files: -a/--attach in any form clap accepts (-a f, -a=f,
    -af, --attach f, --attach=f), or a +forward that keeps the original's files (the gws default)."""
    if argv[1] == "+forward" and "--no-original-attachments" not in argv:
        return True
    return any(a == "--attach" or a.startswith("--attach=") or (a.startswith("-a") and not a.startswith("--"))
               for a in argv)


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
    """The leaf parts of a message, in order. Only multipart/* is entered: a message/* part (an
    attached .eml) is 1 attachment, and the mail inside it is left alone."""
    payload = msg.get_payload()
    if msg.get_content_maintype() == "multipart" and isinstance(payload, list):
        return [leaf for sub in payload for leaf in leaf_parts(sub)]
    return [msg]


def parse(raw):
    """Parse a raw message. `email` parses the body of every message/* part as a mail, even when it is
    base64 (gws sends an attached .eml as message/rfc822 in base64): the result is a mail without
    headers whose body is the base64 text, written back with an extra blank line. Such a part gets
    its encoded body back as payload, so it is written byte for byte and decodes to the file."""
    msg = email.message_from_bytes(raw)
    for part in leaf_parts(msg):
        payload = part.get_payload()
        cte = str(part.get("Content-Transfer-Encoding") or "").strip().lower()
        if (part.get_content_maintype() == "message" and cte in ("base64", "quoted-printable")
                and isinstance(payload, list) and len(payload) == 1 and not payload[0].keys()
                and isinstance(payload[0].get_payload(), str)):
            part.set_payload(payload[0].get_payload())
    return msg


def content_id(part):
    return str(part.get("Content-ID") or "").strip().strip("<>").strip()


def assign_attachment_ids(msg):
    """Give every attachment part without a real Content-ID a unique `f_<10 hex>` id, in place.
    Parts that already have one (inline images of a quoted reply, files of a forwarded Outlook mail)
    are left alone. Returns {position in leaf_parts: id} for the parts it changed."""
    leaves = leaf_parts(msg)
    taken = {content_id(p) for p in leaves}
    assigned = {}
    for n, part in enumerate(leaves):
        if part.get_content_disposition() != "attachment" or content_id(part):
            continue
        aid = "f_" + secrets.token_hex(5)
        while aid in taken:
            aid = "f_" + secrets.token_hex(5)
        taken.add(aid)
        del part["X-Attachment-Id"]
        del part["Content-ID"]
        part["X-Attachment-Id"] = aid
        part["Content-ID"] = "<%s>" % aid
        fn = part.get_filename()
        if fn and fn.isascii() and not part.get_param("name"):
            part.set_param("name", fn)
        assigned[n] = aid
    return assigned


def part_bytes(part):
    """Decoded content of a leaf part. Body text parts ignore line endings (Gmail turns CRLF into LF
    in text parts on upload); attachments must stay byte-identical. A 7bit attached mail is compared
    as written, without line endings either."""
    payload = part.get_payload()
    if isinstance(payload, list):
        return b"".join(m.as_bytes(policy=GEN) for m in payload).replace(b"\r\n", b"\n")
    data = part.get_payload(decode=True) or b""
    if part.get_content_maintype() == "text" and part.get_content_disposition() != "attachment":
        data = data.replace(b"\r\n", b"\n")
    return data


def squash(value):
    """A header value without its folding: Gmail may fold or unfold long headers."""
    return " ".join(str(value or "").split())


def compare_drafts(sent, back, thread, back_thread, assigned):
    """List what differs between the first draft (`sent`, as gws made it) and the new draft Gmail
    stored (`back`). Only the ids the guard `assigned` may differ, and they must be there."""
    problems = []
    # A reply must stay in its thread. A new message (+send) has no thread to keep: Gmail may file
    # the re-created draft under a new thread id, which changes nothing for the recipient.
    if (sent.get("In-Reply-To") or sent.get("References")) and back_thread != thread:
        problems.append("new draft is in another thread")
    a, b = leaf_parts(sent), leaf_parts(back)
    if [(p.get_content_type(), p.get_filename()) for p in a] != [(p.get_content_type(), p.get_filename()) for p in b]:
        problems.append("parts differ")
    else:
        if any(part_bytes(x) != part_bytes(y) for x, y in zip(a, b)):
            problems.append("part content differs")
        cids = [content_id(p) for p in b]
        for n, aid in sorted(assigned.items()):
            p = b[n]
            name = p.get_filename() or "part %d" % n
            if content_id(p) != aid or cids.count(aid) != 1:
                problems.append("%s: Content-ID not kept or not unique" % name)
            elif str(p.get("X-Attachment-Id") or "").strip() != aid:
                problems.append("%s: X-Attachment-Id missing or different from Content-ID" % name)
        empty = [p.get_filename() for p in b if p.get_content_disposition() == "attachment" and not content_id(p)]
        if empty:
            problems.append("attachments still without id: %s" % empty)
    for h in ("From", "To", "Cc", "Bcc", "Subject", "In-Reply-To", "References"):
        if squash(sent.get(h)) != squash(back.get(h)):
            problems.append("header %s changed" % h)
    return problems


def gws_json(args, cwd=None):
    r = subprocess.run(["gws"] + args, capture_output=True, text=True, cwd=cwd)
    i = r.stdout.find("{")
    if r.returncode != 0 or i < 0:
        raise RuntimeError("gws %s failed: %s" % (" ".join(args[:4]), (r.stderr or r.stdout)[-300:].strip()))
    try:
        return json.JSONDecoder().raw_decode(r.stdout[i:])[0]
    except ValueError:
        raise RuntimeError("gws %s gave unreadable output: %s" % (" ".join(args[:4]), r.stdout[i:i + 200]))


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
    first = parse(raw)   # untouched copy, the reference for the checks
    msg = parse(raw)
    assigned = assign_attachment_ids(msg)
    if not assigned:
        return draft, 0
    with tempfile.TemporaryDirectory() as tmp:  # gws --upload reads from the current directory only
        with open(os.path.join(tmp, "draft.eml"), "wb") as f:
            f.write(msg.as_bytes(policy=GEN))
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
        back = parse(get_raw(new["message"]["id"]))
        problems = compare_drafts(first, back, thread, new["message"].get("threadId"), assigned)
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
                     "(checked)\n" % (len(assigned), old_id, new["id"]))
    return new, len(assigned)


def run_draft_with_attachments(argv):
    argv, other_format = force_json(argv)
    if other_format:
        sys.stderr.write("gwsa: draft with attachments, output shown as JSON (the guard reads the draft id)\n")
    r = subprocess.run(["gws"] + argv, stdout=subprocess.PIPE, text=True)  # stderr passes through
    out = r.stdout
    if r.returncode != 0:
        sys.stdout.write(out)
        return r.returncode
    draft_id = None
    try:
        i = out.find("{")
        draft = json.JSONDecoder().raw_decode(out[i:])[0] if i >= 0 else {}
        if "id" not in draft or "message" not in draft:
            raise RuntimeError("no draft id in gws output")
        draft_id = draft["id"]
        new, fixed = fix_draft(draft)
        sys.stdout.write(json.dumps(new, indent=2) + "\n" if fixed else out)
    except Exception as exc:
        sys.stderr.write("gwsa: WARNING attachment-id fix failed (%s). The draft%s is kept as gws made it: its "
                         "attachments may share an empty Content-ID and show as 1 file in Outlook or Apple Mail. "
                         "Do not re-run the same command: it fails the same way and leaves a second draft.\n"
                         % (exc, " %s" % draft_id if draft_id else ""))
        sys.stdout.write(out)
    return 0


# ---------- entry point ----------

def main(argv):
    if sys.version_info < (3, 7):
        sys.stderr.write("gwsa: Gmail guard skipped (it needs Python 3.7+, this is %s)\n" % sys.version.split()[0])
        os.execvp("gws", ["gws"] + argv)
    # The attachment step runs gws from a temporary directory: a relative profile path must not move.
    cfg = os.environ.get("GOOGLE_WORKSPACE_CLI_CONFIG_DIR")
    if cfg:
        os.environ["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] = os.path.abspath(cfg)
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
            and not {"--dry-run", "--help", "-h"} & set(argv) and adds_attachments(argv)
            and os.environ.get("GWSA_KEEP_ATTACHMENT_IDS") != "1"):
        sys.exit(run_draft_with_attachments(argv))
    os.execvp("gws", ["gws"] + argv)


if __name__ == "__main__":
    main(sys.argv[1:])
