from __future__ import annotations

import re
from html import unescape

_EMAIL = re.compile(r"[a-z0-9][a-z0-9._+-]{0,60}@[a-z0-9.-]+\.[a-z]{2,}", re.I)
_NAME = re.compile(r"([A-Z][A-Za-z'.-]{1,30},\s+[A-Z][A-Za-z .'(]{1,48})")
_GROUP = re.compile(r"Group:\s*([A-Za-z][^<\n]{2,60})")
_FROM = re.compile(r"^\s*(.*?)\s*<([^>]+)>\s*$")
_STOP = {
    "professor",
    "prof",
    "doctor",
    "from",
    "department",
    "dept",
    "the",
    "mail",
    "email",
    "please",
    "send",
    "this",
    "that",
    "iit",
    "delhi",
    "and",
    "for",
}

FACULTY_PAGES = (
    ("Civil Engineering", "https://civil.iitd.ac.in/index.php?lmenuid=faculty"),
    ("Electrical Engineering", "https://ee.iitd.ac.in/faculty"),
    ("Computer Science and Engineering", "https://homecse.iitd.ac.in/faculty/"),
    ("Chemistry", "https://chemistry.iitd.ac.in/faculty"),
    ("Mathematics", "https://maths.iitd.ac.in/faculty"),
    ("Humanities and Social Sciences", "https://hss.iitd.ac.in/faculty"),
    ("Mechanical Engineering", "https://mech.iitd.ac.in/faculty"),
)


def faculty_host(url: str) -> bool:
    if not (url or "").startswith("https://"):
        return False
    host = url.split("/", 3)[2].lower().split(":")[0]
    return host == "iitd.ac.in" or host.endswith(".iitd.ac.in")


def curl_fetch_args(url: str) -> list[str]:
    return [
        "curl",
        "-fsSL",
        "--max-redirs",
        "0",
        "--proto",
        "=https",
        "--max-time",
        "25",
        "-A",
        "webmail-mcp",
        url,
    ]


def fetch_public_page(url: str) -> str:
    """Fetch a public faculty page. Some IITD sites omit a certificate intermediate, so curl is the fallback."""
    import ssl
    import subprocess
    import urllib.request

    import certifi

    from assistant_bridge.errors import BridgeError

    if not faculty_host(url):
        raise BridgeError("refusing a directory URL outside IIT Delhi")

    class _StayOnCampus(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not faculty_host(newurl):
                raise BridgeError("refusing a directory redirect outside IIT Delhi")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    context = ssl.create_default_context(cafile=certifi.where())
    request = urllib.request.Request(url, headers={"User-Agent": "webmail-mcp"})
    opener = urllib.request.build_opener(_StayOnCampus, urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")
    except BridgeError:
        raise
    except Exception as first:
        completed = subprocess.run(curl_fetch_args(url), capture_output=True, check=False)
        if completed.returncode != 0 or not completed.stdout:
            raise first
        return completed.stdout.decode("utf-8", errors="replace")


def ingest_html(html: str, department: str) -> list[dict]:
    text = unescape(re.sub(r"<[^>]+>", " ", html or ""))
    text = text.replace("[at]", "@").replace("(at)", "@").replace("[dot]", ".").replace("(dot)", ".")
    text = re.sub(r"\s+", " ", text)
    found: list[dict] = []
    seen: set[str] = set()
    for match in _EMAIL.finditer(text):
        email = match.group(0).lower().strip(".")
        if email in seen or email.startswith("hod"):
            continue
        window = text[max(0, match.start() - 420) : match.start()]
        name = _nearest_name(window, email)
        group_match = None
        for candidate in _GROUP.finditer(window):
            group_match = candidate
        group = ""
        if group_match:
            group = group_match.group(1)
            group = re.split(r"\s+(?:Read more|E-mail|Areas of Interest)\b", group)[0].strip(" .")
        seen.add(email)
        found.append(
            {
                "name": name,
                "email": email,
                "department": department,
                "group": group,
                "source": "directory",
            }
        )
    return found


def person_from_header(value: str, *, source: str = "mailbox") -> dict | None:
    raw = (value or "").strip()
    if not raw or "@" not in raw:
        return None
    match = _FROM.match(raw)
    if match:
        name = match.group(1).strip().strip('"')
        email = match.group(2).strip().lower()
    else:
        email = raw.lower()
        name = ""
    if "@" not in email:
        return None
    if not name or name.lower() == email:
        name = email.split("@", 1)[0].replace(".", " ").replace("_", " ")
    return {"name": name, "email": email, "department": "", "group": "", "source": source}


def lookup(contacts: list[dict], query: str) -> dict:
    tokens = [token for token in re.findall(r"[a-z0-9]+", (query or "").lower()) if token not in _STOP and len(token) > 2]
    scored: list[tuple[int, dict]] = []
    for contact in contacts:
        name = (contact.get("name") or "").lower()
        blob = " ".join(
            [
                name,
                contact.get("department") or "",
                contact.get("group") or "",
                contact.get("email") or "",
            ]
        ).lower()
        if not any(token in name or token in (contact.get("email") or "") for token in tokens):
            continue
        score = 0
        for token in tokens:
            if token in name:
                score += 3
            elif token in blob:
                score += 2
        if score:
            scored.append((score, contact))
    scored.sort(key=lambda item: item[0], reverse=True)
    matches = [_public_contact(item) for _, item in scored[:5]]
    chosen = None
    if scored:
        best_score, best = scored[0]
        second = scored[1][0] if len(scored) > 1 else -1
        if best_score >= second + 2:
            chosen = _public_contact(best)
    return {"query": query, "chosen": chosen, "matches": matches}


def merge_contacts(existing: list[dict], incoming: list[dict]) -> list[dict]:
    by_email = {item["email"].lower(): dict(item) for item in existing if item.get("email")}
    for item in incoming:
        email = (item.get("email") or "").lower()
        if not email:
            continue
        current = by_email.get(email)
        if current is None or (item.get("source") == "directory" and current.get("source") != "directory"):
            by_email[email] = dict(item)
        elif item.get("name") and len(item["name"]) > len(current.get("name") or ""):
            current["name"] = item["name"]
            if item.get("department"):
                current["department"] = item["department"]
            if item.get("group"):
                current["group"] = item["group"]
    return sorted(by_email.values(), key=lambda item: (item.get("name") or "").lower())


def _nearest_name(window: str, email: str) -> str:
    found = list(_NAME.finditer(window))
    if found:
        last, first = [part.strip(" .") for part in found[-1].group(1).split(",", 1)]
        first = re.sub(r"\s*\([^)]*\)\s*", " ", first)
        words = [word for word in first.split() if word.strip(".") != last]
        if last in words:
            words = words[: words.index(last)]
        first = " ".join(words[:3])
        return f"{first} {last}".strip()
    words = re.findall(r"[A-Z][a-zA-Z'.-]{1,20}", window[-80:])
    words = [word for word in words if word.lower() not in {"group", "email", "professor", "read"}]
    if words:
        return " ".join(words[-3:])
    return email.split("@", 1)[0]


def _public_contact(contact: dict) -> dict:
    return {
        "name": contact.get("name") or "",
        "email": contact.get("email") or "",
        "department": contact.get("department") or "",
        "group": contact.get("group") or "",
        "source": contact.get("source") or "",
    }
