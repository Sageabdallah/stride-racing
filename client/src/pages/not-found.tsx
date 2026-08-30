import { Card, CardContent } from "@/components/ui/card";
import { AlertCircle } from "lucide-react";

export default function NotFound() {
  return (
    <div className="min-h-screen w-full flex items-center justify-center bg-black font-dm">
      <Card className="w-full max-w-md mx-4 glass-card border-0">
        <CardContent className="pt-6">
          <div className="flex mb-4 gap-2">
            <AlertCircle className="h-8 w-8 text-racing-orange" />
            <h1 className="text-2xl font-bold text-white font-syne">404 Page Not Found</h1>
          </div>

          <p className="mt-4 text-sm text-white/40">
            Did you forget to add the page to the router?
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
