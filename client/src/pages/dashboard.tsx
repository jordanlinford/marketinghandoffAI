import { useAuth } from "@/hooks/use-auth";
import { useQuery } from "@tanstack/react-query";
import { Link } from "wouter";
import { Navigation } from "@/components/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";
import {
  Plus,
  FolderOpen,
  Clock,
  CheckCircle2,
  AlertCircle,
  FileText,
  TrendingUp,
  ArrowRight,
} from "lucide-react";
import type { Project } from "@shared/schema";

type TaskStats = Record<string, { completed: number; total: number }>;

const statusConfig = {
  pending: { label: "Pending", variant: "secondary" as const, icon: Clock },
  in_progress: { label: "In Progress", variant: "default" as const, icon: TrendingUp },
  review: { label: "Under Review", variant: "outline" as const, icon: AlertCircle },
  completed: { label: "Completed", variant: "secondary" as const, icon: CheckCircle2 },
};

const tierNames = {
  tier_0: "Intro ($1,000)",
  tier_1: "Foundation ($5,000)",
  tier_2: "Growth ($10,000)",
  tier_3: "Enterprise ($15,000)",
};

function ProjectCard({ project, taskStats }: { project: Project; taskStats?: { completed: number; total: number } }) {
  const status = statusConfig[project.status as keyof typeof statusConfig] || statusConfig.pending;
  const StatusIcon = status.icon;
  
  const completedTasks = taskStats?.completed || 0;
  const totalTasks = taskStats?.total || 0;
  const progress = totalTasks > 0 ? (completedTasks / totalTasks) * 100 : 0;

  return (
    <Card className="hover-elevate transition-all duration-200" data-testid={`card-project-${project.id}`}>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            <CardTitle className="text-lg truncate">{project.companyName}</CardTitle>
            <CardDescription className="truncate">{project.website}</CardDescription>
          </div>
          <Badge variant={status.variant} className="shrink-0">
            <StatusIcon className="w-3 h-3 mr-1" />
            {status.label}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          <div>
            <div className="flex items-center justify-between text-sm mb-2">
              <span className="text-muted-foreground">Progress</span>
              <span className="font-medium">{totalTasks > 0 ? `${completedTasks}/${totalTasks} tasks` : "No tasks yet"}</span>
            </div>
            <Progress value={progress} className="h-2" />
          </div>
          
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Package</span>
            <span className="font-medium">{tierNames[project.tier as keyof typeof tierNames]}</span>
          </div>

          <Link href={`/project/${project.id}`}>
            <Button variant="ghost" className="w-full justify-between" data-testid={`button-view-project-${project.id}`}>
              View Details
              <ArrowRight className="h-4 w-4" />
            </Button>
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

function ProjectCardSkeleton() {
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1">
            <Skeleton className="h-5 w-40 mb-2" />
            <Skeleton className="h-4 w-32" />
          </div>
          <Skeleton className="h-6 w-24" />
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          <div>
            <Skeleton className="h-4 w-full mb-2" />
            <Skeleton className="h-2 w-full" />
          </div>
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-9 w-full" />
        </div>
      </CardContent>
    </Card>
  );
}

function StatsCard({ 
  title, 
  value, 
  description, 
  icon: Icon 
}: { 
  title: string; 
  value: string; 
  description: string; 
  icon: React.ElementType;
}) {
  return (
    <Card data-testid={`card-stat-${title.toLowerCase().replace(/\s/g, "-")}`}>
      <CardHeader className="flex flex-row items-center justify-between gap-2 pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-3xl font-bold font-mono">{value}</div>
        <p className="text-xs text-muted-foreground mt-1">{description}</p>
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  const { user, isLoading: authLoading } = useAuth();
  
  const { data: projects, isLoading: projectsLoading } = useQuery<Project[]>({
    queryKey: ["/api/projects"],
    enabled: !!user,
  });

  const { data: taskStats } = useQuery<TaskStats>({
    queryKey: ["/api/projects/stats/tasks"],
    enabled: !!user && !!projects && projects.length > 0,
  });

  const isLoading = authLoading || projectsLoading;

  const totalTasksCompleted = taskStats 
    ? Object.values(taskStats).reduce((sum, s) => sum + s.completed, 0) 
    : 0;

  const activeProjects = projects?.filter(p => p.status !== "completed")?.length || 0;
  const completedProjects = projects?.filter(p => p.status === "completed")?.length || 0;

  return (
    <div className="min-h-screen bg-background">
      <Navigation />

      <main className="max-w-7xl mx-auto px-6 pt-24 pb-12">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-8">
          <div>
            <h1 className="text-3xl font-bold tracking-tight" data-testid="text-dashboard-title">
              Welcome back{user?.firstName ? `, ${user.firstName}` : ""}
            </h1>
            <p className="text-muted-foreground mt-1">
              Manage your optimization projects and track progress.
            </p>
          </div>
          <Link href="/new-project">
            <Button data-testid="button-new-project">
              <Plus className="h-4 w-4 mr-2" />
              New Project
            </Button>
          </Link>
        </div>

        <div className="grid gap-4 md:grid-cols-3 mb-8">
          <StatsCard
            title="Active Projects"
            value={isLoading ? "-" : String(activeProjects)}
            description="Currently in progress"
            icon={FolderOpen}
          />
          <StatsCard
            title="Completed"
            value={isLoading ? "-" : String(completedProjects)}
            description="Successfully delivered"
            icon={CheckCircle2}
          />
          <StatsCard
            title="Tasks Done"
            value={isLoading ? "-" : String(totalTasksCompleted)}
            description="Across all projects"
            icon={FileText}
          />
        </div>

        <div className="mb-6">
          <h2 className="text-xl font-semibold mb-4">Your Projects</h2>
          
          {isLoading ? (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              <ProjectCardSkeleton />
              <ProjectCardSkeleton />
              <ProjectCardSkeleton />
            </div>
          ) : projects && projects.length > 0 ? (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {projects.map((project) => (
                <ProjectCard 
                  key={project.id} 
                  project={project} 
                  taskStats={taskStats?.[project.id]}
                />
              ))}
            </div>
          ) : (
            <Card className="text-center py-12" data-testid="card-empty-state">
              <CardContent>
                <FolderOpen className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
                <h3 className="text-lg font-semibold mb-2">No projects yet</h3>
                <p className="text-muted-foreground mb-6 max-w-sm mx-auto">
                  Start your first optimization project and improve your search visibility.
                </p>
                <Link href="/new-project">
                  <Button data-testid="button-start-first-project">
                    <Plus className="h-4 w-4 mr-2" />
                    Start Your First Project
                  </Button>
                </Link>
              </CardContent>
            </Card>
          )}
        </div>
      </main>
    </div>
  );
}
