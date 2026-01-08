import { Navigation } from "@/components/navigation";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Shield,
  Lock,
  Server,
  FileCheck,
  Users,
  AlertTriangle,
  Database,
  Globe,
  Key,
  CheckCircle2,
  Mail,
} from "lucide-react";

export default function SecurityPage() {
  const companyName = "MarketingHandoffAI";
  const companyEmail = "security@marketinghandoffai.com";
  const lastUpdated = "January 8, 2026";

  const securityFeatures = [
    {
      icon: Lock,
      title: "Encryption",
      description: "All data encrypted in transit (TLS 1.3) and at rest (AES-256)",
    },
    {
      icon: Key,
      title: "Authentication",
      description: "Secure authentication via Replit Auth with session management",
    },
    {
      icon: Server,
      title: "Infrastructure",
      description: "Hosted on enterprise-grade cloud infrastructure with redundancy",
    },
    {
      icon: Database,
      title: "Data Storage",
      description: "PostgreSQL database with automated backups and point-in-time recovery",
    },
  ];

  return (
    <div className="min-h-screen bg-background">
      <Navigation />

      <main className="max-w-4xl mx-auto px-6 pt-24 pb-16">
        <div className="mb-8">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-primary/10 rounded-md">
              <Shield className="h-6 w-6 text-primary" />
            </div>
            <h1 className="text-3xl font-bold tracking-tight" data-testid="text-security-title">
              Security & Compliance
            </h1>
          </div>
          <p className="text-muted-foreground max-w-2xl">
            We take the security of your data seriously. This page provides transparency into our security practices, compliance posture, and data handling procedures.
          </p>
          <p className="text-sm text-muted-foreground mt-2">
            Last Updated: {lastUpdated}
          </p>
        </div>

        <div className="grid gap-4 md:grid-cols-2 mb-8">
          {securityFeatures.map((feature) => (
            <Card key={feature.title}>
              <CardHeader className="pb-2">
                <div className="flex items-center gap-3">
                  <feature.icon className="h-5 w-5 text-primary" />
                  <CardTitle className="text-base">{feature.title}</CardTitle>
                </div>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">{feature.description}</p>
              </CardContent>
            </Card>
          ))}
        </div>

        <Card className="mb-8">
          <CardContent className="p-8">
            <section className="mb-8">
              <div className="flex items-center gap-3 mb-4">
                <Shield className="h-5 w-5 text-primary" />
                <h2 className="text-xl font-semibold">Security Posture</h2>
              </div>
              
              <div className="space-y-4">
                <div>
                  <h3 className="font-medium mb-2">Security Standards</h3>
                  <p className="text-muted-foreground text-sm leading-relaxed mb-3">
                    {companyName} implements security controls aligned with industry best practices. Our security program is designed to protect the confidentiality, integrity, and availability of client data.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="secondary">TLS 1.3 Encryption</Badge>
                    <Badge variant="secondary">AES-256 at Rest</Badge>
                    <Badge variant="secondary">Secure Authentication</Badge>
                    <Badge variant="secondary">Access Controls</Badge>
                  </div>
                </div>

                <div>
                  <h3 className="font-medium mb-2">Authentication & Access</h3>
                  <ul className="text-muted-foreground text-sm space-y-2">
                    <li className="flex items-start gap-2">
                      <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                      Secure authentication via OAuth 2.0 / OpenID Connect
                    </li>
                    <li className="flex items-start gap-2">
                      <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                      Role-based access controls for administrative functions
                    </li>
                    <li className="flex items-start gap-2">
                      <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                      Session management with secure token handling
                    </li>
                    <li className="flex items-start gap-2">
                      <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                      Regular access reviews for privileged accounts
                    </li>
                  </ul>
                </div>

                <div>
                  <h3 className="font-medium mb-2">Infrastructure Security</h3>
                  <ul className="text-muted-foreground text-sm space-y-2">
                    <li className="flex items-start gap-2">
                      <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                      Hosted on enterprise-grade cloud infrastructure
                    </li>
                    <li className="flex items-start gap-2">
                      <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                      Regular security patching and updates
                    </li>
                    <li className="flex items-start gap-2">
                      <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                      Network isolation and firewall protections
                    </li>
                    <li className="flex items-start gap-2">
                      <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                      Logging and monitoring for security events
                    </li>
                  </ul>
                </div>
              </div>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <div className="flex items-center gap-3 mb-4">
                <Database className="h-5 w-5 text-primary" />
                <h2 className="text-xl font-semibold">Data Privacy & Handling</h2>
              </div>
              
              <div className="space-y-4">
                <div>
                  <h3 className="font-medium mb-2">Data Categories Collected</h3>
                  <p className="text-muted-foreground text-sm leading-relaxed mb-3">
                    We collect and process the following categories of data necessary to provide our services:
                  </p>
                  <ul className="text-muted-foreground text-sm space-y-1 list-disc pl-5">
                    <li>Account information (name, email via authentication provider)</li>
                    <li>Company and project information you submit</li>
                    <li>Website URLs and content for optimization</li>
                    <li>Keywords and optimization goals</li>
                    <li>Payment information (processed by Stripe, not stored by us)</li>
                  </ul>
                </div>

                <div>
                  <h3 className="font-medium mb-2">Data Storage & Location</h3>
                  <p className="text-muted-foreground text-sm leading-relaxed">
                    Client data is stored in secure PostgreSQL databases hosted on Replit infrastructure within the United States. We use encrypted connections and implement appropriate access controls to protect your data.
                  </p>
                </div>

                <div>
                  <h3 className="font-medium mb-2">Data Retention & Deletion</h3>
                  <p className="text-muted-foreground text-sm leading-relaxed">
                    We retain client data for the duration of the business relationship and for a limited period thereafter as required for legal, accounting, or dispute resolution purposes. Upon request, we can provide data export or deletion in accordance with applicable data protection laws.
                  </p>
                </div>

                <div>
                  <h3 className="font-medium mb-2">AI & Third-Party Tools</h3>
                  <p className="text-muted-foreground text-sm leading-relaxed">
                    AI and third-party tools are used as assistive technologies; clients remain responsible for validating outputs and ensuring compliance of their content.
                  </p>
                </div>

                <div>
                  <h3 className="font-medium mb-2">Access Control</h3>
                  <p className="text-muted-foreground text-sm leading-relaxed">
                    Access to client data is restricted to authorized personnel who require it to perform their job functions. We implement the principle of least privilege and regularly review access permissions.
                  </p>
                </div>
              </div>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <div className="flex items-center gap-3 mb-4">
                <Globe className="h-5 w-5 text-primary" />
                <h2 className="text-xl font-semibold">Compliance</h2>
              </div>
              
              <div className="space-y-4">
                <div>
                  <h3 className="font-medium mb-2">Privacy Regulations</h3>
                  <p className="text-muted-foreground text-sm leading-relaxed mb-3">
                    Our practices are designed to support compliance with major privacy regulations:
                  </p>
                  <div className="grid gap-3 md:grid-cols-2">
                    <div className="p-3 bg-muted/50 rounded-md">
                      <p className="font-medium text-sm">GDPR</p>
                      <p className="text-xs text-muted-foreground">European data protection regulation</p>
                    </div>
                    <div className="p-3 bg-muted/50 rounded-md">
                      <p className="font-medium text-sm">CCPA</p>
                      <p className="text-xs text-muted-foreground">California Consumer Privacy Act</p>
                    </div>
                  </div>
                </div>

                <div>
                  <h3 className="font-medium mb-2">Security Certifications</h3>
                  <p className="text-muted-foreground text-sm leading-relaxed">
                    {companyName} is not currently certified under SOC 2, ISO 27001, or similar frameworks but aligns its controls with generally accepted security best practices.
                  </p>
                </div>

                <div>
                  <h3 className="font-medium mb-2">Data Processing Agreement</h3>
                  <p className="text-muted-foreground text-sm leading-relaxed">
                    For clients requiring a Data Processing Agreement (DPA) or other contractual documentation, please contact our team at {companyEmail}.
                  </p>
                </div>
              </div>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <div className="flex items-center gap-3 mb-4">
                <AlertTriangle className="h-5 w-5 text-primary" />
                <h2 className="text-xl font-semibold">Incident Response</h2>
              </div>
              
              <div className="space-y-4">
                <p className="text-muted-foreground text-sm leading-relaxed">
                  We maintain incident response procedures to address security events promptly. In the event of a security incident affecting client data:
                </p>
                <ul className="text-muted-foreground text-sm space-y-2">
                  <li className="flex items-start gap-2">
                    <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                    Affected clients will be notified within 72 hours of discovery
                  </li>
                  <li className="flex items-start gap-2">
                    <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                    We will provide details about the nature and scope of the incident
                  </li>
                  <li className="flex items-start gap-2">
                    <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                    Remediation steps and ongoing updates will be communicated
                  </li>
                </ul>
              </div>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <div className="flex items-center gap-3 mb-4">
                <Server className="h-5 w-5 text-primary" />
                <h2 className="text-xl font-semibold">Service Availability & Continuity</h2>
              </div>
              
              <div className="space-y-4">
                <p className="text-muted-foreground text-sm leading-relaxed">
                  We implement measures to ensure service availability and business continuity:
                </p>
                <ul className="text-muted-foreground text-sm space-y-2">
                  <li className="flex items-start gap-2">
                    <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                    Regular automated database backups with point-in-time recovery
                  </li>
                  <li className="flex items-start gap-2">
                    <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                    Redundant infrastructure to minimize single points of failure
                  </li>
                  <li className="flex items-start gap-2">
                    <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                    Monitoring and alerting for service health
                  </li>
                </ul>
              </div>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <div className="flex items-center gap-3 mb-4">
                <Users className="h-5 w-5 text-primary" />
                <h2 className="text-xl font-semibold">Subprocessors</h2>
              </div>
              
              <div className="space-y-4">
                <p className="text-muted-foreground text-sm leading-relaxed mb-3">
                  We use the following key subprocessors to deliver our services:
                </p>
                <div className="space-y-3">
                  <div className="p-3 border rounded-md">
                    <div className="flex items-center justify-between mb-1">
                      <p className="font-medium text-sm">Stripe</p>
                      <Badge variant="outline" className="text-xs">Payment Processing</Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">Secure payment processing for all transactions</p>
                  </div>
                  <div className="p-3 border rounded-md">
                    <div className="flex items-center justify-between mb-1">
                      <p className="font-medium text-sm">Replit</p>
                      <Badge variant="outline" className="text-xs">Infrastructure & Auth</Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">Hosting infrastructure, database, and authentication services</p>
                  </div>
                </div>
                <p className="text-muted-foreground text-sm leading-relaxed">
                  Subprocessors are contractually obligated to maintain appropriate security controls and handle data in accordance with our security standards.
                </p>
              </div>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <div className="flex items-center gap-3 mb-4">
                <FileCheck className="h-5 w-5 text-primary" />
                <h2 className="text-xl font-semibold">Contractual Safeguards</h2>
              </div>
              
              <div className="space-y-4">
                <p className="text-muted-foreground text-sm leading-relaxed">
                  Our Terms and Conditions include important contractual protections:
                </p>
                <ul className="text-muted-foreground text-sm space-y-2">
                  <li className="flex items-start gap-2">
                    <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                    <span><strong>Liability Limitations:</strong> Aggregate liability capped at amounts paid for services</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                    <span><strong>Indemnification:</strong> Mutual indemnification provisions</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                    <span><strong>Termination:</strong> Clear termination rights with data handling procedures</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                    <span><strong>Confidentiality:</strong> Protection of client confidential information</span>
                  </li>
                </ul>
              </div>
            </section>

            <Separator className="my-8" />

            <section>
              <div className="flex items-center gap-3 mb-4">
                <Mail className="h-5 w-5 text-primary" />
                <h2 className="text-xl font-semibold">Security Inquiries</h2>
              </div>
              
              <p className="text-muted-foreground text-sm leading-relaxed mb-4">
                For security-related questions, to request security documentation, or to report a security concern, please contact our team:
              </p>
              <div className="bg-muted/50 rounded-md p-4">
                <p className="font-medium">{companyName} Security Team</p>
                <p className="text-muted-foreground">Email: {companyEmail}</p>
              </div>
              <p className="text-muted-foreground text-sm leading-relaxed mt-4">
                We are committed to working with clients on their security and compliance requirements. Custom security questionnaire responses and additional documentation may be available upon request for enterprise engagements.
              </p>
            </section>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
