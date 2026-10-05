# webmail-mcp

Your IIT Delhi mailbox, in the AI assistant you already use.

Connect once. ChatGPT, Claude, Gemini, Cursor, or any other MCP client can read your institute mail and send from it. The mail password stays on the machine that runs this server. The assistant only gets a token.

The hosted site is [webmail-mcp.ariesiitd.com](https://webmail-mcp.ariesiitd.com/). This repository is the same program, for people who want to run it themselves.

MIT licensed.

## Self-host

You need Python 3.11 or newer.

```bash
git clone https://github.com/rizzhubsane/webmail-mcp
cd webmail-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
webmail-mcp init
webmail-mcp connector iitd --username YOUR_KERBEROS_ID
webmail-mcp enable-send
webmail-mcp token create --name mine --scope read --scope draft --scope send
```

`connector iitd` asks for the webmail password in the terminal. It checks the password with IIT Delhi mail, then stores it encrypted under `~/.assistant-bridge`. That directory is mode `0700`. `master.key` is mode `0600`. The token is printed once. The disk keeps a hash of it.

Do not type the mail password into a chat. Do not commit `master.key`, `vault.json`, `accounts.json`, `oidc.json`, or a token.

Point the assistant at the local process. An example is in [`config/mcp.example.json`](config/mcp.example.json).

```bash
webmail-mcp mcp
```

`webmail-mcp serve` listens on `127.0.0.1` only. Binding `0.0.0.0` is refused unless the process is running as an Azure web app.

A demo inbox needs no mail password:

```bash
webmail-mcp init
webmail-mcp connector mock
webmail-mcp token create --name demo --scope read --scope draft
```

## What it can do

The assistant calls these tools. You talk normally.

| You ask | Tool |
| --- | --- |
| What came in? | `latest_brief`, `list_inbox`, `search_inbox`, `get_message`, `summarize_message` |
| Find the mail I sent | `search_sent` |
| Draft a reply | `compose_mail`, `outline_reply`, `save_draft`, `check_text` |
| Send it | `request_send`, `send_now` |
| Mail this professor | `resolve_person`, `refresh_directory`, `learn_contacts` |
| Show the thread | `get_thread`, `list_followups`, `read_attachment` |
| Put it on my calendar | `extract_event`, `add_calendar_event` |

When you say send, it sends. A leftover `[placeholder]` still blocks the send. Adding an event to the Mac Calendar app works on a Mac. Everywhere else you get an `.ics` file and a Google Calendar link.

`read` can look at mail. `draft` can write a draft. `send` can transmit it. Create the token with only the scopes you want.

## Tests

```bash
pytest
```

The tests do not contact IIT Delhi mail servers.

## Privacy

On the hosted site, the mail password is stored encrypted for your Kerberos id. DevClub sign-in confirms who you are. It does not hand over the mail password. The assistant never receives the password.

If you self-host, that password stays on your computer.

Details for the hosted site are at [webmail-mcp.ariesiitd.com/privacy](https://webmail-mcp.ariesiitd.com/privacy).
