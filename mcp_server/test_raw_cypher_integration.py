#!/usr/bin/env python3
"""Integration tests for raw_cypher_query against live FalkorDBLite.

Run with: python test_raw_cypher_integration.py
"""

import asyncio
import os
import sys

# Load environment from .env file
from dotenv import load_dotenv
load_dotenv()


async def run_integration_tests():
    """Integration tests against live FalkorDBLite."""

    # Import after env is loaded
    from graphiti_mcp_server import raw_cypher_query
    import graphiti_mcp_server as server

    # Initialize the server (mimicking startup)
    print("Initializing Graphiti client...")

    # Check if we need to initialize
    if server.graphiti_client is None:
        print("Client not initialized, setting up...")

        # Use the embedded FalkorDBLite
        from graphiti_core import Graphiti
        from graphiti_core.driver.falkordb_driver import FalkorDriver
        from graphiti_core.llm_client.openai_client import OpenAIClient
        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

        # Check for USE_FALKORDBLITE - accept 1, true, yes (case insensitive)
        use_embedded_val = os.getenv('USE_FALKORDBLITE', 'false').lower()
        use_embedded = use_embedded_val in ('1', 'true', 'yes')

        if use_embedded:
            from redislite import AsyncFalkorDB as EmbeddedAsyncFalkorDB
            print("Using embedded FalkorDBLite...")
            db_path = os.path.expanduser(os.getenv('FALKORDBLITE_PATH', '~/.graphiti/falkordblite.rdb'))
            print(f"Database path: {db_path}")
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            embedded_db = EmbeddedAsyncFalkorDB(dbfilename=db_path)
            falkor_driver = FalkorDriver(falkor_db=embedded_db)
        else:
            print("Using FalkorDB server...")
            falkor_driver = FalkorDriver(
                host=os.getenv('FALKORDB_HOST', 'localhost'),
                port=int(os.getenv('FALKORDB_PORT', '6380')),
                password=os.getenv('FALKORDB_PASSWORD'),
            )

        llm_client = OpenAIClient(
            LLMConfig(
                api_key=os.getenv('OPENAI_API_KEY'),
                model=os.getenv('MODEL_NAME', 'gpt-5-mini')
            )
        )

        embedder = OpenAIEmbedder(
            OpenAIEmbedderConfig(api_key=os.getenv('OPENAI_API_KEY'))
        )

        server.graphiti_client = Graphiti(
            llm_client=llm_client,
            embedder=embedder,
            graph_driver=falkor_driver,
        )
        print("Client initialized!")

    print("\n" + "="*60)
    print("INTEGRATION TESTS - raw_cypher_query against FalkorDBLite")
    print("="*60 + "\n")

    tests_passed = 0
    tests_failed = 0

    # Test 1: Simple node count
    print("Test 1: Count all nodes...")
    try:
        result = await raw_cypher_query(
            query="MATCH (n) RETURN count(n) as node_count",
            max_results=10
        )
        if isinstance(result, list):
            print(f"  ✅ PASSED - Found {result[0].get('node_count', 'N/A')} nodes")
            tests_passed += 1
        else:
            print(f"  ❌ FAILED - Got error: {result}")
            tests_failed += 1
    except Exception as e:
        print(f"  ❌ FAILED - Exception: {e}")
        tests_failed += 1

    # Test 2: List node labels
    print("\nTest 2: List distinct node labels...")
    try:
        result = await raw_cypher_query(
            query="MATCH (n) RETURN DISTINCT labels(n)[0] as label LIMIT 20"
        )
        if isinstance(result, list):
            labels = [r.get('label') for r in result if r.get('label')]
            print(f"  ✅ PASSED - Found labels: {labels[:10]}")
            tests_passed += 1
        else:
            print(f"  ❌ FAILED - Got error: {result}")
            tests_failed += 1
    except Exception as e:
        print(f"  ❌ FAILED - Exception: {e}")
        tests_failed += 1

    # Test 3: Query Episodes
    print("\nTest 3: Query recent Episodes...")
    try:
        result = await raw_cypher_query(
            query="MATCH (e:Episodic) RETURN e.name, e.group_id ORDER BY e.created_at DESC LIMIT 5"
        )
        if isinstance(result, list):
            print(f"  ✅ PASSED - Found {len(result)} episodes")
            for r in result[:3]:
                name = r.get('e.name', 'N/A')
                if name and len(name) > 50:
                    name = name[:50] + "..."
                print(f"      - {name} (group: {r.get('e.group_id', 'N/A')})")
            tests_passed += 1
        else:
            print(f"  ❌ FAILED - Got error: {result}")
            tests_failed += 1
    except Exception as e:
        print(f"  ❌ FAILED - Exception: {e}")
        tests_failed += 1

    # Test 4: Parameterized query
    print("\nTest 4: Parameterized query with group_id filter...")
    try:
        result = await raw_cypher_query(
            query="MATCH (n) WHERE n.group_id = $group_id RETURN count(n) as count",
            params={"group_id": "dotfiles"}
        )
        if isinstance(result, list):
            count = result[0].get('count', 0) if result else 0
            print(f"  ✅ PASSED - Found {count} nodes in 'dotfiles' group")
            tests_passed += 1
        else:
            print(f"  ❌ FAILED - Got error: {result}")
            tests_failed += 1
    except Exception as e:
        print(f"  ❌ FAILED - Exception: {e}")
        tests_failed += 1

    # Test 5: Write operation blocking
    print("\nTest 5: Verify CREATE is blocked...")
    try:
        result = await raw_cypher_query(
            query="CREATE (n:Test {name: 'should_fail'}) RETURN n"
        )
        if isinstance(result, dict) and 'error' in result:
            print(f"  ✅ PASSED - Correctly blocked: {result['error'][:50]}...")
            tests_passed += 1
        else:
            print(f"  ❌ FAILED - Should have been blocked but got: {result}")
            tests_failed += 1
    except Exception as e:
        print(f"  ❌ FAILED - Exception: {e}")
        tests_failed += 1

    # Test 6: DELETE blocking
    print("\nTest 6: Verify DELETE is blocked...")
    try:
        result = await raw_cypher_query(
            query="MATCH (n) DELETE n"
        )
        if isinstance(result, dict) and 'error' in result:
            print(f"  ✅ PASSED - Correctly blocked: {result['error'][:50]}...")
            tests_passed += 1
        else:
            print(f"  ❌ FAILED - Should have been blocked but got: {result}")
            tests_failed += 1
    except Exception as e:
        print(f"  ❌ FAILED - Exception: {e}")
        tests_failed += 1

    # Test 7: Complex traversal
    print("\nTest 7: Graph traversal query...")
    try:
        result = await raw_cypher_query(
            query="""
            MATCH (n)-[r]->(m)
            RETURN labels(n)[0] as from_type, type(r) as rel_type, labels(m)[0] as to_type, count(*) as count
            ORDER BY count DESC
            LIMIT 10
            """
        )
        if isinstance(result, list):
            print(f"  ✅ PASSED - Found {len(result)} relationship patterns")
            for r in result[:3]:
                print(f"      - {r.get('from_type')} --[{r.get('rel_type')}]--> {r.get('to_type')}: {r.get('count')}")
            tests_passed += 1
        else:
            print(f"  ❌ FAILED - Got error: {result}")
            tests_failed += 1
    except Exception as e:
        print(f"  ❌ FAILED - Exception: {e}")
        tests_failed += 1

    # Test 8: Auto LIMIT enforcement
    print("\nTest 8: Auto LIMIT when missing...")
    try:
        result = await raw_cypher_query(
            query="MATCH (n) RETURN n.name",
            max_results=5
        )
        if isinstance(result, list) and len(result) <= 5:
            print(f"  ✅ PASSED - Got {len(result)} results (max was 5)")
            tests_passed += 1
        else:
            print(f"  ❌ FAILED - Got {len(result) if isinstance(result, list) else result}")
            tests_failed += 1
    except Exception as e:
        print(f"  ❌ FAILED - Exception: {e}")
        tests_failed += 1

    print("\n" + "="*60)
    print(f"RESULTS: {tests_passed} passed, {tests_failed} failed")
    print("="*60)

    return tests_failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_integration_tests())
    sys.exit(0 if success else 1)
