# FalkorDB Migration Guide for Graphiti MCP Server

**Status**: Ready for Implementation  
**Expected Migration Time**: 30-60 minutes  
**Performance Gain**: 496x faster P99 latency, 6x better memory efficiency

## Why Migrate to FalkorDB?

### Performance Comparison
| Metric | Neo4j | FalkorDB | Improvement |
|--------|--------|----------|-------------|
| P50 Latency | 577.5ms | 55ms | **10.5x faster** |
| P90 Latency | 4,784ms | 108ms | **44.3x faster** |
| P99 Latency | 46,924ms | 136ms | **496x faster** |
| Memory Usage | 600MB | 100MB | **6x more efficient** |

### Key Advantages
- ✅ **Same Cypher Query Language** - Zero code changes needed
- ✅ **Same Bolt Protocol** - Uses existing Neo4j Python driver
- ✅ **Native Multi-tenancy** - Perfect for GROUP_ID scoping
- ✅ **In-memory Architecture** - C/Rust vs Java for better performance
- ✅ **Open Source** - No licensing restrictions

## Migration Steps

### Prerequisites
- Docker installed and running
- Current Graphiti setup working with Neo4j
- Backup of current Neo4j data (optional, for safety)

### Step 1: Install FalkorDB

```bash
# Pull and run FalkorDB with all required ports
docker run -p 6379:6379 -p 7687:7687 -p 3000:3000 -it \
  -e REDIS_ARGS="--requirepass falkordb" \
  -e FALKORDB_ARGS="BOLT_PORT 7687" \
  --rm falkordb/falkordb:latest

# Ports:
# 6379 - FalkorDB/Redis
# 7687 - Bolt protocol (same as Neo4j)
# 3000 - FalkorDB Browser (optional)
```

### Step 2: Update Configuration

The only change needed is authentication in `.env`:

```bash
# Before (Neo4j)
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=demodemo

# After (FalkorDB)
NEO4J_URI=bolt://localhost:7687  # Same URI!
NEO4J_USER=falkordb              # Changed user
NEO4J_PASSWORD=                  # Empty password (or set custom)
```

### Step 3: Test Connection

```bash
# Test with same Graphiti startup command
~/start_graphiti_mcp_sse.sh
```

Expected output should show successful connection to FalkorDB.

### Step 4: Verify GROUP_ID Isolation

```bash
# Test with different GROUP_IDs (same as before)
cd ~/dotfiles && GRAPHITI_GROUP_ID="test-falkor" ~/start_graphiti_mcp_sse.sh
```

Should show: `Using provided group_id: test-falkor`

### Step 5: Optional Data Migration

If you have existing Neo4j data to migrate:

```bash
# Use FalkorDB's official migration tool
git clone https://github.com/FalkorDB/Neo4j-to-FalkorDB.git
cd Neo4j-to-FalkorDB
pip install -r requirements.txt

# Configure migration (edit migrate_config.json)
# Run migration
python neo4j_to_csv_extractor.py
python falkordb_csv_loader.py
```

## Configuration Files

### New .env.falkordb (Ready to Use)
```bash
# Copy existing .env to .env.neo4j (backup)
cp .env .env.neo4j

# Use new FalkorDB configuration
cp .env.falkordb .env
```

### No Code Changes Required
- ✅ Same Cypher queries
- ✅ Same Python neo4j driver
- ✅ Same GROUP_ID scoping logic
- ✅ Same custom entities
- ✅ Same MCP wrapper functionality

## Performance Testing

Once migration is complete, you can benchmark:

```python
# Simple performance test script (optional)
from neo4j import GraphDatabase
import time

driver = GraphDatabase.driver("bolt://localhost:7687", auth=("falkordb", ""))

# Test query performance
start = time.time()
records, summary, keys = driver.execute_query(
    "UNWIND range(1, 1000) AS i CREATE (n:TestNode {id: i}) RETURN count(n)",
    database_="performance_test"
)
end = time.time()

print(f"Created 1000 nodes in {(end-start)*1000:.2f}ms")
```

## Rollback Plan

If issues arise, rollback is simple:

```bash
# Stop FalkorDB container
docker stop [container_id]

# Restore Neo4j configuration  
cp .env.neo4j .env

# Restart Neo4j (if you had it running before)
# Start Graphiti as normal
~/start_graphiti_mcp_sse.sh
```

## Benefits After Migration

### Immediate Gains
- **Sub-second response times** for all graph queries
- **Reduced memory usage** - perfect for development machines
- **Better multi-tenancy** - GROUP_ID isolation works even better
- **Faster startup times** - in-memory database initialization

### Long-term Advantages  
- **Future-proof architecture** - C/Rust vs Java
- **Better scaling** - horizontal scaling capabilities
- **Cost reduction** - lower infrastructure requirements
- **AI/ML integration** - built-in vector search support

## Troubleshooting

### Common Issues

**Connection Refused**
```bash
# Ensure FalkorDB container is running
docker ps | grep falkordb

# Check port availability
lsof -i :7687
```

**Authentication Failed**
```bash
# Check FalkorDB logs
docker logs [container_id]

# Verify password in .env matches container config
```

**GROUP_ID Not Working**
```bash
# Same troubleshooting as Neo4j - check wrapper logic
echo $GRAPHITI_GROUP_ID
```

## Next Steps After Migration

1. **Monitor Performance** - Compare query times in production use
2. **Optimize Queries** - FalkorDB may benefit from different query patterns  
3. **Explore Vector Search** - FalkorDB includes built-in vector capabilities
4. **Scale Testing** - Test with larger datasets to see full performance gains

## Support Resources

- [FalkorDB Documentation](https://docs.falkordb.com/)
- [Migration Guide](https://www.falkordb.com/blog/neo4j-to-falkordb-migration-guide/)
- [Performance Benchmarks](https://benchmark.falkordb.com/)
- [GitHub Migration Tools](https://github.com/FalkorDB/Neo4j-to-FalkorDB)

---

**Migration Complete**: Your Graphiti MCP server will now run on FalkorDB with dramatically improved performance while maintaining 100% compatibility with existing functionality.