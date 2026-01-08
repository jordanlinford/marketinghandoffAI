import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { X } from "lucide-react";

export function CookieConsent() {
  const [showBanner, setShowBanner] = useState(false);

  useEffect(() => {
    const consent = localStorage.getItem("cookie-consent");
    if (!consent) {
      setShowBanner(true);
    }
  }, []);

  const acceptCookies = () => {
    localStorage.setItem("cookie-consent", "accepted");
    setShowBanner(false);
  };

  const declineCookies = () => {
    localStorage.setItem("cookie-consent", "declined");
    setShowBanner(false);
  };

  if (!showBanner) return null;

  return (
    <div 
      className="fixed bottom-0 left-0 right-0 z-50 bg-card border-t border-border p-4 shadow-lg"
      data-testid="cookie-consent-banner"
    >
      <div className="max-w-7xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
        <div className="flex-1">
          <p className="text-sm text-muted-foreground">
            We use cookies to improve your experience. By continuing to use this site, you accept our{" "}
            <a href="/privacy" className="text-foreground underline hover:text-primary">
              Privacy Policy
            </a>{" "}
            and{" "}
            <a href="/terms" className="text-foreground underline hover:text-primary">
              Terms
            </a>
            .
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={declineCookies}
            data-testid="button-decline-cookies"
          >
            Decline
          </Button>
          <Button
            size="sm"
            onClick={acceptCookies}
            data-testid="button-accept-cookies"
          >
            Accept
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={declineCookies}
            className="sm:hidden"
            data-testid="button-close-cookies"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
