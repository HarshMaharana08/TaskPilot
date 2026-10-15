import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

from .tools import (
    list_invoices,
    read_invoice,
    search_invoice,
    create_invoice,
    verify_invoice,
    find_latest_invoice,
)

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set.")

client = genai.Client(api_key=API_KEY)


# ---------------------------------------------------------
# Tool declarations
# ---------------------------------------------------------

list_invoices_tool = {
    "type": "function",
    "name": "list_invoices",
    "description": (
        "Lists all invoice PDF files available in the invoice repository."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


read_invoice_tool = {
    "type": "function",
    "name": "read_invoice",
    "description": (
        "Reads the contents of a specific invoice PDF."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "The invoice PDF filename.",
            }
        },
        "required": ["filename"],
    },
}


search_invoice_tool = {
    "type": "function",
    "name": "search_invoice",
    "description": (
        "Checks whether an invoice number already exists in the company system."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "invoice_number": {
                "type": "string",
                "description": "The invoice number to search for.",
            }
        },
        "required": ["invoice_number"],
    },
}


create_invoice_tool = {
    "type": "function",
    "name": "create_invoice",
    "description": (
        "Creates an invoice record in the company system. "
        "Use this only after extracting and validating the invoice details."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vendor": {
                "type": "string",
                "description": "Vendor/company name.",
            },
            "invoice_number": {
                "type": "string",
                "description": "Invoice number.",
            },
            "invoice_date": {
                "type": "string",
                "description": "Invoice date in YYYY-MM-DD format.",
            },
            "due_date": {
                "type": "string",
                "description": "Due date in YYYY-MM-DD format.",
            },
            "amount": {
                "type": "number",
                "description": "Invoice total amount.",
            },
        },
        "required": [
            "vendor",
            "invoice_number",
            "invoice_date",
            "due_date",
            "amount",
        ],
    },
}


verify_invoice_tool = {
    "type": "function",
    "name": "verify_invoice",
    "description": (
        "Verifies that an invoice exists in the company system "
        "and that all important fields match the source invoice."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "invoice_number": {
                "type": "string",
                "description": "Invoice number.",
            },
            "expected_vendor": {
                "type": "string",
                "description": "Expected vendor name.",
            },
            "expected_invoice_date": {
                "type": "string",
                "description": "Expected invoice date.",
            },
            "expected_due_date": {
                "type": "string",
                "description": "Expected due date.",
            },
            "expected_amount": {
                "type": "number",
                "description": "Expected invoice amount.",
            },
        },
        "required": [
            "invoice_number",
            "expected_vendor",
            "expected_invoice_date",
            "expected_due_date",
            "expected_amount",
        ],
    },
}


find_latest_invoice_tool = {
    "type": "function",
    "name": "find_latest_invoice",
    "description": (
        "Finds the latest invoice PDF for a given vendor. "
        "Use this when the task asks for the latest or most recent invoice."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vendor": {
                "type": "string",
                "description": (
                    "The vendor name, for example Acme Corporation."
                ),
            }
        },
        "required": ["vendor"],
    },
}


TOOLS = [
    find_latest_invoice_tool,
    list_invoices_tool,
    read_invoice_tool,
    search_invoice_tool,
    create_invoice_tool,
    verify_invoice_tool,
]


# ---------------------------------------------------------
# Local tool execution
# ---------------------------------------------------------

def execute_tool(name, arguments):
    """Execute a tool requested by Gemini."""

    if name == "find_latest_invoice":
        return find_latest_invoice(
            arguments["vendor"]
        )

    if name == "list_invoices":
        return list_invoices()

    if name == "read_invoice":
        return read_invoice(
            filename=arguments["filename"]
        )

    if name == "search_invoice":
        return search_invoice(
            invoice_number=arguments["invoice_number"]
        )

    if name == "create_invoice":
        return create_invoice(
            vendor=arguments["vendor"],
            invoice_number=arguments["invoice_number"],
            invoice_date=arguments["invoice_date"],
            due_date=arguments["due_date"],
            amount=arguments["amount"],
        )

    if name == "verify_invoice":
        return verify_invoice(
            invoice_number=arguments["invoice_number"],
            expected_vendor=arguments["expected_vendor"],
            expected_invoice_date=arguments["expected_invoice_date"],
            expected_due_date=arguments["expected_due_date"],
            expected_amount=arguments["expected_amount"],
        )

    return {
        "success": False,
        "error": f"Unknown tool: {name}",
    }


# ---------------------------------------------------------
# Gemini Agent
# ---------------------------------------------------------

class GeminiTaskAgent:

    def __init__(self):
        self.steps = []

    def record_step(self, action, result):
        self.steps.append({
            "action": action,
            "result": result,
        })

    def run(self, task):

        system_instruction = """
You are TaskPilot, an autonomous AI task worker.

Your job is to complete the user's task by using the available tools.

Follow these principles:

1. Understand the user's goal before acting.
2. Inspect relevant information before making decisions.
3. Use tools to perform real work.
4. When the user asks for the latest or most recent invoice from a vendor,
   use the find_latest_invoice tool first.
5. Do not read multiple invoice files just to determine which one is latest.
6. Never claim that an action succeeded unless a tool confirms it.
7. If a tool fails temporarily, reason about whether retrying is appropriate.
8. Avoid creating duplicate records.
9. After modifying the company system, verify the resulting state.
10. If verification fails, investigate and take corrective action when possible.
11. Keep track of information discovered from previous tool calls.
12. At the end, provide a concise summary of what was done and the evidence
    supporting completion.

For invoice tasks:

- Find the relevant invoice.
- Read the source invoice.
- Extract vendor, invoice number, invoice date, due date, and amount.
- Check whether the invoice already exists.
- Create it if necessary.
- If creation fails temporarily, retry appropriately.
- Verify the final record against the source invoice.

Important:
The verification tool is the source of truth for whether an invoice
was successfully entered into the company system. Do not claim success
without successful verification.
"""

        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=task
                    )
                ],
            )
        ]

        max_iterations = 10

        for iteration in range(max_iterations):

            response = None

            # -------------------------------------------------
            # Ask Gemini what to do next
            # -------------------------------------------------

            for attempt in range(3):

                try:
                    response = client.models.generate_content(
                        model="gemini-3.5-flash",
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            tools=[
                                types.Tool(
                                    function_declarations=[
                                        types.FunctionDeclaration(
                                            name=tool["name"],
                                            description=tool["description"],
                                            parameters_json_schema=tool[
                                                "parameters"
                                            ],
                                        )
                                        for tool in TOOLS
                                    ]
                                )
                            ],
                        ),
                    )

                    break

                except Exception as e:

                    print(
                        f"\n[Gemini Error] "
                        f"Attempt {attempt + 1}/3: {e}"
                    )

                    error_message = str(e)

                    # -------------------------------------------------
                    # Rate-limit handling
                    # -------------------------------------------------

                    if (
                        "429" in error_message
                        or "RESOURCE_EXHAUSTED" in error_message
                    ):

                        if attempt < 2:
                            print(
                                "[Gemini] Rate limit reached. "
                                "Waiting 40 seconds before retrying..."
                            )

                            time.sleep(40)
                            continue

                    # -------------------------------------------------
                    # Retry other temporary API errors
                    # -------------------------------------------------

                    if attempt < 2:

                        print(
                            "[Gemini] Retrying in 2 seconds..."
                        )

                        time.sleep(2)
                        continue

                    # -------------------------------------------------
                    # If the business task was already completed and
                    # verified, don't report it as failed merely because
                    # the LLM could not generate a final response.
                    # -------------------------------------------------

                    verification_succeeded = any(
                        step["action"] == "verify_invoice"
                        and step["result"].get("verified") is True
                        for step in self.steps
                    )

                    if verification_succeeded:

                        return {
                            "task": task,
                            "status": "completed",
                            "summary": (
                                "Task completed successfully. "
                                "The invoice was entered into the "
                                "company system and independently verified."
                            ),
                            "evidence": self.steps,
                            "warning": (
                                "The final Gemini response could not "
                                "be generated because of API quota limits."
                            ),
                            "steps": self.steps,
                        }

                    return {
                        "task": task,
                        "status": "failed",
                        "reason": (
                            "Gemini API unavailable after 3 attempts."
                        ),
                        "error": str(e),
                        "steps": self.steps,
                    }

            # -------------------------------------------------
            # Gemini has finished without requesting a tool.
            # -------------------------------------------------

            function_calls = response.function_calls

            if not function_calls:

                return {
                    "task": task,
                    "status": "completed",
                    "summary": response.text,
                    "steps": self.steps,
                }

            # -------------------------------------------------
            # Add Gemini's response to conversation history.
            # -------------------------------------------------

            contents.append(
                response.candidates[0].content
            )

            # -------------------------------------------------
            # Execute Gemini's requested tools.
            # -------------------------------------------------

            for function_call in function_calls:

                tool_name = function_call.name

                arguments = dict(
                    function_call.args or {}
                )

                print(
                    f"\n[Gemini] Calling {tool_name}"
                )

                print(
                    f"[Arguments] {arguments}"
                )

                result = execute_tool(
                    tool_name,
                    arguments
                )

                self.record_step(
                    tool_name,
                    result
                )

                print(
                    f"[Result] {result}"
                )

                # -------------------------------------------------
                # IMPORTANT:
                # If verification succeeds, the actual business
                # task is complete. Stop immediately instead of
                # making another Gemini API call just to generate
                # a final sentence.
                # -------------------------------------------------

                if (
                    tool_name == "verify_invoice"
                    and result.get("verified") is True
                ):

                    return {
                        "task": task,
                        "status": "completed",
                        "summary": (
                            f"Invoice {result['invoice_number']} "
                            "was entered into the company system "
                            "and independently verified."
                        ),
                        "evidence": {
                            "invoice": result["invoice"],
                            "verification": result,
                        },
                        "steps": self.steps,
                    }

                # -------------------------------------------------
                # Send tool result back to Gemini.
                # -------------------------------------------------

                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=tool_name,
                                response={
                                    "result": result
                                },
                            )
                        ],
                    )
                )

        # ---------------------------------------------------------
        # Maximum iterations reached
        # ---------------------------------------------------------

        return {
            "task": task,
            "status": "failed",
            "reason": "Maximum agent iterations reached.",
            "steps": self.steps,
        }