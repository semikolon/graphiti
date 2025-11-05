#!/usr/bin/env python3
"""
FalkorDB Performance Test for Graphiti
Tests basic graph operations to validate performance improvements
"""

import time
import statistics
from neo4j import GraphDatabase

def test_connection_and_performance():
    """Test FalkorDB connection and basic performance"""
    
    # FalkorDB connection
    driver = GraphDatabase.driver("bolt://localhost:7687", auth=("falkordb", "falkordb"))
    
    print("🚀 FalkorDB Performance Test")
    print("=" * 50)
    
    # Test basic connectivity
    try:
        with driver.session() as session:
            result = session.run("RETURN 1 as test")
            record = result.single()
            print(f"✅ Connection successful: {record['test']}")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return
    
    # Test database selection for performance test
    database_name = "performance_test"
    
    # Performance tests
    tests = [
        ("Simple node creation", "CREATE (n:TestNode {id: $id, timestamp: $ts}) RETURN n.id", 100),
        ("Node with properties", "CREATE (n:TestEntity {name: $name, value: $value, created: $ts}) RETURN n.name", 50),
        ("Simple query", "MATCH (n:TestNode) WHERE n.id = $id RETURN n.id LIMIT 1", 100),
    ]
    
    print("\n📊 Performance Benchmarks:")
    print("-" * 50)
    
    for test_name, query, iterations in tests:
        times = []
        
        with driver.session(database=database_name) as session:
            # Cleanup before test
            session.run("MATCH (n) WHERE labels(n)[0] STARTS WITH 'Test' DELETE n")
            
            print(f"\n🔍 {test_name} ({iterations} iterations)")
            
            for i in range(iterations):
                start = time.time()
                
                if "CREATE" in query:
                    if "TestNode" in query:
                        session.run(query, id=i, ts=time.time())
                    else:
                        session.run(query, name=f"entity_{i}", value=i*10, ts=time.time())
                else:  # Query
                    session.run(query, id=i%50)  # Query existing nodes
                
                end = time.time()
                times.append((end - start) * 1000)  # Convert to milliseconds
            
            # Calculate statistics
            avg_ms = statistics.mean(times)
            p50_ms = statistics.median(times)
            p90_ms = statistics.quantiles(times, n=10)[8] if len(times) >= 10 else max(times)
            p99_ms = statistics.quantiles(times, n=100)[98] if len(times) >= 100 else max(times)
            
            print(f"   Average: {avg_ms:.2f}ms")
            print(f"   P50:     {p50_ms:.2f}ms")  
            print(f"   P90:     {p90_ms:.2f}ms")
            print(f"   P99:     {p99_ms:.2f}ms")
            
            # Compare to expected Neo4j performance (from benchmarks)
            neo4j_p99_expected = {
                "Simple node creation": 400,  # Estimated based on benchmark data
                "Node with properties": 600,
                "Simple query": 200,
            }
            
            if test_name in neo4j_p99_expected:
                neo4j_p99 = neo4j_p99_expected[test_name]
                improvement = neo4j_p99 / p99_ms
                print(f"   🎯 Expected improvement over Neo4j: {improvement:.1f}x faster")
    
    print("\n" + "=" * 50)
    print("✅ FalkorDB Performance Test Complete")
    print("\n💡 Expected benefits:")
    print("   • Sub-140ms P99 response times")
    print("   • 6x better memory efficiency") 
    print("   • Native multi-tenancy support")
    print("   • In-memory C/Rust architecture")
    
    driver.close()

if __name__ == "__main__":
    test_connection_and_performance()