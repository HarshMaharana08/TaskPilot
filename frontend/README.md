# TaskPilot React frontend

The React/Vite UI provides PDF upload/listing, natural-language tasks, Local/
Gemini/Auto mode selection, execution results/steps, and saved task history.
See the [project README](../README.md) for complete setup and API contracts.

Use Node 22.12+ and the committed lockfile:

```powershell
npm.cmd ci
npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

The default API URL is `http://127.0.0.1:8000`. Set `VITE_API_BASE_URL` in an ignored
`.env.local` only when needed; never put a Gemini key in a frontend variable.

```powershell
npm.cmd test
npm.cmd run test:browser
npm.cmd run lint
npm.cmd run build
```

Browser tests use an installed Chrome/Chromium, Node's native WebSocket, a temporary
production build, and private synthetic SQLite/PDF fixtures. They do not require
your local database or uploads and make no live Gemini calls. The isolated backend
uses the project's `.venv`; run the backend dependency setup first. No automation
package or browser is downloaded by the tests. `TASKPILOT_BROWSER` can point to a
nonstandard Chromium executable. Screenshots under `test-results/` are generated
artifacts, not source. See [developer notes](../docs/development.md).
