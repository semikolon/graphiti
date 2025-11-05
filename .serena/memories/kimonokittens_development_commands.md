# Kimonokittens Development Commands

## Running the System
- `ruby json_server.rb` - Start main Agoo server (development on port 3001)
- `cd handbook/frontend && npm run dev` - Start React frontend with Vite
- Environment: Set `RACK_ENV=production` for SSL and production logging

## Testing
- `bundle exec rspec` - Run Ruby test suite
- `cd handbook/frontend && npm test` - Run frontend tests
- Test database cleaning: Use `TRUNCATE TABLE "TableName" RESTART IDENTITY CASCADE;`

## Database Management
- `cd handbook && npx prisma migrate dev --name <migration-name>` - Create migration
- `npx prisma generate` - Generate TypeScript client
- Database URL: Remove `?schema=public` parameter for Ruby pg gem compatibility

## Key Files for Development
- `rent.rb` - Core calculation logic
- `handlers/rent_calculator_handler.rb` - REST API
- `handbook/prisma/schema.prisma` - Database schema
- `json_server.rb` - Server configuration
- `.env` - Environment variables (DATABASE_URL, API keys)

## Architecture Notes
- Single-threaded Agoo server with WebSocket support
- PostgreSQL for all financial data (replaces legacy JSON/SQLite)
- Prisma ORM provides type-safe client for frontend
- Real-time updates via WebSocket pub/sub system