"""Unit tests for Google Stitch MCP Server and design spec."""

import json
from pathlib import Path
from src.stitch.mcp_server import load_spec, handle_initialize, handle_tools_list, handle_tools_call


def test_stitch_spec_file_exists():
    """Verify stitch_design_spec.json exists and is valid JSON."""
    spec = load_spec()
    assert "tokens" in spec
    assert "colors" in spec["tokens"]
    assert spec["tokens"]["colors"]["background"]["base"] == "#0F172A"
    assert "components" in spec
    assert "MetricCard" in spec["components"]
    assert "UsageLineChart" in spec["components"]


def test_stitch_mcp_initialize():
    """Verify JSON-RPC 2.0 initialize response."""
    res = handle_initialize(1)
    assert res["jsonrpc"] == "2.0"
    assert res["id"] == 1
    assert res["result"]["serverInfo"]["name"] == "google-stitch-mcp"


def test_stitch_mcp_tools_list():
    """Verify tool discovery exposes design spec and component tools."""
    res = handle_tools_list(2)
    assert res["id"] == 2
    tool_names = [t["name"] for t in res["result"]["tools"]]
    assert "get_stitch_design_spec" in tool_names
    assert "get_component_specs" in tool_names


def test_stitch_mcp_tools_call():
    """Verify tool call returns component layout and props."""
    res = handle_tools_call(3, {
        "name": "get_component_specs",
        "arguments": {"component_name": "MetricCard"}
    })
    assert res["id"] == 3
    content_text = res["result"]["content"][0]["text"]
    spec_data = json.loads(content_text)
    assert "layout" in spec_data
    assert "backdrop-blur-md" in spec_data["layout"]
