# Security policy

## Supported versions

Only the latest release and `main` get fixes.

## Reporting a vulnerability

Do not open a public issue. Report it privately through GitHub: go to the **Security** tab of this repo and click **Report a vulnerability**.

Include what you found, how to reproduce it, and what an attacker could do with it. I aim to reply within 7 days. This is a personal project, so fixes come on a best-effort basis.

If you find an API key or another secret in the repo or its history, report it the same way.

## Scope

Radio Llama is meant to run on your own machine, for one user at a time.

- The API has no authentication. Do not expose it to the internet. The Docker setup publishes port 8000; keep it behind your own firewall.
- API keys stay in `.env` on the server. The frontend never receives them.
- The public replay page is a static site with no backend.

Reports about running the server openly on the internet, or about the paid providers themselves (Mistral, Anthropic, ElevenLabs), are out of scope.
