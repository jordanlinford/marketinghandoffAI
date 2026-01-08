import type { Express } from "express";
import { createServer, type Server } from "http";
import { storage } from "./storage";
import { isAuthenticated, registerAuthRoutes } from "./replit_integrations/auth";
import { getUncachableStripeClient } from "./stripeClient";
import { projectSubmissionSchema } from "@shared/schema";

// Server-only admin user IDs - persists only for server lifetime
// For MVP, first user to access admin becomes admin
// In production, this should be database-backed
const ADMIN_USER_IDS: string[] = [];

// Helper to check if user is admin
function isAdmin(userId: string): boolean {
  return ADMIN_USER_IDS.includes(userId);
}

// Helper to add admin (only used internally)
function addAdmin(userId: string): void {
  if (!ADMIN_USER_IDS.includes(userId)) {
    ADMIN_USER_IDS.push(userId);
  }
}

const TIER_PRICES: Record<string, number> = {
  tier_1: 500000, // $5,000 in cents
  tier_2: 1000000, // $10,000 in cents
  tier_3: 1500000, // $15,000 in cents
};

const TIER_NAMES: Record<string, string> = {
  tier_1: "Foundation Package",
  tier_2: "Growth Package",
  tier_3: "Enterprise Package",
};

export async function registerRoutes(
  httpServer: Server,
  app: Express
): Promise<Server> {
  // Register auth routes
  registerAuthRoutes(app);

  // Get all projects for current user
  app.get("/api/projects", isAuthenticated, async (req: any, res) => {
    try {
      const userId = req.user.claims.sub;
      const projects = await storage.getProjectsByUserId(userId);
      res.json(projects);
    } catch (error) {
      console.error("Error fetching projects:", error);
      res.status(500).json({ message: "Failed to fetch projects" });
    }
  });

  // Get single project
  app.get("/api/projects/:id", isAuthenticated, async (req: any, res) => {
    try {
      const userId = req.user.claims.sub;
      const project = await storage.getProject(req.params.id);
      
      if (!project) {
        return res.status(404).json({ message: "Project not found" });
      }
      
      // Ensure user owns this project or is admin
      if (project.userId !== userId && !isAdmin(userId)) {
        return res.status(403).json({ message: "Forbidden" });
      }
      
      res.json(project);
    } catch (error) {
      console.error("Error fetching project:", error);
      res.status(500).json({ message: "Failed to fetch project" });
    }
  });

  // Create new project with Stripe checkout
  app.post("/api/projects", isAuthenticated, async (req: any, res) => {
    try {
      const userId = req.user.claims.sub;
      const validatedData = projectSubmissionSchema.parse(req.body);
      
      // Create project in database
      const project = await storage.createProject({
        ...validatedData,
        userId,
        status: "pending",
      });

      // Create Stripe checkout session for one-time payment
      const stripe = await getUncachableStripeClient();
      const tierPrice = TIER_PRICES[validatedData.tier] || TIER_PRICES.tier_1;
      const tierName = TIER_NAMES[validatedData.tier] || TIER_NAMES.tier_1;

      const session = await stripe.checkout.sessions.create({
        payment_method_types: ["card"],
        line_items: [
          {
            price_data: {
              currency: "usd",
              product_data: {
                name: `MarketingHandoffAI - ${tierName}`,
                description: `SEO & AI optimization services for ${validatedData.companyName}`,
              },
              unit_amount: tierPrice,
            },
            quantity: 1,
          },
        ],
        mode: "payment",
        success_url: `${req.protocol}://${req.get("host")}/checkout/success?session_id={CHECKOUT_SESSION_ID}`,
        cancel_url: `${req.protocol}://${req.get("host")}/checkout/cancel`,
        metadata: {
          projectId: project.id,
          userId,
        },
      });

      // Update project with stripe payment ID
      await storage.updateProject(project.id, {
        stripePaymentId: session.id,
        amountPaid: tierPrice / 100,
      });

      // Create default tasks for the project based on tier
      const defaultTasks = getDefaultTasksForTier(validatedData.tier);
      for (let i = 0; i < defaultTasks.length; i++) {
        await storage.createTask({
          projectId: project.id,
          title: defaultTasks[i].title,
          description: defaultTasks[i].description,
          status: "pending",
          order: i,
        });
      }

      res.json({ 
        project, 
        checkoutUrl: session.url 
      });
    } catch (error: any) {
      console.error("Error creating project:", error);
      if (error.name === "ZodError") {
        return res.status(400).json({ message: "Invalid project data", errors: error.errors });
      }
      res.status(500).json({ message: "Failed to create project" });
    }
  });

  // Get task stats for all user projects
  app.get("/api/projects/stats/tasks", isAuthenticated, async (req: any, res) => {
    try {
      const userId = req.user.claims.sub;
      const projects = await storage.getProjectsByUserId(userId);
      
      const stats: Record<string, { completed: number; total: number }> = {};
      
      for (const project of projects) {
        const tasks = await storage.getTasksByProjectId(project.id);
        stats[project.id] = {
          completed: tasks.filter(t => t.status === "completed").length,
          total: tasks.length,
        };
      }
      
      res.json(stats);
    } catch (error) {
      console.error("Error fetching task stats:", error);
      res.status(500).json({ message: "Failed to fetch task stats" });
    }
  });

  // Get tasks for a project
  app.get("/api/projects/:id/tasks", isAuthenticated, async (req: any, res) => {
    try {
      const userId = req.user.claims.sub;
      const project = await storage.getProject(req.params.id);
      
      if (!project) {
        return res.status(404).json({ message: "Project not found" });
      }
      
      // Ensure user owns this project or is admin
      if (project.userId !== userId && !isAdmin(userId)) {
        return res.status(403).json({ message: "Forbidden" });
      }
      
      const tasks = await storage.getTasksByProjectId(req.params.id);
      res.json(tasks);
    } catch (error) {
      console.error("Error fetching tasks:", error);
      res.status(500).json({ message: "Failed to fetch tasks" });
    }
  });

  // Get deliverables for a project
  app.get("/api/projects/:id/deliverables", isAuthenticated, async (req: any, res) => {
    try {
      const userId = req.user.claims.sub;
      const project = await storage.getProject(req.params.id);
      
      if (!project) {
        return res.status(404).json({ message: "Project not found" });
      }
      
      // Ensure user owns this project or is admin
      if (project.userId !== userId && !isAdmin(userId)) {
        return res.status(403).json({ message: "Forbidden" });
      }
      
      const deliverables = await storage.getDeliverablesByProjectId(req.params.id);
      res.json(deliverables);
    } catch (error) {
      console.error("Error fetching deliverables:", error);
      res.status(500).json({ message: "Failed to fetch deliverables" });
    }
  });

  // Admin routes - with role check
  app.get("/api/admin/projects", isAuthenticated, async (req: any, res) => {
    try {
      const userId = req.user.claims.sub;
      
      // MVP bootstrapping: first user to access admin endpoint becomes admin
      if (ADMIN_USER_IDS.length === 0) {
        addAdmin(userId);
        console.log(`First admin bootstrapped: ${userId}`);
      }
      
      // Check if user is admin
      if (!isAdmin(userId)) {
        return res.status(403).json({ message: "Admin access required" });
      }
      
      const projects = await storage.getAllProjects();
      res.json(projects);
    } catch (error) {
      console.error("Error fetching admin projects:", error);
      res.status(500).json({ message: "Failed to fetch projects" });
    }
  });

  app.patch("/api/admin/projects/:id/status", isAuthenticated, async (req: any, res) => {
    try {
      const userId = req.user.claims.sub;
      
      if (!isAdmin(userId)) {
        return res.status(403).json({ message: "Admin access required" });
      }
      
      const { status } = req.body;
      
      if (!["pending", "in_progress", "review", "completed"].includes(status)) {
        return res.status(400).json({ message: "Invalid status" });
      }
      
      const project = await storage.updateProjectStatus(req.params.id, status);
      
      if (!project) {
        return res.status(404).json({ message: "Project not found" });
      }
      
      res.json(project);
    } catch (error) {
      console.error("Error updating project status:", error);
      res.status(500).json({ message: "Failed to update project status" });
    }
  });

  // Admin: Update task status
  app.patch("/api/admin/tasks/:id/status", isAuthenticated, async (req: any, res) => {
    try {
      const userId = req.user.claims.sub;
      
      if (!isAdmin(userId)) {
        return res.status(403).json({ message: "Admin access required" });
      }
      
      const { status } = req.body;
      
      if (!["pending", "in_progress", "completed"].includes(status)) {
        return res.status(400).json({ message: "Invalid status" });
      }
      
      const task = await storage.updateTaskStatus(req.params.id, status);
      
      if (!task) {
        return res.status(404).json({ message: "Task not found" });
      }
      
      res.json(task);
    } catch (error) {
      console.error("Error updating task status:", error);
      res.status(500).json({ message: "Failed to update task status" });
    }
  });

  // Admin: Add deliverable
  app.post("/api/admin/projects/:id/deliverables", isAuthenticated, async (req: any, res) => {
    try {
      const userId = req.user.claims.sub;
      
      if (!isAdmin(userId)) {
        return res.status(403).json({ message: "Admin access required" });
      }
      
      const project = await storage.getProject(req.params.id);
      
      if (!project) {
        return res.status(404).json({ message: "Project not found" });
      }
      
      const deliverable = await storage.createDeliverable({
        projectId: req.params.id,
        title: req.body.title,
        description: req.body.description,
        fileUrl: req.body.fileUrl,
        fileType: req.body.fileType,
      });
      
      res.json(deliverable);
    } catch (error) {
      console.error("Error creating deliverable:", error);
      res.status(500).json({ message: "Failed to create deliverable" });
    }
  });

  // Stripe webhook for checkout completion
  app.post("/api/stripe/checkout-complete", async (req: any, res) => {
    try {
      const { sessionId } = req.body;
      
      if (!sessionId) {
        return res.status(400).json({ message: "Session ID required" });
      }
      
      const stripe = await getUncachableStripeClient();
      const session = await stripe.checkout.sessions.retrieve(sessionId);
      
      if (session.payment_status === "paid" && session.metadata?.projectId) {
        // Update project status to in_progress after successful payment
        await storage.updateProjectStatus(session.metadata.projectId, "in_progress");
      }
      
      res.json({ success: true });
    } catch (error) {
      console.error("Error processing checkout completion:", error);
      res.status(500).json({ message: "Failed to process checkout" });
    }
  });

  return httpServer;
}

function getDefaultTasksForTier(tier: string): { title: string; description: string }[] {
  const baseTasks = [
    { title: "Initial SEO Audit", description: "Comprehensive analysis of current SEO performance" },
    { title: "Keyword Research & Refinement", description: "Identify and prioritize target keywords" },
    { title: "Content Optimization", description: "Optimize titles, meta descriptions, and content structure" },
    { title: "On-page AI Readiness", description: "Prepare content for AI search engines" },
    { title: "Search Ecosystem Submission", description: "Submit to major search engines and directories" },
  ];

  if (tier === "tier_2" || tier === "tier_3") {
    baseTasks.push(
      { title: "Link Placement Outreach", description: "Reach out to relevant sites for backlinks" },
      { title: "Syndication Strategy", description: "Content distribution across platforms" },
      { title: "Technical SEO Checklist", description: "Fix technical SEO issues" },
      { title: "Authority Signal Report", description: "Analysis of domain authority improvements" }
    );
  }

  if (tier === "tier_3") {
    baseTasks.push(
      { title: "AI Search Optimization Audit", description: "Deep dive into AI discoverability" },
      { title: "Advanced Placements", description: "Premium placement opportunities" },
      { title: "Monthly Review Meeting 1", description: "First monthly progress review" },
      { title: "Monthly Review Meeting 2", description: "Second monthly progress review" },
      { title: "Quarterly Follow-up Report", description: "Comprehensive quarterly analysis" }
    );
  }

  return baseTasks;
}
