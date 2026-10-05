# Contributing

Issues and pull requests are welcome.

Do not include a mailbox password, a connector URL, a token, `master.key`, `vault.json`, `accounts.json`, or `oidc.json` in an issue, a pull request, or a commit.

The hosted site at webmail-mcp.ariesiitd.com is separate from this repo. A change here does not change that site until it is deployed.

Run the tests before you open a pull request:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
