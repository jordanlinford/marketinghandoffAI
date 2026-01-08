import { Link } from "wouter";
import { Navigation } from "@/components/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { XCircle, ArrowLeft, ArrowRight } from "lucide-react";

export default function CheckoutCancelPage() {
  return (
    <div className="min-h-screen bg-background">
      <Navigation />

      <main className="max-w-2xl mx-auto px-6 pt-24 pb-12">
        <Card className="text-center py-12" data-testid="card-checkout-cancel">
          <CardContent>
            <div className="w-16 h-16 rounded-full bg-muted flex items-center justify-center mx-auto mb-6">
              <XCircle className="h-8 w-8 text-muted-foreground" />
            </div>
            
            <h1 className="text-3xl font-bold tracking-tight mb-4" data-testid="text-cancel-title">
              Payment Cancelled
            </h1>
            
            <p className="text-muted-foreground mb-8 max-w-md mx-auto">
              Your payment was cancelled and you have not been charged. 
              You can try again whenever you're ready.
            </p>

            <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
              <Link href="/new-project">
                <Button data-testid="button-try-again">
                  Try Again
                  <ArrowRight className="h-4 w-4 ml-2" />
                </Button>
              </Link>
              <Link href="/dashboard">
                <Button variant="outline" data-testid="button-back-dashboard">
                  <ArrowLeft className="h-4 w-4 mr-2" />
                  Back to Dashboard
                </Button>
              </Link>
            </div>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
