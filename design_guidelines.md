# MarketingHandoffAI Design Guidelines

## Design Approach

**Reference-Based Approach**: Drawing from Linear (clean dashboard UX), Stripe (trust and clarity in pricing/payments), and Notion (intuitive forms and data presentation).

**Core Principles**:
- Professional credibility and trust (B2B SaaS)
- Clarity in complex workflows
- Efficient task completion
- Modern, technical sophistication

---

## Typography

**Font Stack**: Inter (primary), JetBrains Mono (code/data displays)

**Hierarchy**:
- Hero headlines: text-6xl lg:text-7xl, font-bold, tracking-tight
- Section headings: text-4xl lg:text-5xl, font-bold
- Subsection headings: text-2xl lg:text-3xl, font-semibold
- Body text: text-base lg:text-lg, leading-relaxed
- UI labels: text-sm, font-medium
- Data/metrics: text-3xl, font-bold, font-mono
- Small print/meta: text-xs, text-sm

---

## Layout System

**Spacing Primitives**: Use Tailwind units of 2, 4, 6, 8, 12, 16, 20, 24, 32

**Section Padding**: py-16 md:py-24 lg:py-32 for major sections, py-12 md:py-20 for subsections

**Containers**: max-w-7xl for full-width, max-w-4xl for forms/content, max-w-6xl for dashboards

**Grid Strategy**: 
- Pricing cards: 3-column grid (lg:grid-cols-3)
- Feature showcase: 2-column (lg:grid-cols-2)
- Dashboard stats: 4-column (lg:grid-cols-4)
- Mobile: Always single column

---

## Component Library

### Navigation
**Primary Nav**: Fixed top navigation with shadow, logo left, navigation center, CTA buttons right. Height h-16 with px-6 horizontal padding. Sticky on scroll.

**Dashboard Nav**: Sidebar navigation (w-64) with collapsible sections, icon + label pattern, active state highlighting.

### Forms
**Project Submission Form**: Multi-step wizard pattern (3 steps: Company Info → Content Upload → Goals). Progress indicator at top. Each step uses card-based layout (p-8, rounded-xl, shadow-lg). Field groups with mb-6 spacing.

**Input Fields**: 
- Text inputs: h-12, px-4, rounded-lg, border-2
- Textareas: min-h-32, p-4
- File upload: Drag-and-drop zone, dashed border, min-h-48
- Labels: mb-2, font-medium positioning above inputs

### Dashboards
**Client Dashboard**: 
- Header with project name and status badge
- Stats overview: 4-card grid showing progress, tasks completed, deliverables, next milestone
- Timeline component: Vertical timeline with milestone markers, completed/upcoming indicators
- Deliverables table: Striped rows, sortable headers, download actions
- Invoice section: Card-based layout showing payment history

**Admin Dashboard**:
- Project queue: Kanban-style columns (New, In Progress, Review, Complete)
- Each project card: Compact with client name, value, deadline, assigned team
- Filterable/searchable header
- Quick actions menu per card

### Pricing Section
**Layout**: 3-column tier cards, emphasized middle tier (scale-105, ring-2). Each card includes:
- Tier name (text-2xl, font-bold)
- Price (text-5xl, font-bold) with "/project" suffix
- Feature list with checkmark icons
- CTA button (full-width)
- "Most Popular" badge on Tier 2

### Buttons
**Primary CTA**: px-8, py-4, rounded-lg, font-semibold, text-lg, shadow-lg
**Secondary**: px-6, py-3, rounded-lg, font-medium, border-2
**On-image buttons**: Backdrop blur (backdrop-blur-md), semi-transparent background, text with high contrast

### Cards
**Project Cards**: p-6, rounded-xl, shadow-md, border
**Stat Cards**: p-8, rounded-2xl, gradient subtle background
**Feature Cards**: p-6, rounded-lg, hover lift effect

### Status Indicators
**Badges**: px-3, py-1, rounded-full, text-xs, font-semibold
**Progress Bars**: h-2, rounded-full, relative positioning with percentage indicator

---

## Images

**Hero Image**: Large, professional workspace or abstract data visualization representing SEO/AI optimization. Full-width hero section (min-h-[600px]), image as background with gradient overlay. Place centered headline and CTAs over image with backdrop-blur buttons.

**Dashboard Screenshots**: Include mockup screenshots in the "How It Works" section showing the client dashboard interface. Use device frame mockups, place in 2-column grid with descriptions.

**Trust Signals**: Small client logos or partner badges in footer or after pricing section. Single row, grayscale treatment, h-8 to h-12 sizing.

**Icon Library**: Use Heroicons throughout for UI elements - outline style for navigation, solid style for status indicators.

---

## Page-Specific Layouts

**Landing Page** (7 sections):
1. Hero with image background, headline, subheading, dual CTAs
2. Problem/Solution split (2-column)
3. How It Works (3-step process cards)
4. Pricing tiers (3-column)
5. Features grid (2×3 layout)
6. Social proof (testimonials + client logos)
7. Final CTA section with contact option

**Authentication Pages**: Centered card (max-w-md), minimal navigation, logo at top, form in card, footer links

**Project Submission**: Full-width layout with left sidebar showing progress, main area for form steps, fixed bottom bar with Previous/Next navigation