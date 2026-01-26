"""pytest configuration for mcp_server tests.

This local conftest.py overrides the root conftest.py which imports kuzu (not needed here).
MCP server tests use their own mock fixtures defined in the test files.
"""

import os
import sys

# Add project root and mcp_server to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
