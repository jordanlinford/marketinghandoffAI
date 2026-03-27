import { useQuery } from "@tanstack/react-query";
import { useRoute, Link } from "wouter";
import { Navigation } from "@/components/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import {
  ArrowLeft,
  Clock,
  CheckCircle2,
  AlertCircle,
  TrendingUp,
  FileText,
  FileSpreadsheet,
  ExternalLink,
  Calendar,
} from "lucide-react";
import type { Project, ProjectTask, Deliverable } from "@shared/schema";

const statusConfig = {
  pending: { label: "Pending", variant: "secondary" as const, icon: Clock },
  in_progress: { label: "In Progress", variant: "default" as const, icon: TrendingUp },
  review: { label: "Under Review", variant: "outline" as const, icon: AlertCircle },
  completed: { label: "Completed", variant: "secondary" as const, icon: CheckCircle2 },
};

const tierNames = {
  tier_0: "Intro",
  tier_1: "Foundation",
  tier_2: "Growth",
  tier_3: "Enterprise",
};

const tierPrices = {
  tier_0: 1000,
  tier_1: 5000,
  tier_2: 10000,
  tier_3: 15000,
};

function TaskItem({ task }: { task: ProjectTask }) {
  const isCompleted = task.status === "completed";
  
  return (
    <div className="flex items-start gap-3 py-3" data-testid={`task-${task.id}`}>
      <div
        className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 mt-0.5 ${
          isCompleted ? "bg-primary text-primary-foreground" : "bg-muted"
        }`}
      >
        {isCompleted ? (
          <CheckCircle2 className="h-4 w-4" />
        ) : (
          <Clock className="h-4 w-4 text-muted-foreground" />
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className={`font-medium ${isCompleted ? "line-through text-muted-foreground" : ""}`}>
          {task.title}
        </p>
        {task.description && (
          <p className="text-sm text-muted-foreground mt-1">{task.description}</p>
        )}
      </div>
    </div>
  );
}

const fileTypeConfig: Record<string, { icon: React.ElementType; color: string; bg: string }> = {
  PDF:        { icon: FileText,        color: "text-red-500",   bg: "bg-red-500/10" },
  Spreadsheet:{ icon: FileSpreadsheet, color: "text-green-600", bg: "bg-green-500/10" },
  Doc:        { icon: FileText,        color: "text-blue-500",  bg: "bg-blue-500/10" },
};

function DeliverableItem({ deliverable }: { deliverable: Deliverable }) {
  const type = deliverable.fileType || "PDF";
  const cfg = fileTypeConfig[type] || fileTypeConfig["PDF"];
  const Icon = cfg.icon;

  return (
    <div className="flex items-start justify-between gap-4 py-4" data-testid={`deliverable-${deliverable.id}`}>
      <div className="flex items-start gap-3 flex-1 min-w-0">
        <div className={`w-10 h-10 rounded-lg ${cfg.bg} flex items-center justify-center shrink-0 mt-0.5`}>
          <Icon className={`h-5 w-5 ${cfg.color}`} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="font-medium">{deliverable.title}</p>
            <Badge variant="secondary" className="text-xs shrink-0">{type}</Badge>
          </div>
          {deliverable.description && (
            <p className="text-sm text-muted-foreground mt-1 leading-relaxed">{deliverable.description}</p>
          )}
          {deliverable.createdAt && (
            <p className="text-xs text-muted-foreground mt-1">
              Delivered {new Date(deliverable.createdAt).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })}
            </p>
          )}
        </div>
      </div>
      {deliverable.fileUrl && (
        <Button variant="outline" size="sm" className="shrink-0" asChild>
          <a href={deliverable.fileUrl} target="_blank" rel="noopener noreferrer">
            <ExternalLink className="h-3.5 w-3.5 mr-1.5" />
            Open
          </a>
        </Button>
      )}
    </div>
  );
}

export default function ProjectDetailPage() {
  const [, params] = useRoute("/project/:id");
  const projectId = params?.id;

  const { data: project, isLoading: projectLoading } = useQuery<Project>({
    queryKey: ["/api/projects", projectId],
    enabled: !!projectId,
  });

  const { data: tasks, isLoading: tasksLoading } = useQuery<ProjectTask[]>({
    queryKey: ["/api/projects", projectId, "tasks"],
    enabled: !!projectId,
  });

  const { data: deliverables, isLoading: deliverablesLoading } = useQuery<Deliverable[]>({
    queryKey: ["/api/projects", projectId, "deliverables"],
    enabled: !!projectId,
  });

  const isLoading = projectLoading || tasksLoading || deliverablesLoading;

  if (isLoading) {
    return (
      <div className="min-h-screen bg-background">
        <Navigation />
        <main className="max-w-6xl mx-auto px-6 pt-24 pb-12">
          <Skeleton className="h-8 w-48 mb-4" />
          <Skeleton className="h-12 w-96 mb-8" />
          <div className="grid md:grid-cols-3 gap-6">
            <Skeleton className="h-48 md:col-span-2" />
            <Skeleton className="h-48" />
          </div>
        </main>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="min-h-screen bg-background">
        <Navigation />
        <main className="max-w-6xl mx-auto px-6 pt-24 pb-12 text-center">
          <h1 className="text-2xl font-bold mb-4">Project Not Found</h1>
          <p className="text-muted-foreground mb-6">
            The project you're looking for doesn't exist or you don't have access to it.
          </p>
          <Link href="/dashboard">
            <Button>Back to Dashboard</Button>
          </Link>
        </main>
      </div>
    );
  }

  const status = statusConfig[project.status as keyof typeof statusConfig] || statusConfig.pending;
  const StatusIcon = status.icon;
  const completedTasks = tasks?.filter((t) => t.status === "completed").length || 0;
  const totalTasks = tasks?.length || 0;
  const progress = totalTasks > 0 ? (completedTasks / totalTasks) * 100 : 0;

  return (
    <div className="min-h-screen bg-background">
      <Navigation />

      <main className="max-w-6xl mx-auto px-6 pt-24 pb-12">
        <Link href="/dashboard">
          <Button variant="ghost" className="mb-4" data-testid="button-back">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Dashboard
          </Button>
        </Link>

        <div className="flex flex-col md:flex-row md:items-start justify-between gap-4 mb-8">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <h1 className="text-3xl font-bold tracking-tight" data-testid="text-project-title">
                {project.companyName}
              </h1>
              <Badge variant={status.variant}>
                <StatusIcon className="w-3 h-3 mr-1" />
                {status.label}
              </Badge>
            </div>
            <a
              href={project.website}
              target="_blank"
              rel="noopener noreferrer"
              className="text-muted-foreground hover:text-foreground flex items-center gap-1"
            >
              {project.website}
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        </div>

        <div className="grid md:grid-cols-3 gap-6">
          <div className="md:col-span-2 space-y-6">
            <Card data-testid="card-project-progress">
              <CardHeader>
                <CardTitle>Project Progress</CardTitle>
                <CardDescription>
                  {completedTasks} of {totalTasks} tasks completed
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Progress value={progress} className="h-2 mb-6" />
                
                {tasks && tasks.length > 0 ? (
                  <div className="divide-y divide-border">
                    {tasks.map((task) => (
                      <TaskItem key={task.id} task={task} />
                    ))}
                  </div>
                ) : (
                  <div className="text-center py-8 text-muted-foreground">
                    <Clock className="h-12 w-12 mx-auto mb-4 opacity-50" />
                    <p>Tasks will appear here once your project begins.</p>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card data-testid="card-deliverables">
              <CardHeader>
                <CardTitle>Deliverables</CardTitle>
                <CardDescription>Files and reports from your project.</CardDescription>
              </CardHeader>
              <CardContent>
                {deliverables && deliverables.length > 0 ? (
                  <div className="divide-y divide-border">
                    {deliverables.map((deliverable) => (
                      <DeliverableItem key={deliverable.id} deliverable={deliverable} />
                    ))}
                  </div>
                ) : (
                  <div className="text-center py-8 text-muted-foreground">
                    <FileText className="h-12 w-12 mx-auto mb-4 opacity-50" />
                    <p>Deliverables will be uploaded here as work progresses.</p>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          <div className="space-y-6">
            <Card data-testid="card-project-details">
              <CardHeader>
                <CardTitle>Project Details</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <p className="text-sm text-muted-foreground">Package</p>
                  <p className="font-medium">
                    {tierNames[project.tier as keyof typeof tierNames]} - $
                    {tierPrices[project.tier as keyof typeof tierPrices]?.toLocaleString()}
                  </p>
                </div>
                <Separator />
                <div>
                  <p className="text-sm text-muted-foreground">Created</p>
                  <p className="font-medium flex items-center gap-2">
                    <Calendar className="h-4 w-4" />
                    {project.createdAt
                      ? new Date(project.createdAt).toLocaleDateString()
                      : "N/A"}
                  </p>
                </div>
                <Separator />
                <div>
                  <p className="text-sm text-muted-foreground mb-2">Primary Keywords</p>
                  <div className="flex flex-wrap gap-2">
                    {project.primaryKeywords?.map((keyword) => (
                      <Badge key={keyword} variant="secondary">
                        {keyword}
                      </Badge>
                    ))}
                  </div>
                </div>
                {project.goals && project.goals.length > 0 && (
                  <>
                    <Separator />
                    <div>
                      <p className="text-sm text-muted-foreground mb-2">Goals</p>
                      <ul className="text-sm space-y-1">
                        {project.goals.map((goal) => (
                          <li key={goal} className="flex items-center gap-2">
                            <CheckCircle2 className="h-3 w-3 text-primary" />
                            {goal.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase())}
                          </li>
                        ))}
                      </ul>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>

            {project.additionalNotes && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Additional Notes</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-sm text-muted-foreground">{project.additionalNotes}</p>
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
