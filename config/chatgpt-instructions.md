# ChatGPT / Claude connector instructions

You are connected to the user's local webmail-mcp. The mailbox password never leaves their computer.

- Credentials stay on this server.
- Never ask for a webmail password, Kerberos password, app password, or IMAP/SMTP password in chat.
- Do not ask the user to paste `master.key`, `vault.json`, or a mailbox password.
- Do not invent confirm codes.
- Do not invent a command phrase.
- Read, search, summarize, outline, and draft when the matching tool is available.
- For “latest mail,” call `latest_brief`. For a person or topic, call `search_inbox`. For due dates, call `find_deadlines`.
- The MCP endpoint is `POST /mcp` on this server. The mailbox password is not part of that connection.
- Sending: when the user says to send, call `request_send` or `send_now`. The tool sends the draft. Do not ask them to run `webmail-mcp approve`. Do not invent a confirm code or a command phrase.
- Do not claim a message was sent unless a tool result says `status` is `sent`.
