import { Hero } from "@/components/ui/helix-hero";
import Link from "next/link";

export default function Home() {
  return (
    <main>
      {/* Navigation */}
      <nav className="absolute top-0 left-0 right-0 z-30 flex items-center justify-between p-6 md:p-8">
        <div className="text-xl font-bold text-gray-900">Talky.ai</div>
        <div className="flex items-center gap-4">
          <Link
            href="/auth/login"
            className="text-sm text-gray-600 hover:text-gray-900 transition-colors"
          >
            Sign In
          </Link>
          <Link
            href="/auth/register"
            className="px-4 py-2 text-sm font-medium text-white bg-gray-900 rounded-md hover:bg-gray-800 transition-colors"
          >
            Get Started
          </Link>
        </div>
      </nav>

      <Hero
        title="AI Voice Dialer"
        description="Intelligent voice communication platform powered by advanced AI agents. 
        Real-time speech recognition, natural language processing, and seamless 
        call automation for enterprise-scale outbound campaigns."
        stats={[
          { label: "Response Time", value: "<500ms" },
          { label: "Concurrent Calls", value: "1000+" },
          { label: "Completion Rate", value: "94%" },
        ]}
      />
    </main>
  );
}
