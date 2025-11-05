# Task Completion Checklist for Kimonokittens

## When Adding New Features
1. **Update Core Logic**: Modify `rent.rb` RentCalculator module if needed
2. **Add API Endpoints**: Update `handlers/rent_calculator_handler.rb` with new routes
3. **Database Changes**: Create Prisma migration if schema changes required
4. **Add Tests**: Write RSpec tests for new functionality
5. **Frontend Integration**: Update React components if UI changes needed
6. **Documentation**: Update DEVELOPMENT.md with technical decisions

## Before Committing Changes
1. **Run Tests**: `bundle exec rspec` must pass
2. **Check Server**: Ensure `ruby json_server.rb` starts without errors
3. **Validate API**: Test new endpoints manually or with curl
4. **Frontend Build**: Ensure React frontend builds without errors
5. **Review Logs**: Check for any error messages in console output

## Code Style and Conventions
- **Ruby**: Use symbols for internal hashes, strings for external/JSON data
- **Database**: All financial data goes through PostgreSQL, no file storage
- **Error Handling**: Use RentCalculator::ValidationError for business logic errors
- **WebSocket**: Return `[101, {}, []]` for upgrades, use `con_id` for client tracking
- **Precision**: Maintain full floating-point precision until final rounding step

## Key Patterns to Follow
- Separation of calculation logic (rent.rb) from API layer (handlers)
- Database as single source of truth for all financial records
- Real-time updates via WebSocket for data changes
- Comprehensive error handling with appropriate HTTP status codes