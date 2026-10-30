# Radio Llama frontend

The web page for Radio Llama. You pick a music style, a host and a language, press one button, follow the progress, then listen to the show with its script and track credits. Past shows can be replayed.

React, Vite and TypeScript. The API types in `src/api/schema.d.ts` are generated from `openapi.json`, which the backend exports.

## Develop

```sh
npm install
npm run dev
```

The dev server runs on port 5173 and proxies `/api` to the backend on port 8000. Start the backend from the repository root first:

```sh
uv run radio-llama serve --reload
```

## After a backend change

Export the schema and regenerate the types. CI fails if either file is out of date.

```sh
uv run radio-llama export-openapi frontend/openapi.json
cd frontend && npm run generate-types
```

## Check and build

```sh
npm run lint
npm run build
```

The build goes to `frontend/dist/`, which the backend serves in production.
