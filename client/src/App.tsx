import { Switch, Route } from "wouter";
import { queryClient } from "./lib/queryClient";
import { QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ThemeProvider } from "@/components/theme-provider";
import { useAuth } from "@/hooks/use-auth";

import LandingPage from "@/pages/landing";
import DashboardPage from "@/pages/dashboard";
import NewProjectPage from "@/pages/new-project";
import ProjectDetailPage from "@/pages/project-detail";
import AdminPage from "@/pages/admin";
import CheckoutSuccessPage from "@/pages/checkout-success";
import CheckoutCancelPage from "@/pages/checkout-cancel";
import TermsPage from "@/pages/terms";
import PrivacyPage from "@/pages/privacy";
import SecurityPage from "@/pages/security";
import NotFound from "@/pages/not-found";

function AuthenticatedRoute({ component: Component }: { component: React.ComponentType }) {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="animate-pulse text-muted-foreground">Loading...</div>
      </div>
    );
  }

  if (!isAuthenticated) {
    window.location.href = "/api/login";
    return null;
  }

  return <Component />;
}

function Router() {
  return (
    <Switch>
      <Route path="/" component={LandingPage} />
      <Route path="/dashboard">
        {() => <AuthenticatedRoute component={DashboardPage} />}
      </Route>
      <Route path="/new-project">
        {() => <AuthenticatedRoute component={NewProjectPage} />}
      </Route>
      <Route path="/project/:id">
        {() => <AuthenticatedRoute component={ProjectDetailPage} />}
      </Route>
      <Route path="/admin">
        {() => <AuthenticatedRoute component={AdminPage} />}
      </Route>
      <Route path="/checkout/success" component={CheckoutSuccessPage} />
      <Route path="/checkout/cancel" component={CheckoutCancelPage} />
      <Route path="/terms" component={TermsPage} />
      <Route path="/privacy" component={PrivacyPage} />
      <Route path="/security" component={SecurityPage} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider defaultTheme="system" storageKey="marketinghandoff-theme">
        <TooltipProvider>
          <Toaster />
          <Router />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}

export default App;
