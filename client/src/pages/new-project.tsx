import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { useLocation } from "wouter";
import { Navigation } from "@/components/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { useToast } from "@/hooks/use-toast";
import { apiRequest, queryClient } from "@/lib/queryClient";
import { projectSubmissionSchema, type ProjectSubmission } from "@shared/schema";
import { 
  ArrowLeft, 
  ArrowRight, 
  Building2, 
  Globe, 
  Search, 
  Target, 
  CheckCircle,
  X,
  Plus,
  Loader2
} from "lucide-react";

const goals = [
  { id: "seo_audit", label: "SEO Audit & Optimization" },
  { id: "ai_readiness", label: "AI Search Readiness" },
  { id: "content_optimization", label: "Content Optimization" },
  { id: "link_building", label: "Link Building & Outreach" },
  { id: "technical_seo", label: "Technical SEO Improvements" },
  { id: "keyword_strategy", label: "Keyword Strategy" },
];

const tiers = [
  {
    id: "tier_1",
    name: "Foundation",
    price: 5000,
    description: "Essential SEO optimization",
  },
  {
    id: "tier_2",
    name: "Growth",
    price: 10000,
    description: "SEO + outreach package",
  },
  {
    id: "tier_3",
    name: "Enterprise",
    price: 15000,
    description: "Full-service optimization",
  },
];

const steps = [
  { id: 1, title: "Company Info", icon: Building2 },
  { id: 2, title: "Keywords & Content", icon: Search },
  { id: 3, title: "Goals & Package", icon: Target },
];

export default function NewProjectPage() {
  const [currentStep, setCurrentStep] = useState(1);
  const [keywordInput, setKeywordInput] = useState("");
  const [urlInput, setUrlInput] = useState("");
  const [, setLocation] = useLocation();
  const { toast } = useToast();

  const form = useForm<ProjectSubmission>({
    resolver: zodResolver(projectSubmissionSchema),
    defaultValues: {
      companyName: "",
      website: "",
      primaryKeywords: [],
      contentUrls: [],
      goals: [],
      additionalNotes: "",
      tier: "tier_1",
    },
  });

  const createProjectMutation = useMutation({
    mutationFn: async (data: ProjectSubmission) => {
      const response = await apiRequest("POST", "/api/projects", data);
      return response.json();
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["/api/projects"] });
      if (data.checkoutUrl) {
        window.location.href = data.checkoutUrl;
      } else {
        toast({
          title: "Project created!",
          description: "Your project has been submitted successfully.",
        });
        setLocation("/dashboard");
      }
    },
    onError: (error: Error) => {
      toast({
        title: "Error",
        description: error.message || "Failed to create project. Please try again.",
        variant: "destructive",
      });
    },
  });

  const addKeyword = () => {
    const keyword = keywordInput.trim();
    if (keyword && !form.getValues("primaryKeywords").includes(keyword)) {
      form.setValue("primaryKeywords", [...form.getValues("primaryKeywords"), keyword]);
      setKeywordInput("");
    }
  };

  const removeKeyword = (keyword: string) => {
    form.setValue(
      "primaryKeywords",
      form.getValues("primaryKeywords").filter((k) => k !== keyword)
    );
  };

  const addUrl = () => {
    const url = urlInput.trim();
    if (url) {
      try {
        new URL(url);
        const currentUrls = form.getValues("contentUrls") || [];
        if (!currentUrls.includes(url)) {
          form.setValue("contentUrls", [...currentUrls, url]);
          setUrlInput("");
        }
      } catch {
        toast({
          title: "Invalid URL",
          description: "Please enter a valid URL.",
          variant: "destructive",
        });
      }
    }
  };

  const removeUrl = (url: string) => {
    form.setValue(
      "contentUrls",
      (form.getValues("contentUrls") || []).filter((u) => u !== url)
    );
  };

  const nextStep = async () => {
    let isValid = true;
    
    if (currentStep === 1) {
      isValid = await form.trigger(["companyName", "website"]);
    } else if (currentStep === 2) {
      isValid = await form.trigger(["primaryKeywords"]);
    }

    if (isValid) {
      setCurrentStep((prev) => Math.min(prev + 1, 3));
    }
  };

  const prevStep = () => {
    setCurrentStep((prev) => Math.max(prev - 1, 1));
  };

  const onSubmit = (data: ProjectSubmission) => {
    createProjectMutation.mutate(data);
  };

  const progress = (currentStep / 3) * 100;

  return (
    <div className="min-h-screen bg-background">
      <Navigation />

      <main className="max-w-4xl mx-auto px-6 pt-24 pb-12">
        <div className="mb-8">
          <Button
            variant="ghost"
            onClick={() => setLocation("/dashboard")}
            className="mb-4"
            data-testid="button-back"
          >
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Dashboard
          </Button>
          <h1 className="text-3xl font-bold tracking-tight" data-testid="text-new-project-title">
            Start a New Project
          </h1>
          <p className="text-muted-foreground mt-1">
            Tell us about your business and optimization goals.
          </p>
        </div>

        <div className="mb-8">
          <div className="flex items-center justify-between mb-4">
            {steps.map((step, index) => (
              <div key={step.id} className="flex items-center flex-1">
                <div className="flex items-center gap-3">
                  <div
                    className={`w-10 h-10 rounded-full flex items-center justify-center ${
                      currentStep >= step.id
                        ? "bg-primary text-primary-foreground"
                        : "bg-muted text-muted-foreground"
                    }`}
                  >
                    {currentStep > step.id ? (
                      <CheckCircle className="h-5 w-5" />
                    ) : (
                      <step.icon className="h-5 w-5" />
                    )}
                  </div>
                  <span
                    className={`text-sm font-medium hidden sm:block ${
                      currentStep >= step.id ? "text-foreground" : "text-muted-foreground"
                    }`}
                  >
                    {step.title}
                  </span>
                </div>
                {index < steps.length - 1 && (
                  <div className="flex-1 h-0.5 bg-muted mx-4" />
                )}
              </div>
            ))}
          </div>
          <Progress value={progress} className="h-1" />
        </div>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)}>
            <Card>
              <CardContent className="pt-6">
                {currentStep === 1 && (
                  <div className="space-y-6">
                    <div className="text-center mb-6">
                      <Building2 className="h-12 w-12 mx-auto text-primary mb-4" />
                      <CardTitle className="text-2xl">Company Information</CardTitle>
                      <CardDescription>Tell us about your business.</CardDescription>
                    </div>

                    <FormField
                      control={form.control}
                      name="companyName"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Company Name</FormLabel>
                          <FormControl>
                            <Input 
                              placeholder="Acme Corporation" 
                              {...field} 
                              data-testid="input-company-name"
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />

                    <FormField
                      control={form.control}
                      name="website"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Website URL</FormLabel>
                          <FormControl>
                            <Input 
                              placeholder="https://example.com" 
                              {...field} 
                              data-testid="input-website"
                            />
                          </FormControl>
                          <FormDescription>Your main website we'll be optimizing.</FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </div>
                )}

                {currentStep === 2 && (
                  <div className="space-y-6">
                    <div className="text-center mb-6">
                      <Search className="h-12 w-12 mx-auto text-primary mb-4" />
                      <CardTitle className="text-2xl">Keywords & Content</CardTitle>
                      <CardDescription>What search terms do you want to rank for?</CardDescription>
                    </div>

                    <FormField
                      control={form.control}
                      name="primaryKeywords"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Primary Keywords</FormLabel>
                          <div className="flex gap-2">
                            <Input
                              placeholder="Enter a keyword"
                              value={keywordInput}
                              onChange={(e) => setKeywordInput(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") {
                                  e.preventDefault();
                                  addKeyword();
                                }
                              }}
                              data-testid="input-keyword"
                            />
                            <Button type="button" onClick={addKeyword} data-testid="button-add-keyword">
                              <Plus className="h-4 w-4" />
                            </Button>
                          </div>
                          <div className="flex flex-wrap gap-2 mt-3">
                            {field.value.map((keyword) => (
                              <Badge key={keyword} variant="secondary" className="gap-1">
                                {keyword}
                                <button
                                  type="button"
                                  onClick={() => removeKeyword(keyword)}
                                  className="ml-1 hover:text-destructive"
                                  data-testid={`button-remove-keyword-${keyword}`}
                                >
                                  <X className="h-3 w-3" />
                                </button>
                              </Badge>
                            ))}
                          </div>
                          <FormDescription>Add keywords you want to rank for.</FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />

                    <FormField
                      control={form.control}
                      name="contentUrls"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Content URLs (Optional)</FormLabel>
                          <div className="flex gap-2">
                            <Input
                              placeholder="https://example.com/blog/article"
                              value={urlInput}
                              onChange={(e) => setUrlInput(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") {
                                  e.preventDefault();
                                  addUrl();
                                }
                              }}
                              data-testid="input-content-url"
                            />
                            <Button type="button" onClick={addUrl} data-testid="button-add-url">
                              <Plus className="h-4 w-4" />
                            </Button>
                          </div>
                          <div className="flex flex-col gap-2 mt-3">
                            {(field.value || []).map((url) => (
                              <div
                                key={url}
                                className="flex items-center justify-between bg-muted px-3 py-2 rounded-md text-sm"
                              >
                                <span className="truncate flex-1">{url}</span>
                                <button
                                  type="button"
                                  onClick={() => removeUrl(url)}
                                  className="ml-2 hover:text-destructive"
                                >
                                  <X className="h-4 w-4" />
                                </button>
                              </div>
                            ))}
                          </div>
                          <FormDescription>Specific pages you want us to optimize.</FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </div>
                )}

                {currentStep === 3 && (
                  <div className="space-y-6">
                    <div className="text-center mb-6">
                      <Target className="h-12 w-12 mx-auto text-primary mb-4" />
                      <CardTitle className="text-2xl">Goals & Package</CardTitle>
                      <CardDescription>Select your goals and choose a package.</CardDescription>
                    </div>

                    <FormField
                      control={form.control}
                      name="goals"
                      render={() => (
                        <FormItem>
                          <FormLabel>Optimization Goals</FormLabel>
                          <div className="grid sm:grid-cols-2 gap-3 mt-2">
                            {goals.map((goal) => (
                              <FormField
                                key={goal.id}
                                control={form.control}
                                name="goals"
                                render={({ field }) => (
                                  <FormItem className="flex items-center space-x-3 space-y-0">
                                    <FormControl>
                                      <Checkbox
                                        checked={field.value?.includes(goal.id)}
                                        onCheckedChange={(checked) => {
                                          if (checked) {
                                            field.onChange([...field.value, goal.id]);
                                          } else {
                                            field.onChange(
                                              field.value.filter((v) => v !== goal.id)
                                            );
                                          }
                                        }}
                                        data-testid={`checkbox-goal-${goal.id}`}
                                      />
                                    </FormControl>
                                    <FormLabel className="font-normal cursor-pointer">
                                      {goal.label}
                                    </FormLabel>
                                  </FormItem>
                                )}
                              />
                            ))}
                          </div>
                          <FormMessage />
                        </FormItem>
                      )}
                    />

                    <FormField
                      control={form.control}
                      name="tier"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Select Package</FormLabel>
                          <div className="grid sm:grid-cols-3 gap-4 mt-2">
                            {tiers.map((tier) => (
                              <div
                                key={tier.id}
                                onClick={() => field.onChange(tier.id)}
                                className={`relative p-4 rounded-lg border-2 cursor-pointer transition-all ${
                                  field.value === tier.id
                                    ? "border-primary bg-primary/5"
                                    : "border-border hover:border-muted-foreground/50"
                                }`}
                                data-testid={`tier-${tier.id}`}
                              >
                                {field.value === tier.id && (
                                  <CheckCircle className="absolute top-2 right-2 h-5 w-5 text-primary" />
                                )}
                                <h4 className="font-semibold">{tier.name}</h4>
                                <p className="text-2xl font-bold mt-1">
                                  ${tier.price.toLocaleString()}
                                </p>
                                <p className="text-sm text-muted-foreground mt-1">
                                  {tier.description}
                                </p>
                              </div>
                            ))}
                          </div>
                          <FormMessage />
                        </FormItem>
                      )}
                    />

                    <FormField
                      control={form.control}
                      name="additionalNotes"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Additional Notes (Optional)</FormLabel>
                          <FormControl>
                            <Textarea
                              placeholder="Any specific requirements or information we should know..."
                              {...field}
                              data-testid="textarea-notes"
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </div>
                )}
              </CardContent>
            </Card>

            <div className="flex items-center justify-between mt-6">
              <Button
                type="button"
                variant="outline"
                onClick={prevStep}
                disabled={currentStep === 1}
                data-testid="button-prev-step"
              >
                <ArrowLeft className="h-4 w-4 mr-2" />
                Previous
              </Button>

              {currentStep < 3 ? (
                <Button type="button" onClick={nextStep} data-testid="button-next-step">
                  Next
                  <ArrowRight className="h-4 w-4 ml-2" />
                </Button>
              ) : (
                <Button 
                  type="submit" 
                  disabled={createProjectMutation.isPending}
                  data-testid="button-submit-project"
                >
                  {createProjectMutation.isPending ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      Creating...
                    </>
                  ) : (
                    <>
                      Submit & Pay
                      <ArrowRight className="h-4 w-4 ml-2" />
                    </>
                  )}
                </Button>
              )}
            </div>
          </form>
        </Form>
      </main>
    </div>
  );
}
