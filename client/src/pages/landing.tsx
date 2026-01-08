import { Link } from "wouter";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Navigation } from "@/components/navigation";
import { 
  Search, 
  Sparkles, 
  BarChart3, 
  FileText, 
  Target, 
  Zap, 
  CheckCircle, 
  ArrowRight,
  Shield,
  Clock,
  TrendingUp
} from "lucide-react";

const features = [
  {
    icon: Search,
    title: "SEO Optimization",
    description: "Comprehensive on-page SEO audits and optimization for maximum search visibility.",
  },
  {
    icon: Sparkles,
    title: "AI Search Ready",
    description: "Prepare your content for AI discovery engines like ChatGPT, Perplexity, and Claude.",
  },
  {
    icon: BarChart3,
    title: "Performance Tracking",
    description: "Detailed analytics and reporting to measure optimization impact.",
  },
  {
    icon: FileText,
    title: "Content Optimization",
    description: "Strategic content improvements for titles, meta descriptions, and structure.",
  },
  {
    icon: Target,
    title: "Keyword Strategy",
    description: "In-depth keyword research and refinement to target high-value search terms.",
  },
  {
    icon: Zap,
    title: "Technical SEO",
    description: "Site speed, crawlability, and technical improvements for better rankings.",
  },
];

const pricingTiers = [
  {
    name: "Foundation",
    price: "$5,000",
    tier: "tier_1",
    description: "Essential SEO optimization package",
    features: [
      "Keyword refinement audit",
      "Content optimization (titles, meta, structure)",
      "On-page AI readiness adjustments",
      "Submission to search ecosystems",
      "Basic performance reporting",
    ],
    popular: false,
  },
  {
    name: "Growth",
    price: "$10,000",
    tier: "tier_2",
    description: "Comprehensive SEO + outreach package",
    features: [
      "Everything in Foundation",
      "Link placement outreach",
      "Syndication strategy",
      "Technical SEO checklist",
      "Authority signal report",
      "Monthly progress reviews",
    ],
    popular: true,
  },
  {
    name: "Enterprise",
    price: "$15,000",
    tier: "tier_3",
    description: "Full-service optimization partnership",
    features: [
      "Everything in Growth",
      "AI search optimization audit",
      "Advanced placements",
      "2 monthly review meetings",
      "Quarterly follow-up report",
      "Priority support",
    ],
    popular: false,
  },
];

const steps = [
  {
    number: "01",
    title: "Submit Your Project",
    description: "Fill out our simple form with your website, keywords, and goals. We'll review your submission within 24 hours.",
  },
  {
    number: "02",
    title: "Strategy & Execution",
    description: "Our team develops a custom optimization strategy and executes best practices for SEO and AI discoverability.",
  },
  {
    number: "03",
    title: "Track & Report",
    description: "Monitor progress through your dashboard. Receive detailed reports on work completed and recommendations.",
  },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-background">
      <Navigation />

      <section className="relative min-h-[600px] flex items-center justify-center pt-16 overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-br from-primary/5 via-background to-background" />
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_30%_20%,hsl(var(--primary)/0.1),transparent_50%)]" />
        
        <div className="relative max-w-4xl mx-auto px-6 py-24 md:py-32 text-center">
          <Badge variant="secondary" className="mb-6" data-testid="badge-hero-tag">
            <Sparkles className="w-3 h-3 mr-1" />
            SEO & AI Optimization
          </Badge>
          
          <h1 className="text-4xl md:text-6xl lg:text-7xl font-bold tracking-tight mb-6" data-testid="text-hero-title">
            Get Found by Search Engines{" "}
            <span className="text-primary">and AI</span>
          </h1>
          
          <p className="text-lg md:text-xl text-muted-foreground max-w-2xl mx-auto mb-8 leading-relaxed" data-testid="text-hero-description">
            Professional optimization services that make your content discoverable 
            by traditional search engines and modern AI systems. Project-based pricing, 
            no long-term contracts.
          </p>
          
          <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
            <a href="/api/login">
              <Button size="lg" className="px-8 py-6 text-lg" data-testid="button-hero-cta">
                Start Your Project
                <ArrowRight className="ml-2 h-5 w-5" />
              </Button>
            </a>
            <a href="#pricing">
              <Button size="lg" variant="outline" className="px-8 py-6 text-lg" data-testid="button-hero-pricing">
                View Pricing
              </Button>
            </a>
          </div>

          <div className="flex items-center justify-center gap-8 mt-12 text-sm text-muted-foreground">
            <div className="flex items-center gap-2">
              <Shield className="h-4 w-4" />
              <span>Secure Payments</span>
            </div>
            <div className="flex items-center gap-2">
              <Clock className="h-4 w-4" />
              <span>24hr Response</span>
            </div>
            <div className="flex items-center gap-2">
              <TrendingUp className="h-4 w-4" />
              <span>Proven Results</span>
            </div>
          </div>
        </div>
      </section>

      <section id="features" className="py-20 md:py-28 bg-card/50">
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center mb-16">
            <Badge variant="secondary" className="mb-4">Features</Badge>
            <h2 className="text-3xl md:text-4xl lg:text-5xl font-bold tracking-tight mb-4" data-testid="text-features-title">
              Comprehensive Optimization Services
            </h2>
            <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
              Everything you need to improve your search visibility and AI discoverability in one streamlined engagement.
            </p>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {features.map((feature) => (
              <Card key={feature.title} className="hover-elevate transition-all duration-200" data-testid={`card-feature-${feature.title.toLowerCase().replace(/\s/g, "-")}`}>
                <CardHeader>
                  <div className="w-12 h-12 rounded-lg bg-primary/10 flex items-center justify-center mb-4">
                    <feature.icon className="h-6 w-6 text-primary" />
                  </div>
                  <CardTitle className="text-xl">{feature.title}</CardTitle>
                </CardHeader>
                <CardContent>
                  <CardDescription className="text-base">{feature.description}</CardDescription>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      <section id="how-it-works" className="py-20 md:py-28">
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center mb-16">
            <Badge variant="secondary" className="mb-4">How It Works</Badge>
            <h2 className="text-3xl md:text-4xl lg:text-5xl font-bold tracking-tight mb-4" data-testid="text-how-it-works-title">
              Simple, Transparent Process
            </h2>
            <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
              Get started in minutes. We handle the complexity while you track progress.
            </p>
          </div>

          <div className="grid md:grid-cols-3 gap-8">
            {steps.map((step, index) => (
              <div key={step.number} className="relative" data-testid={`step-${step.number}`}>
                <div className="text-6xl font-bold text-primary/10 mb-4">{step.number}</div>
                <h3 className="text-xl font-semibold mb-2">{step.title}</h3>
                <p className="text-muted-foreground">{step.description}</p>
                {index < steps.length - 1 && (
                  <div className="hidden md:block absolute top-8 right-0 translate-x-1/2">
                    <ArrowRight className="h-6 w-6 text-muted-foreground/30" />
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="pricing" className="py-20 md:py-28 bg-card/50">
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center mb-16">
            <Badge variant="secondary" className="mb-4">Pricing</Badge>
            <h2 className="text-3xl md:text-4xl lg:text-5xl font-bold tracking-tight mb-4" data-testid="text-pricing-title">
              Project-Based Pricing
            </h2>
            <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
              One-time payments for complete deliverables. No subscriptions, no surprises.
            </p>
          </div>

          <div className="grid md:grid-cols-3 gap-6 lg:gap-8">
            {pricingTiers.map((tier) => (
              <Card 
                key={tier.name}
                className={`relative ${tier.popular ? "ring-2 ring-primary scale-105" : ""}`}
                data-testid={`card-pricing-${tier.name.toLowerCase()}`}
              >
                {tier.popular && (
                  <Badge className="absolute -top-3 left-1/2 -translate-x-1/2">
                    Most Popular
                  </Badge>
                )}
                <CardHeader className="text-center pb-2">
                  <CardTitle className="text-2xl font-bold">{tier.name}</CardTitle>
                  <div className="mt-4">
                    <span className="text-5xl font-bold">{tier.price}</span>
                    <span className="text-muted-foreground">/project</span>
                  </div>
                  <CardDescription className="mt-2">{tier.description}</CardDescription>
                </CardHeader>
                <CardContent className="pt-6">
                  <ul className="space-y-3 mb-6">
                    {tier.features.map((feature) => (
                      <li key={feature} className="flex items-start gap-3">
                        <CheckCircle className="h-5 w-5 text-primary shrink-0 mt-0.5" />
                        <span className="text-sm">{feature}</span>
                      </li>
                    ))}
                  </ul>
                  <a href="/api/login">
                    <Button 
                      className="w-full" 
                      variant={tier.popular ? "default" : "outline"}
                      data-testid={`button-select-${tier.name.toLowerCase()}`}
                    >
                      Get Started
                    </Button>
                  </a>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      <section className="py-20 md:py-28">
        <div className="max-w-4xl mx-auto px-6 text-center">
          <h2 className="text-3xl md:text-4xl lg:text-5xl font-bold tracking-tight mb-6" data-testid="text-cta-title">
            Ready to Get Discovered?
          </h2>
          <p className="text-lg text-muted-foreground mb-8 max-w-2xl mx-auto">
            Join businesses that have improved their search visibility and AI discoverability with MarketingHandoffAI.
          </p>
          <a href="/api/login">
            <Button size="lg" className="px-8 py-6 text-lg" data-testid="button-final-cta">
              Start Your Project Today
              <ArrowRight className="ml-2 h-5 w-5" />
            </Button>
          </a>
        </div>
      </section>

      <footer className="border-t border-border py-12">
        <div className="max-w-7xl mx-auto px-6">
          <div className="flex flex-col md:flex-row items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-primary" />
              <span className="font-semibold">MarketingHandoffAI</span>
            </div>
            <div className="flex flex-wrap items-center justify-center gap-6">
              <Link href="/terms">
                <span className="text-sm text-muted-foreground hover:text-foreground transition-colors cursor-pointer" data-testid="link-terms">
                  Terms and Conditions
                </span>
              </Link>
              <Link href="/privacy">
                <span className="text-sm text-muted-foreground hover:text-foreground transition-colors cursor-pointer" data-testid="link-privacy">
                  Privacy Policy
                </span>
              </Link>
              <Link href="/security">
                <span className="text-sm text-muted-foreground hover:text-foreground transition-colors cursor-pointer" data-testid="link-security">
                  Security
                </span>
              </Link>
            </div>
            <p className="text-sm text-muted-foreground">
              {new Date().getFullYear()} MarketingHandoffAI. All rights reserved.
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
