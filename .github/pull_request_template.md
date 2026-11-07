## Summary

<!-- What does this change and why? Link the issue: "Fixes #123". -->

## Test plan

<!-- How you checked it works: commands you ran, screens you tried. -->

## Checklist

- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest` pass
- [ ] Frontend changed: `npm run lint`, `npm run build` and `npm run build:replay` pass in `frontend/`
- [ ] API model or route changed: `frontend/openapi.json` and `frontend/src/api/schema.d.ts` are regenerated and committed
- [ ] Tests use the fakes only, no paid API call
- [ ] No secrets, `.env`, audio files, `output/` or `cache/` in the diff
- [ ] Commit subjects start with a gitmoji
