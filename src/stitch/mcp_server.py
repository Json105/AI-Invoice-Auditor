"""Google Stitch Model Context Protocol (MCP) Server.

Exposes design specifications, theme tokens, and component definitions
to Antigravity AI agents using the standard JSON-RPC 2.0 stdio transport.
"""

import sys
import json
import os
from pathlib import Path

SPEC_FILE = Path(__file__).parent / "stitch_design_spec.json"


def load_spec() -> dict:
    """Load the Stitch design specification."""
    if SPEC_FILE.exists():
        with open(SPEC_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"error": "Stitch design specification not found."}


def handle_initialize(req_id):
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "protocolVersion": "2024-11-05",
            "serverInfo": {
                "name": "google-stitch-mcp",
                "version": "1.0.0"
            },
            "capabilities": {
                "tools": {}
            }
        }
    }


def handle_tools_list(req_id):
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "tools": [
                {
                    "name": "get_stitch_design_spec",
                    "description": "Retrieve the complete Google Stitch design tokens, colors, typography, and layout specifications.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {}
                    }
                },
                {
                    "name": "get_component_specs",
                    "description": "Retrieve exact React, Tailwind, Recharts, and Framer Motion code specs for a specific component.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "component_name": {
                                "type": "string",
                                "description": "Name of component (e.g. MetricCard, UsageLineChart, SkeletonLoader)"
                            }
                        },
                        "required": ["component_name"]
                    }
                }
            ]
        }
    }


def handle_tools_call(req_id, params):
    tool_name = params.get("name")
    args = params.get("arguments", {})
    spec = load_spec()

    if tool_name == "get_stitch_design_spec":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(spec, ensure_ascii=False, indent=2)
                    }
                ]
            }
        }

    elif tool_name == "get_component_specs":
        comp_name = args.get("component_name")
        comp_data = spec.get("components", {}).get(comp_name)
        if not comp_data:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Component '{comp_name}' not found in Stitch spec. Available: {list(spec.get('components', {}).keys())}"
                        }
                    ],
                    "isError": True
                }
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(comp_data, ensure_ascii=False, indent=2)
                    }
                ]
            }
        }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {
            "code": -32601,
            "message": f"Method '{tool_name}' not found"
        }
    }


def main():
    """Main stdio loop processing JSON-RPC messages."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            req_id = req.get("id")
            method = req.get("method")

            if method == "initialize":
                res = handle_initialize(req_id)
            elif method == "notifications/initialized":
                continue  # notification, no response required
            elif method == "tools/list":
                res = handle_tools_list(req_id)
            elif method == "tools/call":
                res = handle_tools_call(req_id, req.get("params", {}))
            else:
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method '{method}' not supported"}
                }

            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        except Exception as e:
            err_res = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {str(e)}"}
            }
            sys.stdout.write(json.dumps(err_res) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
