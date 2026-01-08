# MarketingHandoffAI

## Overview

MarketingHandoffAI is a B2B SaaS platform that helps clients optimize their content for search engines and AI discovery systems. Clients submit content, keywords, and site information, then the platform runs optimization workflows and tracks work progress. The business model is based on tiered project engagements ($5,000, $10,000, $15,000) with Stripe payment integration.

The application follows a monorepo structure with a React frontend, Express backend, PostgreSQL database, and Stripe for payment processing.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Frontend Architecture
- **Framework**: React 18 with TypeScript
- **Build Tool**: Vite with custom build script
- **Routing**: Wouter (lightweight React router)
- **State Management**: TanStack React Query for server state
- **Styling**: Tailwind CSS with shadcn/ui component library
- **Theme**: Light/dark mode support with CSS variables
- **Forms**: React Hook Form with Zod validation

### Backend Architecture
- **Framework**: Express.js with TypeScript
- **API Pattern**: RESTful endpoints under `/api/*`
- **Authentication**: Replit Auth integration via OpenID Connect
- **Session Management**: PostgreSQL-backed sessions using connect-pg-simple
- **Build Output**: ESBuild bundles server to `dist/index.cjs` for production

### Data Storage
- **Database**: PostgreSQL via Drizzle ORM
- **Schema Location**: `shared/schema.ts` for application tables, `shared/models/auth.ts` for auth tables
- **Migrations**: Drizzle Kit with `db:push` command
- **Key Tables**: users, sessions, projects, projectTasks, deliverables

### Project Structure
```
client/           # React frontend
  src/
    components/   # UI components (shadcn/ui)
    pages/        # Route pages
    hooks/        # Custom React hooks
    lib/          # Utilities and query client
server/           # Express backend
  replit_integrations/  # Replit-specific auth
shared/           # Shared types and schema
  schema.ts       # Drizzle schema definitions
  models/         # Auth models
```

### Key Design Patterns
- **Shared Schema**: Database types are shared between client and server via `@shared/*` path alias
- **Protected Routes**: `isAuthenticated` middleware guards API endpoints
- **Storage Layer**: `IStorage` interface abstracts database operations in `server/storage.ts`
- **Webhook Processing**: Raw body parsing for Stripe webhooks before JSON middleware
- **Admin Authorization**: First user to access `/api/admin/projects` automatically becomes admin (MVP bootstrapping). The `ADMIN_USER_IDS` array is server-only (in `server/routes.ts`); in production, this should be database-backed with persistent role storage.

### Payment Flow
1. User submits project via multi-step form → Creates project with `pending` status
2. User redirected to Stripe Checkout for payment
3. After successful payment, call `/api/stripe/checkout-complete` with sessionId → Updates project to `in_progress`
4. Admin manages project through Kanban dashboard → Updates status as work progresses

## External Dependencies

### Stripe Integration
- **Package**: `stripe-replit-sync` for managed Stripe synchronization
- **Credentials**: Retrieved via Replit Connectors API at runtime
- **Webhook Handling**: Automatic webhook setup with signature verification
- **Pricing Tiers**: Three tiers at $5,000, $10,000, and $15,000

### Replit Services
- **Authentication**: Replit Auth via OpenID Connect (`/api/login`, `/api/logout`)
- **Database**: PostgreSQL provisioned through Replit
- **Environment Variables**: `DATABASE_URL`, `SESSION_SECRET`, `REPLIT_DOMAINS`

### UI Component Library
- **shadcn/ui**: Pre-configured with New York style variant
- **Radix Primitives**: Dialog, Select, Accordion, Tabs, and more
- **Icons**: Lucide React

### Development Tools
- **Vite Plugins**: Replit runtime error overlay, cartographer, dev banner
- **TypeScript**: Strict mode with path aliases (`@/*`, `@shared/*`)