import { Navigation } from "@/components/navigation";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

export default function PrivacyPage() {
  const lastUpdated = "January 8, 2026";
  const companyName = "MarketingHandoffAI";
  const companyEmail = "privacy@marketinghandoffai.com";

  return (
    <div className="min-h-screen bg-background">
      <Navigation />

      <main className="max-w-4xl mx-auto px-6 pt-24 pb-16">
        <div className="mb-8">
          <h1 className="text-3xl font-bold tracking-tight mb-2" data-testid="text-privacy-title">
            Privacy Policy
          </h1>
          <p className="text-muted-foreground">
            Last Updated: {lastUpdated}
          </p>
        </div>

        <Card>
          <CardContent className="prose prose-neutral dark:prose-invert max-w-none p-8">
            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">1. Introduction</h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                {companyName} ("Company," "we," "us," or "our") is committed to protecting your privacy. This Privacy Policy explains how we collect, use, disclose, and safeguard your information when you visit our website and use our SEO optimization and AI discovery services (collectively, the "Services").
              </p>
              <p className="text-muted-foreground leading-relaxed">
                Please read this Privacy Policy carefully. By accessing or using our Services, you acknowledge that you have read, understood, and agree to be bound by this Privacy Policy. If you do not agree with the terms of this Privacy Policy, please do not access or use the Services.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">2. Information We Collect</h2>
              
              <h3 className="text-lg font-medium mb-3">2.1 Personal Information You Provide</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                We collect personal information that you voluntarily provide when you:
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li><strong>Create an Account:</strong> Name, email address, profile information through Replit authentication</li>
                <li><strong>Submit a Project:</strong> Company name, website URL, business description, target keywords, content samples, industry information, and optimization goals</li>
                <li><strong>Make a Payment:</strong> Billing information processed through our payment processor (Stripe)</li>
                <li><strong>Contact Us:</strong> Name, email address, and any information you choose to include in your message</li>
              </ul>

              <h3 className="text-lg font-medium mb-3">2.2 Information Collected Automatically</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                When you access our Services, we may automatically collect:
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li><strong>Device Information:</strong> Browser type, operating system, device identifiers</li>
                <li><strong>Usage Data:</strong> Pages visited, time spent on pages, clicks, and navigation patterns</li>
                <li><strong>Log Data:</strong> IP address, access times, referring URLs, and error logs</li>
                <li><strong>Cookies and Tracking Technologies:</strong> Session cookies for authentication and preferences</li>
              </ul>

              <h3 className="text-lg font-medium mb-3">2.3 Information from Third Parties</h3>
              <p className="text-muted-foreground leading-relaxed">
                We may receive information about you from third-party services you use to access our platform (such as Replit for authentication) and payment processors (such as Stripe for billing).
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">3. How We Use Your Information</h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                We use the information we collect for the following purposes:
              </p>
              
              <h3 className="text-lg font-medium mb-3">3.1 Service Delivery</h3>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li>Provide, operate, and maintain our Services</li>
                <li>Process and complete transactions</li>
                <li>Perform SEO audits, keyword research, and optimization analysis</li>
                <li>Create and deliver project reports and recommendations</li>
                <li>Manage your account and provide customer support</li>
              </ul>

              <h3 className="text-lg font-medium mb-3">3.2 Communication</h3>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li>Send project updates and status notifications</li>
                <li>Respond to your comments, questions, and requests</li>
                <li>Send administrative information such as service changes or policy updates</li>
                <li>Send marketing communications (with your consent, where required)</li>
              </ul>

              <h3 className="text-lg font-medium mb-3">3.3 Improvement and Analytics</h3>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li>Understand how users interact with our Services</li>
                <li>Develop new features, products, and services</li>
                <li>Monitor and analyze usage trends and preferences</li>
                <li>Detect, prevent, and address technical issues</li>
              </ul>

              <h3 className="text-lg font-medium mb-3">3.4 Legal and Security</h3>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2">
                <li>Comply with legal obligations and respond to lawful requests</li>
                <li>Protect our rights, privacy, safety, or property</li>
                <li>Enforce our Terms and Conditions</li>
                <li>Prevent fraud and abuse of our Services</li>
              </ul>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">4. Legal Basis for Processing (GDPR)</h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                For users in the European Economic Area (EEA), we process your personal data based on the following legal grounds:
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2">
                <li><strong>Contract Performance:</strong> Processing necessary to fulfill our contractual obligations to you</li>
                <li><strong>Legitimate Interests:</strong> Processing necessary for our legitimate business interests, such as improving our Services and preventing fraud</li>
                <li><strong>Consent:</strong> Processing based on your explicit consent, which you may withdraw at any time</li>
                <li><strong>Legal Obligations:</strong> Processing necessary to comply with applicable laws and regulations</li>
              </ul>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">5. Information Sharing and Disclosure</h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                We do not sell your personal information. We may share your information in the following circumstances:
              </p>

              <h3 className="text-lg font-medium mb-3">5.1 Service Providers</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                We may share information with third-party vendors, service providers, and contractors who perform services on our behalf, including:
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li>Payment processing (Stripe)</li>
                <li>Authentication services (Replit)</li>
                <li>Cloud hosting and infrastructure</li>
                <li>Analytics and monitoring services</li>
              </ul>

              <h3 className="text-lg font-medium mb-3">5.2 Legal Requirements</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                We may disclose your information if required to do so by law or in response to valid requests by public authorities (e.g., court orders, government requests).
              </p>

              <h3 className="text-lg font-medium mb-3">5.3 Business Transfers</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                In the event of a merger, acquisition, or sale of all or a portion of our assets, your information may be transferred as part of that transaction.
              </p>

              <h3 className="text-lg font-medium mb-3">5.4 With Your Consent</h3>
              <p className="text-muted-foreground leading-relaxed">
                We may share your information with third parties when you have given us your explicit consent to do so.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">6. Data Retention</h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                We retain your personal information for as long as necessary to:
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li>Provide you with the Services you have requested</li>
                <li>Comply with legal obligations (e.g., tax and accounting requirements)</li>
                <li>Resolve disputes and enforce our agreements</li>
                <li>Support business operations and improve our Services</li>
              </ul>
              <p className="text-muted-foreground leading-relaxed">
                When we no longer need to retain your personal information, we will securely delete or anonymize it in accordance with applicable laws.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">7. Data Security</h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                We implement appropriate technical and organizational measures to protect your personal information against unauthorized access, alteration, disclosure, or destruction. These measures include:
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li>Encryption of data in transit using TLS/SSL</li>
                <li>Encryption of sensitive data at rest</li>
                <li>Access controls and authentication requirements</li>
                <li>Regular security assessments and monitoring</li>
                <li>Employee training on data protection practices</li>
              </ul>
              <p className="text-muted-foreground leading-relaxed">
                However, no method of transmission over the Internet or electronic storage is 100% secure. While we strive to protect your personal information, we cannot guarantee its absolute security.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">8. Your Rights and Choices</h2>
              
              <h3 className="text-lg font-medium mb-3">8.1 Access and Portability</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                You have the right to request access to the personal information we hold about you and to receive a copy of that information in a structured, commonly used, and machine-readable format.
              </p>

              <h3 className="text-lg font-medium mb-3">8.2 Correction</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                You have the right to request that we correct any inaccurate or incomplete personal information we hold about you.
              </p>

              <h3 className="text-lg font-medium mb-3">8.3 Deletion</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                You have the right to request that we delete your personal information, subject to certain exceptions (e.g., legal obligations, legitimate business purposes).
              </p>

              <h3 className="text-lg font-medium mb-3">8.4 Restriction and Objection</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                You have the right to request that we restrict the processing of your personal information or to object to our processing based on legitimate interests.
              </p>

              <h3 className="text-lg font-medium mb-3">8.5 Withdraw Consent</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Where we rely on your consent to process your personal information, you have the right to withdraw that consent at any time.
              </p>

              <h3 className="text-lg font-medium mb-3">8.6 Exercising Your Rights</h3>
              <p className="text-muted-foreground leading-relaxed">
                To exercise any of these rights, please contact us at {companyEmail}. We will respond to your request within the timeframe required by applicable law (typically 30 days).
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">9. International Data Transfers</h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Your information may be transferred to and processed in countries other than the country in which you reside. These countries may have data protection laws that are different from the laws of your country.
              </p>
              <p className="text-muted-foreground leading-relaxed">
                When we transfer your information internationally, we take steps to ensure that appropriate safeguards are in place to protect your information, including through the use of Standard Contractual Clauses approved by the European Commission or other lawful transfer mechanisms.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">10. Cookies and Tracking Technologies</h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                We use cookies and similar tracking technologies to collect and track information about your use of our Services. Cookies are small data files stored on your device.
              </p>
              
              <h3 className="text-lg font-medium mb-3">10.1 Types of Cookies We Use</h3>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li><strong>Essential Cookies:</strong> Required for the operation of our Services (e.g., authentication, security)</li>
                <li><strong>Functional Cookies:</strong> Enable personalized features and remember your preferences</li>
                <li><strong>Analytics Cookies:</strong> Help us understand how users interact with our Services</li>
              </ul>

              <h3 className="text-lg font-medium mb-3">10.2 Managing Cookies</h3>
              <p className="text-muted-foreground leading-relaxed">
                You can control and manage cookies through your browser settings. Please note that disabling certain cookies may affect the functionality of our Services.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">11. California Privacy Rights (CCPA)</h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                If you are a California resident, you have additional rights under the California Consumer Privacy Act (CCPA):
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li><strong>Right to Know:</strong> You can request information about the categories and specific pieces of personal information we have collected about you</li>
                <li><strong>Right to Delete:</strong> You can request that we delete your personal information, subject to certain exceptions</li>
                <li><strong>Right to Opt-Out:</strong> You can opt-out of the sale of your personal information (note: we do not sell personal information)</li>
                <li><strong>Right to Non-Discrimination:</strong> We will not discriminate against you for exercising your privacy rights</li>
              </ul>
              <p className="text-muted-foreground leading-relaxed">
                To exercise your California privacy rights, please contact us at {companyEmail}.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">12. Children's Privacy</h2>
              <p className="text-muted-foreground leading-relaxed">
                Our Services are not directed to individuals under the age of 18. We do not knowingly collect personal information from children. If we become aware that we have collected personal information from a child, we will take steps to delete such information promptly.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">13. Changes to This Privacy Policy</h2>
              <p className="text-muted-foreground leading-relaxed">
                We may update this Privacy Policy from time to time. We will notify you of any material changes by posting the new Privacy Policy on this page and updating the "Last Updated" date. We encourage you to review this Privacy Policy periodically. Your continued use of the Services after any changes constitutes your acceptance of the updated Privacy Policy.
              </p>
            </section>

            <Separator className="my-8" />

            <section>
              <h2 className="text-xl font-semibold mb-4">14. Contact Us</h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                If you have any questions, concerns, or requests regarding this Privacy Policy or our data practices, please contact us at:
              </p>
              <div className="bg-muted/50 rounded-md p-4">
                <p className="font-medium">{companyName}</p>
                <p className="text-muted-foreground">Email: {companyEmail}</p>
                <p className="text-muted-foreground mt-2">
                  For EU residents, you may also have the right to lodge a complaint with your local data protection authority.
                </p>
              </div>
            </section>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
