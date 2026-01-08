import { Navigation } from "@/components/navigation";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

export default function TermsPage() {
  const lastUpdated = "January 8, 2026";
  const companyName = "MarketingHandoffAI";
  const companyEmail = "legal@marketinghandoffai.com";

  return (
    <div className="min-h-screen bg-background">
      <Navigation />

      <main className="max-w-4xl mx-auto px-6 pt-24 pb-16">
        <div className="mb-8">
          <h1 className="text-3xl font-bold tracking-tight mb-2" data-testid="text-terms-title">
            Terms and Conditions
          </h1>
          <p className="text-muted-foreground">
            Last Updated: {lastUpdated}
          </p>
        </div>

        <Card>
          <CardContent className="prose prose-neutral dark:prose-invert max-w-none p-8">
            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">1. Agreement to Terms</h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                These Terms and Conditions ("Terms") constitute a legally binding agreement between you ("Client," "you," or "your") and {companyName} ("Company," "we," "us," or "our") governing your access to and use of our SEO optimization, AI discovery optimization, and related digital marketing services (collectively, the "Services").
              </p>
              <p className="text-muted-foreground leading-relaxed mb-4">
                By accessing our platform, submitting a project, or purchasing our Services, you acknowledge that you have read, understood, and agree to be bound by these Terms. If you are entering into these Terms on behalf of a company or other legal entity, you represent that you have the authority to bind such entity to these Terms.
              </p>
              <p className="text-muted-foreground leading-relaxed">
                If you do not agree to these Terms, you must not access or use our Services.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">2. Description of Services</h2>
              <h3 className="text-lg font-medium mb-3">2.1 Service Offerings</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                {companyName} provides professional SEO optimization and AI discovery optimization services designed to improve your digital content's visibility in search engines and AI-powered discovery systems. Our Services are offered through tiered engagement packages:
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li><strong>Foundation Package ($5,000):</strong> Core SEO audit, keyword optimization, and basic AI readiness assessment</li>
                <li><strong>Growth Package ($10,000):</strong> Comprehensive optimization including content strategy, technical SEO, and enhanced AI optimization</li>
                <li><strong>Enterprise Package ($15,000):</strong> Full-service optimization with dedicated support, advanced analytics, and priority implementation</li>
              </ul>

              <h3 className="text-lg font-medium mb-3">2.2 Deliverables</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Specific deliverables for each project will be outlined in your project submission and confirmed upon engagement. Deliverables may include, but are not limited to: SEO audit reports, keyword research documentation, content optimization recommendations, technical implementation guides, and progress reports.
              </p>

              <h3 className="text-lg font-medium mb-3">2.3 Timeline</h3>
              <p className="text-muted-foreground leading-relaxed">
                Project timelines vary based on the selected package and scope of work. Estimated completion timelines will be communicated during the project initiation phase. We make commercially reasonable efforts to meet stated timelines; however, actual completion times may vary based on project complexity and Client responsiveness.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">3. Payment Terms</h2>
              <h3 className="text-lg font-medium mb-3">3.1 Fees and Payment</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                All Services require full payment in advance before work commences. Payment is processed through our secure payment processor (Stripe). By submitting payment, you authorize us to charge your selected payment method for the total amount of your chosen package.
              </p>

              <h3 className="text-lg font-medium mb-3">3.2 Pricing</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                All prices are quoted in United States Dollars (USD) and are exclusive of any applicable taxes. You are responsible for paying any taxes, duties, or other governmental levies associated with your purchase.
              </p>

              <h3 className="text-lg font-medium mb-3">3.3 Refund Policy</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Due to the nature of professional services, refunds are handled on a case-by-case basis:
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li><strong>Before Work Commences:</strong> Full refund available upon written request within 7 days of payment if no work has begun</li>
                <li><strong>After Work Commences:</strong> Partial refunds may be considered based on work completed, at Company's sole discretion</li>
                <li><strong>Completed Projects:</strong> No refunds are available for completed deliverables</li>
              </ul>

              <h3 className="text-lg font-medium mb-3">3.4 Chargebacks</h3>
              <p className="text-muted-foreground leading-relaxed">
                In the event of a chargeback or payment dispute, we reserve the right to suspend Services immediately and pursue collection of any amounts owed, including reasonable attorney's fees and collection costs.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">4. Client Responsibilities and Warranties</h2>
              <h3 className="text-lg font-medium mb-3">4.1 Information Accuracy</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                You represent and warrant that all information provided to us, including but not limited to company information, website URLs, keywords, and content, is accurate, complete, and not misleading. You are solely responsible for the accuracy of information provided.
              </p>

              <h3 className="text-lg font-medium mb-3">4.2 Content Ownership and Compliance</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                You represent and warrant that:
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li>You own or have the legal right to use all content, materials, and intellectual property you provide to us</li>
                <li>Your content does not infringe upon any third party's intellectual property rights, privacy rights, or other legal rights</li>
                <li>Your content complies with all applicable laws and regulations, including but not limited to advertising standards, consumer protection laws, and industry-specific regulations</li>
                <li>You have obtained all necessary consents, permissions, and licenses required for us to use your content in performing the Services</li>
              </ul>

              <h3 className="text-lg font-medium mb-3">4.3 Website Access</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Where Services require access to your website or digital properties, you agree to provide necessary access credentials in a timely manner. You remain solely responsible for maintaining the security of your systems and for any changes made to your website or digital properties.
              </p>

              <h3 className="text-lg font-medium mb-3">4.4 Legal Compliance</h3>
              <p className="text-muted-foreground leading-relaxed">
                You are solely responsible for ensuring that your website, business practices, and use of our deliverables comply with all applicable laws and regulations, including but not limited to the General Data Protection Regulation (GDPR), California Consumer Privacy Act (CCPA), CAN-SPAM Act, and any industry-specific regulations applicable to your business.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">5. Intellectual Property Rights</h2>
              <h3 className="text-lg font-medium mb-3">5.1 Client Materials</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                You retain all ownership rights in the content, materials, and intellectual property you provide to us ("Client Materials"). You grant us a limited, non-exclusive license to use Client Materials solely for the purpose of performing the Services.
              </p>

              <h3 className="text-lg font-medium mb-3">5.2 Deliverables</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Upon full payment, you will own all rights to the specific deliverables created for your project, including reports, recommendations, and customized content. However, we retain ownership of:
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li>Our proprietary methodologies, processes, tools, and know-how</li>
                <li>Generic templates, frameworks, and materials not specifically customized for you</li>
                <li>Any pre-existing intellectual property incorporated into deliverables</li>
              </ul>

              <h3 className="text-lg font-medium mb-3">5.3 Portfolio Rights</h3>
              <p className="text-muted-foreground leading-relaxed">
                Unless you notify us otherwise in writing, we may reference our engagement with you in our portfolio, marketing materials, and case studies, provided that we do not disclose confidential information.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">6. Confidentiality</h2>
              <h3 className="text-lg font-medium mb-3">6.1 Confidential Information</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Each party agrees to maintain the confidentiality of the other party's confidential information and not to disclose such information to any third party without prior written consent, except as required by law or to perform the Services.
              </p>

              <h3 className="text-lg font-medium mb-3">6.2 Exclusions</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Confidential information does not include information that: (a) is or becomes publicly available through no fault of the receiving party; (b) was known to the receiving party prior to disclosure; (c) is independently developed by the receiving party; or (d) is rightfully obtained from a third party without restriction.
              </p>

              <h3 className="text-lg font-medium mb-3">6.3 Duration</h3>
              <p className="text-muted-foreground leading-relaxed">
                Confidentiality obligations shall survive termination of these Terms and continue for a period of three (3) years thereafter.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">7. Disclaimers and Limitations</h2>
              <h3 className="text-lg font-medium mb-3">7.1 No Guarantee of Results</h3>
              <p className="text-muted-foreground leading-relaxed mb-4 font-medium">
                YOU EXPRESSLY ACKNOWLEDGE AND AGREE THAT WE DO NOT AND CANNOT GUARANTEE SPECIFIC RESULTS FROM OUR SERVICES. SEO and digital marketing outcomes are influenced by numerous factors beyond our control, including but not limited to:
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li>Search engine algorithm changes and updates (Google, Bing, etc.)</li>
                <li>AI system behavior and algorithm modifications</li>
                <li>Competitor activities and market conditions</li>
                <li>Changes to your website or business practices</li>
                <li>Third-party platform policies and changes</li>
                <li>Overall market and economic conditions</li>
              </ul>

              <h3 className="text-lg font-medium mb-3">7.2 Third-Party Platforms</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Our Services may involve optimization for third-party platforms including, but not limited to, Google, Bing, ChatGPT, and other search engines and AI systems. We have no control over these platforms and cannot guarantee their behavior, policies, or ranking decisions. Changes to these platforms may affect the results of our Services without notice.
              </p>

              <h3 className="text-lg font-medium mb-3">7.3 Disclaimer of Warranties</h3>
              <p className="text-muted-foreground leading-relaxed mb-4 font-medium">
                TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, OUR SERVICES ARE PROVIDED "AS IS" AND "AS AVAILABLE" WITHOUT WARRANTIES OF ANY KIND, WHETHER EXPRESS, IMPLIED, OR STATUTORY, INCLUDING BUT NOT LIMITED TO IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE, AND NON-INFRINGEMENT.
              </p>

              <h3 className="text-lg font-medium mb-3">7.4 Professional Services Standard</h3>
              <p className="text-muted-foreground leading-relaxed">
                We will perform our Services with reasonable skill and care consistent with industry standards for similar professional services. This is our sole warranty regarding the quality of our Services.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">8. Limitation of Liability</h2>
              <h3 className="text-lg font-medium mb-3">8.1 Exclusion of Damages</h3>
              <p className="text-muted-foreground leading-relaxed mb-4 font-medium">
                TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, IN NO EVENT SHALL {companyName.toUpperCase()}, ITS OFFICERS, DIRECTORS, EMPLOYEES, AGENTS, OR AFFILIATES BE LIABLE FOR ANY:
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li>Indirect, incidental, special, consequential, or punitive damages</li>
                <li>Loss of profits, revenue, business opportunities, or goodwill</li>
                <li>Loss of data or business interruption</li>
                <li>Damages arising from third-party claims</li>
                <li>Damages resulting from search engine ranking changes or algorithm updates</li>
                <li>Damages resulting from AI system behavior or policy changes</li>
              </ul>

              <h3 className="text-lg font-medium mb-3">8.2 Liability Cap</h3>
              <p className="text-muted-foreground leading-relaxed mb-4 font-medium">
                TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, OUR TOTAL CUMULATIVE LIABILITY ARISING OUT OF OR RELATED TO THESE TERMS OR THE SERVICES SHALL NOT EXCEED THE TOTAL AMOUNT PAID BY YOU TO US FOR THE SPECIFIC PROJECT GIVING RISE TO THE CLAIM DURING THE SIX (6) MONTH PERIOD IMMEDIATELY PRECEDING THE CLAIM.
              </p>

              <h3 className="text-lg font-medium mb-3">8.3 Essential Basis</h3>
              <p className="text-muted-foreground leading-relaxed">
                You acknowledge that the limitations of liability in this Section reflect a reasonable allocation of risk and are a fundamental element of the basis of the bargain between you and us. Our Services would not be provided without such limitations.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">9. Indemnification</h2>
              <h3 className="text-lg font-medium mb-3">9.1 Client Indemnification</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                You agree to indemnify, defend, and hold harmless {companyName}, its officers, directors, employees, agents, and affiliates from and against any and all claims, damages, losses, liabilities, costs, and expenses (including reasonable attorney's fees) arising out of or relating to:
              </p>
              <ul className="list-disc pl-6 text-muted-foreground space-y-2 mb-4">
                <li>Your breach of these Terms or any representation or warranty herein</li>
                <li>Your content, materials, or intellectual property</li>
                <li>Your violation of any applicable law or regulation</li>
                <li>Your infringement of any third party's rights</li>
                <li>Any claim that your content caused damage to a third party</li>
                <li>Your website or business practices</li>
              </ul>

              <h3 className="text-lg font-medium mb-3">9.2 Company Indemnification</h3>
              <p className="text-muted-foreground leading-relaxed">
                We agree to indemnify and hold you harmless from claims arising from our gross negligence or willful misconduct in performing the Services, or our infringement of a third party's intellectual property rights through materials we create (excluding materials based on your content or direction).
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">10. Term and Termination</h2>
              <h3 className="text-lg font-medium mb-3">10.1 Term</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                These Terms are effective upon your acceptance (by accessing our platform or purchasing Services) and continue until all Services have been completed and delivered, or until terminated in accordance with this Section.
              </p>

              <h3 className="text-lg font-medium mb-3">10.2 Termination by Client</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                You may terminate your project by providing written notice to us. Upon termination: (a) you remain liable for all fees for work completed prior to termination; (b) no refund will be provided for work already performed; and (c) we will deliver any completed deliverables.
              </p>

              <h3 className="text-lg font-medium mb-3">10.3 Termination by Company</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                We may terminate or suspend Services immediately, without prior notice, if: (a) you breach any provision of these Terms; (b) you fail to provide required information or access; (c) we determine, in our sole discretion, that your content or business practices violate applicable law or our policies; or (d) continuing the engagement would expose us to legal or reputational risk.
              </p>

              <h3 className="text-lg font-medium mb-3">10.4 Effect of Termination</h3>
              <p className="text-muted-foreground leading-relaxed">
                Upon termination: (a) all licenses granted hereunder terminate; (b) each party shall return or destroy the other party's confidential information; (c) you shall pay all amounts due for Services rendered; (d) Sections regarding Intellectual Property, Confidentiality, Disclaimers, Limitation of Liability, Indemnification, and Dispute Resolution shall survive.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">11. Data Protection and Privacy</h2>
              <h3 className="text-lg font-medium mb-3">11.1 Data Processing</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                We collect and process personal data in accordance with our Privacy Policy. By using our Services, you consent to such processing and warrant that all data provided to us has been lawfully collected and that you have the right to share such data with us.
              </p>

              <h3 className="text-lg font-medium mb-3">11.2 Data Protection Compliance</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Where we process personal data on your behalf, we act as a data processor under applicable data protection laws. We implement appropriate technical and organizational measures to protect personal data against unauthorized access, alteration, disclosure, or destruction.
              </p>

              <h3 className="text-lg font-medium mb-3">11.3 International Data Transfers</h3>
              <p className="text-muted-foreground leading-relaxed">
                Our Services may involve the transfer of data to jurisdictions outside your country of residence. By using our Services, you consent to such transfers, which will be conducted in accordance with applicable data protection laws and appropriate safeguards.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">12. Dispute Resolution</h2>
              <h3 className="text-lg font-medium mb-3">12.1 Governing Law</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                These Terms shall be governed by and construed in accordance with the laws of the State of Delaware, United States, without regard to its conflict of law principles.
              </p>

              <h3 className="text-lg font-medium mb-3">12.2 Informal Resolution</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Before initiating any formal dispute resolution proceeding, both parties agree to first attempt to resolve disputes informally by contacting the other party in writing and engaging in good-faith negotiations for a period of at least thirty (30) days.
              </p>

              <h3 className="text-lg font-medium mb-3">12.3 Binding Arbitration</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                If informal resolution is unsuccessful, any dispute, controversy, or claim arising out of or relating to these Terms shall be finally settled by binding arbitration administered by the American Arbitration Association under its Commercial Arbitration Rules. The arbitration shall be conducted in English, and judgment on the award may be entered in any court having jurisdiction.
              </p>

              <h3 className="text-lg font-medium mb-3">12.4 Class Action Waiver</h3>
              <p className="text-muted-foreground leading-relaxed mb-4 font-medium">
                YOU AND {companyName.toUpperCase()} AGREE THAT EACH MAY BRING CLAIMS AGAINST THE OTHER ONLY IN YOUR OR ITS INDIVIDUAL CAPACITY AND NOT AS A PLAINTIFF OR CLASS MEMBER IN ANY PURPORTED CLASS OR REPRESENTATIVE PROCEEDING.
              </p>

              <h3 className="text-lg font-medium mb-3">12.5 Exceptions</h3>
              <p className="text-muted-foreground leading-relaxed">
                Notwithstanding the foregoing, either party may seek injunctive or other equitable relief in any court of competent jurisdiction to protect its intellectual property rights or confidential information.
              </p>
            </section>

            <Separator className="my-8" />

            <section className="mb-8">
              <h2 className="text-xl font-semibold mb-4">13. General Provisions</h2>
              <h3 className="text-lg font-medium mb-3">13.1 Entire Agreement</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                These Terms, together with our Privacy Policy and any project-specific documentation, constitute the entire agreement between you and us regarding the Services and supersede all prior agreements, understandings, and representations.
              </p>

              <h3 className="text-lg font-medium mb-3">13.2 Amendments</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                We reserve the right to modify these Terms at any time. Material changes will be communicated to active clients via email. Your continued use of our Services following notification of changes constitutes acceptance of the modified Terms.
              </p>

              <h3 className="text-lg font-medium mb-3">13.3 Waiver</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Our failure to enforce any right or provision of these Terms shall not be deemed a waiver of such right or provision. Any waiver must be in writing and signed by an authorized representative.
              </p>

              <h3 className="text-lg font-medium mb-3">13.4 Severability</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                If any provision of these Terms is held to be invalid, illegal, or unenforceable, such provision shall be modified to the minimum extent necessary to make it valid and enforceable, or if modification is not possible, severed from these Terms, and the remaining provisions shall continue in full force and effect.
              </p>

              <h3 className="text-lg font-medium mb-3">13.5 Assignment</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                You may not assign or transfer these Terms or any rights hereunder without our prior written consent. We may assign these Terms to an affiliate or in connection with a merger, acquisition, or sale of all or substantially all of our assets.
              </p>

              <h3 className="text-lg font-medium mb-3">13.6 Force Majeure</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                Neither party shall be liable for any failure or delay in performance due to causes beyond its reasonable control, including but not limited to acts of God, natural disasters, war, terrorism, labor disputes, government actions, internet or telecommunications failures, or changes to third-party platforms or algorithms.
              </p>

              <h3 className="text-lg font-medium mb-3">13.7 Independent Contractors</h3>
              <p className="text-muted-foreground leading-relaxed mb-4">
                The relationship between you and us is that of independent contractors. Nothing in these Terms shall be construed to create a partnership, joint venture, agency, or employment relationship.
              </p>

              <h3 className="text-lg font-medium mb-3">13.8 Notices</h3>
              <p className="text-muted-foreground leading-relaxed">
                All notices under these Terms shall be in writing and sent to the email address associated with your account (for notices to you) or to {companyEmail} (for notices to us). Notices are effective upon confirmed delivery.
              </p>
            </section>

            <Separator className="my-8" />

            <section>
              <h2 className="text-xl font-semibold mb-4">14. Contact Information</h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                If you have any questions about these Terms and Conditions, please contact us at:
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
            By using our Services, you acknowledge that you have read, understood, and agree to be bound by these Terms and Conditions.
          </p>
        </div>
      </main>
    </div>
  );
}
