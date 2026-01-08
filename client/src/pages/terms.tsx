import { Navigation } from "@/components/navigation";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

export default function TermsPage() {
  const lastUpdated = "January 8, 2026";
  const effectiveDate = "January 8, 2026";
  const companyName = "MarketingHandoffAI";
  const legalEntity = "MarketingHandoffAI, LLC";
  const companyEmail = "support@marketinghandoffai.com";
  const websiteUrl = "marketinghandoffai.com";

  return (
    <div className="min-h-screen bg-background">
      <Navigation />

      <main className="max-w-4xl mx-auto px-6 pt-24 pb-16">
        <div className="mb-8">
          <h1 className="text-3xl font-bold tracking-tight mb-2" data-testid="text-terms-title">
            Terms and Conditions
          </h1>
          <div className="text-muted-foreground space-y-1">
            <p>Last Updated: {lastUpdated}</p>
            <p>Effective Date: {effectiveDate}</p>
          </div>
        </div>

        <Card>
          <CardContent className="prose prose-neutral dark:prose-invert max-w-none p-8">
            <p className="text-muted-foreground leading-relaxed mb-4">
              This Terms and Conditions agreement ("Agreement") governs your access to and use of {companyName} (the "Service"), including all content, functionality, features, and services offered by {companyName} through the website {websiteUrl}. By signing up for, accessing, or using the Service, you ("Client," "you," "your") accept and agree to be bound by this Agreement.
            </p>
            <p className="text-muted-foreground leading-relaxed mb-8">
              <strong>Legal Entity:</strong> The Service is operated by {legalEntity}, a limited liability company organized under the laws of the State of Delaware, United States.
            </p>

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">1. Acceptance of Terms</h2>
              <p className="text-muted-foreground leading-relaxed">
                By using our Service, you agree to these Terms. If you do not agree to these terms, do not use our Service. Compliance with these Terms is required even if access is free or partially complimentary.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">2. Scope of Services</h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                {companyName} provides professional services to optimize client content for search visibility (including search engine and generative/AI search), advisory, and associated tasks. The specific scope, deliverables, and milestones shall be described in each client engagement or order form.
              </p>
              <p className="text-muted-foreground leading-relaxed font-medium">
                Clients agree that the Service consists of effort, tools, and best-practice execution — not guaranteed search outcomes, rankings, or traffic. Client is responsible for the quality, authority, and factual accuracy of content provided for optimization.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">3. Eligibility</h2>
              <p className="text-muted-foreground leading-relaxed">
                You must be at least 18 years old and have the authority to enter into this Agreement. If representing a business entity, you confirm you have authority to bind that entity to these terms.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">4. Payments and Fees</h2>
              
              <h3 className="text-lg font-medium mb-3">(a) Pricing</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Services are sold in fixed tranches as set on our site or in your engagement form:
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li><strong>Foundation Package:</strong> $5,000</li>
                <li><strong>Growth Package:</strong> $10,000</li>
                <li><strong>Enterprise Package:</strong> $15,000</li>
              </ul>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Clients acknowledge that payment is due in advance of commencement of work.
              </p>

              <h3 className="text-lg font-medium mb-3">(b) Non-Refundable</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Unless otherwise stated in writing, all payments are non-refundable once services commence or deliverables are issued.
              </p>

              <h3 className="text-lg font-medium mb-3">(c) Taxes</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Clients are responsible for applicable taxes associated with services.
              </p>

              <h3 className="text-lg font-medium mb-3">(d) Payment Processing</h3>
              <p className="text-muted-foreground leading-relaxed">
                Payment processing is facilitated by Stripe. You agree to comply with the terms of Stripe for payment processing.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">5. Client Obligations</h2>
              
              <p className="text-muted-foreground leading-relaxed mb-4">
                <strong>(a)</strong> You agree to provide accurate and complete information, including keywords, content, access details, and material necessary to fulfill the engagement.
              </p>
              
              <p className="text-muted-foreground leading-relaxed mb-4">
                <strong>(b)</strong> You retain ownership of your content and are responsible for its legality, compliance, and accuracy.
              </p>
              
              <p className="text-muted-foreground leading-relaxed">
                <strong>(c)</strong> Client is solely responsible for compliance with laws that govern their own content and business practices.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">6. No Guarantees / Disclaimers</h2>
              <p className="text-muted-foreground leading-relaxed font-medium mb-4">
                {companyName} does not guarantee any specific results, including increases in search rankings, traffic, conversions, or citations by AI systems or search engines. Outcomes depend on external platforms and variables beyond our control. Any example results or case studies are not guarantees of future performance.
              </p>
              <p className="text-muted-foreground leading-relaxed font-medium uppercase text-sm">
                THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE," WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, OR NON-INFRINGEMENT. {companyName} DOES NOT WARRANT THAT THE SERVICE WILL BE UNINTERRUPTED, ERROR-FREE, OR COMPLETELY SECURE.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">7. AI and Third-Party Dependencies</h2>
              <p className="text-muted-foreground leading-relaxed">
                The Service may incorporate artificial intelligence systems and third-party platforms. {companyName} does not control and is not responsible for the outputs, availability, or policies of such systems. Client is responsible for reviewing and validating all outputs before use and ensuring compliance with applicable laws and regulations.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">8. Intellectual Property</h2>
              
              <p className="text-muted-foreground leading-relaxed mb-4">
                <strong>(a)</strong> Client retains ownership of their pre-existing content and deliverables included in the project.
              </p>
              
              <p className="text-muted-foreground leading-relaxed mb-4">
                <strong>(b)</strong> {companyName} retains ownership of workflows, proprietary tools, processes, and methodologies used to perform services.
              </p>
              
              <p className="text-muted-foreground leading-relaxed">
                <strong>(c)</strong> You grant {companyName} a limited license to use your content only for the purpose of delivering services under this Agreement.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">9. Restrictions on Use / Acceptable Conduct</h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Clients agree not to use the Service to:
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li>Upload or distribute illegal, harmful, infringing, defamatory, or malicious content.</li>
                <li>Violate any applicable laws, export controls, or third-party rights.</li>
                <li>Attempt to compromise the security of {companyName} systems or other clients.</li>
              </ul>
              <p className="text-muted-foreground leading-relaxed">
                Violations may result in suspension or termination under Section 11.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">10. Confidentiality and Data Handling</h2>
              <p className="text-muted-foreground leading-relaxed">
                We handle client data with industry-standard security measures, but you acknowledge that no system is impenetrable. You consent to the collection and processing of personal and business data as necessary to provide services and for internal business purposes, in accordance with our Privacy Policy.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">11. Termination</h2>
              
              <p className="text-muted-foreground leading-relaxed mb-4">
                <strong>(a)</strong> Either party may terminate this Agreement for material breach if the breach is not cured within thirty (30) days after written notice describing the breach in reasonable detail. Notice must be delivered via email to the address on record or as specified in the engagement form.
              </p>
              
              <p className="text-muted-foreground leading-relaxed mb-4">
                <strong>(b)</strong> {companyName} may suspend or terminate access immediately for serious violations, including non-payment or harmful use of services.
              </p>
              
              <p className="text-muted-foreground leading-relaxed mb-4">
                <strong>(c)</strong> Termination does not relieve Client of payment obligations for work already performed.
              </p>
              
              <p className="text-muted-foreground leading-relaxed">
                <strong>(d)</strong> Upon termination, Client may request export of their data within thirty (30) days. After this period, {companyName} may delete Client data in accordance with standard data retention policies.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">12. Limitation of Liability</h2>
              <p className="text-muted-foreground leading-relaxed font-medium">
                To the maximum extent permitted by law, {companyName}'s aggregate liability is limited to the amount paid by the Client for the services giving rise to the claim. We are not liable for indirect, incidental, consequential, or punitive damages.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">13. Indemnification</h2>
              
              <p className="text-muted-foreground leading-relaxed mb-4">
                <strong>(a) Client Indemnification:</strong> Client agrees to indemnify and hold harmless {companyName} and its affiliates from any claims arising out of or related to Client's content, use of the Service, or breach of this Agreement.
              </p>
              
              <p className="text-muted-foreground leading-relaxed">
                <strong>(b) {companyName} Indemnification:</strong> {companyName} will indemnify Client against third-party claims alleging that the Service (excluding Client content and third-party components) directly infringes a third party's intellectual property rights, provided Client promptly notifies {companyName} of any such claim and cooperates in the defense.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">14. Force Majeure</h2>
              <p className="text-muted-foreground leading-relaxed">
                Neither party shall be liable for any failure or delay in performance due to causes beyond its reasonable control, including but not limited to acts of God, natural disasters, war, terrorism, labor disputes, government actions, internet or telecommunications failures, cloud provider outages, or third-party service disruptions. The affected party shall provide prompt notice and use reasonable efforts to resume performance.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">15. Assignment</h2>
              <p className="text-muted-foreground leading-relaxed">
                Client may not assign or transfer this Agreement or any rights hereunder without {companyName}'s prior written consent. {companyName} may assign this Agreement in connection with a merger, acquisition, corporate reorganization, or sale of all or substantially all of its assets. Any attempted assignment in violation of this section shall be void.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">16. Governing Law</h2>
              <p className="text-muted-foreground leading-relaxed">
                This Agreement is governed by the laws of the State of Delaware, United States, without regard to conflict of law principles.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">17. Changes to Terms</h2>
              <p className="text-muted-foreground leading-relaxed">
                {companyName} may update these Terms at any time. Notice of material changes will be posted on the site or communicated via email. Continued use of the Service after changes constitutes acceptance.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">18. Entire Agreement</h2>
              <p className="text-muted-foreground leading-relaxed">
                This Agreement, including any engagement forms or orders referencing it, constitutes the full understanding between Client and {companyName} regarding the services.
              </p>
            </section>

            <Separator className="my-8" />

            <section>
              <h2 className="text-xl font-semibold mb-4">19. Contact Information</h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Questions about these Terms should be sent to:
              </p>
              <div className="bg-muted/50 rounded-md p-4">
                <p className="font-medium">{companyName}</p>
                <p className="text-muted-foreground">Email: {companyEmail}</p>
              </div>
            </section>
          </CardContent>
        </Card>

        <div className="mt-8 text-center text-sm text-muted-foreground">
          <p>
            By using our Service, you acknowledge that you have read, understood, and agree to be bound by these Terms and Conditions.
          </p>
        </div>
      </main>
    </div>
  );
}
