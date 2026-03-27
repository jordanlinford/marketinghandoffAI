import { db } from "./db";
import { projects, projectTasks, deliverables } from "@shared/schema";
import { eq, and, ilike } from "drizzle-orm";
import { log } from "./index";

const DEMO_USER_ID = "49339926";
const DEMO_WEBSITE_PATTERN = "%onit.com%";

const DEMO_TASKS = [
  { title: "Initial SEO Audit", description: "Comprehensive analysis of current SEO performance including crawl errors, Core Web Vitals, and competitor gap analysis", status: "completed", daysAgo: 40 },
  { title: "Keyword Research & Refinement", description: "Identify and prioritize 80+ target keywords across legal ops, contract management, and e-billing categories", status: "completed", daysAgo: 35 },
  { title: "Content Optimization", description: "Optimize 12 core landing pages including titles, meta descriptions, H-tags, and schema markup", status: "completed", daysAgo: 28 },
  { title: "On-page AI Readiness", description: "Structure content with FAQ schema, entity markup, and conversational query alignment for AI search engines", status: "completed", daysAgo: 20 },
  { title: "Search Ecosystem Submission", description: "Submit sitemap and structured data to Google Search Console, Bing Webmaster Tools, and AI index registries", status: "completed", daysAgo: 18 },
  { title: "Link Placement Outreach", description: "Identify and contact 30+ relevant legal tech publications and directories for backlink placement", status: "completed", daysAgo: 10 },
  { title: "Syndication Strategy", description: "Distribute 5 cornerstone articles across LinkedIn, JD Supra, and legal tech syndication partners", status: "in_progress", daysAgo: null },
  { title: "Technical SEO Checklist", description: "Resolve 14 identified technical issues including redirect chains, canonical tags, and page speed optimizations", status: "in_progress", daysAgo: null },
  { title: "Authority Signal Report", description: "Baseline domain authority measurement and month-1 improvement report", status: "pending", daysAgo: null },
  { title: "AI Search Optimization Audit", description: "Deep-dive analysis of how Onit appears in ChatGPT, Perplexity, and Google AI Overviews responses", status: "pending", daysAgo: null },
  { title: "Advanced Placements", description: "Secure premium placements in G2, Capterra, and tier-1 legal tech media sites", status: "pending", daysAgo: null },
  { title: "Monthly Review Meeting 1", description: "First monthly progress review — KPI analysis, ranking improvements, and strategy adjustments", status: "pending", daysAgo: null },
  { title: "Monthly Review Meeting 2", description: "Second monthly progress review — content performance, backlink velocity, and AI citation tracking", status: "pending", daysAgo: null },
  { title: "Quarterly Follow-up Report", description: "Comprehensive quarterly analysis with ROI summary, ranking gains, and 90-day roadmap", status: "pending", daysAgo: null },
];

const DEMO_DELIVERABLES = [
  {
    title: "Initial SEO & Technical Audit — Onit.com",
    description: "Full crawl audit uncovering 23 technical issues (12 critical), Core Web Vitals failures on 8 key pages, and a 340-keyword gap vs. top competitors. Includes a prioritized fix list and 90-day SEO roadmap.",
    fileUrl: "https://docs.google.com/document/d/1BzKpR8mNvT3qYoJx5hGaWcDfIeL2nMs7rPuF4wH9tSqZ/edit",
    fileType: "PDF",
    daysAgo: 35,
  },
  {
    title: "Keyword Strategy & Opportunity Matrix",
    description: '84 prioritized keywords with monthly search volume, keyword difficulty scores, current ranking positions, and projected traffic gains. Top opportunity: "legal operations software" (3,600 searches/mo, KD 42). Mapped to 12 content assets for optimization.',
    fileUrl: "https://docs.google.com/spreadsheets/d/1CaLqS9nOwV4rBmZrMy7kIcCeHjJg3pOw0uTyI8zL3xTuE/edit",
    fileType: "Spreadsheet",
    daysAgo: 28,
  },
  {
    title: "Month 1 Progress Report — Rankings, Links & AI Visibility",
    description: "Organic impressions up 34% month-over-month. 6 editorial backlinks secured (DA range: 40–72 — JD Supra, Legal Tech News, G2). 12 pages fully re-optimized. 18 target keywords moved into top-30 positions. Onit now cited in 4 out of 10 tested Perplexity queries for \"legal ops software.\"",
    fileUrl: "https://docs.google.com/document/d/1DdMtU0pQiX6sOoBtNa9mKeBgKh4qPw1vSzJ7yM4xVuF/edit",
    fileType: "PDF",
    daysAgo: 10,
  },
];

function daysAgo(days: number): Date {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d;
}

export async function seedDemoData() {
  try {
    const [onitProject] = await db
      .select()
      .from(projects)
      .where(and(
        eq(projects.userId, DEMO_USER_ID),
        ilike(projects.website, DEMO_WEBSITE_PATTERN)
      ))
      .limit(1);

    if (!onitProject) {
      log("No Onit project found — skipping demo seed", "seed");
      return;
    }

    const projectId = onitProject.id;

    // Update the project to correct tier and status
    await db
      .update(projects)
      .set({
        companyName: "Onit, Inc.",
        tier: "tier_3",
        status: "in_progress",
        amountPaid: 15000,
        stripePaymentId: "cs_live_a1TnX9pQr2mVkWz4oJsYdBcGeLfH7iUe3hNqA8vF5tRwP6KbS0MjC",
        updatedAt: new Date(),
      })
      .where(eq(projects.id, projectId));

    // Seed tasks
    const existingTasks = await db
      .select()
      .from(projectTasks)
      .where(eq(projectTasks.projectId, projectId));

    if (existingTasks.length === 0) {
      for (let i = 0; i < DEMO_TASKS.length; i++) {
        const t = DEMO_TASKS[i];
        await db.insert(projectTasks).values({
          projectId,
          title: t.title,
          description: t.description,
          status: t.status,
          order: i + 1,
          completedAt: t.status === "completed" && t.daysAgo ? daysAgo(t.daysAgo) : null,
        });
      }
      log(`Seeded ${DEMO_TASKS.length} tasks for Onit project`, "seed");
    } else if (existingTasks.length < DEMO_TASKS.length) {
      await db.delete(projectTasks).where(eq(projectTasks.projectId, projectId));
      for (let i = 0; i < DEMO_TASKS.length; i++) {
        const t = DEMO_TASKS[i];
        await db.insert(projectTasks).values({
          projectId,
          title: t.title,
          description: t.description,
          status: t.status,
          order: i + 1,
          completedAt: t.status === "completed" && t.daysAgo ? daysAgo(t.daysAgo) : null,
        });
      }
      log(`Re-seeded ${DEMO_TASKS.length} tasks for Onit project`, "seed");
    } else {
      log("Onit tasks already seeded — skipping task seed", "seed");
    }

    // Seed deliverables
    const existingDeliverables = await db
      .select()
      .from(deliverables)
      .where(eq(deliverables.projectId, projectId));

    if (existingDeliverables.length === 0) {
      for (const d of DEMO_DELIVERABLES) {
        await db.insert(deliverables).values({
          projectId,
          title: d.title,
          description: d.description,
          fileUrl: d.fileUrl,
          fileType: d.fileType,
        });
      }
      log(`Seeded ${DEMO_DELIVERABLES.length} deliverables for Onit project`, "seed");
    } else {
      log("Onit deliverables already seeded — skipping", "seed");
    }

    log("Demo data seed complete", "seed");
  } catch (err: any) {
    log(`Demo seed error: ${err.message}`, "seed");
  }
}
