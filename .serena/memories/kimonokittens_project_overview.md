# Kimonokittens Project Overview

## Project Purpose
Multi-project monorepo for home-automation and finance tools running on Raspberry Pi at kimonokittens.com. Primary focus is a Ruby-based rent calculation system for kollektiv living with real-time dashboard updates.

## Tech Stack
- **Backend**: Ruby with Agoo server, PostgreSQL + Prisma ORM
- **Frontend**: React with WebSocket real-time updates
- **Infrastructure**: Raspberry Pi deployment, SSL in production
- **External APIs**: Weather, train departures, electricity bills, Strava

## Code Structure
- `rent.rb` - Core rent calculation logic with RentCalculator module
- `handlers/` - REST API handlers (rent_calculator_handler.rb, etc.)
- `json_server.rb` - Main Agoo server with WebSocket support
- `handbook/` - React frontend and Prisma schema
- `lib/` - Utility classes (RentDb, DataBroadcaster, etc.)
- `spec/` - RSpec test suite

## Key Components
1. **RentCalculator Module**: Weight-based distribution, room adjustments, precision handling
2. **REST API**: Comprehensive endpoints for calculations, forecasts, roommate management
3. **Real-time Updates**: WebSocket pub/sub system for dashboard updates
4. **Database Models**: Tenant, RentLedger, RentConfig, CoOwnedItem
5. **External Integrations**: Electricity bill parsing, weather/train APIs

## Current Scale
Designed for 3-4 person household with manual quarterly invoice handling and simple room adjustments.