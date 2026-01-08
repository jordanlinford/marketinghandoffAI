import { useQuery, useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Navigation } from "@/components/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { apiRequest, queryClient } from "@/lib/queryClient";
import {
  Search,
  Clock,
  CheckCircle2,
  AlertCircle,
  TrendingUp,
  ExternalLink,
  MoreVertical,
  Users,
  DollarSign,
  FolderOpen,
} from "lucide-react";
import type { Project } from "@shared/schema";

const statusConfig = {
  pending: { label: "Pending", variant: "secondary" as const, icon: Clock, color: "bg-yellow-500" },
  in_progress: { label: "In Progress", variant: "default" as const, icon: TrendingUp, color: "bg-blue-500" },
  review: { label: "Review", variant: "outline" as const, icon: AlertCircle, color: "bg-purple-500" },
  completed: { label: "Completed", variant: "secondary" as const, icon: CheckCircle2, color: "bg-green-500" },
};

const tierNames = {
  tier_1: "Foundation",
  tier_2: "Growth",
  tier_3: "Enterprise",
};

const tierPrices = {
  tier_1: 5000,
  tier_2: 10000,
  tier_3: 15000,
};

function ProjectCard({ 
  project, 
  onStatusChange 
}: { 
  project: Project; 
  onStatusChange: (id: string, status: string) => void;
}) {
  const status = statusConfig[project.status as keyof typeof statusConfig] || statusConfig.pending;
  const StatusIcon = status.icon;

  return (
    <Card className="hover-elevate" data-testid={`admin-card-project-${project.id}`}>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <CardTitle className="text-base truncate">{project.companyName}</CardTitle>
            <a
              href={project.website}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
            >
              {new URL(project.website).hostname}
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
          <Badge variant={status.variant} className="shrink-0">
            <StatusIcon className="w-3 h-3 mr-1" />
            {status.label}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Package</span>
            <span className="font-medium">
              {tierNames[project.tier as keyof typeof tierNames]}
            </span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Value</span>
            <span className="font-medium font-mono">
              ${tierPrices[project.tier as keyof typeof tierPrices]?.toLocaleString()}
            </span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Created</span>
            <span className="font-medium">
              {project.createdAt
                ? new Date(project.createdAt).toLocaleDateString()
                : "N/A"}
            </span>
          </div>

          <div className="pt-2">
            <Select
              value={project.status}
              onValueChange={(value) => onStatusChange(project.id, value)}
            >
              <SelectTrigger className="w-full" data-testid={`select-status-${project.id}`}>
                <SelectValue placeholder="Update Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="pending">Pending</SelectItem>
                <SelectItem value="in_progress">In Progress</SelectItem>
                <SelectItem value="review">Review</SelectItem>
                <SelectItem value="completed">Completed</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function StatsCard({ 
  title, 
  value, 
  icon: Icon,
  description,
}: { 
  title: string; 
  value: string; 
  icon: React.ElementType;
  description?: string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2 pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-3xl font-bold font-mono">{value}</div>
        {description && (
          <p className="text-xs text-muted-foreground mt-1">{description}</p>
        )}
      </CardContent>
    </Card>
  );
}

export default function AdminPage() {
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const { toast } = useToast();

  const { data: projects, isLoading } = useQuery<Project[]>({
    queryKey: ["/api/admin/projects"],
  });

  const updateStatusMutation = useMutation({
    mutationFn: async ({ id, status }: { id: string; status: string }) => {
      const response = await apiRequest("PATCH", `/api/admin/projects/${id}/status`, { status });
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["/api/admin/projects"] });
      toast({
        title: "Status updated",
        description: "Project status has been updated successfully.",
      });
    },
    onError: (error: Error) => {
      toast({
        title: "Error",
        description: error.message || "Failed to update status.",
        variant: "destructive",
      });
    },
  });

  const handleStatusChange = (id: string, status: string) => {
    updateStatusMutation.mutate({ id, status });
  };

  const filteredProjects = projects?.filter((project) => {
    const matchesSearch =
      project.companyName.toLowerCase().includes(searchQuery.toLowerCase()) ||
      project.website.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus = statusFilter === "all" || project.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  const projectsByStatus = {
    pending: filteredProjects?.filter((p) => p.status === "pending") || [],
    in_progress: filteredProjects?.filter((p) => p.status === "in_progress") || [],
    review: filteredProjects?.filter((p) => p.status === "review") || [],
    completed: filteredProjects?.filter((p) => p.status === "completed") || [],
  };

  const totalRevenue = projects?.reduce(
    (sum, p) => sum + (tierPrices[p.tier as keyof typeof tierPrices] || 0),
    0
  ) || 0;

  const activeProjects = projects?.filter((p) => p.status !== "completed").length || 0;

  return (
    <div className="min-h-screen bg-background">
      <Navigation />

      <main className="max-w-7xl mx-auto px-6 pt-24 pb-12">
        <div className="mb-8">
          <h1 className="text-3xl font-bold tracking-tight" data-testid="text-admin-title">
            Admin Dashboard
          </h1>
          <p className="text-muted-foreground mt-1">
            Manage all projects and track progress.
          </p>
        </div>

        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4 mb-8">
          <StatsCard
            title="Total Projects"
            value={isLoading ? "-" : String(projects?.length || 0)}
            icon={FolderOpen}
            description="All time"
          />
          <StatsCard
            title="Active Projects"
            value={isLoading ? "-" : String(activeProjects)}
            icon={TrendingUp}
            description="In progress"
          />
          <StatsCard
            title="Total Revenue"
            value={isLoading ? "-" : `$${totalRevenue.toLocaleString()}`}
            icon={DollarSign}
            description="Project value"
          />
          <StatsCard
            title="Clients"
            value={isLoading ? "-" : String(new Set(projects?.map((p) => p.userId)).size)}
            icon={Users}
            description="Unique clients"
          />
        </div>

        <div className="flex flex-col sm:flex-row gap-4 mb-6">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search projects..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10"
              data-testid="input-search-projects"
            />
          </div>
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-full sm:w-48" data-testid="select-filter-status">
              <SelectValue placeholder="Filter by status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Status</SelectItem>
              <SelectItem value="pending">Pending</SelectItem>
              <SelectItem value="in_progress">In Progress</SelectItem>
              <SelectItem value="review">Review</SelectItem>
              <SelectItem value="completed">Completed</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {isLoading ? (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="space-y-4">
                <Skeleton className="h-8 w-32" />
                <Skeleton className="h-48" />
                <Skeleton className="h-48" />
              </div>
            ))}
          </div>
        ) : (
          <Tabs defaultValue="kanban" className="space-y-4">
            <TabsList>
              <TabsTrigger value="kanban" data-testid="tab-kanban">Kanban View</TabsTrigger>
              <TabsTrigger value="list" data-testid="tab-list">List View</TabsTrigger>
            </TabsList>

            <TabsContent value="kanban">
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                {Object.entries(projectsByStatus).map(([status, statusProjects]) => {
                  const config = statusConfig[status as keyof typeof statusConfig];
                  return (
                    <div key={status} className="space-y-4">
                      <div className="flex items-center gap-2">
                        <div className={`w-3 h-3 rounded-full ${config.color}`} />
                        <h3 className="font-semibold">{config.label}</h3>
                        <Badge variant="secondary" className="ml-auto">
                          {statusProjects.length}
                        </Badge>
                      </div>
                      <div className="space-y-4">
                        {statusProjects.map((project) => (
                          <ProjectCard
                            key={project.id}
                            project={project}
                            onStatusChange={handleStatusChange}
                          />
                        ))}
                        {statusProjects.length === 0 && (
                          <Card className="border-dashed">
                            <CardContent className="py-8 text-center text-muted-foreground text-sm">
                              No projects
                            </CardContent>
                          </Card>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </TabsContent>

            <TabsContent value="list">
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {filteredProjects?.map((project) => (
                  <ProjectCard
                    key={project.id}
                    project={project}
                    onStatusChange={handleStatusChange}
                  />
                ))}
                {filteredProjects?.length === 0 && (
                  <Card className="col-span-full text-center py-12">
                    <CardContent>
                      <FolderOpen className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
                      <p className="text-muted-foreground">No projects found.</p>
                    </CardContent>
                  </Card>
                )}
              </div>
            </TabsContent>
          </Tabs>
        )}
      </main>
    </div>
  );
}
