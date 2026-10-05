# Security

webmail-mcp can read and send mail. Treat a connector URL the way you treat a password.

If you are self-hosting, keep `~/.assistant-bridge` private. Do not commit `master.key`, `vault.json`, `tokens.json`, `accounts.json`, or `oidc.json`.

To report a problem, open a GitHub issue only if it does not include a token, a password, or someone else's mail. Otherwise email the maintainer through the address on the GitHub profile.

The hosted service stores a mail password encrypted on its server, for the Kerberos id that signed in. If you do not want that, run this repo yourself.
