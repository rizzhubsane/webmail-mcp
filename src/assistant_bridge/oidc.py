from __future__ import annotations

import hashlib
import html
import json
import os
import secrets
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import certifi

from assistant_bridge.crypto import load_fernet
from assistant_bridge.errors import BridgeError

AUTHORIZE_URL = "https://auth.devclub.in/api/oauth/authorize"
TOKEN_URL = "https://auth.devclub.in/api/oauth/token"
USERINFO_URL = "https://auth.devclub.in/api/oauth/userinfo"
_PENDING_SECONDS = 600
_PUBLIC_REDIRECT = "https://webmail-mcp.ariesiitd.com/auth/callback"


def _allowed_redirect(uri: str) -> bool:
    return uri == _PUBLIC_REDIRECT or uri.startswith("http://127.0.0.1") or uri.startswith("http://localhost")

def _page(body: str) -> str:
    logo = _mark("aries")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>webmail-mcp</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,500;1,8..60,400&display=swap" rel="stylesheet">
<style>
  :root {{ color-scheme: light; --ink: #141413; --paper: #f4f1ea; --muted: #5e584e; --line: #ddd4c6; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; min-height: 100vh; background: var(--paper); color: var(--ink);
    font: 18px/1.55 "Source Serif 4", Georgia, serif; }}
  header, main {{ width: min(40rem, calc(100% - 3rem)); margin: 0 auto; }}
  header {{ display: flex; justify-content: space-between; align-items: center; padding: 1.1rem 0 0; }}
  .mark {{ font-family: "Source Serif 4", Georgia, serif; font-size: 1.2rem;
    letter-spacing: -0.03em; text-decoration: none; color: var(--ink); }}
  .mark img {{ height: 52px; width: auto; display: block; }}
  header nav {{ display: flex; gap: 1.1rem; }}
  header a.quiet {{ color: var(--muted); text-underline-offset: 0.2em; }}
  main {{ padding: 3.5rem 0 5rem; }}
  h1 {{ font-family: "Source Serif 4", Georgia, serif; font-weight: 450;
    font-size: clamp(2.1rem, 4vw, 2.8rem); line-height: 1.08; letter-spacing: -0.03em; margin: 0 0 1rem; }}
  a.action, button {{ display: inline-block; margin-top: 1.25rem; background: var(--ink); color: var(--paper);
    text-decoration: none; font-weight: 500; padding: 0.8rem 1.05rem; border-radius: 8px; border: 0;
    font: inherit; cursor: pointer; }}
  input {{ width: 100%; box-sizing: border-box; margin-top: 0.85rem; padding: 0.85rem 0.9rem;
    border-radius: 8px; border: 1px solid var(--line); background: #fbf8f2; color: var(--ink); font: inherit; }}
  input[readonly] {{ font-size: 0.92rem; }}
  .url-row {{ display: flex; gap: 0.6rem; align-items: stretch; margin-top: 0.85rem; }}
  .url-row input {{ margin-top: 0; min-width: 0; }}
  .url-row button {{ margin-top: 0; flex: none; font-family: "Public Sans", sans-serif; font-size: 14px; }}
  @media (max-width: 640px) {{
    .url-row {{ flex-direction: column; }}
    .url-row button {{ width: fit-content; }}
  }}
  p, dd {{ color: #3f3c36; }}
  dt {{ margin-top: 0.9rem; font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; color: #8a8174; }}
  dd {{ margin: 0; }}
  label {{ display: block; margin-top: 1.25rem; font-size: 0.78rem; letter-spacing: 0.08em;
    text-transform: uppercase; color: #8a8174; }}
</style>
</head>
<body>
<header>
  <a class="mark" href="/"><img alt="Aries" src="{logo}"></a>
  <nav>
    <a class="quiet" href="/setup">Setup</a>
    <a class="quiet" href="/privacy">Privacy</a>
    <a class="quiet" href="/">Home</a>
  </nav>
</header>
<main>
  {body}
</main>
</body>
</html>
"""


@dataclass
class PendingLogin:
    state: str
    verifier: str
    created: float


def _mark(name: str) -> str:
    import base64

    raw = (Path(__file__).resolve().parent / "brand" / f"{name}.png").read_bytes()
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def sign_in_page() -> str:
    return _public(
        "Introducing IITD Webmail MCP",
        """
  <p class="date">Oct 5, 2026</p>
  <h1>Introducing IITD Webmail MCP</h1>
  <p class="dek">Your IIT Delhi mail, in the assistants you already use.</p>
  __HERO__
  <p>You can already ask ChatGPT about Gmail. This does the same thing for your institute mail.</p>
  <p>Connect once and then your AI assistant can use your webmail read and write your IITD webmails.</p>

  <h2 id="use">How you connect it</h2>
  <ol class="steps">
    <li><strong>Sign in with IIT Delhi.</strong></li>
    <li><strong>Confirm your webmail password on this site.</strong> We check it with IIT Delhi mail. It is stored encrypted on our server.</li>
    <li><strong>Copy the address you get, and paste it into the assistant.</strong> Then follow the steps for the assistant you want to use.</li>
  </ol>
  <p class="go"><a class="btn" href="/auth/login">Sign in with IIT Delhi</a></p>
  <p>Each assistant has its own screen. <a href="/setup">The setup page</a> has the steps for ChatGPT, Claude, and Gemini.</p>

  <section class="related">
    <h2>Set it up</h2>
    <div class="cards">
      <a href="/setup#chatgpt"><strong>ChatGPT</strong><span>Settings, then Apps. Paste the address. Leave auth off.</span></a>
      <a href="/setup#claude"><strong>Claude</strong><span>Add a connector. Choose Streamable HTTP.</span></a>
      <a href="/setup#gemini"><strong>Gemini</strong><span>Add an MCP server. Same address.</span></a>
    </div>
    __PROMPT_BOX__
  </section>
  <h2>Run it yourself</h2>
  <p>This site stores your mail password encrypted on our server. If you don't want that, the code is open source. Run it on your own computer and the password stays there.</p>
  <p><a href="/self-host">How to self-host</a> · <a href="/privacy">Privacy</a> · <a href="https://github.com/rizzhubsane/webmail-mcp">GitHub</a></p>
""",
    )


def docs_page() -> str:
    return _public(
        "Set up webmail-mcp",
        """
  <h1>Set up webmail-mcp</h1>
  <p class="dek">Get your address here, then paste it into the assistant you use.</p>
  <nav class="jump">
    <a href="#address">Get the address</a>
    <a href="#chatgpt">ChatGPT</a>
    <a href="#claude">Claude</a>
    <a href="#gemini">Gemini</a>
    <a href="#other">Another assistant</a>
    <a href="#asking">What to ask</a>
  </nav>

  <h2 id="address">Get the address</h2>
  <p>Do this here first. Then open the assistant.</p>
  <ol class="steps">
    <li><strong>Sign in with IIT Delhi.</strong> <a href="/auth/login">Sign in with IIT Delhi</a>.</li>
    <li><strong>Confirm your webmail password on the next page.</strong> We check it with IIT Delhi mail and store it encrypted on our server. Type it here, not in a chat.</li>
    <li><strong>Copy the address.</strong> It’s one line, like <span class="name">https://webmail-mcp.ariesiitd.com<wbr>/mcp?token=…</span> Click the box so the whole line is selected. If it splits onto two lines when you paste it, the assistant won’t take it. Don’t share this address. It opens your mail.</li>
  </ol>

  <div class="faq">
    <div class="faq-item open" id="chatgpt">
      <button class="faq-q" type="button" aria-expanded="true">
        <span class="faq-n">01</span><span class="faq-title">ChatGPT</span><span class="faq-icon" aria-hidden="true"></span>
      </button>
      <div class="faq-a"><div class="faq-in">
        <ol class="steps">
          <li>Open ChatGPT. Go to <strong>Settings</strong>, then <strong>Apps</strong>. Some accounts still call this Plugins. Make a new one.</li>
          <li>Name it <strong>webmail-mcp</strong>. Paste the whole address where it asks for a server URL. Set authentication to <strong>No Auth</strong>. Don’t add a password here. The address already logs you in.</li>
          <li>Save, then open a <strong>new chat</strong>. A chat that was already open won’t see it.</li>
          <li>Try “What came in today?”</li>
        </ol>
      </div></div>
    </div>
    <div class="faq-item" id="claude">
      <button class="faq-q" type="button" aria-expanded="false">
        <span class="faq-n">02</span><span class="faq-title">Claude</span><span class="faq-icon" aria-hidden="true"></span>
      </button>
      <div class="faq-a"><div class="faq-in">
        <ol class="steps">
          <li>On claude.ai, open <strong>Customize</strong>, then <strong>Connectors</strong>, and add a custom connector.</li>
          <li>Choose <strong>Streamable HTTP</strong>. Paste the whole address, including the <span class="name">?token=</span> part. Leave sign-in off.</li>
          <li>If Claude warns you about sign-in, choose <strong>Add anyway</strong>. Claude’s own sign-in will not open your mail.</li>
          <li>Open a new chat and ask.</li>
        </ol>
      </div></div>
    </div>
    <div class="faq-item" id="gemini">
      <button class="faq-q" type="button" aria-expanded="false">
        <span class="faq-n">03</span><span class="faq-title">Gemini</span><span class="faq-icon" aria-hidden="true"></span>
      </button>
      <div class="faq-a"><div class="faq-in">
        <ol class="steps">
          <li>In Gemini, add a custom MCP server if you see that option.</li>
          <li>Paste the same address. There’s no extra sign-in.</li>
          <li>Open a new chat and ask.</li>
        </ol>
      </div></div>
    </div>
    <div class="faq-item" id="other">
      <button class="faq-q" type="button" aria-expanded="false">
        <span class="faq-n">04</span><span class="faq-title">Another assistant</span><span class="faq-icon" aria-hidden="true"></span>
      </button>
      <div class="faq-a"><div class="faq-in">
        <p>If yours isn’t listed above, copy this and paste it into that assistant. It will tell you the clicks for that app.</p>
        __PROMPT_BOX__
      </div></div>
    </div>
    <div class="faq-item" id="asking">
      <button class="faq-q" type="button" aria-expanded="false">
        <span class="faq-n">05</span><span class="faq-title">What to ask</span><span class="faq-icon" aria-hidden="true"></span>
      </button>
      <div class="faq-a"><div class="faq-in">
        <p>Just talk normally.</p>
        <ul class="asks">
          <li>What came in this morning?</li>
          <li>Find the email from the department about the exam.</li>
          <li>Find the mail I sent about the medical certificate.</li>
          <li>What deadlines are in my recent mail?</li>
          <li>Draft a polite reminder about the exam date.</li>
          <li>Send it.</li>
        </ul>
        <p>Read the draft before you say send. Saying send is what actually sends it, from your IIT Delhi account.</p>
        <p>Mail you got is in the inbox. Mail you sent is in Sent. If you’re looking for something you wrote, say you sent it. It can read a PDF that came with a mail. It won’t attach a password file or a key.</p>
        <p class="note">Your webmail password stays on this site. What you paste into the assistant is the address. If that address gets out, stop using it. Don’t post it, and don’t send it to anyone.</p>
      </div></div>
    </div>
  </div>
""",
    )


def guide_page() -> str:
    return _public(
        "Tools",
        """
  <h1>What the assistant can do</h1>
  <p class="dek">These are the tools behind the chat. You don't call them yourself. The assistant does, when you ask in plain language.</p>
  <div class="faq">
    <div class="faq-item open" id="read">
      <button class="faq-q" type="button" aria-expanded="true"><span class="faq-n">01</span><span class="faq-title">Read mail you received</span><span class="faq-icon" aria-hidden="true"></span></button>
      <div class="faq-a"><div class="faq-in">
        <p>Ask what came in, open one message, or get a short summary.</p>
        <ul class="asks">
          <li><strong>latest_brief</strong> Newest mail, with a short preview.</li>
          <li><strong>list_inbox</strong> A list from the inbox, or another folder you name.</li>
          <li><strong>search_inbox</strong> Search mail you received. This does not look in Sent.</li>
          <li><strong>get_message</strong> Read one message.</li>
          <li><strong>summarize_message</strong> A short summary: what it says, what it asks, any real deadline.</li>
          <li><strong>list_folders</strong> The folders in the mailbox. Nothing is deleted.</li>
          <li><strong>sync_inbox</strong> and <strong>get_briefing</strong> What is new since last time.</li>
        </ul>
      </div></div>
    </div>
    <div class="faq-item" id="sent">
      <button class="faq-q" type="button" aria-expanded="false"><span class="faq-n">02</span><span class="faq-title">Find mail you sent</span><span class="faq-icon" aria-hidden="true"></span></button>
      <div class="faq-a"><div class="faq-in">
        <p><strong>search_sent</strong> looks in Sent. Use it when you say you wrote the mail, or you want a draft based on something you already sent. The inbox will not have that message.</p>
      </div></div>
    </div>
    <div class="faq-item" id="draft">
      <button class="faq-q" type="button" aria-expanded="false"><span class="faq-n">03</span><span class="faq-title">Draft</span><span class="faq-icon" aria-hidden="true"></span></button>
      <div class="faq-a"><div class="faq-in">
        <p>Drafts stay on the server until you say send.</p>
        <ul class="asks">
          <li><strong>compose_mail</strong> A new mail. It does not send.</li>
          <li><strong>outline_reply</strong> and <strong>save_draft</strong> A reply to a message you already have.</li>
          <li><strong>list_drafts</strong> Drafts saved so far.</li>
          <li><strong>check_text</strong> Catches leftover placeholders. A short note like “Hi” is allowed when you are sending it.</li>
        </ul>
      </div></div>
    </div>
    <div class="faq-item" id="send">
      <button class="faq-q" type="button" aria-expanded="false"><span class="faq-n">04</span><span class="faq-title">Send</span><span class="faq-icon" aria-hidden="true"></span></button>
      <div class="faq-a"><div class="faq-in">
        <p><strong>request_send</strong> and <strong>send_now</strong> send the draft when you say send. Read the draft first. There is no extra code to type.</p>
      </div></div>
    </div>
    <div class="faq-item" id="people">
      <button class="faq-q" type="button" aria-expanded="false"><span class="faq-n">05</span><span class="faq-title">People</span><span class="faq-icon" aria-hidden="true"></span></button>
      <div class="faq-a"><div class="faq-in">
        <p>Say “mail Professor Prashanth in geotech” and it can look up the address.</p>
        <ul class="asks">
          <li><strong>resolve_person</strong> A name and department, from a local name book.</li>
          <li><strong>refresh_directory</strong> Fills that book from public IIT Delhi faculty pages. It does not use your mail password.</li>
          <li><strong>learn_contacts</strong> Remembers people already in your mail.</li>
        </ul>
      </div></div>
    </div>
    <div class="faq-item" id="thread">
      <button class="faq-q" type="button" aria-expanded="false"><span class="faq-n">06</span><span class="faq-title">A thread, a follow-up, a file</span><span class="faq-icon" aria-hidden="true"></span></button>
      <div class="faq-a"><div class="faq-in">
        <ul class="asks">
          <li><strong>get_thread</strong> One conversation, including what you sent back.</li>
          <li><strong>list_followups</strong> Mail you sent with no reply after a few days, and deadlines due tomorrow.</li>
          <li><strong>read_attachment</strong> Text from a PDF or a text file on a message.</li>
        </ul>
        <p>It will not attach a password file or a key.</p>
      </div></div>
    </div>
    <div class="faq-item" id="calendar">
      <button class="faq-q" type="button" aria-expanded="false"><span class="faq-n">07</span><span class="faq-title">Calendar</span><span class="faq-icon" aria-hidden="true"></span></button>
      <div class="faq-a"><div class="faq-in">
        <p><strong>extract_event</strong> pulls a date, place, and link out of a message. It does not add anything yet.</p>
        <p><strong>add_calendar_event</strong> runs only when you ask. It writes a calendar file and a Google Calendar link. Adding it straight into the Mac Calendar app works when you run this on your own Mac.</p>
      </div></div>
    </div>
    <div class="faq-item" id="log">
      <button class="faq-q" type="button" aria-expanded="false"><span class="faq-n">08</span><span class="faq-title">What already happened</span><span class="faq-icon" aria-hidden="true"></span></button>
      <div class="faq-a"><div class="faq-in">
        <p><strong>list_actions</strong> and <strong>get_action</strong> show replies and sends. <strong>list_audit</strong> is the server log of those actions. The log does not keep your password or the raw token.</p>
      </div></div>
    </div>
  </div>
""",
    )


def privacy_page() -> str:
    return _public(
        "Privacy",
        """
  <p class="date">Oct 5, 2026</p>
  <h1>Privacy</h1>
  <p class="dek">What the hosted site keeps, and what it never sees.</p>
  <p>This applies to <span class="name">webmail-mcp.ariesiitd.com</span>. webmail-mcp is an independent student project. It is not an official IIT Delhi service.</p>
  <h2>Sign in</h2>
  <p>DevClub tells us your Kerberos id, and the name and email on that login. That step does not include your webmail password.</p>
  <h2>The mail password</h2>
  <p>You type it on this site. We check it with IIT Delhi mail. A wrong password is not saved. A right one is stored encrypted on our server, for that Kerberos id. The assistant never sees it. Don't type it into ChatGPT, Claude, Gemini, Cursor, or any other chat.</p>
  <h2>The address</h2>
  <p>After mail is connected, you get one private URL. Anyone with that URL can read and send your mail. Don't put it in a group chat, a screenshot, or a repository. We keep a hash of the token. The page shows the full URL once, so you can copy it.</p>
  <h2>Your mail</h2>
  <p>The assistant can read mail, search what you sent, draft, and send when you say send. It can read a PDF that came with a message. It will not attach a password file or a key. We don't sell your mail, and we don't use it to train a model.</p>
  <h2>Logs</h2>
  <p>The server keeps a log of actions, including sends, so they can be looked up later. That log does not store your password or the raw token.</p>
  <h2>If you don't want us to hold the password</h2>
  <p>Run it yourself. The code is open source. On your own computer, the password stays there. <a href="/self-host">How to self-host</a>.</p>
""",
    )


def self_host_page() -> str:
    return _public(
        "Self-host",
        """
  <h1>Run it yourself</h1>
  <p class="dek">Same tool. Your machine. The mail password never goes to our server.</p>
  <p>The hosted site stores the password encrypted so ChatGPT can reach your mail without you leaving a laptop on. If you don't want that, clone the repo and run it locally.</p>
  <p><a class="btn" href="https://github.com/rizzhubsane/webmail-mcp">GitHub</a></p>
  <h2>On your computer</h2>
  <ol class="steps">
    <li>Install Python 3.11 or newer.</li>
    <li>Clone <span class="name">https://github.com/rizzhubsane/webmail-mcp</span> and install it.</li>
  </ol>
  <pre>git clone https://github.com/rizzhubsane/webmail-mcp
cd webmail-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
webmail-mcp init
webmail-mcp connector iitd --username YOUR_KERBEROS_ID
webmail-mcp enable-send
webmail-mcp token create --name mine --scope read --scope draft --scope send</pre>
  <p>The password prompt reads the keyboard. It is encrypted before it is written. Nothing in that step is sent to webmail-mcp.ariesiitd.com.</p>
  <p>Then point your assistant at the local server. A stdio config looks like this. Use your own paths and the token the command just printed.</p>
  <pre>{
  "mcpServers": {
    "webmail-mcp": {
      "command": "/path/to/.venv/bin/webmail-mcp",
      "args": ["mcp"],
      "env": {
        "ASSISTANT_BRIDGE_HOME": "/path/to/assistant-bridge",
        "ASSISTANT_BRIDGE_TOKEN": "PASTE_TOKEN"
      }
    }
  }
}</pre>
  <p>The data directory is <span class="name">~/.assistant-bridge</span> unless you set <span class="name">ASSISTANT_BRIDGE_HOME</span>. It should stay private. Don't commit <span class="name">master.key</span>, <span class="name">vault.json</span>, <span class="name">accounts.json</span>, or <span class="name">oidc.json</span>.</p>
  <p>The license is MIT. Issues and pull requests are welcome on the repo. Don't paste a token or a mail password into an issue.</p>
""",
    )


def _public(title: str, article: str) -> str:
    page = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,560;1,8..60,400&display=swap" rel="stylesheet">
<style>
  :root { color-scheme: light; --ink: #141413; --paper: #faf9f5; --muted: #6b6862; --line: #e6e1d6; --clay: #e7a394; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--paper); color: var(--ink);
    font: 20px/1.55 "Source Serif 4", "Iowan Old Style", Georgia, serif; }
  a { color: inherit; }
  .bar { position: sticky; top: 0; z-index: 5; background: var(--paper);
    font-family: "Public Sans", "Avenir Next", sans-serif; font-size: 14px; }
  .bar-inner, footer .inner { width: min(1080px, calc(100% - 40px)); margin: 0 auto;
    display: flex; align-items: center; justify-content: space-between; min-height: 64px; gap: 1.5rem; }
  .mark { font-family: "Source Serif 4", Georgia, serif; font-size: 22px; letter-spacing: -0.03em; text-decoration: none; }
  .bar .mark { display: flex; align-items: center; line-height: 0; }
  .bar .mark img { height: 58px; width: auto; display: block; }
  .links { display: flex; gap: 1.4rem; }
  .links a { text-decoration: none; }
  .btn { display: inline-block; background: var(--ink); color: var(--paper); text-decoration: none;
    font-family: "Public Sans", sans-serif; font-size: 14px; font-weight: 500;
    padding: 0.55rem 0.9rem; border-radius: 8px; }
  article { width: min(720px, calc(100% - 40px)); margin: 0 auto; padding-bottom: 4.5rem; }
  .date { margin: 2.6rem 0 0.7rem; font-family: "Public Sans", sans-serif; font-size: 14px; color: var(--muted); }
  article > h1:first-child { margin-top: 2.6rem; }
  h1 { margin: 0 0 0.75rem; font-weight: 460; font-size: clamp(2.6rem, 5vw, 3.5rem);
    line-height: 1.05; letter-spacing: -0.03em; }
  .dek { margin: 0 0 1.6rem; color: #3f3c36; font-size: 1.2rem; line-height: 1.4; }
  .asks { list-style: none; margin: 0.2rem 0 1.3rem; padding: 0; }
  .asks li { margin: 0; padding: 0.72rem 0; border-top: 1px solid var(--line); font-style: italic; }
  .asks li:last-child { border-bottom: 1px solid var(--line); }
  ol.steps { list-style: none; margin: 0.3rem 0 1.3rem; padding: 0; counter-reset: step; }
  ol.steps li { counter-increment: step; position: relative; margin: 0 0 1.05rem; padding: 0.05rem 0 0 2.55rem; }
  ol.steps li::before { content: counter(step); position: absolute; left: 0; top: 0.22rem;
    width: 1.55rem; height: 1.55rem; border-radius: 999px; border: 1px solid var(--line);
    font-family: "Public Sans", sans-serif; font-size: 12px; font-style: normal;
    display: grid; place-items: center; }
  .jump { display: flex; flex-wrap: wrap; gap: 0.45rem 1.15rem; margin: 0 0 0.2rem;
    font-family: "Public Sans", sans-serif; font-size: 14px; }
  .jump a { color: var(--muted); text-decoration: none; }
  .name { overflow-wrap: anywhere; }
  .note { margin-top: 1.4rem; padding: 0.1rem 0 0.1rem 1rem; border-left: 2px solid var(--ink); }
  .hero { background: var(--clay); border-radius: 12px; margin: 0 0 1.8rem; min-height: 280px;
    display: flex; align-items: center; justify-content: center; gap: 0.4rem; padding: 2rem 1.6rem; }
  .hero .seal { height: 148px; width: auto; }
  .hero .wires { width: 132px; height: 168px; flex: none; }
  .hero .wires .base { stroke: rgba(250,249,245,0.45); stroke-width: 1.5; fill: none; }
  .hero .wires .flow { fill: none; stroke: #faf9f5; stroke-width: 2.4; stroke-linecap: round;
    stroke-dasharray: 28 110; stroke-dashoffset: 138; animation: hero-flow 3.2s linear infinite; }
  .hero .wires .d2 { animation-delay: .7s; }
  .hero .wires .d3 { animation-delay: 1.4s; }
  .hero .wires circle { fill: #faf9f5; }
  @keyframes hero-flow { to { stroke-dashoffset: -138; } }
  @media (prefers-reduced-motion: reduce) {
    .hero .wires .flow { animation: none; stroke-dasharray: none; }
  }
  .hero .marks { display: flex; flex-direction: column; justify-content: space-between; height: 168px; }
  .hero .marks img { height: 34px; width: auto; display: block; }
  h2 { margin: 2.4rem 0 0.7rem; font-weight: 460; font-size: 2rem; letter-spacing: -0.03em; line-height: 1.15; scroll-margin-top: 5rem; }
  .faq { margin: 2.4rem 0 0.4rem; border-top: 1px solid var(--line); }
  .faq-item { border-bottom: 1px solid var(--line); scroll-margin-top: 5rem; }
  .faq-q { width: 100%; display: flex; align-items: center; gap: 0.9rem; padding: 1.05rem 0.15rem;
    background: none; border: 0; color: inherit; font: inherit; text-align: left; cursor: pointer; }
  .faq-n { flex: none; width: 2.05rem; height: 2.05rem; border-radius: 999px; display: grid; place-items: center;
    font-family: "Public Sans", sans-serif; font-size: 13px; color: var(--muted); }
  .faq-item.open .faq-n { background: var(--ink); color: var(--paper); }
  .faq-title { flex: 1; font-size: 1.45rem; letter-spacing: -0.03em; }
  .faq-icon { flex: none; width: 1.2rem; text-align: center; font-size: 1.45rem; font-weight: 300; line-height: 1; }
  .faq-item.open .faq-icon::before { content: "×"; }
  .faq-item:not(.open) .faq-icon::before { content: "+"; }
  .faq-a { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 0.4s ease; }
  .faq-item.open .faq-a { grid-template-rows: 1fr; }
  .faq-in { overflow: hidden; }
  .faq-in > :first-child { margin-top: 0.15rem; }
  .faq-in > :last-child { margin-bottom: 1.15rem; }
  @media (prefers-reduced-motion: reduce) { .faq-a { transition: none; } }
  p { margin: 0 0 1rem; }
  ol { margin: 0.2rem 0 1.2rem; padding-left: 1.3rem; }
  li { margin: 0 0 0.85rem; padding-left: 0.25rem; }
  li strong { font-weight: 560; }
  .go { margin: 1.4rem 0 0.4rem; text-align: center; }
  pre { margin: 1rem 0 1.2rem; padding: 1rem 1.1rem; background: #fff; border: 1px solid var(--line);
    border-radius: 10px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere;
    font: 14px/1.5 ui-monospace, "SF Mono", monospace; }
  .related { margin-top: 3.5rem; padding-top: 1.4rem; border-top: 1px solid var(--line); }
  .related h2 { margin-top: 0; }
  .cards { display: grid; grid-template-columns: 1fr 1fr; gap: 1.4rem 1.6rem; }
  .cards a { text-decoration: none; }
  .cards strong { display: block; font-size: 1.15rem; font-weight: 560; margin-bottom: 0.35rem;
    text-decoration: underline; text-underline-offset: 0.18em; }
  .ask-ai { margin-top: 1.6rem; }
  button.btn { border: 0; cursor: pointer; }
  .ask-ai p { margin: 0.75rem 0 0; }
  .cards span { display: block; color: #3f3c36; font-size: 1rem; line-height: 1.45; }
  footer { background: #141413; color: #d7d3cb; font-family: "Public Sans", sans-serif; font-size: 14px; line-height: 1.5; }
  footer .inner { align-items: flex-start; padding: 2.6rem 0 2.2rem; display: grid;
    grid-template-columns: 1.4fr 1fr 1fr 1.3fr; gap: 1.5rem; }
  footer a { color: #d7d3cb; text-decoration: none; }
  footer strong { display: block; color: #fff; font-weight: 500; margin-bottom: 0.7rem; }
  footer p, footer li { margin: 0 0 0.4rem; }
  footer ul { list-style: none; margin: 0; padding: 0; }
  .fine { width: min(1080px, calc(100% - 40px)); margin: 0 auto; padding: 0 0 1.6rem; color: #8d897f; }
  @media (max-width: 800px) {
    .links a:first-child { display: none; }
    .bar-inner { flex-wrap: nowrap; min-height: 56px; }
    .bar .mark img { height: 46px; }
    .cards, footer .inner { grid-template-columns: 1fr; }
    article { width: min(720px, calc(100% - 32px)); }
    .hero { min-height: 0; padding: 1.3rem 0.7rem; }
    .hero .seal { height: 92px; }
    .hero .wires { width: 56px; height: 112px; }
    .hero .marks { height: 112px; }
    .hero .marks img { height: 22px; }
  }
</style>
</head>
<body>
<header class="bar">
  <div class="bar-inner">
    <a class="mark" href="/"><img alt="Aries" src="__ARIES__"></a>
    <nav class="links">
      <a href="/#use">How it works</a>
      <a href="/setup">Setup</a>
      <a href="/guide">Tools</a>
    </nav>
    <a class="btn" href="/auth/login">Sign in</a>
  </div>
</header>
<article>
__ARTICLE__
</article>
<footer>
  <div class="inner">
    <div>
      <a class="mark" href="/" style="color:#faf9f5">webmail-mcp</a>
      <p style="margin-top:0.8rem">IIT Delhi mail, for the assistants you already use.</p>
    </div>
    <div>
      <strong>Set up</strong>
      <ul>
        <li><a href="/auth/login">Sign in with IIT Delhi</a></li>
        <li><a href="/setup">Setup</a></li>
        <li><a href="/guide">Tools</a></li>
        <li><a href="/privacy">Privacy</a></li>
        <li><a href="/self-host">Self-host</a></li>
        <li><a href="https://github.com/rizzhubsane/webmail-mcp">GitHub</a></li>
      </ul>
    </div>
    <div>
      <strong>Assistants</strong>
      <ul>
        <li><a href="/setup#chatgpt">ChatGPT</a></li>
        <li><a href="/setup#claude">Claude</a></li>
        <li><a href="/setup#gemini">Gemini</a></li>
      </ul>
    </div>
    <div>
      <strong>Keep in mind</strong>
      <ul>
        <li>One address, one mailbox</li>
        <li>The password stays encrypted</li>
        <li>When you say send, it sends</li>
      </ul>
    </div>
  </div>
  <p class="fine">webmail-mcp · IIT Delhi</p>
</footer>
<script>
function setFaq(item, open) {
  item.classList.toggle("open", open);
  var button = item.querySelector(".faq-q");
  if (button) button.setAttribute("aria-expanded", open ? "true" : "false");
}
document.querySelectorAll(".faq-item").forEach(function (item) {
  item.querySelector(".faq-q").addEventListener("click", function () {
    var willOpen = !item.classList.contains("open");
    document.querySelectorAll(".faq-item").forEach(function (other) { setFaq(other, false); });
    setFaq(item, willOpen);
  });
});
function openFaqFromHash() {
  var id = location.hash.replace("#", "");
  if (!id) return;
  var item = document.getElementById(id);
  if (!item || !item.classList.contains("faq-item")) return;
  document.querySelectorAll(".faq-item").forEach(function (other) { setFaq(other, other === item); });
}
openFaqFromHash();
window.addEventListener("hashchange", openFaqFromHash);
document.querySelectorAll("[data-copy-prompt]").forEach(function (button) {
  button.addEventListener("click", function () {
    var text = button.parentElement.querySelector(".prompt").textContent;
    var note = button.parentElement.querySelector(".copied");
    function done() { note.textContent = "Copied."; }
    function fallback() {
      var area = document.createElement("textarea");
      area.value = text;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
      done();
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(fallback);
    } else {
      fallback();
    }
  });
});
</script>
</body>
</html>
"""
    page = page.replace("__TITLE__", html.escape(title))
    page = page.replace("__ARTICLE__", article).replace("__HERO__", _HERO).replace("__PROMPT_BOX__", _PROMPT_BOX)
    page = page.replace("__PROMPT__", html.escape(_SETUP_PROMPT))
    for token, name in (("__IITD__", "iitd"), ("__CHATGPT__", "chatgpt"), ("__CLAUDE__", "claude"), ("__GEMINI__", "gemini"), ("__ARIES__", "aries")):
        page = page.replace(token, _mark(name))
    return page


_HERO = """<div class="hero">
    <img class="seal" alt="Indian Institute of Technology Delhi" src="__IITD__">
    <svg class="wires" viewBox="0 0 140 180" aria-hidden="true">
      <path class="base" d="M0 90 H66"/>
      <path class="base" d="M66 90 L140 22"/>
      <path class="base" d="M66 90 H140"/>
      <path class="base" d="M66 90 L140 158"/>
      <path class="flow" d="M0 90 H66 L140 22"/>
      <path class="flow d2" d="M0 90 H140"/>
      <path class="flow d3" d="M0 90 H66 L140 158"/>
      <circle cx="66" cy="90" r="4"/>
    </svg>
    <div class="marks">
      <img alt="ChatGPT" src="__CHATGPT__">
      <img alt="Claude" src="__CLAUDE__">
      <img alt="Gemini" src="__GEMINI__">
    </div>
  </div>"""


_SETUP_PROMPT = """Help me add webmail-mcp to this app.

webmail-mcp lets you read and send my IIT Delhi mail. I will have a private address that looks like this:

https://webmail-mcp.ariesiitd.com/mcp?token=YOUR_TOKEN

I get that address by opening https://webmail-mcp.ariesiitd.com/, signing in with IIT Delhi, and confirming my webmail password on that site. The site checks the password with IIT Delhi mail and stores it encrypted. I must not type that password into this chat.

To add it:
- It is a remote MCP server, over Streamable HTTP.
- The server URL is the whole address, on one line.
- Authentication is No Auth. The token in the address is the login. Don't ask me for another password.
- If this app warns that the server wanted a sign-in, add it anyway.
- After it is added, I should start a new chat.

Tell me the exact clicks in this app. If you are not sure of a menu name, say so.

Once it is connected I can ask about mail I received and mail I sent. When I say send, and I have already read the draft, it sends from my IIT Delhi account. Don't share my address with anyone."""

_PROMPT_BOX = """<div class="ask-ai">
  <button type="button" class="btn" data-copy-prompt>Copy Setup Prompt</button>
  <p class="copied">You can ask your own AI for setup instructions.</p>
  <pre class="prompt" hidden>__PROMPT__</pre>
</div>"""


def load_client(home: Path) -> dict:
    path = home / "oidc.json"
    if not path.exists():
        return _client_from_env()
    data = json.loads(path.read_text(encoding="utf-8"))
    secret_token = data.get("client_secret") or ""
    if not secret_token:
        raise BridgeError("IIT Delhi login is not configured on this machine", status=503)
    secret = load_fernet(home).decrypt(secret_token.encode()).decode()
    redirects = [item for item in data.get("redirect_uris") or [] if _allowed_redirect(item)]
    if not redirects:
        raise BridgeError("no approved local login return address", status=503)
    scopes = data.get("scopes") or ["openid", "profile", "email", "iitd"]
    return {
        "client_id": data["client_id"],
        "client_secret": secret,
        "redirect_uris": redirects,
        "scope": " ".join(scopes),
    }


def _client_from_env() -> dict:
    client_id = os.environ.get("OIDC_CLIENT_ID", "").strip()
    client_secret = os.environ.get("OIDC_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise BridgeError("IIT Delhi login is not configured on this machine", status=503)
    redirects = [
        item.strip()
        for item in os.environ.get(
            "OIDC_REDIRECT_URIS",
            f"{_PUBLIC_REDIRECT},http://127.0.0.1:8765/auth/callback,http://localhost:8765/auth/callback",
        ).split(",")
        if item.strip() and _allowed_redirect(item.strip())
    ]
    if not redirects:
        raise BridgeError("no approved local login return address", status=503)
    scopes = os.environ.get("OIDC_SCOPES", "openid profile email phone iitd").split()
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uris": redirects,
        "scope": " ".join(scopes),
    }


def redirect_for(hostname: str | None, redirect_uris: list[str]) -> str:
    host = (hostname or "").split(":")[0].lower()
    for uri in redirect_uris:
        if urllib.parse.urlparse(uri).hostname == host:
            return uri
    public = os.environ.get("ASSISTANT_BRIDGE_PUBLIC_URL", "").strip().rstrip("/")
    if public:
        candidate = f"{public}/auth/callback"
        if candidate in redirect_uris:
            return candidate
    raise BridgeError("open http://127.0.0.1:8765 to sign in", status=400)


def new_login() -> PendingLogin:
    return PendingLogin(state=secrets.token_urlsafe(24), verifier=secrets.token_urlsafe(64), created=time.time())


def take_login(pending: dict[str, PendingLogin], state: str) -> PendingLogin:
    login = pending.pop(state, None)
    if login is None or time.time() - login.created > _PENDING_SECONDS:
        raise BridgeError("sign-in expired; start again", status=400)
    return login


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return _b64(digest)


def _b64(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def authorize_url(client: dict, redirect_uri: str, login: PendingLogin) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "scope": client["scope"],
            "state": login.state,
            "code_challenge": code_challenge(login.verifier),
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def finish_login(client: dict, redirect_uri: str, code: str, verifier: str) -> dict:
    token = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "code_verifier": verifier,
        },
    )
    access = token.get("access_token")
    if not access:
        raise BridgeError("IIT Delhi login did not return an access token", status=502)
    info = _get_json(USERINFO_URL, {"Authorization": f"Bearer {access}"})
    return public_profile(info)


def public_profile(info: dict) -> dict:
    keys = ("sub", "name", "email", "kerberos", "entry_number", "department", "hostel")
    return {key: str(info.get(key) or "") for key in keys}


def mail_form(session: dict, message: str = "") -> str:
    name = html.escape(session.get("name") or session["kerberos"])
    kerberos = html.escape(session["kerberos"])
    notice = f"<p>{html.escape(message)}</p>" if message else ""
    return _page(
        f"<h1>Connect mail</h1>"
        f"<p>Signed in as {name} ({kerberos}).</p>"
        "<p>Type your webmail password. We check it with IIT Delhi mail, then store it encrypted on our server. Type it here, not in a chat.</p>"
        f"{notice}"
        '<form method="post" action="/auth/mail">'
        '<label for="mail-password">Mail password</label>'
        '<input id="mail-password" type="password" name="password" autocomplete="current-password" required autofocus>'
        '<button type="submit">Connect mail</button>'
        "</form>"
    )


def connected_page(session: dict, connector_url: str | None) -> str:
    kerberos = html.escape(session["kerberos"])
    if connector_url:
        shown = html.escape(connector_url, quote=True)
        extra = (
            "<p>Paste this whole address into the assistant. It has to start with https. "
            "The steps for ChatGPT, Claude, and Gemini are on the <a href=\"/setup\">setup page</a>.</p>"
            '<label for="connector-url">Server URL</label>'
            '<div class="url-row">'
            f'<input id="connector-url" readonly value="{shown}" onclick="this.select()">'
            '<button type="button" id="copy-url">Copy URL</button>'
            "</div>"
            "<script>"
            "document.getElementById('copy-url').addEventListener('click', function () {"
            "  var input = document.getElementById('connector-url');"
            "  var button = this;"
            "  function done() { button.textContent = 'Copied'; }"
            "  function fallback() {"
            "    input.select();"
            "    document.execCommand('copy');"
            "    done();"
            "  }"
            "  if (navigator.clipboard && navigator.clipboard.writeText) {"
            "    navigator.clipboard.writeText(input.value).then(done).catch(fallback);"
            "  } else { fallback(); }"
            "});"
            "</script>"
        )
    else:
        extra = "<p>Mail is already connected for this account.</p>"
    return _page(f"<h1>Mail is connected</h1><p>Signed in as {kerberos}.</p>{extra}")


def result_page(profile: dict) -> str:
    rows = "".join(
        f"<dt>{html.escape(label)}</dt><dd>{html.escape(profile.get(key) or '—')}</dd>"
        for label, key in (
            ("Name", "name"),
            ("Email", "email"),
            ("Kerberos", "kerberos"),
            ("Entry number", "entry_number"),
            ("Department", "department"),
            ("Hostel", "hostel"),
        )
    )
    return _page(f"<p>Signed in.</p><dl>{rows}</dl>")


def error_page(message: str) -> str:
    return _page(f"<p>{html.escape(message)}</p>" '<a class="action" href="/auth/login">Try again</a>')


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _post_form(url: str, fields: dict[str, str]) -> dict:
    body = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("Accept", "application/json")
    return _read_json(request)


def _get_json(url: str, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)
    return _read_json(request)


def _read_json(request: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=20, context=_ssl_context()) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise BridgeError(f"IIT Delhi login failed ({exc.code})", status=502) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise BridgeError("IIT Delhi login could not be completed", status=502) from exc
    if not isinstance(payload, dict):
        raise BridgeError("IIT Delhi login returned an unexpected response", status=502)
    if payload.get("error"):
        raise BridgeError("IIT Delhi login was rejected", status=502)
    return payload
