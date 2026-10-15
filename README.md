# TaskPilot — Autonomous AI Task Worker

TaskPilot is an AI-powered task execution prototype that converts natural-language instructions into multi-step workflows, executes actions through application tools, recovers from transient failures, and verifies the final result.

The current implementation focuses on invoice processing. Given a task such as *"Find the latest Acme invoice, extract its details, add it to the company system, and verify the entry"*, TaskPilot discovers available invoice PDFs, identifies the latest invoice, extracts and validates its data, creates a database record if needed, and verifies the result.

## Features

- **Natural-language task execution:** Submit invoice-processing instructions through a web dashboard.
- **AI-assisted execution:** Integrates with the Gemini API, with a local execution mode for deterministic invoice workflows.
- **Invoice discovery:** Lists available PDF invoices and selects the latest invoice for supported vendors using the invoice date.
- **PDF data extraction:** Extracts invoice numbers, invoice dates, due dates, and amounts from PDF documents.
- **Input validation:** Checks required fields, date formats, positive amounts, and due-date consistency before database writes.
- **Failure recovery:** Demonstrates retry behavior after a simulated temporary invoice-creation failure.
- **Safe database operations:** Checks for existing invoice records and attempts recovery when a database operation has an uncertain outcome.
- **Post-execution verification:** Compares the stored invoice against the extracted source data.
- **Execution history:** Persists task runs and results in SQLite so previous executions can be reviewed.
- **Execution dashboard:** Provides a React interface for submitting tasks, viewing execution steps, inspecting invoice details, and reviewing task history.
- **Clarification handling:** Requests a supported vendor when the task does not specify one.

## Architecture

```text
User Instruction
      |
      v
React Dashboard
      |
      v
FastAPI Task API
      |
      v
Task Agent
      |
      +---- List Invoice PDFs
      |
      +---- Select Latest Invoice
      |
      +---- Read PDF and Extract Data
      |
      +---- Validate Extracted Fields
      |
      +---- Search Existing Database Record
      |
      +---- Create or Recover from Failure
      |
      +---- Verify Stored Invoice
      |
      v
SQLite Database + Execution History
      |
      v
Task Result and Execution Trace
      |
      v
React Dashboard
```

### Technology Stack

| Component | Technologies |
|---|---|
| Backend API | Python, FastAPI, Uvicorn |
| Task execution | Python, Gemini API, local agent |
| PDF processing | PyMuPDF |
| Database and ORM | SQLite, SQLAlchemy |
| Frontend | React, Vite, JavaScript, CSS |
| Data validation | Python validation logic |
| Development | Git, GitHub |

## Project Structure

```text
TaskPilot/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── agent.py
│   │   ├── gemini_agent.py
│   │   ├── tools.py
│   │   ├── models.py
│   │   ├── database.py
│   │   ├── seed.py
│   │   └── reset_demo.py
│   └── requirements.txt
├── data/
│   ├── invoices/
│   └── create_invoices.py
├── frontend/
│   ├── src/
│   ├── package.json
│   └── vite.config.js
├── .env.example
├── .gitignore
└── README.md
```

The SQLite database is created locally and is intentionally excluded from version control.

## Getting Started

### Prerequisites

Install the following before running the application:

- Python 3.12 or a compatible Python version
- Node.js and npm
- Git (optional, for version control)
- A Gemini API key if you want to use AI-assisted execution

### 1. Clone the Repository

```bash
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd TaskPilot
```

Replace the placeholder URL with your repository URL after publishing the project.

### 2. Set Up the Python Environment

From the project root, run:

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
```

If PowerShell blocks script execution, activate the environment using the appropriate policy for your machine or run its Python executable directly.

### 3. Configure Environment Variables

Create a `.env` file in the project root if you plan to use Gemini.

```env
GEMINI_API_KEY=your_gemini_api_key
```

Keep your real API key private. Never commit `.env` or expose the key in frontend code.

For local execution, use the dashboard's local mode if available; Gemini credentials are not needed for that mode.

### 4. Start the Backend

From the project root, activate the virtual environment and run:

```powershell
uvicorn backend.app.main:app --reload
```

The backend will be available at:

- API root: http://127.0.0.1:8000/
- Health endpoint: http://127.0.0.1:8000/health
- Interactive API documentation: http://127.0.0.1:8000/docs

Keep this terminal running.

### 5. Start the Frontend

Open a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open the local URL printed by Vite, typically:

http://localhost:5173/

Keep the backend running while using the dashboard.

## Example Task

Submit a task similar to the following through the dashboard:

> Find the latest Acme invoice, extract its amount and due date, enter it into the company system, and verify the saved record.

A successful execution should follow these stages:

1. Discover available invoice PDFs.
2. Identify the invoice with the latest invoice date for the requested vendor.
3. Read the selected PDF.
4. Extract the invoice number, invoice date, due date, and amount.
5. Validate the extracted values.
6. Check whether the invoice already exists.
7. Create the invoice if necessary, retrying the demonstrated temporary failure when appropriate.
8. Verify the stored record against the extracted data.
9. Persist the task result and execution trace for later review.

The sample data includes Acme and Globex invoices. Results depend on the current database contents and task execution mode.

## Execution Modes

TaskPilot supports the following execution modes in the backend API:

- **Auto:** Attempts Gemini-assisted execution and can fall back to local execution for configured API availability or quota failures.
- **Gemini:** Uses the Gemini-powered agent.
- **Local:** Runs the deterministic local invoice workflow without making a Gemini API call.

Exact behavior depends on the current implementation and the availability of the Gemini API.

## API Overview

| Endpoint | Purpose |
|---|---|
| `GET /` | Check that the API is running |
| `GET /health` | Health check |
| `POST /tasks` | Submit a task for execution |
| `GET /tasks/history` | Retrieve previous task runs |

Additional invoice-related endpoints are available through the interactive API documentation at `/docs`.

## Data and Demo Reset

TaskPilot stores invoice records and task execution history in a local SQLite database under `data/`.

The repository includes sample invoice PDFs and a script for generating demo invoices. Database reset behavior depends on the implementation in `backend/app/reset_demo.py`; inspect that script before running it if you need to preserve existing records.

Do not commit the local database, environment files, or API credentials.

## Current Limitations

- Invoice processing is the primary supported workflow; this is not a general-purpose computer-use agent.
- Invoice extraction relies on the format of the supplied PDF documents and may require additional handling for unfamiliar layouts.
- The company system is represented by a local SQLite database rather than a live enterprise application.
- Failure recovery is demonstrated using a simulated temporary error; it is not a comprehensive production retry framework.
- Gemini execution depends on API availability, model access, and quota limits.
- The prototype has not been presented as a production-hardened service with authentication, authorization, distributed execution, or comprehensive automated test coverage.

## Future Improvements

- Add more task types and integrations with external business systems.
- Introduce human approval for sensitive or irreversible actions.
- Add automated unit and integration tests.
- Improve structured logging, observability, and failure diagnostics.
- Support more invoice formats and stronger extraction validation.
- Add authentication, authorization, and deployment configuration.

## License

Choose a license before distributing the project publicly. Until a license is added, do not assume others have permission to reuse or redistribute the code.